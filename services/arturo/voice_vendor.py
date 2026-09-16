"""Runtime voice-vendor preference (spec §4.1).

One JSON file, read PER CALL — never an env var read at boot — so a Settings flip takes effect on
the next conversation with no :5071 restart. Append-only audit log. A vendor is `allowed` only
when its credentials/config are present; set() refuses anything else (no silent fallback).
"""
import json
import os
import threading
import time
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))
DEFAULT_PATH = ORCHESTRA_DIR / "state/voice-vendor.json"
DEFAULT_LOG = ORCHESTRA_DIR / "state/voice-vendor.log"
DEFAULT_VENDOR = "elevenlabs"

REQUIRED = {
    "elevenlabs": ["ELEVENLABS_API_KEY"],
    "hume": ["HUME_API_KEY", "HUME_CONFIG_ID", "HUME_CONFIG_VERSION"],
}


def _secret(k):
    try:
        for line in open(ORCHESTRA_DIR / ".env.secrets"):
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get(k, "")


class VendorStore:
    def __init__(self, path=DEFAULT_PATH, log_path=DEFAULT_LOG, creds=_secret):
        self.path = Path(path)
        self.log_path = Path(log_path)
        self.creds = creds
        self._lock = threading.Lock()

    def _read(self):
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {"vendor": DEFAULT_VENDOR, "changed_at": None, "changed_by": None, "source": "default"}

    def get(self):
        v = self._read().get("vendor")
        return v if v in REQUIRED else DEFAULT_VENDOR

    def unavailable(self):
        out = {}
        for vendor, keys in REQUIRED.items():
            missing = [k for k in keys if not self.creds(k)]
            if missing:
                out[vendor] = "missing " + ", ".join(missing)
        return out

    def state(self):
        d = self._read()
        un = self.unavailable()
        d["allowed"] = [v for v in REQUIRED if v not in un]
        d["unavailable"] = un
        return d

    def unavailable_reason(self, vendor):
        """Why a vendor can't be used right now, or "" if it's available (empty string so the
        relay's `if reason:` refuse-guard reads it directly). Used by the relay to refuse a
        stale-but-unavailable preference visibly at claim (spec §6 / grain-review S1)."""
        if vendor not in REQUIRED:
            return f"unknown vendor {vendor!r}"
        return self.unavailable().get(vendor) or ""

    def set(self, vendor, by="unknown", source="api"):
        if vendor not in REQUIRED:
            raise ValueError(f"unknown vendor {vendor!r}; allowed: {sorted(REQUIRED)}")
        un = self.unavailable()
        if vendor in un:
            raise ValueError(f"vendor {vendor!r} unavailable: {un[vendor]}")
        st = {"vendor": vendor, "changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "changed_by": by, "source": source}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(self.path)
            with open(self.log_path, "a") as f:
                f.write(json.dumps(st) + "\n")
        return st


_default = VendorStore()


def get_vendor():
    return _default.get()


def state():
    return _default.state()


def unavailable_reason(vendor):
    return _default.unavailable_reason(vendor)


def set_vendor(vendor, by="unknown", source="api"):
    return _default.set(vendor, by=by, source=source)
