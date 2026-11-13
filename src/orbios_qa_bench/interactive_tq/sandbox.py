"""Check the local worker's actual resource envelope before inference."""
from pathlib import Path
from platform import machine

CGROUP = Path("/sys/fs/cgroup")


def require_isolation() -> dict[str, object]:
    limits = {name: (CGROUP / name).read_text().strip() for name in ("memory.max", "memory.swap.max", "cpu.max")}
    interfaces = sorted(p.name for p in Path("/sys/class/net").iterdir())
    quota, period = limits["cpu.max"].split()
    if (machine() not in {"aarch64", "x86_64"} or interfaces != ["lo"] or limits["memory.max"] != "2147483648"
            or limits["memory.swap.max"] != "0" or quota == "max" or int(quota) > 4 * int(period)):
        raise RuntimeError("Run this worker in the documented offline Linux ARM64 or AMD64 container.")
    return {"architecture": machine(), "interfaces": interfaces, **limits}
