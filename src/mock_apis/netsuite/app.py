"""NetSuite (mock): SuiteQL over REST for one account, Vandelay Industries.

Authentication
    Token-Based Authentication: OAuth 1.0a with HMAC-SHA256. The ``Authorization``
    header is ``OAuth realm="<ACCOUNT>", oauth_consumer_key=..., oauth_token=...,
    oauth_signature_method="HMAC-SHA256", oauth_timestamp, oauth_nonce,
    oauth_version="1.0", oauth_signature``. The signature base string is
    ``METHOD&enc(URL without query)&enc(sorted, encoded query + oauth params)``, keyed
    with ``enc(consumer_secret)&enc(token_secret)``. Timestamps must be within 300s
    and nonces may not repeat. Every failure is ``401 INVALID_LOGIN_ATTEMPT``.

Endpoint
    ``POST /services/rest/query/v1/suiteql?limit=&offset=`` with the
    ``Prefer: transient`` header and the body ``{"q": "<SuiteQL>"}``.
    - ``limit`` is 1-1000 (default 1000). ``offset`` must be a multiple of
      ``limit`` and below 100,000.
    - Responses are ``{links, count, hasMore, items, offset, totalResults}``, and
      each item has its own ``links``.

SuiteQL subset
    ``SELECT <col [AS alias] | TO_CHAR(col, 'fmt') [AS alias] | *> FROM <table>
    [WHERE <col> >= TO_DATE('<value>', 'YYYY-MM-DD HH24:MI:SS')]
    [ORDER BY <col> [ASC|DESC], ...]``. ``TO_CHAR`` accepts ``'YYYY-MM-DD'`` and
    ``'YYYY-MM-DD HH24:MI:SS'``.
    As in SuiteQL:
    - every value is a string, and booleans are ``"T"``/``"F"``;
    - raw dates use the account's ``M/D/YYYY`` format, and datetimes are
      ``M/D/YYYY h:mm am``;
    - null fields are left out of the item.

Errors
    ``{"type", "title", "status", "o:errorDetails": [{"detail", "o:errorCode"}]}``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_apis.netsuite.data import COLUMNS, generate

SUITEQL_PATH = "/services/rest/query/v1/suiteql"
ACCOUNT_ID = "VANDELAY_SB1"
DEMO_CREDENTIALS = {
    "account_id": ACCOUNT_ID,
    "consumer_key": "vandelay-demo-consumer-key",
    "consumer_secret": "vandelay-demo-consumer-secret",
    "token_id": "vandelay-demo-token-id",
    "token_secret": "vandelay-demo-token-secret",
}
MAX_LIMIT, MAX_OFFSET = 1000, 100_000
MAX_SKEW_SECONDS = 300
PARAMS = ("limit", "offset")
SQL_FORMATS = {"YYYY-MM-DD": "%Y-%m-%d", "YYYY-MM-DD HH24:MI:SS": "%Y-%m-%d %H:%M:%S"}
GRAMMAR = (
    "The mock supports SELECT <column [AS alias] | TO_CHAR(column, 'format') [AS alias] | *> "
    "FROM <table> [WHERE <column> >= TO_DATE('<value>', 'YYYY-MM-DD HH24:MI:SS')] "
    "[ORDER BY <column> [ASC|DESC], ...]"
)
STATUS_TITLES = {400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
                 405: "Method Not Allowed"}  # fmt: skip


class NetSuiteError(Exception):
    def __init__(self, status: int, code: str, *details: str) -> None:
        self.status, self.code, self.details = status, code, details

    def response(self) -> JSONResponse:
        return JSONResponse(
            {
                "type": "https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5."
                + str(self.status - 399),
                "title": STATUS_TITLES.get(self.status, "Error"),
                "status": self.status,
                "o:errorDetails": [
                    {"detail": detail, "o:errorCode": self.code} for detail in self.details
                ],
            },
            self.status,
        )


def _invalid_query(message: str) -> NetSuiteError:
    return NetSuiteError(
        400,
        "INVALID_PARAMETER",
        "Invalid search query. Detailed unprocessed description follows. "
        f"Search error occurred: {message}",
    )


def _login_failure(reason: str) -> NetSuiteError:
    return NetSuiteError(
        401,
        "INVALID_LOGIN_ATTEMPT",
        f"Invalid login attempt: {reason}. For more details, see the Login Audit Trail "
        "in the NetSuite UI at Setup > Users/Roles > User Management > View Login Audit Trail.",
    )


# --- SuiteQL ------------------------------------------------------------------------


@dataclass(frozen=True)
class Selected:
    column: str
    alias: str
    fmt: str | None = None  # TO_CHAR format


@dataclass(frozen=True)
class Query:
    table: str
    columns: list[Selected]
    lower_bound: tuple[str, datetime] | None
    order_by: list[tuple[str, bool]]  # (column, descending)


_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_QUERY = re.compile(
    rf"^\s*SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<table>{_IDENT})"
    rf"(?:\s+WHERE\s+(?P<where>.+?))?"
    rf"(?:\s+ORDER\s+BY\s+(?P<order>.+?))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TO_CHAR = re.compile(
    rf"^TO_CHAR\(\s*(?P<col>{_IDENT})\s*,\s*'(?P<fmt>[^']*)'\s*\)(?:\s+AS\s+(?P<alias>{_IDENT}))?$",
    re.IGNORECASE,
)
_COLUMN = re.compile(rf"^(?P<col>{_IDENT})(?:\s+AS\s+(?P<alias>{_IDENT}))?$", re.IGNORECASE)
_WHERE = re.compile(
    rf"^(?P<col>{_IDENT})\s*>=\s*TO_DATE\(\s*'(?P<value>[^']*)'\s*,\s*'(?P<fmt>[^']*)'\s*\)$",
    re.IGNORECASE,
)
_ORDER = re.compile(rf"^(?P<col>{_IDENT})(?:\s+(?P<dir>ASC|DESC))?$", re.IGNORECASE)
_UNSUPPORTED = re.compile(r"\b(JOIN|GROUP\s+BY|HAVING|UNION|BUILTIN\.\w+)\b", re.IGNORECASE)


def _split(text: str) -> list[str]:
    """Split a comma-separated list, ignoring commas inside parentheses or quotes."""
    parts, depth, quoted, current = [], 0, False, ""
    for char in text:
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")":
            depth -= 1
        if char == "," and depth == 0 and not quoted:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return parts


def _column(table: str, name: str) -> str:
    column = name.lower()
    if column not in COLUMNS[table]:
        raise _invalid_query(f"Field '{name}' for record '{table}' was not found.")
    return column


def parse(sql: str) -> Query:
    match = _QUERY.match(sql)
    if not match or _UNSUPPORTED.search(sql):
        raise _invalid_query(f"unsupported query. {GRAMMAR}")
    table = match["table"].lower()
    if table not in COLUMNS:
        raise _invalid_query(f"Record '{match['table']}' was not found.")

    columns: list[Selected] = []
    for part in _split(match["select"]):
        if part == "*":
            columns += [Selected(c, c) for c in COLUMNS[table]]
        elif m := _TO_CHAR.match(part):
            column = _column(table, m["col"])
            if COLUMNS[table][column] not in ("date", "datetime"):
                raise _invalid_query(f"TO_CHAR: field '{column}' on '{table}' is not a date")
            if m["fmt"] not in SQL_FORMATS:
                raise _invalid_query(
                    f"TO_CHAR format '{m['fmt']}' is not supported; use one of "
                    + ", ".join(f"'{f}'" for f in SQL_FORMATS)
                )
            columns.append(Selected(column, (m["alias"] or column).lower(), m["fmt"]))
        elif m := _COLUMN.match(part):
            column = _column(table, m["col"])
            columns.append(Selected(column, (m["alias"] or column).lower()))
        else:
            raise _invalid_query(f"cannot select {part!r}. {GRAMMAR}")

    lower_bound = None
    if match["where"]:
        m = _WHERE.match(match["where"].strip())
        if not m:
            raise _invalid_query(f"unsupported WHERE clause {match['where'].strip()!r}. {GRAMMAR}")
        column = _column(table, m["col"])
        if COLUMNS[table][column] not in ("date", "datetime"):
            raise _invalid_query(f"TO_DATE: field '{column}' on '{table}' is not a date")
        if m["fmt"] not in SQL_FORMATS:
            raise _invalid_query(f"TO_DATE format '{m['fmt']}' is not supported. {GRAMMAR}")
        try:
            bound = datetime.strptime(m["value"], SQL_FORMATS[m["fmt"]])
        except ValueError:
            raise _invalid_query(
                f"TO_DATE value '{m['value']}' does not match format '{m['fmt']}'"
            ) from None
        lower_bound = (column, bound)

    order_by: list[tuple[str, bool]] = []
    for part in _split(match["order"]) if match["order"] else []:
        m = _ORDER.match(part)
        if not m:
            raise _invalid_query(f"unsupported ORDER BY term {part!r}. {GRAMMAR}")
        order_by.append((_column(table, m["col"]), (m["dir"] or "").upper() == "DESC"))
    return Query(table, columns, lower_bound, order_by)


def _comparable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return value


def run(query: Query, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if query.lower_bound:
        column, bound = query.lower_bound
        rows = [r for r in rows if r.get(column) is not None and _comparable(r[column]) >= bound]
    for column, descending in reversed(query.order_by):
        present = sorted((r for r in rows if r.get(column) is not None),
                         key=lambda r, c=column: r[c], reverse=descending)  # fmt: skip
        rows = present + [r for r in rows if r.get(column) is None]  # nulls last
    return rows


def _render(value: Any, kind: str, fmt: str | None) -> str | None:
    if value is None:
        return None
    if fmt:
        return value.strftime(SQL_FORMATS[fmt])
    if kind == "bool":
        return "T" if value else "F"
    if kind == "date":
        return f"{value.month}/{value.day}/{value.year}"
    if kind == "datetime":
        hour = value.hour % 12 or 12
        return f"{value.month}/{value.day}/{value.year} {hour}:{value.minute:02d} " + (
            "pm" if value.hour >= 12 else "am"
        )
    if kind == "number":
        return f"{value:.2f}".rstrip("0").rstrip(".")  # amounts and hours: at most 2 dp
    return str(value)


def project(query: Query, row: dict[str, Any]) -> dict[str, Any]:
    kinds = COLUMNS[query.table]
    item: dict[str, Any] = {"links": []}
    for sel in query.columns:
        rendered = _render(row.get(sel.column), kinds[sel.column], sel.fmt)
        if rendered is not None:
            item[sel.alias] = rendered
    return item


# --- authentication -----------------------------------------------------------------


def _pct(value: str) -> str:
    return quote(value, safe="~-._")


def _verify(request: Request, credentials: dict[str, str], seen: set[str], now: float) -> None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("OAuth "):
        raise _login_failure("send an OAuth 1.0a Authorization header (Token-Based Authentication)")
    params = {k: unquote(v) for k, v in re.findall(r'(\w+)="([^"]*)"', header)}
    missing = [
        k
        for k in ("realm", "oauth_consumer_key", "oauth_token", "oauth_signature_method",
                  "oauth_timestamp", "oauth_nonce", "oauth_version", "oauth_signature")
        if not params.get(k)
    ]  # fmt: skip
    if missing:
        raise _login_failure(f"the Authorization header is missing {', '.join(missing)}")
    if params["realm"] != credentials["account_id"]:
        raise _login_failure(f"realm {params['realm']!r} is not this account")
    if params["oauth_consumer_key"] != credentials["consumer_key"]:
        raise _login_failure("the consumer key is not recognised")
    if params["oauth_token"] != credentials["token_id"]:
        raise _login_failure("the token is not recognised")
    if params["oauth_signature_method"] != "HMAC-SHA256":
        raise _login_failure("oauth_signature_method must be HMAC-SHA256")
    try:
        skew = abs(now - int(params["oauth_timestamp"]))
    except ValueError:
        raise _login_failure("oauth_timestamp must be unix seconds") from None
    if skew > MAX_SKEW_SECONDS:
        raise _login_failure(f"oauth_timestamp is {int(skew)}s from server time")

    oauth = {k: v for k, v in params.items() if k.startswith("oauth_") and k != "oauth_signature"}
    pairs = sorted(
        (_pct(k), _pct(v)) for k, v in [*request.query_params.multi_items(), *oauth.items()]
    )
    url = str(request.url.replace(query="", fragment=""))
    base_string = "&".join(
        [request.method.upper(), _pct(url), _pct("&".join(f"{k}={v}" for k, v in pairs))]
    )
    key = f"{_pct(credentials['consumer_secret'])}&{_pct(credentials['token_secret'])}"
    expected = base64.b64encode(
        hmac.new(key.encode(), base_string.encode(), hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, params["oauth_signature"]):
        raise _login_failure("the signature does not match the request")
    nonce = f"{params['oauth_token']}:{params['oauth_nonce']}"
    if nonce in seen:
        raise _login_failure("this nonce was already used; sign every request afresh")
    seen.add(nonce)


# --- request handling ---------------------------------------------------------------


def _paging(request: Request) -> tuple[int, int]:
    params = request.query_params
    problems = [f"Invalid query parameter: {name}" for name in params if name not in PARAMS]
    limit, offset = MAX_LIMIT, 0
    try:
        limit = int(params.get("limit", MAX_LIMIT))
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError
    except ValueError:
        problems.append(
            f"The limit query parameter must be between 1 and {MAX_LIMIT}; "
            f"got {params.get('limit')!r}"
        )
        limit = MAX_LIMIT
    try:
        offset = int(params.get("offset", 0))
        if offset < 0:
            raise ValueError
    except ValueError:
        problems.append(f"The offset query parameter must be 0 or more; got {params['offset']!r}")
    else:
        if offset % limit:
            problems.append(
                f"The offset query parameter must be a multiple of the limit ({limit}); "
                f"got {offset}"
            )
        if offset >= MAX_OFFSET:
            problems.append(
                f"The offset query parameter must be less than {MAX_OFFSET}; page larger "
                "result sets with a WHERE clause on an increasing column instead"
            )
    if problems:
        raise NetSuiteError(400, "INVALID_PARAMETER", *problems)
    return limit, offset


def _links(request: Request, limit: int, offset: int, has_more: bool) -> list[dict[str, str]]:
    def href(at: int) -> str:
        return str(request.url.include_query_params(limit=limit, offset=at))

    links = [{"rel": "self", "href": href(offset)}]
    if offset:
        links.append({"rel": "previous", "href": href(max(0, offset - limit))})
    if has_more:
        links.append({"rel": "next", "href": href(offset + limit)})
    return links


def create_app(
    *,
    dataset: dict[str, list[dict[str, Any]]] | None = None,
    credentials: dict[str, str] | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    app = FastAPI(title="NetSuite (mock)", docs_url="/docs", redoc_url=None)
    app.state.dataset = dataset if dataset is not None else generate()
    account = credentials or DEMO_CREDENTIALS
    seen_nonces: set[str] = set()

    @app.exception_handler(NetSuiteError)
    async def _netsuite_error(_: Request, exc: NetSuiteError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            message = f"No REST resource at {request.url.path}; available: POST {SUITEQL_PATH}"
            return NetSuiteError(404, "NONEXISTENT_ID", message).response()
        if exc.status_code == 405:
            message = f"{request.method} is not allowed on {request.url.path}; use POST"
            return NetSuiteError(405, "INVALID_METHOD", message).response()
        return NetSuiteError(exc.status_code, "UNEXPECTED_ERROR", str(exc.detail)).response()

    @app.post(SUITEQL_PATH)
    async def suiteql(request: Request) -> dict[str, Any]:
        _verify(request, account, seen_nonces, clock())
        if request.headers.get("Prefer", "").lower() != "transient":
            raise NetSuiteError(
                400,
                "INVALID_PARAMETER",
                "SuiteQL requests must include the header 'Prefer: transient'",
            )
        limit, offset = _paging(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("q"), str):
            raise NetSuiteError(
                400, "INVALID_PARAMETER", 'The request body must be a JSON object with a "q" string'
            )
        query = parse(body["q"])
        rows = run(query, app.state.dataset[query.table])
        page = rows[offset : offset + limit]
        has_more = offset + limit < len(rows)
        return {
            "links": _links(request, limit, offset, has_more),
            "count": len(page),
            "hasMore": has_more,
            "items": [project(query, row) for row in page],
            "offset": offset,
            "totalResults": len(rows),
        }

    return app
