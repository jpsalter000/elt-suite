"""Shared HTTP client with retry/backoff for transient failures."""

import logging
import time

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}
log = logging.getLogger(__name__)


class RetryTransport(httpx.HTTPTransport):
    """Retries rate-limited and 5xx responses with exponential backoff."""

    def __init__(self, max_retries: int = 5, backoff: float = 1.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.max_retries = max_retries
        self.backoff = backoff

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response = super().handle_request(request)
            if response.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                return response
            delay = float(response.headers.get("Retry-After", self.backoff * 2**attempt))
            log.warning(
                "%s %s -> %s, retrying in %.1fs",
                request.method,
                request.url,
                response.status_code,
                delay,
            )
            response.close()
            time.sleep(delay)
        raise AssertionError("unreachable")


def make_client(**kwargs) -> httpx.Client:
    return httpx.Client(transport=RetryTransport(), timeout=60.0, **kwargs)
