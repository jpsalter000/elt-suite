"""Shopfront REST API client (see :mod:`mock_apis.shopfront` for the API itself).

Auth: OAuth2 client credentials (``client_id`` / ``client_secret``). Access
tokens are short-lived; on a ``token_expired`` 401 the client exchanges its
refresh token and retries the request once. Refresh tokens are single-use and
rotate on every exchange; if one is rejected (``invalid_grant``), the client
falls back to a fresh client-credentials grant.

Pagination: keyset cursors. The first request carries ``limit`` and the
``updated_since`` lower bound (inclusive); later requests send only the returned
``next_cursor`` and ``limit`` until ``has_more`` is false.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from elt_suite.contract import JobContext, Record, SourceError

DEFAULT_PAGE_SIZE = 100


def _api_error(resp: httpx.Response, action: str) -> SourceError:
    try:
        error = resp.json()["error"]
        detail = "".join(f"; {d['param']}: {d['message']}" for d in error.get("details", []))
        explanation = f"{error['code']}: {error['message']}{detail}"
    except (ValueError, KeyError, TypeError):
        explanation = resp.text[:500]
    return SourceError(f"Shopfront {action} failed: {resp.status_code} {explanation}")


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


class ShopfrontSession:
    """Holds the current token pair and transparently refreshes it."""

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    def _grant(self, form: dict[str, str]) -> None:
        resp = self.ctx.http.post(f"{self.ctx.base_url}/oauth/token", data=form)
        if resp.status_code != 200:
            raise _api_error(resp, f"token request ({form['grant_type']})")
        body = resp.json()
        self.access_token, self.refresh_token = body["access_token"], body["refresh_token"]

    def authenticate(self) -> None:
        self._grant(
            {
                "grant_type": "client_credentials",
                "client_id": self.ctx.credentials["client_id"],
                "client_secret": self.ctx.credentials["client_secret"],
            }
        )

    def renew(self) -> None:
        if self.refresh_token:
            try:
                self._grant({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
                self.ctx.log.debug("refreshed Shopfront access token")
                return
            except SourceError as exc:
                if "invalid_grant" not in str(exc):
                    raise
                self.ctx.log.info("refresh token rejected; re-authenticating: %s", exc)
        self.authenticate()

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.access_token is None:
            self.authenticate()
        for attempt in (1, 2):
            resp = self.ctx.http.get(
                f"{self.ctx.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            if resp.status_code == 401 and _error_code(resp) == "token_expired" and attempt == 1:
                self.renew()
                continue
            if resp.status_code != 200:
                raise _api_error(resp, f"GET {path}")
            return resp.json()
        raise AssertionError("unreachable")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def list_all(ctx: JobContext, path: str) -> Iterator[Record]:
    session = ShopfrontSession(ctx)
    limit = ctx.consumer.extra.get("page_size", DEFAULT_PAGE_SIZE)
    params: dict[str, Any] = {"limit": limit}
    if ctx.lower_bound:
        params["updated_since"] = _iso_utc(ctx.lower_bound)
    while True:
        page = session.get(path, params)
        yield from page["data"]
        if not page["pagination"]["has_more"]:
            return
        params = {"cursor": page["pagination"]["next_cursor"], "limit": limit}


def fetch_customers(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/v1/customers")


def fetch_orders(ctx: JobContext) -> Iterator[Record]:
    return list_all(ctx, "/v1/orders")
