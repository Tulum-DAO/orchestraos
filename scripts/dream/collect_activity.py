#!/usr/bin/env python3
"""collect-activity.py — harvest Shaw's own activity into dream/facts/YYYY-MM-DD.jsonl

Usage:
  python3 collect-activity.py            # collect yesterday+today (idempotent)
  python3 collect-activity.py --days 14  # backfill N days
Facts: {ts, surface, agent, actor, kind, text}
Surfaces: cli (agent transcripts), telegram, dashboard, ui, voice.
"""
import json, os, re, sys, glob, hashlib
from datetime import datetime, timedelta, timezone

# The data dir is orchestra_cli's data_dir() ($ORCHESTRA_DIR first), like every other script here.
# orchestra_cli lives in this file's checkout; appended, never prepended, so nothing is shadowed.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402

ORCH = str(_data_dir())
PROJECTS = os.path.expanduser("~/.claude/projects")
FACTS_DIR = os.path.join(ORCH, "dream", "facts")
CHECKPOINT_PATH = os.path.join(ORCH, "dream", ".collect-checkpoint.json")
OMNI_GLOBAL = os.path.expanduser("~/scripts/omni-context/global")

EXCLUDE_PATTERNS = [
    r"^\[MSG from ",
    r"^Read /tmp/agent-msg-",
    r"^Read /tmp/agent-init-",
    r"^Read /tmp/agent-resume-",
    r"^Base directory for this skill:",
    r"^<system-reminder>",
    r"^<task-notification>",
    r"^Brain \(as of ",
    r"^\[Request interrupted",
    r"^Caveat: ",
    r"^<command-name>",
    r"^You were resumed after",
    r"^Answer the user's question using ONLY",
    r"^Context: This summary will be shown",
]
EXCLUDE_RE = [re.compile(p) for p in EXCLUDE_PATTERNS]
TOKEN_RE = re.compile(r"\b(EAA[A-Za-z0-9]{50,}|sk-[A-Za-z0-9-]{30,}|gh[ps]_[A-Za-z0-9]{30,}|xox[bp]-[A-Za-z0-9-]{20,})\b")

def is_shaw_text(text):
    if not text or not text.strip():
        return False
    return not any(rx.search(text) for rx in EXCLUDE_RE)

def redact(text):
    return TOKEN_RE.sub("[REDACTED_TOKEN]", text)

def _fact(ts, surface, agent, kind, text, actor="shaw"):
    return {"ts": ts, "surface": surface, "agent": agent, "actor": actor,
            "kind": kind, "text": redact(text.strip())[:2000]}

# Text that reaches a pane as a "user" turn but that no person typed. Other processes type into panes the
# same way a person does, so the transcript records these as typed; only the text gives them away.
INJECTED_RE = re.compile(
    r"^(Stop hook feedback:|<task-notification>|<local-command-|<cross-session-message|"
    r"Another Claude session sent a message|You are [\w.-]+\. Read /tmp/agent-|"
    r"You are [\w.-]+, resumed \(session |ingest your hydrate|Check your inbox|Voice call ended:|"
    r"\[(QUEUE-DIGEST|ROTATION|LINEAGE|WAL DELTA|pulse|fwd from|cron|DECISION ANSWERED|APPROVAL RESOLVED |"
    r"Usage limit reached|Earlier usage-limit notes)|"
    r"You are (a|an|the) |# You are |You extract |Distill this |Classify each |"
    r"From the (meeting/)?conversation transcript)")
# NOT injected, on purpose:
# - "[LONG-MSG chip-dodge] ..." is the gateway's wrapper around the operator's OWN long chat message: the
#   head of the text plus a pointer to the full body, which sits in an ephemeral /tmp file this collector
#   does not read. The banner head is kept as the text and the turn stays "shaw".
# - A bare "continue" is either the operator or the automatic continue reply; the text can't tell them
#   apart, so it keeps whatever the metadata says.


def turn_author(o, text):
    """Who wrote this user turn, from the transcript's own metadata, then the text.
    "shaw" only when nothing says otherwise; an older transcript without origin fields stays "shaw"."""
    origin = o.get("origin") if isinstance(o.get("origin"), dict) else {}
    source = o.get("promptSource")
    if o.get("isMeta"):
        return "hook"
    if o.get("isSidechain"):
        return "subagent"
    if str(o.get("entrypoint") or "").startswith("sdk"):
        return "headless"
    if source == "suggestion_accepted":
        return "model-suggestion"
    if source == "system" or origin.get("kind") not in (None, "human"):
        return "system"
    if INJECTED_RE.match(text.lstrip()):
        return "injected"
    if source is None and not origin:
        return "unknown"                         # an older transcript: no origin fields to read
    return "shaw"


def transcript_ref(path):
    """<project dir>/<session file>: stable across machines' home dirs. The project dir name is the
    encoded working dir, so it still names the user; the rows are private data."""
    return "/".join(os.path.normpath(path).split(os.sep)[-2:])


def _turn_facts(o, agent, ref):
    """0..N facts for one parsed transcript record. Each text block is cited by the message uuid and its
    position among the message's text blocks, which no later write to the transcript can shift."""
    if o.get("type") != "user":
        return []
    c = o.get("message", {}).get("content")
    if isinstance(c, str):
        texts = [c]
    elif isinstance(c, list):
        texts = [b.get("text", "") for b in c
                 if isinstance(b, dict) and b.get("type") == "text"]
    else:
        texts = []
    uuid = o.get("uuid")
    out = []
    for part, t in enumerate(texts):
        if is_shaw_text(t):
            fa = _fact(o.get("timestamp", ""), "cli", agent, "prompt", t, actor=turn_author(o, t))
            fa["citation"] = {"transcript": ref, "uuid": uuid, "part": part}
            fa["source_key"] = f"cli:{uuid}:{part}" if uuid else None
            out.append(fa)
    return out


def extract_transcript_facts(path, agent):
    facts = []
    ref = transcript_ref(path)
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                facts.extend(_turn_facts(o, agent, ref))
    except OSError:
        pass
    return facts

# --------------------------------------------------------------------------- #
# A1 — incremental byte-offset checkpoint + MERGE-into-day-file (CPU-discipline
# Part A, the MERGE option). Transcripts are append-only JSONL, so we read only the
# bytes appended since the last dream and MERGE the new facts into the day-file
# (never clobber).
# --------------------------------------------------------------------------- #
# Profiling counters (Part C) — the harness resets these and reads them to report the
# cold (full-read) vs warm (tail) IO profile. Zero-cost when unused.
_PROFILE = {"files_read": 0, "bytes_read": 0}


def profile_reset():
    _PROFILE["files_read"] = 0
    _PROFILE["bytes_read"] = 0


def profile_stats():
    return dict(_PROFILE)


def _facts_from_line(line, agent, ref=""):
    """Parse one JSONL transcript line into 0..N facts (same rule as
    extract_transcript_facts, applied per line)."""
    try:
        o = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return []
    return _turn_facts(o, agent, ref)


def _first_line_head(path):
    """Stable identity of a file's first line — detects in-place rewrite/compaction
    (same inode, size >= offset) that a byte-offset alone would miss."""
    try:
        with open(path, "rb") as f:
            first = f.readline()
    except OSError:
        return ""
    return hashlib.sha1(first).hexdigest()[:16]


def read_file_incremental(path, agent, entry):
    """Read a transcript from its checkpoint offset (append) or from 0 (reset/unknown).

    Parses only COMPLETE lines; advances the offset to the byte after the LAST
    COMPLETE NEWLINE actually consumed (never tell()/stat size — a writer may append
    between our stat and our read, and a partial final record must be left for next
    run). Applies the reset guards (#3) and fails safe to from-0 on any inconsistency.

    Returns (facts, new_entry, mode) where mode is "tail" or "from0" and new_entry is
    offsets-only: {inode, size, offset, mtime, head}.
    """
    st = os.stat(path)
    inode, size, mtime = st.st_ino, st.st_size, st.st_mtime
    start = 0
    mode = "from0"

    if entry:
        try:
            e_off = int(entry["offset"])
            e_inode = int(entry["inode"])
            e_size = int(entry["size"])
            e_mtime = float(entry["mtime"])
            e_head = entry.get("head")
            ok = True
            if e_inode != inode:
                ok = False                       # #3 inode change
            elif size < e_off:
                ok = False                       # #3 truncation/rotation (size < offset)
            elif mtime < e_mtime:
                ok = False                       # #3 mtime went backwards
            elif e_off > 0:
                with open(path, "rb") as f:
                    f.seek(e_off - 1)
                    prev = f.read(1)
                if prev != b"\n":
                    ok = False                   # #2 offset not on a newline boundary
                elif e_head is not None and _first_line_head(path) != e_head:
                    ok = False                   # #3 in-place rewrite (first-line id changed)
            if ok:
                mode, start = "tail", e_off
        except (KeyError, ValueError, TypeError):
            mode, start = "from0", 0             # #4 corrupt entry -> fail-safe from 0

    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    if data:
        _PROFILE["files_read"] += 1
        _PROFILE["bytes_read"] += len(data)
    last_nl = data.rfind(b"\n")
    consumed = last_nl + 1 if last_nl != -1 else 0
    facts = []
    if consumed:
        for raw in data[:consumed].split(b"\n"):
            if raw.strip():
                facts.extend(_facts_from_line(raw.decode("utf-8", "replace"), agent,
                                              transcript_ref(path)))

    new_entry = {"inode": inode, "size": size, "offset": start + consumed,
                 "mtime": mtime, "head": _first_line_head(path)}
    return facts, new_entry, mode


def merge_into_day_file(day_path, new_facts):
    """Option (i): read the existing day-file, union the new facts, dedupe by the
    existing key (ts, text[:120]), sort by ts, atomic-write. Existing records are
    listed FIRST so a re-harvest of an already-recorded fact is a no-op and the
    existing record wins deterministically on a key collision. Returns final facts."""
    existing = []
    if os.path.exists(day_path):
        try:
            with open(day_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        existing.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            existing = []
    seen, out = set(), []
    for fa in existing + list(new_facts):        # existing first => existing wins
        k = (fa.get("ts", ""), (fa.get("text", "") or "")[:120])
        if k in seen:
            continue
        seen.add(k)
        out.append(fa)
    out.sort(key=lambda f: f.get("ts", ""))       # stable: preserves existing-before-new
    tmp = day_path + ".tmp"
    with open(tmp, "w") as f:
        for fa in out:
            f.write(json.dumps(fa) + "\n")
    os.replace(tmp, day_path)
    return out


def load_checkpoint(path):
    """Load the offsets-only checkpoint. Any load error => {} (fail-safe: every file
    then reads from 0, never a silent skip)."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_checkpoint(path, cp):
    """Atomic write (tmp + os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cp, f)
    os.replace(tmp, path)


def collect_cli(since, checkpoint=None):
    """Harvest Shaw's CLI transcript prompts. When a checkpoint is supplied, each
    file is read INCREMENTALLY from its saved byte offset (append-only transcripts),
    so a steady-state run reads only the bytes appended since the last dream instead
    of re-parsing every in-window file from byte 0.

    Returns (facts, new_checkpoint). The caller persists new_checkpoint AFTER the
    day-files are written, so a write failure just re-reads (merge dedupes)."""
    cp = dict(checkpoint) if checkpoint else {}
    new_cp = {}
    facts = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        base = os.path.basename(path)
        if base.startswith("agent-"):  # subagent transcripts: not Shaw
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if datetime.fromtimestamp(mtime, tz=timezone.utc) < since:
            # Outside the window — don't read, but keep any existing offset so a
            # later append is still incremental.
            if path in cp:
                new_cp[path] = cp[path]
            continue
        agent = os.path.basename(os.path.dirname(path)).replace("-home-shaw-", "")
        try:
            f, entry, _mode = read_file_incremental(path, agent, cp.get(path))
        except OSError:
            continue
        facts.extend(f)
        new_cp[path] = entry
    return facts, new_cp

def collect_jsonl(path, surface, want, ts_key, text_key, agent="-"):
    facts = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not want(o):
                    continue
                t = str(o.get(text_key, ""))
                if is_shaw_text(t):
                    facts.append(_fact(str(o.get(ts_key, "")), surface, agent, "message", t))
    except OSError:
        pass
    return facts

def collect_pocket(since):
    """Pocket recordings = Shaw's OFF-SURFACE activity (calls, meetings, ads he
    listened to). One fact per recording: title + category + duration + summary
    excerpt from transcript.md. This closes the 'off-surface actions invisible
    to the fact stream' gap (2026-08-08 dream audit)."""
    facts = []
    base = os.path.expanduser("~/pocket-recordings")
    if not os.path.isdir(base):
        return facts
    for d in sorted(os.listdir(base)):
        meta_path = os.path.join(base, d, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            meta = json.load(open(meta_path))
        except (OSError, ValueError):
            continue
        ts = str(meta.get("created_at", ""))
        dt = parse_ts(ts)
        if not dt or dt < since:
            continue
        title = str(meta.get("title", d))
        cat = str(meta.get("category", "uncategorized"))
        mins = int(float(meta.get("duration") or 0) // 60)
        summary = ""
        t_md = os.path.join(base, d, "transcript.md")
        try:
            body = open(t_md, errors="replace").read()
            m = re.search(r"## Summary\s*\n+(.+?)(?=\n## |\Z)", body, re.S)
            if m:
                summary = " ".join(m.group(1).split())[:400]
        except OSError:
            pass
        text = f"[POCKET RECORDING · {cat} · {mins}min] {title}. {summary}".strip()
        facts.append(_fact(ts, "pocket", "pocket-agent", "recording", text))
        # Length-adaptive detail: longer calls/meetings get distilled facts from
        # the extraction cache (dream/pocket-extracts/, built by pocket-extract.py).
        rid = str(meta.get("recording_id", d))
        cache_path = os.path.join(os.path.dirname(FACTS_DIR), "pocket-extracts", f"{rid}.json")
        try:
            cached = json.load(open(cache_path))
            if not cached.get("skip"):
                for ex in cached.get("facts", [])[:15]:
                    facts.append(_fact(ts, "pocket", "pocket-agent", "recording_fact",
                                       f"[FROM RECORDING: {title}] {ex}"))
        except (OSError, ValueError):
            pass
    return facts


def collect_all(since, checkpoint=None):
    facts, new_cp = collect_cli(since, checkpoint)
    facts += collect_pocket(since)
    facts += collect_jsonl(os.path.join(ORCH, "logs/telegram-messages.jsonl"),
                           "telegram", lambda o: o.get("dir") == "inbound", "ts", "text", "gm")
    facts += collect_jsonl(os.path.join(ORCH, "state/unified-conversation.jsonl"),
                           "dashboard", lambda o: o.get("role") == "shaw", "ts", "content")
    facts += collect_jsonl(os.path.join(ORCH, "state/users/shaw/ui-events.jsonl"),
                           "ui", lambda o: o.get("user_id") == "shaw", "timestamp", "action")
    facts += collect_jsonl(os.path.join(ORCH, "logs/voice-call-trace.jsonl"),
                           "voice", lambda o: True, "ts", "user", "jarvis-voice")
    return facts, new_cp

def parse_ts(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None

def main():
    days = 2
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    fact_since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    # Steady-state runs (<=2d window) read transcripts incrementally from the
    # checkpoint; a wider backfill re-reads fully (checkpoint ignored for READING)
    # but still refreshes it. MERGE preserves prior day-file facts either way.
    use_ckpt = days <= 2
    cp_in = load_checkpoint(CHECKPOINT_PATH) if use_ckpt else {}
    raw_facts, new_cp = collect_all(since, cp_in)
    facts = []
    for fa in raw_facts:
        dt = parse_ts(fa["ts"])
        if dt and dt >= fact_since:
            fa["_dt"] = dt
            facts.append(fa)
    facts.sort(key=lambda f: f["_dt"])
    by_day = {}
    for fa in facts:
        by_day.setdefault(fa.pop("_dt").strftime("%Y-%m-%d"), []).append(fa)
    os.makedirs(FACTS_DIR, exist_ok=True)
    for day, dayfacts in sorted(by_day.items()):
        seen, out = set(), []
        for fa in dayfacts:
            k = (fa["ts"], fa["text"][:120])
            if k not in seen:
                seen.add(k)
                out.append(fa)
        # MERGE into the day-file (never clobber) — the #1 data-loss fix. An
        # incremental run only carries the new tail; prior facts are preserved.
        merged = merge_into_day_file(os.path.join(FACTS_DIR, f"{day}.jsonl"), out)
        print(f"{day}: +{len(out)} new, {len(merged)} total")
    # Persist the checkpoint ONLY after day-files are written (advance offsets only
    # once their facts are durably merged).
    save_checkpoint(CHECKPOINT_PATH, new_cp)
    link = os.path.join(OMNI_GLOBAL, "dream-facts")
    if os.path.isdir(OMNI_GLOBAL):
        try:
            if os.path.islink(link):
                if os.readlink(link) != FACTS_DIR:
                    os.remove(link)
                    os.symlink(FACTS_DIR, link)
            else:
                os.symlink(FACTS_DIR, link)
        except OSError as e:
            print(f"warn: could not publish symlink {link}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
