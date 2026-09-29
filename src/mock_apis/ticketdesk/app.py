"""Ticketdesk: a fictional support-desk REST API.

Authentication
    A static API key in the ``X-API-Key`` header.

Pagination
    ``page`` (1-based) and ``per_page`` (1-200, default 25). The body is a bare
    JSON array; ``X-Total-Count`` gives the filtered total and the ``Link`` header
    gives ``rel="next"`` (absent on the last page) and ``rel="last"``, both keeping
    the request's filters.

Filtering
    ``modified_after`` is **exclusive** and uses ``YYYY-MM-DD HH:MM:SS`` (UTC).
    Tickets also filter by ``status``; agents by ``active``.

Errors
    ``{"message": str, "errors": [{"field", "message"}]}``; validation failures
    are 422 and list every invalid parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_apis.ticketdesk.data import FORMAT, STATUSES, generate

DEMO_API_KEY = "initech-demo-key"
DEFAULT_PER_PAGE, MAX_PER_PAGE = 25, 200
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class ApiError(Exception):
    def __init__(self, status: int, message: str, errors: list[dict] | None = None) -> None:
        self.status, self.message, self.errors = status, message, errors or []


@dataclass(frozen=True)
class Endpoint:
    path: str
    key: str
    filters: tuple[str, ...]  # endpoint-specific filters beyond modified_after

    @property
    def allowed(self) -> list[str]:
        return sorted(["modified_after", "page", "per_page", *self.filters])


ENDPOINTS = [
    Endpoint("/api/v2/tickets", "tickets", ("status",)),
    Endpoint("/api/v2/agents", "agents", ("active",)),
]


def _positive_int(value: str, field: str, errors: list[dict], upper: int | None = None) -> int:
    rule = f"between 1 and {upper}" if upper else "1 or greater"
    try:
        number = int(value)
    except ValueError:
        errors.append({"field": field, "message": f"must be a whole number {rule}; got {value!r}"})
        return 0
    if number < 1 or (upper is not None and number > upper):
        errors.append({"field": field, "message": f"must be {rule}; got {number}"})
    return number


def _validate(request: Request, endpoint: Endpoint) -> tuple[int, int, dict[str, Any]]:
    params = request.query_params
    errors: list[dict[str, str]] = []
    allowed = ", ".join(endpoint.allowed)

    for name in params:
        if name not in endpoint.allowed:
            hint = (
                "Ticketdesk paginates with page and per_page; follow the Link header's "
                'rel="next" URL. '
                if name in ("cursor", "offset", "limit")
                else ""
            )
            errors.append(
                {"field": name, "message": f"{hint}unknown parameter; allowed: {allowed}"}
            )

    page = _positive_int(params.get("page", "1"), "page", errors)
    per_page = _positive_int(
        params.get("per_page", str(DEFAULT_PER_PAGE)), "per_page", errors, MAX_PER_PAGE
    )

    filters: dict[str, Any] = {}
    if "modified_after" in params:
        value = params["modified_after"]
        if not _TIMESTAMP.match(value):
            errors.append(
                {
                    "field": "modified_after",
                    "message": "must use the format YYYY-MM-DD HH:MM:SS (UTC), "
                    f"e.g. 2026-05-01 00:00:00; got {value!r}",
                }
            )
        else:
            try:
                datetime.strptime(value, FORMAT)
                filters["modified_after"] = value
            except ValueError:
                errors.append(
                    {
                        "field": "modified_after",
                        "message": f"is not a real date and time; got {value!r}",
                    }
                )
    if "status" in params and "status" in endpoint.filters:
        if params["status"] in STATUSES:
            filters["status"] = params["status"]
        else:
            errors.append(
                {
                    "field": "status",
                    "message": f"must be one of: {', '.join(STATUSES)}; got {params['status']!r}",
                }
            )
    if "active" in params and "active" in endpoint.filters:
        value = params["active"].lower()
        if value in ("true", "false"):
            filters["active"] = value == "true"
        else:
            errors.append(
                {
                    "field": "active",
                    "message": f"must be true or false; got {params['active']!r}",
                }
            )

    if errors:
        errors.sort(key=lambda e: e["field"])
        noun = "parameter" if len(errors) == 1 else "parameters"
        fields = ", ".join(e["field"] for e in errors)
        raise ApiError(422, f"Validation failed for {len(errors)} {noun}: {fields}", errors)
    return page, per_page, filters


def create_app(
    *, dataset: dict[str, list[dict]] | None = None, api_key: str = DEMO_API_KEY
) -> FastAPI:
    app = FastAPI(title="Ticketdesk (mock)", docs_url="/docs", redoc_url=None)
    data = dataset if dataset is not None else generate()
    app.state.dataset = {
        key: sorted(rows, key=lambda r: (r["modified_at"], r["id"])) for key, rows in data.items()
    }

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"message": exc.message, "errors": exc.errors}, exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            available = ", ".join(e.path for e in ENDPOINTS)
            message = f"No endpoint at {request.url.path}. Available: {available}"
            return JSONResponse({"message": message, "errors": []}, 404)
        return JSONResponse({"message": str(exc.detail), "errors": []}, exc.status_code)

    def authenticate(request: Request) -> None:
        key = request.headers.get("x-api-key")
        if not key:
            where = (
                " Send the key as a header, not in the query string."
                if "api_key" in request.query_params
                else ""
            )
            raise ApiError(401, f"Missing X-API-Key header.{where}")
        if key != api_key:
            raise ApiError(401, "The API key is not valid. Check the X-API-Key header.")

    def register(endpoint: Endpoint) -> None:
        @app.get(endpoint.path, name=endpoint.key)
        async def handler(request: Request) -> JSONResponse:
            authenticate(request)
            page, per_page, filters = _validate(request, endpoint)
            rows = app.state.dataset[endpoint.key]
            if "modified_after" in filters:
                rows = [r for r in rows if r["modified_at"] > filters["modified_after"]]
            if "status" in filters:
                rows = [r for r in rows if r["status"] == filters["status"]]
            if "active" in filters:
                rows = [r for r in rows if r["active"] is filters["active"]]

            total = len(rows)
            last_page = -(-total // per_page)
            body = rows[(page - 1) * per_page : page * per_page]

            def link(n: int, rel: str) -> str:
                query = {**request.query_params, "page": str(n)}
                return f'<{request.url.replace(query=urlencode(query))}>; rel="{rel}"'

            links = []
            if page < last_page:
                links.append(link(page + 1, "next"))
            if total:
                links.append(link(last_page, "last"))
            headers = {"X-Total-Count": str(total)}
            if links:
                headers["Link"] = ", ".join(links)
            return JSONResponse(body, headers=headers)

    for endpoint in ENDPOINTS:
        register(endpoint)

    return app
