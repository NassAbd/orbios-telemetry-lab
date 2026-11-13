"""JSON Lines worker, intended only for a network-disabled local container."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from pydantic import ValidationError

from .presentation import LAB_REQUEST, Lab
from .sandbox import require_isolation
from .service import Service


def respond(lab: Lab, line: str | bytes) -> dict[str, object]:
    try:
        request = LAB_REQUEST.validate_json(line)
        return {"ok": True, "data": lab.dispatch(request)}
    except ValidationError as exc:
        return {"ok": False, "error": "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {"ok": False, "error": "Local worker error. Check the launcher terminal."}


def main() -> None:
    require_isolation()
    root = Path("/bench")
    lab = Lab(root, Service(root))
    for line in sys.stdin:
        reply = respond(lab, line)
        print(json.dumps(reply, ensure_ascii=True, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
