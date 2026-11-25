"""Decision log for Jev hooks and tools: one JSON line per decision, never a credential.

The log path is explicit: JEV_LOG, else None (no logging). Hook entry points set a
default under $ORCHESTRA_DIR/logs; library code and tests never write anywhere they were
not told to.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


def log_path() -> Optional[str]:
    return os.environ.get("JEV_LOG") or None


def record(kind: str, payload: Dict[str, Any], path: Optional[str] = None) -> None:
    """Append one line. Logging must never break a hook, so OSErrors are swallowed here only."""
    target = path or log_path()
    if not target:
        return
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind, **payload}
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def usage_of(result: Dict[str, Any]) -> Dict[str, Any]:
    """The loggable bits of a client result: model, usage, cost. No raw state."""
    meta = result.get("meta") or {}
    return {"model": result.get("model"), "usage": result.get("usage"),
            "elapsed_ms": meta.get("elapsed_ms"), "provider": meta.get("provider"),
            "cost": meta.get("cost")}
