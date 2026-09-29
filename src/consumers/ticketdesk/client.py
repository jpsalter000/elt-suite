"""Ticketdesk REST API client (see :mod:`mock_apis.ticketdesk` for the API itself).

Auth: a static ``api_key`` sent as the ``X-API-Key`` header.

Pagination: ``page``/``per_page``; the client follows the ``Link`` header's
``rel="next"`` URL (which keeps the filters) until there is none.

Incremental: Ticketdesk's ``modified_after`` is *exclusive* and timestamps have
one-second resolution, while elt-suite lower bounds are inclusive. Several
records can share a ``modified_at``, so passing the bound as-is would silently
skip records at the watermark. The client sends ``lower_bound - 1s`` instead,
making the filter equivalent to ``modified_at >= lower_bound``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from elt_suite.contract import JobContext, Record, SourceError

DEFAULT_PAGE_SIZE = 100
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
RESOLUTION = timedelta(seconds=1)


def _api_error(resp: httpx.Response, path: str) -> SourceError:
    try:
        body = resp.json()
        detail = "".join(f"; {e['field']}: {e['message']}" for e in body.get("errors", []))
        explanation = f"{body['message']}{detail}"
    except (ValueError, KeyError, TypeError):
        explanation = resp.text[:500]
    return SourceError(f"Ticketdesk GET {path} failed: {resp.status_code}: {explanation}")


def _inclusive_modified_after(bound: datetime) -> str:
    if bound.tzinfo is not None:
        bound = bound.astimezone(UTC).replace(tzinfo=None)
    return (bound - RESOLUTION).strftime(TIMESTAMP_FORMAT)


def list_all(ctx: JobContext, path: str) -> Iterator[Record]:
    headers = {"X-API-Key": ctx.credentials["api_key"]}
    params: dict[str, Any] | None = {
        "per_page": ctx.consumer.extra.get("page_size", DEFAULT_PAGE_SIZE)
    }
    if ctx.lower_bound:
        params["modified_after"] = _inclusive_modified_after(ctx.lower_bound)

    url: str | None = f"{ctx.base_url}{path}"
    while url:
        resp = ctx.http.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            raise _api_error(resp, path)
        yield from resp.json()
        next_link = resp.links.get("next")
        url, params = (next_link["url"], None) if next_link else (None, None)


def fetch_tickets(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/api/v2/tickets")


def fetch_agents(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/api/v2/agents")
