"""Helpline: a second fictional support-desk REST API.

Authentication
    Every request is signed with a shared secret. Send ``X-Helpline-Key`` (key id),
    ``X-Helpline-Timestamp`` (unix seconds, within 300s of server time) and
    ``X-Helpline-Signature``: hex HMAC-SHA256 over
    ``METHOD\\nPATH\\nCANONICAL_QUERY\\nTIMESTAMP``, where CANONICAL_QUERY is the
    query parameters sorted by name and value, each percent-encoded
    (``quote(x, safe="")``) and joined as ``k=v`` with ``&``.

Changes feeds
    ``GET /v1/cases/changes`` and ``/v1/staff/changes``. Start with ``start_time``
    (ISO 8601 with a UTC offset; inclusive on ``updated_at``), then pass
    ``next_token`` back as ``since_token`` until ``has_more`` is false. Keep the last
    token to resume. Changes arrive in commit order; ``updated_at`` is when the
    edit started, so a change can surface after newer-looking ones.

Errors
    ``{"ok": false, "error", "message", "problems": {param: reason}}``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_apis.helpline.data import generate

DEMO_KEY_ID = "hooli-demo"
DEMO_SECRET = "hooli-demo-signing-secret"
MAX_SKEW_SECONDS = 300
DEFAULT_LIMIT, MAX_LIMIT = 100, 500
PARAMS = ("limit", "since_token", "start_time")
SIGNATURE_HEADERS = ("X-Helpline-Key", "X-Helpline-Timestamp", "X-Helpline-Signature")


@dataclass(frozen=True)
class Feed:
    path: str
    key: str
    ident: str


FEEDS = [
    Feed("/v1/cases/changes", "cases", "case_number"),
    Feed("/v1/staff/changes", "staff", "staff_id"),
]


class ApiError(Exception):
    def __init__(
        self, status: int, error: str, message: str, problems: dict[str, str] | None = None
    ) -> None:
        self.status, self.error, self.message, self.problems = status, error, message, problems

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {"ok": False, "error": self.error, "message": self.message}
        if self.problems is not None:
            body["problems"] = self.problems
        return JSONResponse(body, self.status)


def canonical_query(items: list[tuple[str, str]]) -> str:
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(items))


def _verify_signature(request: Request, keys: dict[str, str]) -> None:
    missing = [h for h in SIGNATURE_HEADERS if not request.headers.get(h)]
    if missing:
        raise ApiError(
            401,
            "missing_signature",
            f"Sign every request with the headers {', '.join(SIGNATURE_HEADERS)}; "
            f"missing: {', '.join(missing)}",
        )
    key_id = request.headers["X-Helpline-Key"]
    if key_id not in keys:
        raise ApiError(401, "unknown_key", f"X-Helpline-Key {key_id!r} is not recognised")
    stamp = request.headers["X-Helpline-Timestamp"]
    try:
        skew = abs(time.time() - int(stamp))
    except ValueError:
        raise ApiError(
            401, "invalid_timestamp", f"X-Helpline-Timestamp must be unix seconds; got {stamp!r}"
        ) from None
    if skew > MAX_SKEW_SECONDS:
        raise ApiError(
            401,
            "stale_request",
            f"request timestamp is {int(skew)}s from server time; allowed skew is "
            f"{MAX_SKEW_SECONDS}s. Sign each request with the current time.",
        )
    message = "\n".join(
        [
            request.method,
            request.url.path,
            canonical_query(list(request.query_params.multi_items())),
            stamp,
        ]
    )
    expected = hmac.new(keys[key_id].encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers["X-Helpline-Signature"]):
        raise ApiError(
            401,
            "invalid_signature",
            "signature does not match; send the hex HMAC-SHA256 of "
            "METHOD\\nPATH\\nCANONICAL_QUERY\\nTIMESTAMP using your signing secret",
        )


def _encode_token(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _decode_token(value: str) -> dict:
    try:
        payload = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError from exc
    if not isinstance(payload, dict) or not {"f", "c", "i", "s"} <= payload.keys():
        raise ValueError
    return payload


def _parse_start_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            "must be an ISO 8601 timestamp with a UTC offset, e.g. 2026-05-01T00:00:00Z; "
            f"got {value!r}"
        ) from None
    if parsed.tzinfo is None:
        raise ValueError(
            f"must include a UTC offset, e.g. 2026-05-01T00:00:00Z or -05:00; got {value!r}"
        )
    return parsed


def _validate(request: Request, feed: Feed) -> dict[str, Any]:
    params = request.query_params
    problems: dict[str, str] = {}
    allowed = ", ".join(PARAMS)
    for name in params:
        if name == "page":
            problems[name] = (
                f"unknown parameter; Helpline feeds page with since_token (allowed: {allowed})"
            )
        elif name not in PARAMS:
            problems[name] = f"unknown parameter; allowed: {allowed}"

    limit = DEFAULT_LIMIT
    if "limit" in params:
        try:
            limit = int(params["limit"])
            if not 1 <= limit <= MAX_LIMIT:
                problems["limit"] = f"must be between 1 and {MAX_LIMIT}; got {limit}"
        except ValueError:
            problems["limit"] = (
                f"must be an integer between 1 and {MAX_LIMIT}; got {params['limit']!r}"
            )

    position: dict[str, Any] | None = None
    if "start_time" in params and "since_token" in params:
        problems["since_token"] = "pass start_time or since_token, not both"
    elif "since_token" in params:
        try:
            position = _decode_token(params["since_token"])
            if position["f"] != feed.path:
                problems["since_token"] = (
                    f"was issued by {position['f']} and can't continue {feed.path}"
                )
        except ValueError:
            problems["since_token"] = (
                "is not a valid token; pass next_token from a previous response unchanged"
            )
    elif "start_time" in params:
        try:
            start = _parse_start_time(params["start_time"])
            position = {"f": feed.path, "c": None, "i": None, "s": start.isoformat()}
        except ValueError as exc:
            problems["start_time"] = str(exc)
    else:
        problems["start_time"] = (
            "provide start_time (ISO 8601 with a UTC offset) for an initial sync, "
            "or since_token to continue one"
        )

    if problems:
        names = sorted(problems)
        noun = "problem" if len(names) == 1 else "problems"
        raise ApiError(
            400,
            "invalid_request",
            f"{len(names)} {noun} with the request: {', '.join(names)}",
            {name: problems[name] for name in names},
        )
    assert position is not None
    return {"limit": limit, "position": position}


def _public(record: dict, feed: Feed) -> dict:
    if record.get("deleted"):
        return {feed.ident: record[feed.ident], "deleted": True, "updated_at": record["updated_at"]}
    return {k: v for k, v in record.items() if not k.startswith("_")}


def _ident(key: str) -> str:
    return next(f.ident for f in FEEDS if f.key == key)


def create_app(
    *,
    dataset: dict[str, list[dict]] | None = None,
    keys: dict[str, str] | None = None,
) -> FastAPI:
    app = FastAPI(title="Helpline (mock)", docs_url="/docs", redoc_url=None)
    data = dataset if dataset is not None else generate()
    app.state.dataset = {
        key: sorted(rows, key=lambda r, k=key: (r["_committed_at"], r[_ident(k)]))
        for key, rows in data.items()
    }
    # Changes committed after this moment are not visible yet (None = all visible).
    app.state.visible_until = None
    signing_keys = keys if keys is not None else {DEMO_KEY_ID: DEMO_SECRET}

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            available = ", ".join(f.path for f in FEEDS)
            message = f"No endpoint at {request.url.path}; available: {available}"
            return ApiError(404, "not_found", message).response()
        return ApiError(exc.status_code, "http_error", str(exc.detail)).response()

    def register(feed: Feed) -> None:
        @app.get(feed.path, name=feed.key)
        async def handler(request: Request) -> dict[str, Any]:
            _verify_signature(request, signing_keys)
            q = _validate(request, feed)
            pos, limit = q["position"], q["limit"]
            start = datetime.fromisoformat(pos["s"])

            rows = app.state.dataset[feed.key]
            if app.state.visible_until is not None:
                rows = [r for r in rows if r["_committed_at"] <= app.state.visible_until]
            if pos["c"] is not None:
                after = (datetime.fromisoformat(pos["c"]), pos["i"])
                rows = [r for r in rows if (r["_committed_at"], r[feed.ident]) > after]
            rows = [r for r in rows if datetime.fromisoformat(r["updated_at"]) >= start]

            page, has_more = rows[:limit], len(rows) > limit
            last = page[-1] if page else None
            token = _encode_token(
                {
                    "f": feed.path,
                    "c": last["_committed_at"].isoformat() if last else pos["c"],
                    "i": last[feed.ident] if last else pos["i"],
                    "s": pos["s"],
                }
            )
            return {
                "changes": [_public(r, feed) for r in page],
                "next_token": token,
                "has_more": has_more,
            }

    for feed in FEEDS:
        register(feed)

    return app
