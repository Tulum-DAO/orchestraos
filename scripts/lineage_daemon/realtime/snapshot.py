"""Build B — the realtime SNAPSHOT sink: the language-neutral seam between B1's
Python real-time lane and the TS telemetry API.

ARCHITECTURE (one writer lane, many readers):
  * The LIVE B1 daemon writes two ephemeral hot surfaces here each tick:
      status.json            — per-seat DERIVED status (from status_deriver)
      deltas/<session>.log    — COURT-GATED clean token deltas (write-gate below)
  * The TS API READS them. It never runs derive_status, never scans /proc.

REBUILDABLE-FROM-WAL CONTRACT (runbook §3, load-bearing): everything under
`~/.orchestra/realtime/` is EPHEMERAL and rebuildable from the durable
`state/wal/` store + a fresh /proc sample — it is NEVER the source of durable
evidence. status.json is a live projection of the current derive pass; the delta
logs are a bounded drop-oldest tail (the durable transcript is the WAL). Losing
this directory costs only the live hot tail, reconstructed on the next tick.

INERT: this module is ADDITIVE. Importing/constructing writes nothing. It does
NOT modify B1's merged daemon; wiring daemon.tick() -> write_status_snapshot /
append_delta_if_clean is the LAST (systemd) step.

BLOCKING-2 (claude COUNTER, DEC-1788461603) — THE WRITE-GATE is the mid-stream
clean->flagged guarantee: `append_delta_if_clean` disposes every delta through
B1's court-scrub seam (token_extractor) and appends ONLY clean text. A flagged /
fail-closed / unresolved-lineage seat appends ZERO body bytes. The API's slow
re-check is a BACKSTOP; this write-gate is the primary boundary.

status.json is strictly BODY-FREE (status/metadata only — never a last-line or
chrome preview that could smuggle model voice past the court gate).
"""
import json
import os
import re
import time

from .ansi import strip_ansi
from .token_extractor import disposition_for_stream

STATUS_SCHEMA = "realtime-status/v1"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".orchestra", "realtime")
# Only these keys ever reach status.json (body-free invariant).
_STATUS_SEAT_KEYS = ("lineage_root", "runtime", "status", "degraded")
# Bounded delta log (ring discipline — matches the pipe-pane drop-oldest ring).
MAX_DELTA_FRAMES = 500
# Session ids become filenames; keep them to a safe charset, no traversal.
_SAFE_SESSION = re.compile(r"[^A-Za-z0-9._-]")


def realtime_dir(base=None):
    """Resolve the hot-surface dir. Precedence: explicit `base` arg >
    ORCHESTRA_REALTIME_DIR env > ~/.orchestra/realtime default."""
    if base is not None:
        return base
    return os.environ.get("ORCHESTRA_REALTIME_DIR") or DEFAULT_DIR


def _safe_session(session):
    """Sanitize a session id for use as a filename (no `/` or `..` traversal)."""
    name = _SAFE_SESSION.sub("_", str(session))
    return name or "_"


def _deltas_dir(base):
    return os.path.join(realtime_dir(base), "deltas")


# --- status snapshot ---------------------------------------------------------

def write_status_snapshot(base, seats_status):
    """Atomically write status.json. `seats_status` maps session -> a dict that
    MAY carry extra keys; only the body-free status/metadata keys graduate.
    tmp+rename => a reader never sees a partial file."""
    d = realtime_dir(base)
    os.makedirs(d, exist_ok=True)
    ts = time.time()
    seats = {}
    for session, rec in (seats_status or {}).items():
        seat = {"session": session, "ts": ts}
        for k in _STATUS_SEAT_KEYS:
            if k in rec and rec[k] is not None:
                seat[k] = rec[k]
        seats[session] = seat
    payload = {"schema": STATUS_SCHEMA, "ts": ts, "seats": seats}
    path = os.path.join(d, "status.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)                       # atomic; no *.tmp left behind
    return payload


def read_status_snapshot(base=None):
    """Return the parsed status snapshot, or None if absent/unreadable
    (INERT daemon unwired => absent => the API serves an empty/stale fleet)."""
    path = os.path.join(realtime_dir(base), "status.json")
    try:
        with open(path) as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict) or obj.get("schema") != STATUS_SCHEMA:
        return None
    return obj


# --- delta log ---------------------------------------------------------------

def append_delta(base, session, text, *, seq):
    """Low-level append of ONE already-clean frame to deltas/<session>.log.
    Bounded (drop-oldest to MAX_DELTA_FRAMES). Callers writing live token deltas
    MUST use append_delta_if_clean (the court write-gate) — this primitive does
    NOT dispose. session id is sanitized (no path traversal)."""
    d = _deltas_dir(base)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, _safe_session(session) + ".log")
    frame = {"seq": seq, "ts": time.time(), "text": text}
    # read-tail-rewrite keeps the log bounded (small hot tail, not the durable
    # store — the WAL is durable; this is a rebuildable ring).
    lines = []
    try:
        with open(path) as fh:
            lines = [ln for ln in fh.read().splitlines() if ln]
    except OSError:
        lines = []
    lines.append(json.dumps(frame))
    if len(lines) > MAX_DELTA_FRAMES:
        lines = lines[-MAX_DELTA_FRAMES:]
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return frame


def append_delta_if_clean(base, session, runtime, raw_pty, *, lineage_root,
                          flag_store, seq):
    """THE COURT WRITE-GATE (BLOCKING-2). Dispose raw_pty through B1's
    token_extractor seam and append ONLY clean, ANSI-stripped text. A flagged /
    fail-closed / empty-lineage seat appends ZERO body bytes. Returns True iff a
    clean frame was appended.

    This is the primary mid-stream clean->flagged guarantee: because a flagged
    lineage never reaches the log, the API cannot relay what was never written.
    """
    # BLOCKING-1: an empty/None lineage_root must fail CLOSED — never let it fall
    # through to LineageFlagStore.status(None) which would read (clean, readable).
    if not lineage_root:
        return False
    d = disposition_for_stream(runtime, raw_pty, lineage_root=lineage_root,
                               flag_store=flag_store)
    if d.get("stream_mode") != "clean" or not d.get("streamed"):
        return False                            # flagged / fail-closed => no write
    append_delta(base, session, strip_ansi_text(d.get("text", "")), seq=seq)
    return True


def strip_ansi_text(text):
    """token_extractor already ANSI-strips clean text; this is idempotent
    defense so a frame on disk is never raw escape bytes."""
    return strip_ansi(text)


def read_delta_frames(base, session, *, after_seq=0):
    """Return clean frames with seq > after_seq (oldest-first). Absent log =>
    []. Never raises on a malformed line (skips it)."""
    path = os.path.join(_deltas_dir(base), _safe_session(session) + ".log")
    out = []
    try:
        with open(path) as fh:
            raw = fh.read()
    except OSError:
        return []
    for ln in raw.splitlines():
        if not ln:
            continue
        try:
            f = json.loads(ln)
        except ValueError:
            continue
        if isinstance(f, dict) and f.get("seq", 0) > after_seq:
            out.append(f)
    return out
