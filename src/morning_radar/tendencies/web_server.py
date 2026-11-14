"""Loopback-only development editor. GitHub is the deployed durable inbox."""

from __future__ import annotations

import json
import mimetypes
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from morning_radar.tendencies.core_store import CoreStore
from morning_radar.tendencies.web_input import WebRequest, accept_input
from morning_radar.tendencies.web_pages import build_pages, page_name

CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


class EditorApp:
    def __init__(
        self,
        store: CoreStore,
        repository: Path,
        *,
        origin: str,
        passphrase: str,
        synthetic: bool = False,
    ):
        self.store, self.repository = store, repository
        self.origin, self.passphrase = origin, passphrase
        self.synthetic = synthetic
        self.output = store.root / "web-preview"
        self.sessions: dict[str, tuple[str, float]] = {}
        self.attempts: list[float] = []

    def build(self):
        build_pages(
            self.store,
            self.output / "tendency",
            repository=self.repository,
            local_write=True,
            synthetic=self.synthetic,
        )

    def request(self, method: str, path: str, headers, body: bytes = b"") -> tuple:
        base_headers = {
            "Content-Security-Policy": CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }

        def result(code, value, extra=None):
            return (
                code,
                base_headers | {"Content-Type": "application/json; charset=utf-8"} | (extra or {}),
                json.dumps(value, ensure_ascii=False).encode(),
            )

        if headers.get("Host") != urlsplit(self.origin).netloc:
            return result(403, {"message": "请求主机不受信任"})
        if method == "POST" and (
            headers.get("Origin") != self.origin
            or headers.get("Content-Type") != "application/json"
        ):
            return result(403, {"message": "跨站或不支持的请求"})
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except Exception:
            return result(403, {"message": "会话无效"})
        session_key = cookie.get("radar_session")
        session = self.sessions.get(session_key.value) if session_key else None
        if session and session[1] < time.monotonic():
            session = None
        if path == "/api/session" and method == "GET":
            return (
                result(200, {"csrf": session[0]})
                if session
                else result(401, {"message": "尚未授权"})
            )
        if method == "POST":
            if len(body) > 150000:
                return result(413, {"message": "输入过大"})
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Object required")
            except ValueError:
                return result(400, {"message": "输入格式无效"})
            if path == "/api/login":
                self.attempts = [t for t in self.attempts if t > time.monotonic() - 60]
                if len(self.attempts) >= 10:
                    return result(429, {"message": "尝试过多，请稍后重试"})
                self.attempts.append(time.monotonic())
                if set(payload) != {"passphrase"} or not isinstance(payload["passphrase"], str):
                    return result(400, {"message": "不支持的输入"})
                if not secrets.compare_digest(payload["passphrase"], self.passphrase):
                    return result(401, {"message": "授权失败"})
                key, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                self.sessions[key] = (csrf, time.monotonic() + 3600)
                return result(
                    200,
                    {"message": "会话已授权"},
                    {
                        "Set-Cookie": (
                            f"radar_session={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600"
                        )
                    },
                )
            if path != "/api/submissions":
                return result(404, {"message": "没有此入口"})
            if not session or not secrets.compare_digest(
                headers.get("X-CSRF-Token", ""), session[0]
            ):
                return result(403, {"message": "需要授权会话与有效 CSRF"})
            try:
                request = WebRequest.model_validate(payload)
                saved = accept_input(
                    self.store,
                    request,
                    author="local-owner",
                    source={"channel": "local-session"},
                    synthetic=self.synthetic,
                )
                conflict = saved["acceptance"]["status"] == "conflict"
                return result(
                    409 if conflict else 201,
                    {
                        "message": "版本冲突；原文已保存，请查看最新版本后重新提交。"
                        if conflict
                        else "C 已持久接收原文；尚不表示判断采纳或公开发布。",
                        "receipt_url": "/tendency/" + page_name("s", saved["submission_id"]),
                        "submission_id": saved["submission_id"],
                    },
                )
            except ValueError:
                return result(
                    409, {"message": "输入校验或操作身份冲突；请保留草稿检查后另起投递。"}
                )
            except OSError:
                return result(503, {"message": "写入暂不可用，请保留草稿并重试同一操作。"})
        if method != "GET":
            return result(405, {"message": "不支持的请求"})
        if path.startswith("/api/"):
            return result(404, {"message": "没有此入口"})
        route = unquote(urlsplit(path).path)
        if route in {"/", "/tendency/"}:
            return result(302, {"message": "打开 Tendency"}, {"Location": "/tendency/index.html"})
        if "\\" in route or ".." in route.split("/"):
            return result(400, {"message": "路径无效"})
        target = (self.output / route.lstrip("/")).resolve()
        if self.output.resolve() not in target.parents or target.suffix not in {
            ".html",
            ".css",
            ".js",
        }:
            return result(404, {"message": "没有此页面"})
        try:
            if target.suffix == ".html":
                self.build()
            content = target.read_bytes()
        except (OSError, ValueError):
            return result(404, {"message": "页面不可读取"})
        mime = mimetypes.guess_type(target.name)[0] or "text/plain"
        return 200, base_headers | {"Content-Type": mime + "; charset=utf-8"}, content


def serve(store: CoreStore, repository: Path, *, port: int = 8768, synthetic: bool = False) -> None:
    passphrase = secrets.token_urlsafe(32)
    private = store.root / ".local-write-passphrase"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text(passphrase, encoding="utf-8")
    private.chmod(0o600)
    app = EditorApp(
        store,
        repository,
        origin=f"http://127.0.0.1:{port}",
        passphrase=passphrase,
        synthetic=synthetic,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 150000 or self.headers.get("Transfer-Encoding"):
                    self.send_error(413)
                    return
                self.connection.settimeout(10)
                body = self.rfile.read(length) if self.command == "POST" else b""
                status, headers, content = app.request(self.command, self.path, self.headers, body)
            except Exception:
                status, headers, content = (
                    503,
                    {"Content-Type": "text/plain"},
                    b"Service unavailable",
                )
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass  # Never log input, auth values, cookies, URL queries or internal paths.

    app.build()
    with HTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"Loopback editor: http://127.0.0.1:{port}/ (local only)", flush=True)
        print("Runtime passphrase is in the workspace private file; never publish it.", flush=True)
        server.serve_forever()
