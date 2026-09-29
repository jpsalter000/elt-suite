"""Shopfront: a fictional e-commerce REST API.

Authentication
    ``POST /oauth/token`` (form-encoded). ``grant_type=client_credentials`` with
    ``client_id``/``client_secret`` returns a short-lived bearer ``access_token``
    and a ``refresh_token``. ``grant_type=refresh_token`` exchanges a refresh
    token for a new pair; each refresh token works once.

Pagination
    Keyset cursors over ``(updated_at, id)``. Responses carry
    ``pagination.next_cursor``; pass it back as ``cursor`` (optionally with
    ``limit``) until ``has_more`` is false. A cursor encodes the original filters.

Errors
    ``{"error": {"code", "message", "details": [{"param", "message"}]}}`` with every
    invalid parameter reported at once.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_apis.shopfront.data import generate, timestamp

DEMO_CLIENT_ID = "globex-demo"
DEMO_CLIENT_SECRET = "globex-demo-secret"

ENDPOINTS = {"/v1/customers": "customers", "/v1/orders": "orders"}
LIST_PARAMS = ("cursor", "limit", "updated_since")
DEFAULT_LIMIT, MAX_LIMIT = 50, 100

Clock = Callable[[], datetime]


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: list[dict] | None = None
    ) -> None:
        self.status, self.code, self.message, self.details = status, code, message, details


def _error_body(code: str, message: str, details: list[dict] | None = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


@dataclass
class TokenStore:
    clock: Clock
    ttl: timedelta
    access: dict[str, datetime] = field(default_factory=dict)  # token -> expiry
    refresh: dict[str, bool] = field(default_factory=dict)  # token -> already used

    def issue(self) -> dict[str, Any]:
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        self.access[access] = self.clock() + self.ttl
        self.refresh[refresh] = False
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(self.ttl.total_seconds()),
            "refresh_token": refresh,
        }

    def check(self, header: str | None) -> None:
        scheme, _, token = (header or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise ApiError(
                401,
                "missing_token",
                "send an access token in the header 'Authorization: Bearer <token>'; "
                "get one from POST /oauth/token",
            )
        expiry = self.access.get(token)
        if expiry is None:
            raise ApiError(401, "invalid_token", "access token not recognised; request a new one")
        if self.clock() >= expiry:
            raise ApiError(
                401,
                "token_expired",
                f"access token expired at {timestamp(expiry)}; exchange your refresh_token "
                "at POST /oauth/token with grant_type=refresh_token",
            )


def _encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> dict:
    padded = value + "=" * (-len(value) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError from exc
    if not isinstance(payload, dict) or not {"e", "u", "i", "s"} <= payload.keys():
        raise ValueError
    return payload


def _parse_since(value: str) -> str:
    """Validate an ISO 8601 timestamp and normalise it to the API's UTC format."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"must be an ISO 8601 timestamp with a timezone, e.g. 2026-05-01T00:00:00Z; "
            f"got {value!r}"
        ) from None
    if parsed.tzinfo is None:
        raise ValueError(f"must include a timezone (e.g. a trailing Z for UTC); got {value!r}")
    return timestamp(parsed)


def _parse_list_query(request: Request, endpoint: str) -> tuple[int, str | None, tuple | None]:
    """Validate query parameters; return (limit, updated_since, after-key)."""
    params = request.query_params
    details: list[dict[str, str]] = []
    allowed = ", ".join(LIST_PARAMS)

    for name in params:
        if name == "page":
            details.append(
                {
                    "param": name,
                    "message": "Shopfront paginates with cursors, not pages: pass "
                    "pagination.next_cursor from the previous response as cursor "
                    f"(allowed: {allowed})",
                }
            )
        elif name not in LIST_PARAMS:
            details.append({"param": name, "message": f"unknown parameter; allowed: {allowed}"})

    limit = DEFAULT_LIMIT
    if "limit" in params:
        try:
            limit = int(params["limit"])
        except ValueError:
            details.append(
                {
                    "param": "limit",
                    "message": f"must be an integer between 1 and {MAX_LIMIT}; "
                    f"got {params['limit']!r}",
                }
            )
        else:
            if not 1 <= limit <= MAX_LIMIT:
                details.append(
                    {
                        "param": "limit",
                        "message": f"must be between 1 and {MAX_LIMIT}; got {limit}",
                    }
                )

    since: str | None = None
    if "updated_since" in params:
        try:
            since = _parse_since(params["updated_since"])
        except ValueError as exc:
            details.append({"param": "updated_since", "message": str(exc)})

    after: tuple | None = None
    if "cursor" in params:
        try:
            cursor = _decode_cursor(params["cursor"])
        except ValueError:
            details.append(
                {
                    "param": "cursor",
                    "message": "is not a valid cursor; pass pagination.next_cursor from a "
                    "previous response unchanged",
                }
            )
        else:
            if cursor["e"] != endpoint:
                details.append(
                    {
                        "param": "cursor",
                        "message": f"was issued by {cursor['e']} and can't be used with {endpoint}",
                    }
                )
            elif "updated_since" in params:
                details.append(
                    {
                        "param": "updated_since",
                        "message": "the cursor already encodes the original query's filters; "
                        "pass only cursor (and optionally limit)",
                    }
                )
            else:
                since, after = cursor["s"], (cursor["u"], cursor["i"])

    if details:
        noun = "parameter" if len(details) == 1 else "parameters"
        names = ", ".join(d["param"] for d in details)
        raise ApiError(
            400, "invalid_parameters", f"{len(details)} invalid {noun}: {names}", details
        )
    return limit, since, after


def create_app(
    *,
    dataset: dict[str, list[dict]] | None = None,
    clock: Clock | None = None,
    token_ttl_seconds: int = 300,
    client_id: str = DEMO_CLIENT_ID,
    client_secret: str = DEMO_CLIENT_SECRET,
) -> FastAPI:
    app = FastAPI(title="Shopfront (mock)", docs_url="/docs", redoc_url=None)
    data = dataset if dataset is not None else generate()
    app.state.dataset = {
        key: sorted(rows, key=lambda r: (r["updated_at"], r["id"])) for key, rows in data.items()
    }
    tokens = TokenStore(clock or (lambda: datetime.now(UTC)), timedelta(seconds=token_ttl_seconds))

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_error_body(exc.code, exc.message, exc.details), exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            available = ", ".join(["POST /oauth/token", *(f"GET {p}" for p in ENDPOINTS)])
            message = f"no endpoint {request.method} {request.url.path}; available: {available}"
            return JSONResponse(_error_body("not_found", message), 404)
        return JSONResponse(_error_body("http_error", str(exc.detail)), exc.status_code)

    @app.post("/oauth/token")
    async def token(request: Request) -> dict[str, Any]:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        grant = form.get("grant_type", "")
        if grant == "client_credentials":
            for name in ("client_id", "client_secret"):
                if not form.get(name):
                    raise ApiError(400, "invalid_request", f"{name} is required")
            if (form["client_id"], form["client_secret"]) != (client_id, client_secret):
                raise ApiError(401, "invalid_client", "client_id or client_secret is incorrect")
            return tokens.issue()
        if grant == "refresh_token":
            refresh = form.get("refresh_token", "")
            if not refresh:
                raise ApiError(400, "invalid_request", "refresh_token is required")
            if refresh not in tokens.refresh:
                raise ApiError(
                    400,
                    "invalid_grant",
                    "refresh_token not recognised; request a new token with "
                    "grant_type=client_credentials",
                )
            if tokens.refresh[refresh]:
                raise ApiError(
                    400,
                    "invalid_grant",
                    "refresh_token has already been used; refresh tokens are single-use, so "
                    "use the one from the latest response or request a new token with "
                    "grant_type=client_credentials",
                )
            tokens.refresh[refresh] = True
            return tokens.issue()
        raise ApiError(
            400,
            "unsupported_grant_type",
            f"grant_type must be 'client_credentials' or 'refresh_token'; got {grant!r}",
        )

    def list_endpoint(path: str, key: str) -> None:
        @app.get(path, name=key)
        async def handler(request: Request) -> dict[str, Any]:
            tokens.check(request.headers.get("authorization"))
            limit, since, after = _parse_list_query(request, path)
            rows = app.state.dataset[key]
            if since:
                rows = [r for r in rows if r["updated_at"] >= since]
            if after:
                rows = [r for r in rows if (r["updated_at"], r["id"]) > after]
            page, has_more = rows[:limit], len(rows) > limit
            next_cursor = None
            if has_more:
                last = page[-1]
                next_cursor = _encode_cursor(
                    {"e": path, "u": last["updated_at"], "i": last["id"], "s": since}
                )
            return {"data": page, "pagination": {"next_cursor": next_cursor, "has_more": has_more}}

    for path, key in ENDPOINTS.items():
        list_endpoint(path, key)

    return app
