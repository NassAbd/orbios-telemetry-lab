"""Smoke-test a running local Compose project with Python's standard library."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
OPENER = build_opener(ProxyHandler({}))


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], cwd=ROOT, text=True).strip()


def request(path: str, data: dict[str, object] | None = None, *, origin: str | None = None) -> dict:
    req = Request(BASE + path)
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", origin)
    with OPENER.open(req, timeout=45) as response:
        body = json.load(response)
    assert body["ok"], body
    return body["data"]


def resources(container: str) -> dict:
    code = (
        "import json, os, platform; from pathlib import Path; root = Path('/sys/fs/cgroup'); "
        "print(json.dumps({'uid': os.getuid(), 'architecture': platform.machine(), "
        "'interfaces': sorted(os.listdir('/sys/class/net')), "
        "'routes': Path('/proc/net/route').read_text().splitlines()[1:], "
        "**{f: (root/f).read_text().strip() for f in ('cpu.max','memory.max','memory.swap.max','memory.peak','memory.events')}}))"
    )
    return json.loads(docker("exec", container, "python", "-c", code))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default="orbios-telemetry-lab")
    args = parser.parse_args()
    endpoint = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    if not endpoint:
        endpoint = docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    if not endpoint.startswith(("unix://", "npipe://")):
        raise SystemExit("Use a local Docker socket or named pipe.")
    compose = ("compose", "-p", args.project_name)
    ids = {role: docker(*compose, "ps", "-q", role) for role in ("worker", "web")}
    containers = {role: json.loads(docker("inspect", cid))[0] for role, cid in ids.items()}
    for info in containers.values():
        config = info["HostConfig"]
        assert info["State"]["Health"]["Status"] == "healthy"
        assert config["ReadonlyRootfs"] and not config["Privileged"]
        assert "ALL" in config["CapDrop"] and "no-new-privileges:true" in config["SecurityOpt"]
        assert len(info["Mounts"]) == 1 and info["Mounts"][0]["Type"] == "volume"
        assert info["Mounts"][0]["Destination"] == "/run/telemetry-lab"
    worker_config = containers["worker"]["HostConfig"]
    assert worker_config["NetworkMode"] == "none" and not worker_config["PortBindings"]
    assert containers["web"]["HostConfig"]["PortBindings"] == {
        "8765/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8765"}],
    }
    assert not containers["web"]["Mounts"][0]["RW"]
    assert containers["worker"]["Mounts"][0]["Name"] == containers["web"]["Mounts"][0]["Name"]

    assert request("/healthz")["status"] == "ready"
    with OPENER.open(BASE + "/", timeout=5) as response:
        html = response.read().decode()
    for label in ("Ask Telemetry", "Telemetry Catalogue", "How It Works", "Explain this answer"):
        assert label in html
    try:
        request("/api/request", {"action": "inspect"}, origin="https://example.invalid")
    except HTTPError as exc:
        assert exc.code == 403
    else:
        raise AssertionError("Foreign origin was accepted")
    for mode in ("symbolic", "minilm", "hybrid"):
        question = "What is the SoC temperature right now?"
        answer = request("/api/request", {"action": "ask", "question": question, "mode": mode})
        assert answer["question"] == question and answer["mode"] == mode
        assert answer["lines"] == ["Q 1 P001 1m now"] and answer["decisions"]
        assert answer["readings"][0]["packet"]["fields"]["v"] == "58"

    app = "compose_test_" + uuid4().hex[:8]
    manifest = {"id": app, "label": app, "version": "1.0-sim", "budget_s": 120,
                "metrics": [{"id": "pulse", "label": "Test pulse", "kind": "event"}]}
    request("/api/request", {"action": "save", "manifest": manifest})
    try:
        mid = f"A.{app}.pulse"
        assert any(p["id"] == mid for p in request("/api/state")["catalogue"])
        details = request("/api/request", {"action": "metric", "id": mid})
        assert details["registered"] and details["preview"]
    finally:
        request("/api/request", {"action": "remove", "id": app})
    assert all(a["id"] != app for a in request("/api/state")["apps"])

    measured = {role: resources(cid) for role, cid in ids.items()}
    worker = measured["worker"]
    assert worker["interfaces"] == ["lo"] and not worker["routes"]
    assert worker["cpu.max"] == "400000 100000"
    assert worker["memory.max"] == "2147483648" and worker["memory.swap.max"] == "0"
    assert all(data["uid"] == 10001 for data in measured.values())
    assert all("oom 0" in data["memory.events"].splitlines() for data in measured.values())

    report = {
        "checks": ["health", "UI entry points", "foreign origin rejected", "all three modes",
                   "synthetic catalogue edits", "worker isolated", "loopback port", "resource limits"],
        "resources": measured,
        "images": {role: {"id": info["Image"], "size_bytes": int(docker("image", "inspect", info["Image"], "--format", "{{.Size}}"))}
                   for role, info in containers.items()},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
