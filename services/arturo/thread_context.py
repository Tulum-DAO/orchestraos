"""thread_context.py — what any agent's call knows at start, and what reaches it during the call.

Shaw, 2026-11-25: "ensure each new call has everything above it, including previous calls, and timestamps of every
message sent and every phrase spoken, so there's continuity ... that same mechanism will need to exist across ANY
agent." One contract for every agent (congruent, not identical: each keeps its own voice, role and tools):

  the THREAD = the agent's chat as the operator sees it (the API's transcript items with audience "user") plus every
  phrase of every call that agent took (state/voice-calls/vc_*.json), in time order, every line stamped
  [ISO-UTC  Shaw-local] channel who: words.

At call start the newest of it goes in verbatim up to a token budget; what does not fit is carried as one summary line
per call or chat stretch (Shaw's rotation rule: recent verbatim, older summarized, sized in tokens, not words). During
the call, chat lines newer than the cursor become stamped notes, each within one session append.

Pure where it can be: the transcript fetch is injectable, journals are read from a directory the caller names. Never
raises into a call: an unreadable source is a line saying so, not an exception.
"""
import glob
import json
import math
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — py<3.9
    ZoneInfo = None

DEFAULT_BUDGET_TOKENS = 6000      # opening context; GPT-Live's start frame has no stated cap, appends do (500)
APPEND_MAX_TOKENS = 500           # native protocol.APPEND_MAX_TOKENS
LINE_MAX_CHARS = 1200             # one very long chat reply must not eat the whole budget
MERGE_GAP_S = 120                 # one speaker's phrases further apart than this in a call are separate turns
SUMMARY_SHARE = 0.2               # of the budget, for the EARLIER block
# The opening is fenced as history that is over (live probes 2026-11-25: unfenced, the thread ended on verbatim call
# dialogue and Arturo never greeted, native-90a7e00cd3d1; thread off, he greeted at 1.5 s, native-8f4a14b348a8).
PAST_OPEN = "PAST CONTEXT: background only. Everything below happened BEFORE this call and is over."
PAST_CLOSE = "END OF PAST CONTEXT. This is a NEW call that just connected; nobody has spoken in it yet."
SUMMARY_ITEM_CHARS = 200
CALL_FILES_MAX = 40               # newest journals considered (by mtime); the directory holds hundreds
CALL_MAX_AGE_S = 14 * 86400
_SECRET_RE = re.compile(r"(sk-)[A-Za-z0-9_-]{6,}|((?:KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=)\S+", re.I)
_SPOKEN_ROLES_SKIP = {"tool", "system", "tool_result", "function"}


@dataclass
class Line:
    at: float                     # epoch seconds UTC
    who: str
    text: str
    channel: str                  # "chat" | "call"
    call_id: str = ""
    role: str = ""                # "user" | "agent": who spoke, by role, so a name clash never folds two speakers
    phrases: int = 1              # call phrases folded into this turn (merge_phrases)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4)


_NUM_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_ISO_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?(Z|[+-][0-9]{2}:[0-9]{2})")
# datetime's range (years 1..9999) in epoch seconds: anything outside is not a time (the API twin uses the same).
_MIN_EPOCH, _MAX_EPOCH = -62135596800, 253402300799


def _in_range(x: float) -> bool:
    return math.isfinite(x) and _MIN_EPOCH <= x <= _MAX_EPOCH


def _ms(x: float) -> float:
    return math.floor(x * 1000) / 1000


def _epoch(v) -> Optional[float]:
    """An epoch number or a full ISO date-time WITH a zone, kept to the millisecond; anything else (a date alone, a
    boolean, "inf", "1_000") is None. The API twin parses exactly these (callContext.ts epoch)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return _ms(float(v)) if _in_range(float(v)) else None
    if not isinstance(v, str):
        return None
    s = v.strip()
    if _NUM_RE.fullmatch(s):
        x = float(s)
        return _ms(x) if _in_range(x) else None
    if not _ISO_RE.fullmatch(s):
        return None
    try:
        x = datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        return _ms(x) if _in_range(x) else None
    except (ValueError, OverflowError, OSError):
        return None


_EXACT = {"loaded": False, "values": []}


def _exact_secrets() -> list:
    """This install's own gateway token, by reference (never printed): blanked by exact value like the API does."""
    if not _EXACT["loaded"]:
        _EXACT["loaded"] = True
        path = os.environ.get("WATCH_GATEWAY_TOKEN_FILE") or os.path.join(
            os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra"), "state", "watch-gateway-token")
        try:
            with open(path, encoding="utf-8") as fh:
                v = fh.read().strip()
            if len(v) >= 8:
                _EXACT["values"] = [v]
        except OSError:
            pass
    return _EXACT["values"]


def _redact(text: str) -> str:
    """Secret shapes blanked (brain.redact's patterns) and the install's token by exact value, on every piece BEFORE it
    is clipped: the JSON transcript route is not redacted and the session is a third-party model."""
    text = text or ""
    for v in _exact_secrets():
        text = text.replace(v, "[redacted]")
    return _SECRET_RE.sub(lambda m: (m.group(1) + "[redacted]") if m.group(1) else (m.group(2) + "[redacted]"), text)


def _clip(text: str, n: int, tail: bool = False) -> str:
    text = " ".join(_redact(str(text or "")).split())
    if len(text) <= n:
        return text
    return "…" + text[len(text) - (n - 1):] if tail else text[: n - 1] + "…"


# --- sources --------------------------------------------------------------------------------------------------------

def chat_lines(items: Iterable[dict], agent_label: str, operator_label: str = "Operator",
               since: Optional[float] = None) -> list:
    """The chat as the operator sees it: text items tagged for the user, not machine turns (mail, approvals, relay)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("kind") != "text":
            continue
        # what the operator sees: tagged for the user, or their own message held for delivery (queued, untagged)
        if not (it.get("audience") == "user" or (it.get("queued") is True and it.get("role") == "user")):
            continue
        if isinstance(it.get("machine"), str) and it["machine"]:           # a machine turn, not the chat
            continue
        if not isinstance(it.get("text"), str):
            continue
        text = it["text"].strip()
        at = _epoch(it.get("ts"))
        if not text or at is None or (since is not None and at <= since):
            continue
        who = operator_label if it.get("role") == "user" else agent_label
        out.append(Line(at=at, who=who, text=text, channel="chat"))
    return out


def _no_constant(name):
    raise ValueError(f"not JSON: {name}")


def _journal_files(calls_dir, now: float) -> list:
    files = []
    for f in glob.glob(os.path.join(str(calls_dir), "vc_*.json")):
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        if now - mt <= CALL_MAX_AGE_S:
            files.append((mt, f))
    files.sort(reverse=True)
    return [f for _, f in files[:CALL_FILES_MAX]]


def call_lines(calls_dir, owner: Optional[str], exclude: Optional[str] = None, assistant_label: Optional[str] = None,
               operator_label: str = "Operator", now: Optional[float] = None, thread_id: Optional[str] = None) -> list:
    """Every spoken phrase of the owner's calls. owner None = Arturo's own calls (journals with no `agent`), else the
    calls placed with that agent. A file still marked live counts (the previous call may finalize seconds after the
    next starts); `exclude` is the call now starting."""
    label = assistant_label or (owner if owner else "Arturo")
    out = []
    for f in _journal_files(calls_dir, now if now is not None else datetime.now(timezone.utc).timestamp()):
        try:
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh, parse_constant=_no_constant)       # NaN/Infinity: not JSON (the API twin agrees)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        cid = doc["call_id"] if isinstance(doc.get("call_id"), str) and doc["call_id"] else os.path.basename(f)[:-5]
        if exclude and cid == exclude:
            continue
        agent = doc["agent"] if isinstance(doc.get("agent"), str) and doc["agent"] else None
        if thread_id is not None:
            if doc.get("thread_id") != thread_id:                      # the calls slotted into that page's thread
                continue
        elif agent != (owner or None):                                 # only a named agent owns a call
            continue
        start = _epoch(doc.get("started_at"))
        for t in doc.get("turns") or []:
            if not isinstance(t, dict):
                continue
            role = str(t.get("role") or "")
            if not isinstance(t.get("text"), str):
                continue
            text = t["text"].strip()
            if not text or role in _SPOKEN_ROLES_SKIP:
                continue
            at = _epoch(t.get("ts"))
            at = at if at is not None else start
            if at is None:
                continue
            out.append(Line(at=at, who=operator_label if role == "user" else label, text=text, channel="call",
                            call_id=cid, role="user" if role == "user" else "agent"))
    return out


# --- rendering ------------------------------------------------------------------------------------------------------

def _tz(tz: str):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — a bad zone name shows UTC, never fails a call
        return timezone.utc


def stamp(at: float, tz: str, tz_label: str) -> str:
    utc = datetime.fromtimestamp(at, tz=timezone.utc)
    local = utc.astimezone(_tz(tz))
    return f"[{utc.strftime('%Y-%m-%dT%H:%M:%SZ')} {local.strftime('%H:%M')} {tz_label}]"


def _where(line: Line) -> str:
    return f"call {line.call_id}" if line.channel == "call" else "chat"


def format_line(line: Line, tz: str, tz_label: str) -> str:
    # a merged turn over the cap keeps its tail: the newest words are the ones a caller refers back to
    return (f"{stamp(line.at, tz, tz_label)} {_where(line)} {line.who}: "
            f"{_clip(line.text, LINE_MAX_CHARS, tail=line.phrases > 1)}")


def _summaries(dropped: list, tz: str, tz_label: str, call_summary: dict) -> list:
    """One line per call (or chat stretch between calls) that did not fit verbatim, oldest first."""
    groups = []
    for ln in dropped:
        key = ln.call_id if ln.channel == "call" else "chat"
        if groups and groups[-1][0] == key:
            groups[-1][1].append(ln)
        else:
            groups.append((key, [ln]))
    out = []
    for key, lines in groups:
        first = lines[0]
        n = sum(l.phrases for l in lines)
        if key == "chat":
            gist = _clip(" / ".join(f"{l.who}: {l.text}" for l in lines[-2:]), SUMMARY_ITEM_CHARS)
            out.append(f"- {stamp(first.at, tz, tz_label)} chat, {n} message(s) not shown; last: {gist}")
        else:
            said = call_summary.get(key) or ""
            gist = _clip(said or " / ".join(f"{l.who}: {l.text}" for l in lines[-2:]), SUMMARY_ITEM_CHARS)
            out.append(f"- {stamp(first.at, tz, tz_label)} call {key}, {n} phrase(s) not shown; {gist}")
    return out


def merge_phrases(lines: list) -> list:
    """A turn is an exchange: consecutive phrases by the same speaker (same role and name) in the same call become one
    turn, stamped at its first phrase, while each phrase follows the one before within MERGE_GAP_S. Chat lines never
    merge. `lines` in time order."""
    out, last_at = [], None
    for ln in lines:
        prev = out[-1] if out else None
        if (prev is not None and ln.channel == "call" and prev.channel == "call" and ln.call_id == prev.call_id
                and ln.role == prev.role and ln.who == prev.who and ln.at - last_at <= MERGE_GAP_S):
            out[-1] = Line(at=prev.at, who=prev.who, text=f"{prev.text} {ln.text}", channel="call",
                           call_id=prev.call_id, role=prev.role, phrases=prev.phrases + 1)
        else:
            out.append(ln)
        last_at = ln.at
    return out


def render(lines: list, budget_tokens: int = DEFAULT_BUDGET_TOKENS, tz: str = "UTC", tz_label: str = "UTC",
           title: str = "THREAD", call_summary: Optional[dict] = None) -> str:
    head = (f"{title}: everything above this call, oldest first. Every line is [UTC time, {tz_label} time] then "
            "chat or call <id>, who, and the words. Newest lines are verbatim; older ones are summarized.")
    if not lines:
        return f"{head}\n(no earlier chat or calls in this thread)"
    lines = merge_phrases(sorted(lines, key=lambda l: l.at))
    summary_cap = max(40, int(budget_tokens * SUMMARY_SHARE))
    room = budget_tokens - estimate_tokens(head) - summary_cap
    kept = []
    for ln in reversed(lines):
        s = format_line(ln, tz, tz_label)
        cost = estimate_tokens(s) + 1
        if cost > room:
            break
        kept.append(s)
        room -= cost
    kept.reverse()
    dropped = lines[: len(lines) - len(kept)]
    parts = [head]
    if dropped:
        summ = _summaries(dropped, tz, tz_label, call_summary or {})
        block, used = [], 0
        for s in reversed(summ):                       # the most recent summaries matter most
            c = estimate_tokens(s) + 1
            if used + c > summary_cap:
                block.append(f"- ({len(summ) - len(block)} older item(s) omitted)")
                break
            block.append(s)
            used += c
        block.reverse()
        parts.append("EARLIER (summarized):")
        parts.extend(block)
    parts.append("RECENT (verbatim):")
    parts.extend(kept)
    return "\n".join(parts)


def live_notes(line: Line, tz: str, tz_label: str, max_tokens: int = APPEND_MAX_TOKENS) -> list:
    """A chat line that arrived during the call, as stamped notes that each fit one session append."""
    prefix = f"{stamp(line.at, tz, tz_label)} {_where(line)} {line.who} (typed, during this call): "
    room = max(40, max_tokens * 4 - len(prefix) - 16)
    text = " ".join(_redact(line.text or "").split())
    chunks = [text[i:i + room] for i in range(0, len(text), room)] or [""]
    return [(prefix if i == 0 else f"{stamp(line.at, tz, tz_label)} (cont.) ") + c for i, c in enumerate(chunks)]


# --- loading --------------------------------------------------------------------------------------------------------

def api_base() -> str:
    """The Node API: $ORCH_API_URL, else loopback on $ORCHESTRA_API_PORT (8888). Same as agent_call.api_base."""
    url = (os.environ.get("ORCH_API_URL") or "").strip()
    if url:
        return url.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('ORCHESTRA_API_PORT') or 8888}"


def fetch_transcript(agent: str, limit: int = 300, base: Optional[str] = None, timeout: float = 5.0) -> list:
    url = f"{base or api_base()}/api/agents/{urllib.parse.quote(str(agent), safe='')}/transcript?limit={int(limit)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return (json.loads(resp.read() or b"{}") or {}).get("items") or []


def shaw_tz(orch_dir: Optional[str] = None) -> tuple:
    """(tz, label) from state/shaw-location.json — the single source of truth for Shaw's current zone."""
    roots = [orch_dir, os.environ.get("ORCHESTRA_DIR"), os.path.expanduser("~/.orchestra")]
    for root in [r for r in roots if r]:
        try:
            with open(os.path.join(root, "state", "shaw-location.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("tz"):
                return d["tz"], d.get("label") or d["tz"]
        except (OSError, ValueError):
            continue
    return "UTC", "UTC"


def operator_name(orch_dir: Optional[str] = None) -> str:
    """The operator's own name (operator_store: <data dir>/state/arturo/operator.json), else "Operator"."""
    roots = [orch_dir, os.environ.get("ORCHESTRA_DIR"), os.path.expanduser("~/.orchestra")]
    for root in [r for r in roots if r]:
        try:
            with open(os.path.join(root, "state", "arturo", "operator.json"), encoding="utf-8") as fh:
                name = (((json.load(fh) or {}).get("facts") or {}).get("name") or {}).get("value")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (OSError, ValueError, AttributeError):
            continue
    return "Operator"


def load(chat_agent: str, call_owner: Optional[str], calls_dir, exclude: Optional[str] = None,
         fetch: Optional[Callable] = None, budget_tokens: int = DEFAULT_BUDGET_TOKENS, tz: Optional[str] = None,
         tz_label: Optional[str] = None, chat_limit: int = 300, operator_label: Optional[str] = None,
         assistant_label: Optional[str] = None) -> str:
    """The opening context for a call: `chat_agent`'s chat (the page the call was placed from) merged with the
    `call_owner`'s calls (None = Arturo's own). The same function serves every agent's call."""
    if tz is None or tz_label is None:
        tz, tz_label = shaw_tz()
    operator_label = operator_label or operator_name()
    lines, notes = [], []
    try:
        items = (fetch or fetch_transcript)(chat_agent, chat_limit)
        lines += chat_lines(items, agent_label=chat_agent, operator_label=operator_label)
    except Exception as e:  # noqa: BLE001 — the calls still come through
        notes.append(f"({chat_agent} chat could not be read: {type(e).__name__})")
    lines += call_lines(calls_dir, owner=call_owner, exclude=exclude, assistant_label=assistant_label,
                        operator_label=operator_label)
    text = render(lines, budget_tokens=budget_tokens, tz=tz, tz_label=tz_label)
    return "\n".join([PAST_OPEN, text] + notes + [PAST_CLOSE])


# --- the standalone Arturo page's own thread (the operator 2026-11-25, gm msg_e7402cbb) ------------------------------
# A call placed from that page opens with ITS thread: the typed turns and the earlier calls of the web_ thread it is
# slotted into (the proxy's threads.db, thread_store), never gm's chat. Same stamps, same fence as load().
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")          # thread_store / call_threads
_PAGE_TAG_RE = re.compile(r"^\[Context:[^\]]*\]\s*")
THREAD_ROWS_MAX = 400


def threads_db_path(orch_dir: Optional[str] = None):
    root = orch_dir or os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra")
    return os.path.join(root, "state", "arturo", "threads.db")


def thread_readable(thread_id: str, principal: Optional[str], db=None) -> bool:
    """The proxy's _thread_refusal rule (Claude review of #644): "fleet" reads any thread; any other caller reads only
    a thread it started (thread_store started_by); unknown or unreadable = no, for a non-fleet caller."""
    if not isinstance(thread_id, str) or not THREAD_ID_RE.fullmatch(thread_id):
        return False
    if principal == "fleet":
        return True
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db or threads_db_path()}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT started_by FROM threads WHERE id=?", (thread_id,)).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — cannot tell: not theirs
        return False
    return row is not None and row[0] == (principal or "anonymous")


def thread_entries(thread_id: str, db, operator_label: str = "Operator", assistant_label: str = "Arturo") -> list:
    """(role, Line) for the thread's turns, oldest first. A typed turn is "chat"; a spoken one is "call <id>", the id
    taken from the call's end marker that follows it. Spoken lines still open when a typed turn arrives belong to a
    call that ended without its marker ("earlier"); at the end of the thread they are the call still open ("live").
    The end marker and the hidden page tag are not words anyone said. Read-only; raises if the store cannot be read.
    A bad id reads nothing and never opens the store."""
    if not isinstance(thread_id, str) or not THREAD_ID_RE.fullmatch(thread_id):
        return []
    import sqlite3
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        rows = conn.execute("SELECT role, content, ts, mode, meta FROM turns WHERE thread_id=? ORDER BY seq DESC "
                            "LIMIT ?", (thread_id, THREAD_ROWS_MAX)).fetchall()
    finally:
        conn.close()
    out, pending = [], []
    for role, content, ts, mode, meta in reversed(rows):
        try:
            m = json.loads(meta) if meta else {}
        except ValueError:
            m = {}
        if isinstance(m, dict) and m.get("kind") == "call_end":
            cid = m.get("call_id") if isinstance(m.get("call_id"), str) else "call"
            for line in pending:
                line.call_id = cid
            pending = []
            continue
        text = _PAGE_TAG_RE.sub("", content or "").strip() if role == "user" else (content or "").strip()
        at = _epoch(ts)
        if not text or at is None or role not in ("user", "assistant"):
            continue
        who = operator_label if role == "user" else assistant_label
        if mode == "voice":
            line = Line(at=at, who=who, text=text, channel="call", call_id="live")
            pending.append(line)
        else:
            for p in pending:
                p.call_id = "earlier"
            pending = []
            line = Line(at=at, who=who, text=text, channel="chat", call_id="")
        out.append((role, line))
    return out


def thread_lines(thread_id: str, db, operator_label: str = "Operator", assistant_label: str = "Arturo") -> list:
    """thread_entries without the roles."""
    return [line for _, line in thread_entries(thread_id, db, operator_label, assistant_label)]


def load_thread(thread_id: str, db=None, calls_dir=None, exclude: Optional[str] = None,
                budget_tokens: int = DEFAULT_BUDGET_TOKENS, tz: Optional[str] = None, tz_label: Optional[str] = None,
                operator_label: Optional[str] = None) -> str:
    """The opening for a call placed from the standalone Arturo page: that page's thread, fenced like load(). If the
    thread store cannot be read, the voice-call journals that name the thread stand in, with a note."""
    if tz is None or tz_label is None:
        tz, tz_label = shaw_tz()
    operator_label = operator_label or operator_name()
    notes = []
    try:
        lines = thread_lines(thread_id, db or threads_db_path(), operator_label=operator_label)
    except Exception as e:  # noqa: BLE001 — the journals that name the thread still come through
        notes.append(f"(thread {thread_id} could not be read: {type(e).__name__})")
        lines = call_lines(calls_dir, owner=None, exclude=exclude, operator_label=operator_label,
                           thread_id=thread_id) if calls_dir else []
    lines = [l for l in lines if not (exclude and l.call_id == exclude)]
    text = render(lines, budget_tokens=budget_tokens, tz=tz, tz_label=tz_label, title=f"THREAD {thread_id}")
    return "\n".join([PAST_OPEN, text] + notes + [PAST_CLOSE])
