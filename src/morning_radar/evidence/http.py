"""SSRF-resistant, bounded direct fetches for Candidate evidence."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

USER_AGENT = "MorningRadar-Evidence/0.5 (+https://github.com/)"
SUPPORTED_CONTENT_TYPES = {
    "application/json",
    "application/xhtml+xml",
    "text/html",
    "text/plain",
}


class EvidenceFetchError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class EvidenceFetchResult:
    requested_url: str
    final_url: str
    content_type: str
    text: str
    canonical_url: str | None
    redirect_chain: tuple[str, ...]
    response_bytes: int


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.canonical_url: str | None = None
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        attributes = dict(attrs)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").casefold():
            self.canonical_url = attributes.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data.strip())


def _is_safe_address(value: str) -> bool:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    return address.is_global and not any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


class SafeEvidenceFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        attempts: int = 2,
        maximum_response_bytes: int = 1_000_000,
        maximum_redirects: int = 3,
        client: httpx.Client | None = None,
        resolver=socket.getaddrinfo,
    ) -> None:
        self.attempts = attempts
        self.maximum_response_bytes = maximum_response_bytes
        self.maximum_redirects = maximum_redirects
        self.resolver = resolver
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json"},
            follow_redirects=False,
            cookies=None,
            trust_env=False,
        )

    def _validate_target(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EvidenceFetchError("UNSUPPORTED_URL")
        if parsed.username or parsed.password:
            raise EvidenceFetchError("URL_CREDENTIALS_REJECTED")
        try:
            port = parsed.port
        except ValueError as exc:
            raise EvidenceFetchError("PORT_REJECTED") from exc
        if port not in {None, 80, 443}:
            raise EvidenceFetchError("PORT_REJECTED")
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname.endswith(".local") or "." not in hostname:
            raise EvidenceFetchError("LOCAL_HOST_REJECTED")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise EvidenceFetchError("IP_LITERAL_REJECTED")
        try:
            default_port = 443 if parsed.scheme == "https" else 80
            addresses = self.resolver(
                hostname, port or default_port, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise EvidenceFetchError("DNS_FAILED") from exc
        resolved = {entry[4][0] for entry in addresses}
        if not resolved or any(not _is_safe_address(address) for address in resolved):
            raise EvidenceFetchError("UNSAFE_DNS_ADDRESS")

    def fetch(self, url: str) -> EvidenceFetchResult:
        requested_url = url
        current = url
        redirects: list[str] = []

        @retry(
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
            ),
            stop=stop_after_attempt(self.attempts),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=1),
            reraise=True,
        )
        def request(target: str) -> httpx.Response:
            request_value = self.client.build_request(
                "GET",
                target,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,text/plain,application/json",
                },
            )
            return self.client.send(
                request_value,
                stream=True,
                follow_redirects=False,
            )

        for _ in range(self.maximum_redirects + 1):
            self._validate_target(current)
            try:
                response = request(current)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                raise EvidenceFetchError("NETWORK_FAILED") from exc
            if response.is_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise EvidenceFetchError("REDIRECT_WITHOUT_LOCATION")
                redirects.append(current)
                current = urljoin(current, location)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                response.close()
                raise EvidenceFetchError("HTTP_STATUS_FAILED") from exc
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].casefold()
            if content_type not in SUPPORTED_CONTENT_TYPES:
                response.close()
                raise EvidenceFetchError("UNSUPPORTED_CONTENT_TYPE")
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    response.close()
                    raise EvidenceFetchError("INVALID_CONTENT_LENGTH") from exc
                if declared_size > self.maximum_response_bytes:
                    response.close()
                    raise EvidenceFetchError("RESPONSE_TOO_LARGE")
            chunks: list[bytes] = []
            body_size = 0
            try:
                for chunk in response.iter_bytes():
                    body_size += len(chunk)
                    if body_size > self.maximum_response_bytes:
                        raise EvidenceFetchError("RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise EvidenceFetchError("NETWORK_FAILED") from exc
            finally:
                response.close()
            body = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            text = body.decode(encoding, errors="replace")
            canonical_url = None
            if content_type in {"text/html", "application/xhtml+xml"}:
                parser = _HTMLTextExtractor()
                try:
                    parser.feed(text)
                except (UnicodeError, ValueError) as exc:
                    raise EvidenceFetchError("PARSE_FAILED") from exc
                text = " ".join(parser.parts)
                canonical_url = (
                    urljoin(str(response.url), parser.canonical_url)
                    if parser.canonical_url
                    else None
                )
                if canonical_url:
                    try:
                        self._validate_target(canonical_url)
                    except EvidenceFetchError:
                        canonical_url = None
            return EvidenceFetchResult(
                requested_url=requested_url,
                final_url=str(response.url),
                content_type=content_type,
                text=text[:20_000],
                canonical_url=canonical_url,
                redirect_chain=tuple(redirects),
                response_bytes=body_size,
            )
        raise EvidenceFetchError("TOO_MANY_REDIRECTS")
