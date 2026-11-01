"""gateway_deliver.py — hand words to a seat through the gateway's POST /agent-message.

The gateway's verified_inject is the one delivery path that knows every kind of seat: a tmux seat gets the verified
paste (idle gate, typed-text guard, submit verify, pane lock), a headless seat gets its supervisor's stdin (its pane
holds the supervisor, so a key typed there lands in the wrong process). Any code outside the gateway that delivers
text to a seat calls deliver() here instead of typing into the pane itself.

Same URL and bearer as the gateway itself. Under `orchestra up` the env says (WATCH_GATEWAY_URL,
WATCH_GATEWAY_TOKEN_FILE); from a plain shell or cron it does not, so both come from the install's settings
(orchestra.toml [gateway] host/port, the data dir's state/watch-gateway-token), as `orchestra up` derives them.
Last fallbacks: 127.0.0.1:9091 and the legacy ~/.config/jarvis token. Loopback only.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:9091"
LEGACY_TOKEN = "~/.config/jarvis/watch-gateway-token"


def _settings():
    """The install's settings (orchestra_cli/settings.py), or None when they cannot be read."""
    try:
        import sys
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from orchestra_cli.settings import load_settings
        return load_settings()
    except Exception:  # noqa: BLE001 — no readable config: the last fallbacks
        return None


def gateway_url() -> str:
    env = (os.environ.get("WATCH_GATEWAY_URL") or "").strip()
    if env:
        return env.rstrip("/")
    st = _settings()
    if st is not None and getattr(st, "gateway_port", None):
        return f"http://{st.gateway_host or '127.0.0.1'}:{st.gateway_port}"
    return DEFAULT_URL


def token_path() -> Path:
    env = (os.environ.get("WATCH_GATEWAY_TOKEN_FILE") or "").strip()
    if env:
        return Path(env)
    st = _settings()
    if st is not None:
        try:
            p = Path(st.token_file)
            if p.exists():
                return p
        except Exception:  # noqa: BLE001
            pass
    return Path(os.path.expanduser(LEGACY_TOKEN))


def gateway_token() -> str:
    try:
        return token_path().read_text().strip()
    except OSError:
        return ""


def headless_seat(session, orch_dir=None):
    """The seat id when `session` is a switched-on headless seat that is not in Dev, else None (any failure: None)."""
    try:
        try:
            import headless_switch as HS
        except ImportError:
            from scripts import headless_switch as HS
        return HS.headless_seat(session, orch_dir=orch_dir)
    except Exception:  # noqa: BLE001 — unreadable switch: treat as a tmux seat, as every other reader does
        return None


def deliver(session, text, *, source="mail", force=False, accepts_held=False, actor="automation",
            timeout=30.0, url=None, token=None, urlopen=None) -> dict:
    """POST /agent-message. Returns {"ok", "delivered", "held", "status", "reason", "body"}:
      delivered: the seat took the words (a tmux submit verified, or the supervisor queued them);
      held:      accepted durably for the seat's next turn boundary (only when accepts_held);
      status:    the HTTP status, 0 when the gateway could not be reached;
      reason:    the gateway's refusal reason ("busy", "unverified_submit", ...) or why it was not reached.
    Never raises."""
    if os.environ.get("PYTEST_CURRENT_TEST") and url is None and urlopen is None:
        # A test that forgot its stand-in must never reach the live gateway.
        return {"ok": False, "delivered": False, "held": False, "status": 599,
                "reason": "blocked under pytest (no stand-in gateway)", "body": {}}
    payload = {"session": session, "text": text, "source": "person" if source == "person" else "mail"}
    if force:
        payload["force"] = True
    if accepts_held:
        payload["accepts"] = ["held"]
    req = urllib.request.Request(
        f"{(url or gateway_url()).rstrip('/')}/agent-message", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token or gateway_token()}",
                 "X-Actor": actor})
    status, body = 0, {}
    try:
        with (urlopen or urllib.request.urlopen)(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            body = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:     # 409 busy / 502 unverified / 404 no such session carry a JSON body
        status = e.code
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            body = {}
    except Exception as e:  # noqa: BLE001 — unreachable, timed out, bad JSON: not delivered, said plainly
        return {"ok": False, "delivered": False, "held": False, "status": 0,
                "reason": f"gateway_unreachable:{type(e).__name__}", "body": {}}
    if not isinstance(body, dict):
        body = {}
    delivered = bool(body.get("ok") and body.get("delivered"))
    held = bool(body.get("ok") and body.get("held"))
    reason = "" if (delivered or held) else str(body.get("reason") or body.get("error") or f"http_{status}")
    return {"ok": delivered or held, "delivered": delivered, "held": held, "status": status,
            "reason": reason, "body": body}
