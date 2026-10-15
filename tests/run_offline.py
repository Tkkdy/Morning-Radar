"""Run explicitly selected pytest suites with external network transports blocked."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def blocked(*args, **kwargs):
    raise AssertionError("E2/E3 offline gate: external network is forbidden")


if __name__ == "__main__":
    with (
        patch("socket.socket.connect", blocked),
        patch("socket.socket.connect_ex", blocked),
        patch("socket.create_connection", blocked),
        patch("socket.getaddrinfo", blocked),
        patch("httpx.HTTPTransport.handle_request", blocked),
        patch("httpx.AsyncHTTPTransport.handle_async_request", blocked),
    ):
        raise SystemExit(pytest.main(["-p", "no:cacheprovider", *sys.argv[1:]]))
