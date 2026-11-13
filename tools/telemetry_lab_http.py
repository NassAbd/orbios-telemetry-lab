"""Loopback HTTP relay for the isolated synthetic telemetry worker."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Protocol

WEB = Path(__file__).resolve().parents[1] / "benchmark/interactive_tq/web"


class WorkerBridge(Protocol):
    def request(self, data: dict[str, object]) -> dict[str, object]: ...


class Server(HTTPServer):
    def __init__(
        self, port: int, bridge: WorkerBridge, *, bind: str = "127.0.0.1",
        hosts: tuple[str, ...] | None = None,
    ) -> None:
        self.bridge = bridge
        super().__init__((bind, port), Handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.hosts = hosts or (self.authority,)

    def get_request(self):  # HTTPServer is serial; prevent a stalled local request from holding it indefinitely.
        connection, address = super().get_request()
        connection.settimeout(12)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    server: Server
    STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/catalogue.js": ("catalogue.js", "text/javascript; charset=utf-8"),
              "/pipeline.js": ("pipeline.js", "text/javascript; charset=utf-8"),
              "/style.css": ("style.css", "text/css; charset=utf-8")}

    def reply(self, code: int, body: bytes, mime: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, code: int, data: dict[str, object]) -> None:
        self.reply(code, json.dumps(data, ensure_ascii=True).encode("ascii"), "application/json")

    def good_host(self) -> bool:
        if self.headers.get("Host") in self.server.hosts:
            return True
        self.json_reply(421, {"ok": False, "error": "Use the printed 127.0.0.1 URL."})
        return False

    def forward(self, data: dict[str, object]) -> None:
        try:
            answer = self.server.bridge.request(data)
        except RuntimeError as exc:
            self.json_reply(503, {"ok": False, "error": str(exc)})
        else:
            self.json_reply(200 if answer.get("ok") else 422, answer)

    def do_GET(self) -> None:
        if not self.good_host():
            return
        if self.path == "/api/state":
            self.forward({"action": "inspect"})
        elif self.path == "/healthz":
            self.forward({"action": "health"})
        elif self.path in self.STATIC:
            name, mime = self.STATIC[self.path]
            self.reply(200, (WEB / name).read_bytes(), mime)
        else:
            self.json_reply(404, {"ok": False, "error": "Unknown route."})

    def do_POST(self) -> None:
        if not self.good_host():
            return
        if self.path != "/api/request":
            self.json_reply(404, {"ok": False, "error": "Unknown route."})
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.json_reply(403, {"ok": False, "error": "This API only accepts the local demo origin."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768 or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected a JSON request under 32 KiB.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
        except (ValueError, TimeoutError) as exc:
            self.json_reply(400, {"ok": False, "error": str(exc)})
            return
        self.forward(data)
