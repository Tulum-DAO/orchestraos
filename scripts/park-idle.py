#!/usr/bin/env python3
"""park-idle.py — reclaim RAM by retiring genuinely-DONE idle agents, SAFELY.

Authorized by GM (2026-08-11 memory crisis) as the durable replacement for
improvised OOM kills. Design principles (hard-won this session):

  1. REVERSIBLE — retire via REGISTRY-REMOVAL, never by setting resumable=false.
     agent-recovery (service-watchdog, every cycle) resurrects an agent only if it
     is BOTH resumable=true AND present in registry AND last_active<30min. Removing
     the registry entry stops auto-resurrection WITHOUT gating resumable — so the
     agent's resume_command + session_id stay intact and it can be resumed by hand
     anytime (respects feedback_never_prevent_resurrections).

  2. DONE-ONLY, CONSERVATIVE — the ONLY class auto-retired is the textbook-safe one:
     a SUPERSEDED PREDECESSOR whose live successor exists (explicit succeeded_by /
     superseded_by pointer to a live session). Mail to the retired name follows the
     lineage-routing fix to the live head, so nothing is stranded. Everything else
     (done-marker-idle, pending-work, active, protected, held, attached) is REPORTED
     for a human, never auto-killed. No name-stem heuristics (DEC-1786280521).

  3. NEVER touch: infra, always_on, attached sessions, GM-held residents, or any
     agent with pending composer input / active generation / open work.

  4. DRY-RUN by default. --execute retires the auto-safe class only, capped by --max.
     --agent NAME retires one specific agent (still safety-gated unless --force).

Usage:
  python3 scripts/park-idle.py                 # dry-run: classify + reclaim table
  python3 scripts/park-idle.py --execute        # retire the superseded-safe class
  python3 scripts/park-idle.py --execute --max 3
  python3 scripts/park-idle.py --agent foo --execute   # retire one (safety-gated)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
REGISTRY = ORCHESTRA_DIR / "registry.json"
AGENT_SESSIONS = ORCHESTRA_DIR / "state" / "agent-sessions.json"
LIVE_ROSTER = ORCHESTRA_DIR / "state" / "live-roster.json"
LOG_FILE = ORCHESTRA_DIR / "logs" / "park-idle.log"
OPERATOR_PRESENCE = ORCHESTRA_DIR / "state" / "operator-presence.json"

# --- identity-store cutover seam (INERT until the operator-armed) --------------------
# Cheap flag-file check FIRST so that when the cutover is not armed (the default
# + current state) this script imports nothing new and behaves byte-identically.
# Only when armed do we lazily import the store and retire through the DB.
def _cutover_active() -> bool:
    # Resolve the flag path at CALL time from the (test-injectable) ORCHESTRA_DIR
    # module global so the check is hermetic against the live armed flag. Cheap
    # flag-FILE check; imports nothing new on the flag-off path.
    return (ORCHESTRA_DIR / "state" / "identity-store-cutover.flag").exists() \
        or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"


def _db_retire(name: str, reason: str, session_record=None) -> bool:
    """Retire through the transactional store when the cutover is armed. Lazy
    import (only when active) so park-idle keeps zero new dependencies while
    INERT. Passes the retired session record (status=retired + resume fields) so
    the store KEEPS it resumable (DP-A2). Returns True iff the DB handled the
    retire."""
    if not _cutover_active():
        return False
    if str(ORCHESTRA_DIR) not in sys.path:
        sys.path.insert(0, str(ORCHESTRA_DIR))
    from scripts.identity_store import identity_writer
    return identity_writer.retire_agent(str(ORCHESTRA_DIR), name, reason,
                                        session_record=session_record)

# Gap 8 (the operator-directed): an idle-but-was-working agent must never be prematurely
# retired. Tuning knobs for the two time-window guards.
VIEW_GRACE_S = 300           # 8b: the operator viewing an agent in the app protects it for this long
RECENT_WORK_COOLDOWN_S = 600  # 8c: retire is held for this long after last activity
GIT_TIMEOUT_S = 10           # 8a: bound the git calls; timeout => fail-safe (block)
PROMPT_CHAR = "\u276f"

# Infra sessions that are never agents / never targets.
INFRA = {"dashboard", "custom-llm", "api-server", "combo-proxy", "telegram-router",
         "pocket-service", "pocket-webhook", "jarvis-service"}

# Always-protected agents (control plane, live heads, active resident work). These
# are NEVER auto-retired regardless of pane state. Kept explicit + conservative.
PROTECT = {
    "gm", "agent-state-truth", "chatmode-qa-loop",
    "pocket-goal-mining-design", "pocket-agent", "assistant-ui-dev",
    "orchestra-builder", "orchestra-builder-v2",
    # GM-held residents (unsaved work at high ctx):
    "jarvis-poc-builder-v3", "orchestraos-app-dev-v3", "orchestraos-app-dev-v4",
    "orchestraos-app-dev",
}

# Strong "I'm done" markers an agent prints when it has finished + parked. Matched
# only near the LIVE input box (recent), never deep scrollback.
DONE_MARKERS = re.compile(
    r"(mission complete|standing down|parked clean|nothing to act on|"
    r"standing by|handed off|successor has the work|✅ *resolved|work is done|"
    r"all (tasks|items) (done|complete))",
    re.IGNORECASE,
)
# In-progress / pending signals → NEVER retire.
OPEN_WORK = re.compile(r"(\u25fb|\u25a1|\bTODO\b|\bopen\b.*task|esc to interrupt|Running\u2026)", re.IGNORECASE)


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{ts} [park-idle] {msg}\n")
    except Exception:
        pass
    print(f"[park-idle] {msg}")


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(p, obj):
    p = str(p)
    d = os.path.dirname(os.path.abspath(p))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def live_sessions() -> set:
    r = tmux("list-sessions", "-F", "#{session_name}")
    return {s for s in r.stdout.split("\n") if s} if r.returncode == 0 else set()


def session_attached() -> dict:
    r = tmux("list-sessions", "-F", "#{session_name}|#{session_attached}")
    out = {}
    if r.returncode == 0:
        for line in r.stdout.strip().split("\n"):
            if "|" in line:
                n, a = line.rsplit("|", 1)
                out[n] = a != "0"
    return out


def successor(entry: dict):
    """Explicit successor pointer, normalizing both field names (see message-router)."""
    if not entry:
        return None
    return entry.get("succeeded_by") or entry.get("superseded_by")


# --- Gap 8 signal helpers (IO, bounded) — computed in main(), passed PURE into
#     classify() so the classifier stays deterministic + unit-testable. ---

class _GitError(Exception):
    """git call raised/timed out (repo unreadable) -> caller fail-SAFE blocks."""


def _git_count(cwd, rev_range, timeout=GIT_TIMEOUT_S):
    """git rev-list --count <rev_range> -> int, or None when the range does not
    resolve (e.g. no @{upstream} / no such base = a clean non-zero exit).

    Raises _GitError on a subprocess exception/timeout (unreadable repo) so the
    caller can fail-SAFE block — distinct from a clean 'range absent' None.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "rev-list", "--count", rev_range],
            capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        raise _GitError(rev_range)
    if r.returncode != 0:
        return None          # range does not resolve (no upstream / no base)
    try:
        return int(r.stdout.strip() or "0")
    except ValueError:
        return None


def _unique_commits_vs_base(cwd, base_hint=None, timeout=GIT_TIMEOUT_S) -> bool:
    """8a refinement (DEC-1786649045): True iff HEAD has UNIQUE commits vs its
    base branch — real unpushed work on an untracked branch. base = the lineage
    root branch hint if present, else main, else master. If NO base resolves
    (a genuinely standalone repo with no main/master), there is nothing to be
    'ahead of' -> False (don't freeze an untracked worktree that has no base)."""
    bases = [b for b in (base_hint, "main", "master") if b]
    for base in bases:
        cnt = _git_count(cwd, f"{base}..HEAD", timeout=timeout)  # may raise _GitError
        if cnt is not None:          # base exists + range resolved
            return cnt > 0
    return False                     # no base to compare against -> allow


def cwd_has_uncommitted_work(cwd, base_hint=None, timeout=GIT_TIMEOUT_S) -> bool:
    """8a — True if the cwd's git repo has UNCOMMITTED or UNPUSHED work, or is
    UNREADABLE (fail-SAFE: never retire on a repo we can't assess).

    Block when: dirty working tree; OR commits ahead of @{upstream}; OR
    (no upstream configured but) UNIQUE commits vs the base branch. Allow when:
    not a repo / missing cwd; OR clean tree with no upstream AND no unique
    commits vs base (an untracked worktree with nothing to lose — must NOT be
    frozen, DEC-1786649045). git error/timeout -> fail-SAFE block.
    """
    if not cwd or not os.path.isdir(cwd):
        return False   # no repo path to protect — nothing to lose here
    try:
        st = subprocess.run(["git", "-C", str(cwd), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return True    # fail-safe: unreadable repo / timeout
    if st.returncode != 0:
        if "not a git repository" in (st.stderr or "").lower():
            return False   # not a repo -> nothing to commit
        return True        # fail-safe on any other git error
    if st.stdout.strip():
        return True        # dirty working tree (uncommitted changes)
    try:
        # clean tree: commits ahead of @{upstream} (tracked branch, unpushed)?
        ahead = _git_count(cwd, "@{upstream}..HEAD", timeout=timeout)
        if ahead is not None:
            return ahead > 0   # upstream configured -> authoritative
        # no upstream configured -> block ONLY on unique commits vs base (real
        # unpushed work); a clean untracked worktree with no unique commits is safe.
        return _unique_commits_vs_base(cwd, base_hint=base_hint, timeout=timeout)
    except _GitError:
        return True            # fail-SAFE: git error/timeout = can't prove safe


def _dirty_files(cwd, timeout=GIT_TIMEOUT_S) -> set:
    """The set of currently-dirty (modified/added/untracked) file paths in cwd's
    git repo, as repo-relative strings. {} on any error (caller decides)."""
    if not cwd or not os.path.isdir(cwd):
        return set()
    try:
        r = subprocess.run(["git", "-C", str(cwd), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return set()
    if r.returncode != 0:
        return set()
    files = set()
    for line in r.stdout.splitlines():
        # porcelain: 'XY <path>' (or 'XY <old> -> <new>' for renames)
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.add(path)
    return files


def _own_edited_targets(jsonl_path, opener=open, max_bytes=4_000_000):
    """The set of file paths this agent's OWN session Edit/Write'd, read from its
    .jsonl tool-calls (bounded tail read — fable-wedge-safe). Returns the raw
    target strings the tools recorded, or None if the jsonl is missing/unreadable
    (the caller MUST fail-safe on None — it cannot prove ambient dirt isn't its
    own)."""
    import json as _json
    targets = set()
    try:
        with opener(jsonl_path) as f:
            try:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
            except (OSError, ValueError):
                pass
            for line in f:
                if '"file_path"' not in line:
                    continue
                try:
                    o = _json.loads(line)
                except (ValueError, TypeError):
                    continue
                for block in _tool_use_blocks(o):
                    if block.get("name") in ("Edit", "Write", "NotebookEdit"):
                        fp = (block.get("input") or {}).get("file_path")
                        if fp:
                            targets.add(fp)
    except (OSError, ValueError):
        return None   # unreadable -> caller fail-safe
    return targets


def _tool_use_blocks(record):
    """Yield tool_use content blocks from a jsonl record (assistant message)."""
    msg = record.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                yield b


def own_work_at_risk(cwd, jsonl_path, base_hint=None, timeout=GIT_TIMEOUT_S) -> bool:
    """A-with-guard (H7, DEC-1786724046): True iff the predecessor's OWN work is
    at risk — either it has unique unpushed commits vs base, OR a file IT edited
    this session (jsonl Edit/Write target) is currently dirty in the shared cwd.
    Ambient dirt the predecessor never touched is NOT at-risk (that is the whole
    point of the narrowing). FAIL-SAFE: on git error / unresolvable base, defer
    to `cwd_has_uncommitted_work` semantics via _unique_commits_vs_base's
    fail-safe; an unreadable jsonl contributes no own-targets (so only the
    commit check gates — conservative because commits are the durable risk)."""
    try:
        if _unique_commits_vs_base(cwd, base_hint=base_hint, timeout=timeout):
            return True
    except _GitError:
        return True   # can't prove pushed -> fail-safe block
    dirty = _dirty_files(cwd, timeout=timeout)
    if not dirty:
        return False
    own = _own_edited_targets(jsonl_path) if jsonl_path else None
    if own is None:
        # tree is dirty but we CANNOT read the predecessor's own edits -> we
        # cannot prove the dirt isn't its own work. FAIL-SAFE: block the retire.
        return True
    if not own:
        return False   # provably touched nothing -> the dirt is ambient
    # dirty are repo-relative; own targets may be absolute — match on suffix.
    for d in dirty:
        for o in own:
            if o == d or o.endswith("/" + d) or o.endswith(d):
                return True
    return False


def load_presence(path=OPERATOR_PRESENCE) -> dict:
    """Read state/operator-presence.json (or {} if missing/corrupt — 8b fails OPEN)."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_iso(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def operator_viewing_agent(name, presence, now=None, grace=VIEW_GRACE_S) -> bool:
    """8b — True iff the operator is viewing THIS agent in the app/phone now or within
    `grace` seconds. FAIL-OPEN: absent/stale/malformed signal => False (a missing
    focus signal must NEVER block retires fleet-wide — positive-signal rule).

    Reads two shapes of the (app-emitted) focus signal, whichever the app writes:
      * flat:   {"viewing": "<agent>", "viewed_at": "<iso>"}
      * per-agent: {"agents": {"<agent>": {"viewed_at": "<iso>"}}}
    The app EMIT side is a DEPENDENCY (not built here) — until it writes one of
    these, this guard is inert by design (fail-open).
    """
    if not isinstance(presence, dict) or not presence:
        return False
    now = now or datetime.now(timezone.utc)
    viewed_at = None
    if presence.get("viewing") == name:
        viewed_at = _parse_iso(presence.get("viewed_at"))
    agents = presence.get("agents")
    if viewed_at is None and isinstance(agents, dict):
        entry = agents.get(name) or {}
        viewed_at = _parse_iso(entry.get("viewed_at"))
    if viewed_at is None:
        return False   # no positive signal for this agent -> fail-open
    return (now - viewed_at).total_seconds() <= grace


def session_activity_age(name) -> float:
    """8c — seconds since the tmux session's last activity (last terminal output),
    a local proxy for the detector's state_age_s. Returns None if unknown (the
    daemon path may pass the real detector state_age_s into classify instead)."""
    r = tmux("list-sessions", "-F", "#{session_name}|#{session_activity}")
    if r.returncode != 0:
        return None
    now = time.time()
    for line in r.stdout.strip().split("\n"):
        if "|" in line:
            n, _, act = line.rpartition("|")
            if n == name:
                try:
                    return now - int(act)
                except ValueError:
                    return None
    return None


def composer_has_typed_text(pane_ansi: str) -> bool:
    """True if the input line holds real (non-dim) typed text = pending human/agent
    instruction. Mirrors message-router's fail-safe: unknown → treat as busy/typed.

    A char counts as typed only if it is visible, NOT dim (SGR 2), and NOT the
    reverse-video cursor block (SGR 7). The empty-composer PLACEHOLDER ghost
    renders its first char as a reverse-video cursor block (`\\x1b[7mT`) with the
    rest dim — without recognizing SGR 7 that single cursor char false-positived
    as typed, spuriously blocking a legit retire (GM-verified on the jarvis-gm
    relic; reference_ghost_vs_typed_composer). A real typed line puts the cursor
    block on the TRAILING space past the text, so the default-styled text still
    reads True."""
    # Delegates to composer_state (the ONE SGR-aware reader, gm ghost-suggestion
    # commission): typed => True; empty/ghost/placeholder/working => False; an
    # unreadable pane (no prompt line) => True (fail-safe: not a clean idle TUI ->
    # busy). The placeholder ghost (dim + reverse cursor) reads False, as before.
    from scripts.composer_state import composer_text
    _t = composer_text(pane_ansi, runtime=None)  # runtime inferred from the glyph
    if _t is None:
        return True  # no visible prompt -> not a clean idle TUI -> treat as busy
    return bool(_t)


def classify(name, entry, in_registry, always_on, is_attached,
             live, meta, pane_plain, pane_ansi,
             has_uncommitted_work=False, operator_viewing=False, state_age_s=None,
             own_work_at_risk=False):
    """Pure classifier → (category, reason). Categories:
      PROTECTED, ATTACHED, HELD, PENDING_WORK,
      SUPERSEDED_SAFE  (the ONLY auto-retire class),
      DONE_MARKER_IDLE (report-only; needs human), UNKNOWN.

    Gap 8 signals (pure; computed by main() / the daemon and passed in — never
    IO here):
      has_uncommitted_work : 8a — cwd git repo dirty/unpushed/unreadable (fail-safe)
      operator_viewing         : 8b — the operator viewing this agent in app/phone (fail-open)
      state_age_s          : 8c — seconds since last activity (None => unknown)

    A-with-guard (H7, DEC-1786724046): for a SUPERSEDED agent whose live head
    exists, broad ambient shared-tree dirt (8a) must NOT block retire — the
    predecessor already handed off and the shared main tree is chronically dirty
    with others' work. Only the predecessor's OWN at-risk work blocks:
      own_work_at_risk : the predecessor's own jsonl Edit/Write targets ∩ the
                         currently-dirty files is non-empty, OR it has unique
                         unpushed commits vs base. Computed by the daemon (IO
                         there); "dirt I edited this session" blocks, ambient
                         dirt does not. NON-superseded agents keep broad 8a.
    All guards ONLY ADD block conditions (monotonic safety); none loosens a
    retire beyond the ratified A-with-guard narrowing for superseded agents.
    """
    # --- Human-safety hard stops FIRST — never retire out from under a human or
    # an active turn, EVEN IF superseded. A client attached, typed/pending input,
    # or an active generation marker beats the superseded-override below.
    if is_attached:
        return ("ATTACHED", "a client is attached")
    if OPEN_WORK.search(pane_plain or ""):
        return ("PENDING_WORK", "open-work / generation marker in pane")
    if composer_has_typed_text(pane_ansi or ""):
        return ("PENDING_WORK", "typed text in composer (pending instruction)")

    # --- 8b/8c stay AHEAD of the override (monotonic — apply to ALL agents,
    # superseded or not; a live-work signal always beats the override).
    # 8b: the operator viewing in the OrchestraOS app / iPhone (fail-open when absent).
    if operator_viewing:
        return ("HELD", "the operator viewing in app")
    # 8c: momentarily idle between turns after real work — recent-activity cooldown.
    if state_age_s is not None and state_age_s < RECENT_WORK_COOLDOWN_S:
        return ("PENDING_WORK", "recent activity within cooldown")

    # --- Superseded-override (Gap 6, DEC-1786581566) + A-with-guard (H7,
    # DEC-1786724046): a lineage predecessor whose terminal successor is LIVE is
    # safe to retire, INCLUDING a PROTECTED / always_on agent (the rotation case).
    # Guarded to fire ONLY when the terminal successor is actually live — a
    # protected agent with a dangling/not-yet-live successor stays PROTECTED.
    succ = successor(entry)
    if succ:
        # walk to terminal (cycle-guarded) and require it LIVE
        seen = {name}
        cur = name
        while True:
            nxt = successor(meta.get(cur) or {})
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            cur = nxt
        terminal = cur
        tsess = (meta.get(terminal) or {}).get("tmux_session", terminal)
        if terminal != name and tsess in live:
            # A-with-guard: the ONLY dirt that blocks a superseded agent is its
            # OWN at-risk work (broad ambient 8a is deliberately NOT consulted
            # here — that is the ratified moved surface).
            if own_work_at_risk:
                return ("PENDING_WORK",
                        "superseded but predecessor's own edits are uncommitted "
                        "(own jsonl-touched ∩ dirty, or unique unpushed commits)")
            return ("SUPERSEDED_SAFE", f"superseded → live head {terminal}")
        # Has a successor pointer but its head is NOT live yet: a protected agent
        # stays protected (retryable next cycle once the successor comes up).
        if name in INFRA or name in PROTECT or always_on:
            return ("PROTECTED", "superseded but successor not live yet — protected, retryable")
        return ("UNKNOWN", "has successor pointer but no live head — leave (retryable)")

    # --- Non-superseded: broad 8a (ambient dirt) still hard-blocks (unchanged).
    if has_uncommitted_work:
        return ("PENDING_WORK", "uncommitted/unpushed work in cwd")

    # --- No successor pointer → normal protection gates apply.
    if name in INFRA or name in PROTECT:
        return ("PROTECTED", "infra/always-protected")
    if always_on:
        return ("PROTECTED", "always_on")

    # No successor pointer. A live head itself (nothing supersedes it) = don't retire.
    # Otherwise a done-marker + idle composer = report-only human-review candidate.
    if DONE_MARKERS.search(pane_plain or ""):
        return ("DONE_MARKER_IDLE", "done-marker + idle composer (needs human confirm)")
    return ("UNKNOWN", "no successor pointer, no done-marker — leave")


def comprehension_gate_blocks(agent_entry, passed_fn) -> bool:
    """Guard 8d (Phase B2, spec §6.5) — PURE predicate: a rotation retire (the
    target has a succeeded_by/superseded_by pointer) is BLOCKED unless the
    successor holds a PASSING comprehension artifact (passed_fn(successor) is
    True — normally rotation_gate_manual.comprehension_passed, fail-closed).
    Plain retires (no successor pointer) are never blocked here."""
    succ = successor(agent_entry or {})
    if not succ:
        return False
    try:
        return not bool(passed_fn(succ))
    except Exception:
        return True   # fail-closed: an erroring gate never waves a rotation through


def _comprehension_passed(successor_id: str) -> bool:
    """IO shim for guard 8d — fail-closed if the helper itself can't load."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import rotation_gate_manual
        return rotation_gate_manual.comprehension_passed(successor_id)
    except Exception:
        return False


def claude_rss_mb(name: str) -> int:
    r = tmux("list-panes", "-t", name, "-F", "#{pane_pid}")
    pid = r.stdout.strip().split("\n")[0] if r.returncode == 0 else ""
    if not pid:
        return 0
    ps = subprocess.run(["ps", "--ppid", pid, "-o", "rss,comm"], capture_output=True, text=True).stdout
    for l in ps.split("\n"):
        if "claude" in l:
            try:
                return int(l.split()[0]) // 1024
            except (ValueError, IndexError):
                return 0
    return 0


# Resume-critical fields a retire must NEVER lose (Phase B3, critique #9 —
# the twice-flagged resume_command wipe): marking retired only ADDS/updates
# status fields on the EXISTING entry; it never rebuilds the dict.
RESUME_CRITICAL_FIELDS = ("resume_command", "session_id", "resumable")


def mark_retired(meta: dict, name: str, reason: str, now=None) -> dict:
    """PURE: mark meta[name] retired IN PLACE, preserving every existing key
    (resume_command/session_id/resumable included — the gen-9 wipe surface).
    A missing/malformed entry gets a fresh retired record (nothing to lose)
    instead of being silently skipped. Returns the updated entry."""
    entry = meta.get(name)
    if not isinstance(entry, dict):
        entry = {}          # was previously skipped silently — keep a record
    entry.update({
        "status": "retired",
        "retired_at": (now or datetime.now(timezone.utc)).isoformat(),
        "retired_by": "park-idle",
        "retired_reason": reason,
    })
    meta[name] = entry
    return entry


def _cv4_observe_retire(store, agent_id, record, on_disk):
    """CV4 W2 write-fence SHADOW routing (observe-only; NEVER alters the retire).
    Reuses the W4/W5 checked-RETURN pattern: observe_write RETURNS 1=logged /
    0=failed / -1=disabled — we check the RETURN, not absence-of-exception (the
    W7 dead-except trap). rc==0 => ONE visible log line; the real retire ALWAYS
    proceeds. Fully wrapped: a broken/missing guard can never break park-idle.
    Off-switches (kill-file + CV4_WRITE_FENCE_MODE) live in write_fence."""
    try:
        import importlib.util
        _wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "continuity", "write_fence.py")
        _spec = importlib.util.spec_from_file_location("cv4_write_fence", _wf_path)
        _wf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_wf)
        rc = _wf.observe_write(store=store, key=agent_id, record=record,
                               on_disk=on_disk, writer="park-idle")
        if rc == 0:
            log(f"CV4 W2 observe error (rate-limited): shadow write failed for "
                f"{store}/{agent_id} (real retire proceeded)")
    except Exception as e:
        log(f"CV4 W2 observe error (rate-limited): {e!r} (real retire proceeded)")


def retire(name: str, reg: dict, meta: dict, roster: dict, reason: str) -> bool:
    """Registry-removal FIRST (stops auto-resurrection), then kill the session,
    preserving status=retired + resume_command in agent-sessions (REVERSIBLE)."""
    # 1. registry-removal (the resurrection gate)
    _cv4_reg_entry = reg.get("agents", {}).get(name)          # CV4 W2 removed row
    _cv4_observe_retire("registry", name, _cv4_reg_entry, _cv4_reg_entry)  # CV4 W2
    reg.get("agents", {}).pop(name, None)
    # 2. agent-sessions: mark retired, PRESERVE resume_command + session_id + resumable
    _cv4_sess_before = dict(meta.get(name) or {})             # CV4 W2 prior record
    mark_retired(meta, name, reason)
    _cv4_observe_retire("sessions", name, meta.get(name), _cv4_sess_before)  # CV4 W2
    # cutover: when the store is the identity write-truth, retire through the DB
    # (drop canonical + remove the registry doc + KEEP the retired session doc, one
    # txn) and skip the direct-JSON registry/sessions writes (the projector
    # regenerates them). Computed AFTER mark_retired so the DB persists the retired
    # session record. INERT: _use_db is False while the flag is unarmed.
    _use_db = _db_retire(name, reason, meta.get(name))
    if not _use_db:
        _atomic_write(REGISTRY, reg)
        _atomic_write(AGENT_SESSIONS, meta)
    # 3. live-roster removal
    if isinstance(roster, dict) and name in roster:
        roster.pop(name, None)
        _atomic_write(LIVE_ROSTER, roster)
    elif isinstance(roster, dict) and isinstance(roster.get("agents"), dict):
        roster["agents"].pop(name, None)
        _atomic_write(LIVE_ROSTER, roster)
    # 4. kill the (now registry-orphaned) tmux session
    tmux("kill-session", "-t", f"={name}")
    log(f"RETIRED {name} — {reason} (registry-removed, resume_command preserved)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually retire (default dry-run)")
    ap.add_argument("--max", type=int, default=5, help="max retirements per run")
    ap.add_argument("--agent", default=None, help="target one specific agent")
    ap.add_argument("--include-done-markers", action="store_true",
                    help="ALSO auto-retire DONE_MARKER_IDLE (heuristic; off by default)")
    ap.add_argument("--force", action="store_true", help="with --agent: skip safety gate")
    ap.add_argument("--force-no-comprehension", action="store_true",
                    help="guard 8d bypass: retire a rotation predecessor even though its "
                         "successor has NO passing comprehension artifact (loudly logged)")
    args = ap.parse_args()

    reg = _load(REGISTRY)
    meta = _load(AGENT_SESSIONS)
    roster = _load(LIVE_ROSTER)
    live = live_sessions()
    attached = session_attached()
    presence = load_presence()          # Gap 8b (fail-open if empty)
    reg_agents = reg.get("agents", {})

    targets = [args.agent] if args.agent else sorted(s for s in live if s not in INFRA)

    auto_classes = {"SUPERSEDED_SAFE"}
    if args.include_done_markers:
        auto_classes.add("DONE_MARKER_IDLE")

    rows = []
    for name in targets:
        if name not in live:
            print(f"  {name}: not a live session — skip")
            continue
        entry = meta.get(name, {})
        pane_plain = tmux("capture-pane", "-t", name, "-p").stdout
        pane_ansi = tmux("capture-pane", "-t", name, "-e", "-p").stdout
        # Gap 8 signals (bounded IO; only computed for the agents we might retire).
        cwd = entry.get("cwd") or reg_agents.get(name, {}).get("cwd")
        cat, reason = classify(
            name, entry, name in reg_agents,
            bool(reg_agents.get(name, {}).get("always_on")),
            attached.get(name, False), live, meta, pane_plain, pane_ansi,
            has_uncommitted_work=cwd_has_uncommitted_work(cwd),   # 8a
            operator_viewing=operator_viewing_agent(name, presence),      # 8b (fail-open)
            state_age_s=session_activity_age(name),               # 8c
        )
        rss = claude_rss_mb(name)
        rows.append((rss, name, cat, reason))

    rows.sort(reverse=True)
    print(f"\n{'RSS':>6}  {'AGENT':<34} {'CLASS':<17} REASON")
    print("-" * 96)
    reclaimable = 0
    to_retire = []
    for rss, name, cat, reason in rows:
        mark = ""
        eligible = (cat in auto_classes) or (args.agent == name and args.force)
        if eligible:
            mark = " <== RETIRE"
            reclaimable += rss
            to_retire.append((name, reason, rss))
        print(f"{rss:>5}M  {name:<34} {cat:<17} {reason}{mark}")
    print("-" * 96)
    print(f"eligible to retire: {len(to_retire)} agent(s), ~{reclaimable}M reclaimable")

    if not args.execute:
        print("\n(dry-run — pass --execute to retire the eligible class; --max caps count)")
        return

    done = 0
    for name, reason, rss in to_retire:
        if done >= args.max:
            print(f"--max {args.max} reached; stopping")
            break
        # Guard 8d (Phase B2): a rotation retire requires the successor's PASSING
        # comprehension artifact — a rotation cannot complete un-gated. Plain
        # retires (no succeeded_by) are unaffected.
        succ = successor(meta.get(name, {}))
        if succ and comprehension_gate_blocks(meta.get(name, {}), _comprehension_passed):
            if args.force_no_comprehension:
                log(f"GUARD-8D BYPASSED (--force-no-comprehension): retiring {name} even though "
                    f"successor '{succ}' has NO passing comprehension artifact — logged loud")
            else:
                log(f"REFUSED retire of {name} — guard 8d: successor '{succ}' has no passing "
                    f"comprehension artifact (state/agent-handoffs/{succ}.comprehension.json); "
                    f"grade via rotation_gate_manual.record_readback or use --force-no-comprehension")
                continue
        retire(name, reg, meta, roster, reason)
        done += 1
    free = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.split("\n")
    log(f"executed: retired {done} agent(s). {free[1] if len(free) > 1 else ''}")


if __name__ == "__main__":
    main()
