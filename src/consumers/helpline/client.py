"""Helpline REST API client (see :mod:`mock_apis.helpline` for the API itself).

Auth: every request is signed with HMAC-SHA256 over
``METHOD\\nPATH\\nCANONICAL_QUERY\\nTIMESTAMP`` using ``secret``, and sent with
``X-Helpline-Key`` (``key_id``), ``X-Helpline-Timestamp`` and
``X-Helpline-Signature``.

Pagination: a changes feed. The first request sends ``start_time`` (the lower
bound, inclusive on ``updated_at``); later requests send the returned
``next_token`` as ``since_token`` until ``has_more`` is false.

Late arrivals: changes are delivered in commit order and ``updated_at`` can
precede the commit by up to ~25 minutes, so a change can land after the
previous run already saved a newer watermark. The consumer config sets
``lookback_seconds`` so each run re-reads that window; upserts on
``case_number`` make the overlap harmless.

Deletes arrive as tombstones: ``{"case_number", "deleted": true, "updated_at"}``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from elt_suite.contract import JobContext, Record, SourceError

DEFAULT_PAGE_SIZE = 500


def canonical_query(params: dict[str, Any]) -> str:
    items = sorted((str(k), str(v)) for k, v in params.items())
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in items)


def sign(method: str, path: str, params: dict[str, Any], key_id: str, secret: str) -> dict:
    stamp = str(int(time.time()))
    message = "\n".join([method, path, canonical_query(params), stamp]).encode()
    return {
        "X-Helpline-Key": key_id,
        "X-Helpline-Timestamp": stamp,
        "X-Helpline-Signature": hmac.new(secret.encode(), message, hashlib.sha256).hexdigest(),
    }


def _api_error(resp: httpx.Response, path: str) -> SourceError:
    try:
        body = resp.json()
        detail = "".join(f"; {k}: {v}" for k, v in (body.get("problems") or {}).items())
        explanation = f"{body['error']}: {body['message']}{detail}"
    except (ValueError, KeyError, TypeError, AttributeError):
        explanation = resp.text[:500]
    return SourceError(f"Helpline GET {path} failed: {resp.status_code} {explanation}")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def sync(ctx: JobContext, path: str) -> Iterator[Record]:
    key_id, secret = ctx.credentials["key_id"], ctx.credentials["secret"]
    limit = ctx.consumer.extra.get("page_size", DEFAULT_PAGE_SIZE)
    start = ctx.lower_bound or datetime(1970, 1, 1, tzinfo=UTC)
    params: dict[str, Any] = {"start_time": _iso_utc(start), "limit": limit}

    while True:
        headers = sign("GET", path, params, key_id, secret)
        resp = ctx.http.get(f"{ctx.base_url}{path}", params=params, headers=headers)
        if resp.status_code != 200:
            raise _api_error(resp, path)
        body = resp.json()
        yield from body["changes"]
        if not body["has_more"]:
            return
        params = {"since_token": body["next_token"], "limit": limit}


def fetch_cases(ctx: JobContext) -> Iterator[Record]:
    return sync(ctx, "/v1/cases/changes")


def fetch_staff(ctx: JobContext) -> Iterator[Record]:
    return sync(ctx, "/v1/staff/changes")
