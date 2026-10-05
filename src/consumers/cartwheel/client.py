"""Cartwheel REST API client (see :mod:`mock_apis.cartwheel` for the API itself).

Auth: HTTP Basic with ``username`` / ``password``.

Pagination: ``offset``/``limit`` until ``offset >= total``. The API's default
``sort=modified`` is unstable across pages (ties shuffle), which silently
duplicates and skips records, so the client always pages with ``sort=id``.

Deletes: soft-deleted records are hidden by default. The client asks for them
(``include_deleted=true``) so ``isDeleted``/``deletedAt`` reach the warehouse and
deletions can propagate downstream.

Incremental: ``modified_since`` takes inclusive unix seconds.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from elt_suite.contract import JobContext, Record, SourceError

DEFAULT_PAGE_SIZE = 250


def _api_error(resp: httpx.Response, path: str) -> SourceError:
    try:
        problem = resp.json()
        detail = "".join(
            f"; {p['name']}: {p['reason']}" for p in problem.get("invalid_params") or []
        )
        explanation = f"{problem['title']}: {problem['detail']}{detail}"
    except (ValueError, KeyError, TypeError):
        explanation = resp.text[:500]
    return SourceError(f"Cartwheel GET {path} failed: {resp.status_code} {explanation}")


def _unix_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def list_all(ctx: JobContext, path: str) -> Iterator[Record]:
    auth = httpx.BasicAuth(ctx.credentials["username"], ctx.credentials["password"])
    params: dict[str, Any] = {
        "sort": "id",
        "include_deleted": "true",
        "limit": ctx.consumer.extra.get("page_size", DEFAULT_PAGE_SIZE),
        "offset": 0,
    }
    if ctx.lower_bound:
        params["modified_since"] = _unix_seconds(ctx.lower_bound)

    while True:
        resp = ctx.http.get(f"{ctx.base_url}{path}", params=params, auth=auth)
        if resp.status_code != 200:
            raise _api_error(resp, path)
        body = resp.json()
        yield from body["results"]
        params["offset"] += params["limit"]
        if params["offset"] >= body["total"]:
            return


def fetch_customers(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/api/v3/customers")


def fetch_orders(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/api/v3/orders")


def fetch_order_items(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/api/v3/order-items")
