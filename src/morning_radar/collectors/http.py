"""Shared bounded HTTP behavior for public API adapters."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

USER_AGENT = "MorningRadar/0.1 (+https://github.com/)"


class HttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        attempts: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self.attempts = attempts
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
            response = self.client.get(url, **kwargs)
            response.raise_for_status()
            return response

        return request()
