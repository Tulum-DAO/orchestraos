"""ElevenLabs post-call webhook signature check (gm msg_1fb3f0ee; the exposure on card apr_15363e88).

/webhook/post-call is public over the Funnel, and each push makes the proxy fetch from ElevenLabs with the operator's
key. Only pushes SIGNED by ElevenLabs are accepted. Format (elevenlabs-python webhooks_custom.py): header
`ElevenLabs-Signature: t=<unix seconds>,v0=<hex>`, hex = HMAC-SHA256(secret, f"{t}.{raw_body}"); the SDK refuses
anything older than 30 minutes. The shared secret is read BY REFERENCE at request time from .env.secrets
(ELEVENLABS_WEBHOOK_SECRET), so setting it needs no restart, and it is never logged.

Mode (env ARTURO_POSTCALL_AUTH): "enforce" (default) refuses with 401; "log" lets the push through but counts
what it would have refused. No secret configured = refuse (enforce): an unconfigured check must never be an
open door. Refusals are counted and logged with the reason only, never body content."""
import hashlib
import hmac
import os
import threading
import time
from pathlib import Path

import sys
# The ONE data-dir default is orchestra_cli.settings.data_dir (data-dir sweep S5); orchestra_cli
# lives in this file's checkout, appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402
SECRET_KEY = "ELEVENLABS_WEBHOOK_SECRET"
HEADER = "ElevenLabs-Signature"
MAX_AGE_S = 30 * 60          # the SDK's tolerance
MAX_SKEW_S = 5 * 60          # a timestamp from the future is a forgery or a broken clock

_counts = {}
_lock = threading.Lock()


def _secret(k):
    """The process env first, then <data dir>/.env.secrets: the same two places every other Arturo secret
    is read from (docs/ARTURO.md). Resolved per call, so setting it needs no restart."""
    env = os.environ.get(k, "").strip()
    if env:
        return env
    try:
        for line in open(Path(os.environ.get("ORCHESTRA_DIR") or _data_dir()) / ".env.secrets"):
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def verify(raw_body, header, secret, now=None):
    """None when the signature is valid, else a short reason (never contains body content)."""
    now = time.time() if now is None else now
    if not header:
        return "missing"
    t = sig = None
    for part in str(header).split(","):
        part = part.strip()
        if part.startswith("t="):
            t = part[2:]
        elif part.startswith("v0="):
            sig = part
    if t is None or sig is None:
        return "malformed"
    try:
        ts = int(t)
    except ValueError:
        return "malformed"
    if ts < now - MAX_AGE_S:
        return "stale"
    if ts > now + MAX_SKEW_S:
        return "future"
    body = raw_body.decode("utf-8", "replace") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)
    want = "v0=" + hmac.new(secret.encode("utf-8"), f"{t}.{body}".encode("utf-8"), hashlib.sha256).hexdigest()
    return None if hmac.compare_digest(sig, want) else "bad_signature"


def check(raw_body, header):
    """(allow, reason). reason is None for a valid push; in log mode a bad push is allowed with its reason."""
    secret = _secret(SECRET_KEY)
    reason = "no_secret" if not secret else verify(raw_body, header, secret)
    if reason is None:
        return True, None
    with _lock:
        _counts[reason] = _counts.get(reason, 0) + 1
    enforce = os.environ.get("ARTURO_POSTCALL_AUTH", "enforce") != "log"
    return (not enforce), reason


def counts():
    with _lock:
        return dict(_counts)


def reset_counts():
    with _lock:
        _counts.clear()
