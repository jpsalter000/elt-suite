"""NetSuite SuiteQL client.

Auth: Token-Based Authentication (OAuth 1.0a, HMAC-SHA256) with credentials
``account_id``, ``consumer_key``, ``consumer_secret``, ``token_id`` and
``token_secret``. Records are pulled from the SuiteQL REST endpoint
(``/services/rest/query/v1/suiteql``) using ``limit``/``offset`` paging until
``hasMore`` is false.

Datetimes are rendered with ``TO_CHAR`` so values are independent of the
integration user's date-format preference. NetSuite evaluates them in the
account's timezone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Iterator
from urllib.parse import quote

import httpx

from elt_suite.contract import JobContext, Record, SourceError

SUITEQL_PATH = "/services/rest/query/v1/suiteql"
SQL_DATETIME = "YYYY-MM-DD HH24:MI:SS"
PY_DATETIME = "%Y-%m-%d %H:%M:%S"

CUSTOMER_COLUMNS = f"""
    id, entityid, companyname, email, phone, isinactive, subsidiary,
    TO_CHAR(datecreated, '{SQL_DATETIME}') AS datecreated,
    TO_CHAR(lastmodifieddate, '{SQL_DATETIME}') AS lastmodifieddate
"""

TRANSACTION_COLUMNS = f"""
    id, tranid, type, status, entity, currency, foreigntotal, memo,
    TO_CHAR(trandate, 'YYYY-MM-DD') AS trandate,
    TO_CHAR(lastmodifieddate, '{SQL_DATETIME}') AS lastmodifieddate
"""

DEPARTMENT_COLUMNS = "id, name, isinactive"

EMPLOYEE_COLUMNS = f"""
    id, entityid, firstname, lastname, email, department, title, isinactive,
    TO_CHAR(hiredate, 'YYYY-MM-DD') AS hiredate,
    TO_CHAR(releasedate, 'YYYY-MM-DD') AS releasedate,
    laborcost, custentity_burdened_cost, custentity_hours_per_day,
    TO_CHAR(lastmodifieddate, '{SQL_DATETIME}') AS lastmodifieddate
"""

PROJECT_COLUMNS = f"""
    id, entityid, companyname, parent, custentity_project_status,
    TO_CHAR(startdate, 'YYYY-MM-DD') AS startdate,
    TO_CHAR(projectedenddate, 'YYYY-MM-DD') AS projectedenddate,
    department, projectmanager, isinactive,
    TO_CHAR(lastmodifieddate, '{SQL_DATETIME}') AS lastmodifieddate
"""

ITEM_COLUMNS = "id, itemid, displayname, itemtype, isinactive"

TIME_ENTRY_COLUMNS = f"""
    id, employee, TO_CHAR(trandate, 'YYYY-MM-DD') AS trandate, hours, customer, item,
    department, memo, isbillable,
    TO_CHAR(lastmodifieddate, '{SQL_DATETIME}') AS lastmodifieddate
"""

TRANSACTION_LINE_COLUMNS = (
    "transaction, id, linesequencenumber, mainline, entity, item, memo, foreignamount"
)

ACCOUNTING_LINE_COLUMNS = "transaction, transactionline, account, amount, posting"

ACCOUNT_COLUMNS = "id, acctnumber, fullname, accttype, isinactive"


def _pct(value: str) -> str:
    return quote(value, safe="~-._")


class TokenAuth(httpx.Auth):
    """OAuth 1.0a request signing as required by NetSuite TBA."""

    def __init__(self, credentials: dict[str, str]) -> None:
        self.realm = credentials["account_id"].upper().replace("-", "_")
        self.consumer_key = credentials["consumer_key"]
        self.consumer_secret = credentials["consumer_secret"]
        self.token_id = credentials["token_id"]
        self.token_secret = credentials["token_secret"]

    def auth_flow(self, request: httpx.Request):
        oauth = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_token": self.token_id,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": str(int(time.time())),
            "oauth_nonce": secrets.token_hex(16),
            "oauth_version": "1.0",
        }
        params = sorted(
            (_pct(k), _pct(v)) for k, v in [*request.url.params.multi_items(), *oauth.items()]
        )
        base_url = str(request.url.copy_with(query=None, fragment=None))
        base_string = "&".join(
            [request.method.upper(), _pct(base_url), _pct("&".join(f"{k}={v}" for k, v in params))]
        )
        key = f"{_pct(self.consumer_secret)}&{_pct(self.token_secret)}"
        digest = hmac.new(key.encode(), base_string.encode(), hashlib.sha256).digest()
        oauth["oauth_signature"] = base64.b64encode(digest).decode()

        header = ", ".join(f'{k}="{_pct(v)}"' for k, v in oauth.items())
        request.headers["Authorization"] = f'OAuth realm="{self.realm}", {header}'
        yield request


def _api_error(resp: httpx.Response) -> SourceError:
    """NetSuite explains failures in ``o:errorDetails``; surface that, not just the status."""
    try:
        body = resp.json()
        details = "; ".join(d["detail"] for d in body["o:errorDetails"])
        reason = f"{body.get('title') or resp.reason_phrase}: {details}"
    except (ValueError, KeyError, TypeError):
        reason = resp.text[:500] or resp.reason_phrase
    return SourceError(f"NetSuite SuiteQL request failed: {resp.status_code} {reason}")


def suiteql(ctx: JobContext, query: str) -> Iterator[Record]:
    """Stream every row of a SuiteQL query, one page at a time."""
    host = ctx.credentials["account_id"].lower().replace("_", "-")
    url = ctx.consumer.effective_base_url.format(account_id=host) + SUITEQL_PATH
    auth = TokenAuth(ctx.credentials)
    limit = ctx.consumer.extra.get("page_size", 1000)
    offset = 0
    while True:
        resp = ctx.http.post(
            url,
            params={"limit": limit, "offset": offset},
            json={"q": query},
            headers={"Prefer": "transient"},
            auth=auth,
        )
        if resp.is_error:
            raise _api_error(resp)
        page = resp.json()
        for item in page.get("items", []):
            item.pop("links", None)
            yield item
        if not page.get("hasMore"):
            return
        offset += limit


def _incremental_query(ctx: JobContext, columns: str, table: str) -> str:
    query = f"SELECT {columns} FROM {table}"
    inc = ctx.job.incremental_loading
    if inc and ctx.lower_bound:
        bound = ctx.lower_bound.strftime(PY_DATETIME)
        query += (
            f" WHERE {inc.incremental_key} >= TO_DATE('{bound}', '{SQL_DATETIME}')"
            f" ORDER BY {inc.incremental_key}, id"
        )
    else:
        query += " ORDER BY id"
    return query


def _full_query(columns: str, table: str, order_by: str = "id") -> str:
    return f"SELECT {columns} FROM {table} ORDER BY {order_by}"


def fetch_customers(ctx: JobContext) -> Iterator[Record]:
    return suiteql(ctx, _incremental_query(ctx, CUSTOMER_COLUMNS, "customer"))


def fetch_transactions(ctx: JobContext) -> Iterator[Record]:
    return suiteql(ctx, _incremental_query(ctx, TRANSACTION_COLUMNS, "transaction"))


def fetch_departments(ctx: JobContext) -> Iterator[Record]:
    return suiteql(ctx, _full_query(DEPARTMENT_COLUMNS, "department"))


def fetch_employees(ctx: JobContext) -> Iterator[Record]:
    return suiteql(ctx, _incremental_query(ctx, EMPLOYEE_COLUMNS, "employee"))


def fetch_projects(ctx: JobContext) -> Iterator[Record]:
    """Projects are ``job`` records in SuiteQL."""
    return suiteql(ctx, _incremental_query(ctx, PROJECT_COLUMNS, "job"))


def fetch_items(ctx: JobContext) -> Iterator[Record]:
    return suiteql(ctx, _full_query(ITEM_COLUMNS, "item"))


def fetch_time_entries(ctx: JobContext) -> Iterator[Record]:
    """Time entries are ``timebill`` records in SuiteQL."""
    return suiteql(ctx, _incremental_query(ctx, TIME_ENTRY_COLUMNS, "timebill"))


def fetch_transaction_lines(ctx: JobContext) -> Iterator[Record]:
    """Lines have no modification date of their own, so they are re-read in full."""
    query = _full_query(TRANSACTION_LINE_COLUMNS, "transactionline", "transaction, id")
    return suiteql(ctx, query)


def fetch_transaction_accounting_lines(ctx: JobContext) -> Iterator[Record]:
    query = _full_query(
        ACCOUNTING_LINE_COLUMNS, "transactionaccountingline", "transaction, transactionline"
    )
    return suiteql(ctx, query)


def fetch_accounts(ctx: JobContext) -> Iterator[Record]:
    return suiteql(ctx, _full_query(ACCOUNT_COLUMNS, "account"))
