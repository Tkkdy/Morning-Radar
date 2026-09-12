"""Shared bounded HTTP behavior for public API adapters."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

USER_AGENT = "MorningRadar/0.1 (+https://github.com/)"


class RequestBudgetExceeded(RuntimeError):
    """A collector-specific public-network budget has no request starts left."""


class RequestStartBudget:
    """Count actual HTTP attempts and reject starts after a cap or deadline."""

    def __init__(self, *, maximum_requests: int, deadline_seconds: float) -> None:
        self.maximum_requests = maximum_requests
        self.deadline_seconds = deadline_seconds
        self.deadline_at: float | None = None
        self.used = 0

    def before_attempt(self) -> None:
        from time import monotonic

        if self.deadline_at is None:
            self.deadline_at = monotonic() + self.deadline_seconds
        if monotonic() > self.deadline_at:
            raise RequestBudgetExceeded("request start deadline exceeded")
        if self.used >= self.maximum_requests:
            raise RequestBudgetExceeded("network request budget exhausted")
        self.used += 1


class HttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        attempts: int = 3,
        client: httpx.Client | None = None,
        before_attempt: Callable[[], None] | None = None,
    ) -> None:
        self.attempts = attempts
        self.before_attempt = before_attempt
        self.request_attempts = 0
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self.client.headers["User-Agent"] = USER_AGENT

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        @retry(
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
            ),
            stop=stop_after_attempt(self.attempts),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
            reraise=True,
        )
        def request() -> httpx.Response:
            request_kwargs = kwargs
            if self.before_attempt is not None:
                self.before_attempt()
            self.request_attempts += 1
            if self.before_attempt is not None:
                # Discovery clients deliberately do not follow redirects: a redirect
                # is another physical request and must not escape this small budget.
                request_kwargs = {**request_kwargs, "follow_redirects": False}
            response = self.client.get(url, **request_kwargs)
            if response.status_code == 304:
                return response
            response.raise_for_status()
            return response

        return request()
