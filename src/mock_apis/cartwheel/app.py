"""Cartwheel: a second fictional commerce REST API.

Authentication
    HTTP Basic (``Authorization: Basic base64(username:password)``).

Pagination
    ``offset`` (0-based) and ``limit`` (1-250, default 50). The body is
    ``{"results", "total", "offset", "limit"}``, where ``total`` counts the filtered
    records.

Sorting
    ``sort=modified`` (the default) orders by the hour-resolution ``modified``
    timestamp only, so ties come back in arbitrary order and offset paging can
    duplicate and skip records. ``sort=id`` is stable.

Filtering
    ``modified_since`` (unix seconds, inclusive). Soft-deleted records are hidden
    unless ``include_deleted=true``.

Errors
    RFC 7807 ``application/problem+json``; 400s list every invalid parameter in
    ``invalid_params``.
"""

from __future__ import annotations

import base64
import binascii
import random
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_apis.cartwheel.data import generate

DEMO_USERNAME = "umbrella-demo"
DEMO_PASSWORD = "umbrella-demo-password"
DEFAULT_LIMIT, MAX_LIMIT = 50, 250
PARAMS = ("include_deleted", "limit", "modified_since", "offset", "sort")
SORTS = {"id": None, "modified": "modified"}
PROBLEM_BASE = "https://cartwheel.example/problems/"


@dataclass(frozen=True)
class Endpoint:
    path: str
    key: str
    pk: str


ENDPOINTS = [
    Endpoint("/api/v3/customers", "customers", "customerId"),
    Endpoint("/api/v3/orders", "orders", "orderId"),
    Endpoint("/api/v3/order-items", "order_items", "orderItemId"),
]


class Problem(Exception):
    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str,
        invalid_params: list[dict] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status, self.slug, self.title, self.detail = status, slug, title, detail
        self.invalid_params, self.headers = invalid_params, headers

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {
            "type": PROBLEM_BASE + self.slug,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
        }
        if self.invalid_params is not None:
            body["invalid_params"] = self.invalid_params
        return JSONResponse(
            body, self.status, headers=self.headers, media_type="application/problem+json"
        )


def _unauthorized(detail: str) -> Problem:
    return Problem(
        401, "unauthorized", "Unauthorized", detail,
        headers={"WWW-Authenticate": 'Basic realm="Cartwheel"'},
    )  # fmt: skip


def _check_auth(header: str | None, username: str, password: str) -> None:
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "basic" or not value:
        raise _unauthorized(
            "Send HTTP Basic credentials: Authorization: Basic base64(username:password)"
        )
    try:
        user, _, secret = base64.b64decode(value).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        raise _unauthorized("Malformed Basic credentials; expected base64(username:password)")
    if (user, secret) != (username, password):
        raise _unauthorized("username or password is incorrect")


def _int_param(params, name: str, default: int, low: int, high: int | None, errors: list) -> int:
    if name not in params:
        return default
    rule = f"between {low} and {high}" if high is not None else f"{low} or greater"
    try:
        value = int(params[name])
    except ValueError:
        errors.append({"name": name, "reason": f"must be an integer {rule}; got {params[name]!r}"})
        return default
    if value < low or (high is not None and value > high):
        errors.append({"name": name, "reason": f"must be {rule}; got {value}"})
    return value


def _validate(request: Request) -> dict[str, Any]:
    params = request.query_params
    errors: list[dict[str, str]] = []
    allowed = ", ".join(PARAMS)
    for name in params:
        if name == "page":
            errors.append(
                {
                    "name": name,
                    "reason": f"Cartwheel pages with offset and limit, not page (allowed: {allowed})",
                }
            )
        elif name not in PARAMS:
            errors.append({"name": name, "reason": f"unknown parameter; allowed: {allowed}"})

    query: dict[str, Any] = {
        "offset": _int_param(params, "offset", 0, 0, None, errors),
        "limit": _int_param(params, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT, errors),
        "since": None,
        "sort": params.get("sort", "modified"),
        "include_deleted": False,
    }
    if "modified_since" in params:
        try:
            query["since"] = int(params["modified_since"])
        except ValueError:
            errors.append(
                {
                    "name": "modified_since",
                    "reason": "must be unix seconds (an integer), e.g. 1767225600; "
                    f"got {params['modified_since']!r}",
                }
            )
    if query["sort"] not in SORTS:
        errors.append(
            {
                "name": "sort",
                "reason": f"must be one of: {', '.join(SORTS)}; got {query['sort']!r}",
            }
        )
    if "include_deleted" in params:
        value = params["include_deleted"].lower()
        if value in ("true", "false"):
            query["include_deleted"] = value == "true"
        else:
            errors.append(
                {
                    "name": "include_deleted",
                    "reason": f"must be true or false; got {params['include_deleted']!r}",
                }
            )

    if errors:
        errors.sort(key=lambda e: e["name"])
        names = ", ".join(e["name"] for e in errors)
        detail = (
            f"{len(errors)} parameters are invalid: {names}"
            if len(errors) > 1
            else f"1 parameter is invalid: {names}"
        )
        raise Problem(400, "invalid-parameters", "Invalid request parameters", detail, errors)
    return query


def _unstable_modified_order(rows: list[dict], offset: int) -> list[dict]:
    """Order by ``modified`` only; ties are shuffled differently on every page request."""
    rng = random.Random(offset)
    return sorted(rows, key=lambda r: (r["modified"], rng.random()))


def create_app(
    *,
    dataset: dict[str, list[dict]] | None = None,
    username: str = DEMO_USERNAME,
    password: str = DEMO_PASSWORD,
) -> FastAPI:
    app = FastAPI(title="Cartwheel (mock)", docs_url="/docs", redoc_url=None)
    app.state.dataset = dataset if dataset is not None else generate()

    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem) -> JSONResponse:
        return exc.response()

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            available = ", ".join(e.path for e in ENDPOINTS)
            detail = f"No resource at {request.url.path}. Available: {available}"
            return Problem(404, "not-found", "Not Found", detail).response()
        return Problem(exc.status_code, "http-error", "HTTP error", str(exc.detail)).response()

    def register(endpoint: Endpoint) -> None:
        @app.get(endpoint.path, name=endpoint.key)
        async def handler(request: Request) -> dict[str, Any]:
            _check_auth(request.headers.get("authorization"), username, password)
            q = _validate(request)
            rows = app.state.dataset[endpoint.key]
            if not q["include_deleted"]:
                rows = [r for r in rows if not r["isDeleted"]]
            if q["since"] is not None:
                rows = [r for r in rows if r["modified"] >= q["since"]]
            if q["sort"] == "id":
                rows = sorted(rows, key=lambda r: r[endpoint.pk])
            else:
                rows = _unstable_modified_order(rows, q["offset"])
            page = rows[q["offset"] : q["offset"] + q["limit"]]
            return {"results": page, "total": len(rows), "offset": q["offset"], "limit": q["limit"]}

    for endpoint in ENDPOINTS:
        register(endpoint)

    return app
