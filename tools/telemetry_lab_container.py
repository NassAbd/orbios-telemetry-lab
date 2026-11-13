"""Compose transport for the existing telemetry lab. The worker listens on AF_UNIX."""
from __future__ import annotations

import json
import signal
import socket
import socketserver
import sys
from pathlib import Path
from typing import NoReturn
from urllib.request import ProxyHandler, build_opener

from telemetry_lab_http import Server

IPC = Path("/run/telemetry-lab/worker.sock")
ROOT = Path("/bench")
MAX_REQUEST = 262144
MAX_REPLY = 4194304


class SocketBridge:
    def request(self, data: dict[str, object], *, timeout: float = 45) -> dict[str, object]:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect(str(IPC))
                connection.sendall(json.dumps(data, ensure_ascii=True).encode("ascii") + b"\n")
                with connection.makefile("rb") as response:
                    line = response.readline(MAX_REPLY + 1)
                if not line.endswith(b"\n") or len(line) > MAX_REPLY:
                    raise ValueError("Incomplete reply")
                result = json.loads(line)
                if not isinstance(result, dict):
                    raise ValueError("Invalid reply")
                return result
        except (OSError, ValueError) as exc:
            raise RuntimeError("The isolated worker is unavailable. Check Docker Compose logs.") from exc


def worker() -> None:
    from orbios_qa_bench.interactive_tq.presentation import Lab
    from orbios_qa_bench.interactive_tq.sandbox import require_isolation
    from orbios_qa_bench.interactive_tq.service import Service
    from orbios_qa_bench.interactive_tq.worker import respond

    isolation = require_isolation()
    service = Service(ROOT)
    startup_ms = service.warm_index()
    lab = Lab(ROOT, service)

    class RequestHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(45)
            try:
                line = self.rfile.readline(MAX_REQUEST + 1)
                if not line.endswith(b"\n") or len(line) > MAX_REQUEST:
                    return
                result = respond(lab, line)
                self.wfile.write(json.dumps(result, ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n")
            except OSError:
                # A client can leave while the serial worker is still finishing its request.
                return

    # An unclean shutdown can leave a socket inode on the Compose-managed volume.
    if IPC.is_socket():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            try:
                probe.connect(str(IPC))
            except ConnectionRefusedError:
                IPC.unlink()
            else:
                raise RuntimeError("The IPC volume already has a running worker.")
    with socketserver.UnixStreamServer(str(IPC), RequestHandler) as server:
        try:
            IPC.chmod(0o600)
            print(json.dumps({"status": "ready", "model_and_index_ms": startup_ms, **isolation}), flush=True)
            server.serve_forever()
        finally:
            IPC.unlink(missing_ok=True)


def web() -> None:
    bridge = SocketBridge()
    with Server(8765, bridge, bind="0.0.0.0", hosts=("127.0.0.1:8765", "localhost:8765")) as server:
        print("OrbiOS Telemetry Lab is ready at http://localhost:8765", flush=True)
        server.serve_forever()


def stop(signum: int, frame: object) -> NoReturn:
    raise KeyboardInterrupt


def main() -> None:
    action = sys.argv[1]
    if action in {"web", "worker"}:
        signal.signal(signal.SIGTERM, stop)
        try:
            (worker if action == "worker" else web)()
        except KeyboardInterrupt:
            return
    elif action == "worker-health":
        if not SocketBridge().request({"action": "health"}, timeout=1.5).get("ok"):
            raise SystemExit(1)
    elif action == "web-health":
        opener = build_opener(ProxyHandler({}))
        with opener.open("http://127.0.0.1:8765/healthz", timeout=1.5) as response:
            if response.status != 200:
                raise SystemExit(1)
    else:
        raise SystemExit(f"Unknown container action: {action}")


if __name__ == "__main__":
    main()
