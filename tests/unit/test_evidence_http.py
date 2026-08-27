import socket
from datetime import UTC, datetime

import httpx
import pytest

from morning_radar.evidence import (
    EvidenceFetchError,
    OfficialSurfaceResolver,
    SafeEvidenceFetcher,
    SurfaceTrustStatus,
)


def public_dns(host: str, port: int, **kwargs):
    del host, kwargs
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetch_extracts_text_and_canonical_without_scripts_or_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text=(
                '<html><head><link rel="canonical" href="/official"></head>'
                "<body>Vision API is available<script>secret()</script></body></html>"
            ),
            request=request,
        )

    result = SafeEvidenceFetcher(
        client=client(handler), resolver=public_dns
    ).fetch("https://docs.example.com/vision")

    assert result.text == "Vision API is available"
    assert result.canonical_url == "https://docs.example.com/official"


def test_redirect_target_is_revalidated_and_private_dns_is_rejected() -> None:
    def resolver(host: str, port: int, **kwargs):
        address = "93.184.216.34" if host == "public.example.com" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://private.example.com/secret"},
            request=request,
        )

    with pytest.raises(EvidenceFetchError, match="UNSAFE_DNS_ADDRESS"):
        SafeEvidenceFetcher(client=client(handler), resolver=resolver).fetch(
            "https://public.example.com/start"
        )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://127.0.0.1/a", "IP_LITERAL_REJECTED"),
        ("https://host.local/a", "LOCAL_HOST_REJECTED"),
        ("https://example.com:8443/a", "PORT_REJECTED"),
        ("https://example.com:invalid/a", "PORT_REJECTED"),
        ("https://user:pass@example.com/a", "URL_CREDENTIALS_REJECTED"),
    ],
)
def test_unsafe_targets_are_rejected_before_request(url: str, reason: str) -> None:
    with pytest.raises(EvidenceFetchError, match=reason):
        SafeEvidenceFetcher(
            client=client(lambda request: pytest.fail("network must not run")),
            resolver=public_dns,
        ).fetch(url)


def test_timeout_is_bounded_and_reported() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(EvidenceFetchError, match="NETWORK_FAILED"):
        SafeEvidenceFetcher(
            client=client(handler), resolver=public_dns, attempts=2
        ).fetch("https://example.com/a")
    assert calls == 2


@pytest.mark.parametrize(
    ("headers", "body", "reason"),
    [
        ({"Content-Type": "application/octet-stream"}, b"binary", "UNSUPPORTED_CONTENT_TYPE"),
        ({"Content-Type": "text/plain", "Content-Length": "100"}, b"x", "RESPONSE_TOO_LARGE"),
        ({"Content-Type": "text/plain"}, b"x" * 100, "RESPONSE_TOO_LARGE"),
    ],
)
def test_content_type_and_response_size_are_bounded(headers, body, reason) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, content=body, request=request)

    with pytest.raises(EvidenceFetchError, match=reason):
        SafeEvidenceFetcher(
            client=client(handler), resolver=public_dns, maximum_response_bytes=10
        ).fetch("https://example.com/a")


def test_official_surface_seed_verifies_subdomain_and_reuses_cache(tmp_path) -> None:
    path = tmp_path / "official_surfaces.json"
    now = datetime(2026, 8, 22, tzinfo=UTC)
    resolver = OfficialSurfaceResolver(
        cache_path=path,
        seeds={"deepseek.com": "DeepSeek"},
        now=now,
    )

    trust = resolver.verify("https://api-docs.deepseek.com/guides/vision")
    assert trust is not None
    assert trust.entity == "DeepSeek"
    assert trust.relationship == "verified_subdomain"

    reloaded = OfficialSurfaceResolver(
        cache_path=path,
        seeds={},
        now=now,
    ).verify("https://api-docs.deepseek.com/other")
    assert reloaded is not None
    assert reloaded.status is SurfaceTrustStatus.VERIFIED


def test_unknown_surface_is_not_authenticated_by_name_guess(tmp_path) -> None:
    resolver = OfficialSurfaceResolver(
        cache_path=tmp_path / "cache.json",
        seeds={"deepseek.com": "DeepSeek"},
    )

    assert resolver.verify("https://deepseek-news.example.com/post") is None
