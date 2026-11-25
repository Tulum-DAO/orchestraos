"""tracker_snapshot.py — a compact, READ-ONLY snapshot of the operator's finances from his spending tracker, for
the turns and calls he makes from inside the tracker (host_page.py).

Two of the tracker's read-only agent tools (README "Agent API + MCP"): month_summary and list_upcoming, over
POST <SPENDING_AGENT_URL>/api/agent/tools/<name> with the tracker's agent token, read from the token FILE by path at
every fetch (SPENDING_AGENT_TOKEN_FILE; rotating the token needs no restart here). The token is never logged,
returned or kept. X-Agent-Source: arturo.

Never in the way: one fetch is bounded by TIMEOUT_S in total (both tools), a result (or a failure) is cached TTL_S,
a stale result is served while a background refresh runs, and a failure is one log line and no snapshot.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("tracker_snapshot")

DEFAULT_URL = "http://127.0.0.1:8760"
DEFAULT_TOKEN_FILE = "~/apps/spending-tracker/data/agent-token"
TTL_S = float(os.environ.get("SPENDING_SNAPSHOT_TTL_S", "60"))
TIMEOUT_S = float(os.environ.get("SPENDING_SNAPSHOT_TIMEOUT_S", "2.5"))
MAX_CHARS = 1200
UPCOMING_DAYS = 14


class Unavailable(Exception):
    """Why there is no snapshot, in words safe to log (never the token, never a response body)."""


def _url():
    return (os.environ.get("SPENDING_AGENT_URL") or DEFAULT_URL).rstrip("/")


def _token():
    path = os.path.expanduser(os.environ.get("SPENDING_AGENT_TOKEN_FILE") or DEFAULT_TOKEN_FILE)
    try:
        with open(path) as f:
            tok = f.read().strip()
    except OSError as e:
        raise Unavailable(f"token file unreadable ({type(e).__name__})") from None
    if not tok:
        raise Unavailable("token file empty")
    return tok


def _call(base, name, body, token, timeout):
    req = urllib.request.Request(f"{base}/api/agent/tools/{name}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                          "X-Agent-Source": "arturo"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(256 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise Unavailable(f"{name}: HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:       # TimeoutError is an OSError
        reason = getattr(e, "reason", e)
        why = "timeout" if isinstance(reason, TimeoutError) else type(reason).__name__
        raise Unavailable(f"{name}: {why}") from None
    except ValueError:
        raise Unavailable(f"{name}: not JSON") from None
    if not isinstance(data, dict):
        raise Unavailable(f"{name}: unexpected shape")
    return data


def _money(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    return ("-" if n < 0 else "") + f"${abs(n):,.2f}"


def _s(v, n=40):
    return " ".join(str(v or "").split())[:n]


def render(summary, upcoming):
    """The snapshot as a few plain lines, at most MAX_CHARS."""
    ms, up = summary or {}, upcoming or {}
    lines = []
    if ms:
        line = (f"This month ({_s(ms.get('month'), 7)}): income {_money(ms.get('income'))}, spending "
                f"{_money(ms.get('spending'))}, net {_money(ms.get('net'))}.")
        if ms.get("has_accounts") and ms.get("current_balance") is not None:
            line += f" Balance now {_money(ms.get('current_balance'))}"
            if ms.get("projected_end_of_month_balance") is not None:
                line += f", projected end of month {_money(ms.get('projected_end_of_month_balance'))}"
            line += "."
        if ms.get("unpaid_bills_left") is not None:
            line += f" Bills still unpaid this month {_money(ms.get('unpaid_bills_left'))}."
        lines.append(line)
        cats = [c for c in ms.get("by_category") or [] if isinstance(c, dict)][:6]
        if cats:
            lines.append("Spending by category: " + "; ".join(
                f"{_s(c.get('category'), 30)} {_money(c.get('amount'))}" for c in cats) + ".")
        tops = [t for t in ms.get("top_merchants") or [] if isinstance(t, dict)][:5]
        if tops:
            lines.append("Top merchants: " + "; ".join(
                f"{_s(t.get('merchant'), 30)} {_money(t.get('amount'))}" for t in tops) + ".")
    if up:
        items = [i for i in up.get("items") or [] if isinstance(i, dict)]
        due = [i for i in items if not i.get("paid")]
        head = (f"Next {int(up.get('days') or UPCOMING_DAYS)} days: unpaid bills {_money(up.get('unpaid_bills_total'))}, "
                f"expected income {_money(up.get('expected_income_total'))}.")
        lines.append(head)
        if due:
            rows = []
            for i in due[:10]:
                tag = " OVERDUE" if i.get("overdue") else (" autopay" if i.get("autopay") else "")
                kind = "bill" if i.get("kind") == "bill" else "payout"
                rows.append(f"{_s(i.get('name'), 30)} {_money(abs(float(i.get('amount') or 0)))} {kind} "
                            f"{_s(i.get('date'), 10)}{tag}")
            lines.append("Due: " + "; ".join(rows) + ".")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1].rstrip() + "…"


def fetch(timeout=None):
    """One snapshot, or raises Unavailable. Both tools share one deadline."""
    timeout = TIMEOUT_S if timeout is None else timeout
    deadline = time.monotonic() + timeout
    token = _token()                         # by path, at every fetch
    base = _url()
    out = []
    for name, body in (("month_summary", {}), ("list_upcoming", {"days": UPCOMING_DAYS})):
        left = deadline - time.monotonic()
        if left <= 0.05:
            raise Unavailable(f"{name}: no time left")
        out.append(_call(base, name, body, token, left))
    del token
    text = render(out[0], out[1])
    if not text:
        raise Unavailable("empty snapshot")
    return text


class Snapshot:
    """The cached snapshot. get() returns text or None; it blocks only for the very first fetch (bounded by the
    fetch's own timeout), and never while another fetch is already running."""

    def __init__(self, fetch_fn=fetch, ttl=TTL_S, clock=time.monotonic):
        self._fetch, self._ttl, self._clock = fetch_fn, ttl, clock
        self._lock = threading.Lock()
        self._val, self._at, self._busy = None, None, False

    def get(self):
        with self._lock:
            fresh = self._at is not None and self._clock() - self._at < self._ttl
            if fresh or self._busy:
                return self._val
            had = self._at is not None
            self._busy = True
        if had:                              # stale: serve what we have, refresh behind it
            threading.Thread(target=self._refresh, name="tracker-snapshot", daemon=True).start()
            return self._val
        return self._refresh()

    def _refresh(self):
        try:
            val = self._fetch()
        except Unavailable as e:
            log.warning(f"tracker snapshot omitted: {e}")
            val = None
        except Exception as e:  # noqa: BLE001 — never the turn's problem; name the class only
            log.warning(f"tracker snapshot omitted: {type(e).__name__}")
            val = None
        with self._lock:
            self._val, self._at, self._busy = val, self._clock(), False
        return val


SNAPSHOT = Snapshot()


def get():
    """The process-wide cached snapshot (text or None)."""
    return SNAPSHOT.get()
