#!/usr/bin/env python3
"""
Agent Session Index — fast lookup for agent spawn/resume.

Maintains state/agent-sessions.json so the GM can instantly spawn any agent
without grepping through hundreds of JSONL files.

Usage:
    # Full scan — discover all sessions from ~/.claude/projects/
    python3 session-index.py scan

    # Update a single agent entry (called by spawn-agent.sh)
    python3 session-index.py update <agent_id> [--session-id X] [--cwd X] [--prompt X] [--summary "X"]

    # Lookup a single agent
    python3 session-index.py lookup <agent_id>

    # List all indexed agents
    python3 session-index.py list
"""

import json
import os
import sys
import re
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
INDEX_FILE = ORCHESTRA_DIR / "state" / "agent-sessions.json"
REGISTRY_FILE = ORCHESTRA_DIR / "registry.json"
STATE_DIR = ORCHESTRA_DIR / "state" / "agents"
HANDOFF_DIR = ORCHESTRA_DIR / "state" / "agent-handoffs"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


class IndexWriteRefused(RuntimeError):
    """The proposed index would cross agent identities — NOTHING was written."""


def _preflight_invariants(index: dict) -> list:
    """INV1/INV2 as a WRITE GATE, not an audit (gm msg_557a9257, P0).

    This writer crossed gm <-> orchestra-builder three times in one night, ten
    minutes apart, and a respawn inside a crossed window resumes the wrong
    session into the wrong pane. Detection already existed (sid_invariants.py)
    but ran downstream of the writer that caused the defect — a detector
    behind the thing it polices cannot prevent it. So the check moves to the
    moment of writing, and a violating write is REFUSED rather than logged.

    Scoped deliberately to INV1 (duplicate sid) and INV2 (registry
    disagreement) — the two that constitute an identity CROSSING. INV3-6 are
    audit-time concerns and must never block a write.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import sid_invariants as SI
    except ImportError:
        return []                    # never block a write on a missing checker
    try:
        registry = (load_registry() or {}).get("agents", {}) or {}
    except Exception:
        registry = {}
    violations = []
    seen: dict = {}
    for key in sorted(index):
        row = index[key]
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not sid:
            continue
        if sid in seen and not SI.same_lineage(seen[sid], key, registry):
            violations.append(
                f"INV1 duplicate sid {sid} shared by {seen[sid]!r} and {key!r}")
        seen[sid] = key
        r_sid = (registry.get(key) or {}).get("session_id")
        if r_sid and r_sid != sid:
            violations.append(
                f"INV2 {key!r}: index would say {sid}, registry says {r_sid}")
    return violations


def _cv4_observe_session_index(store, key):
    """CV4 W3 write-fence SHADOW routing (observe-only; NEVER alters/slows the scan).
    session-index is the STALE OBSERVATION PLANE (A2-8): its writes are MAINTENANCE
    and must NEVER be authority-granting — so we observe them UNSTAMPED, which
    classifies as would_grandfather, categorically NEVER would_accept. observe_write
    RETURNS 1/0/-1; we check the RETURN (rc==0 => one visible stderr line), never
    absence-of-exception (W7 dead-except trap). SHORT 1s DB timeout + full swallow:
    a scan that can't write the shadow must still complete (a frozen scan freezes
    the fleet — the self-deadlock/reconciler history). Real store write ALWAYS proceeds."""
    try:
        import importlib.util
        _wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "continuity", "write_fence.py")
        _spec = importlib.util.spec_from_file_location("cv4_write_fence", _wf_path)
        _wf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_wf)
        rc = _wf.observe_write(store=store, key=key, record={}, on_disk={},
                               writer="session-index", timeout=1.0)
        if rc == 0:
            sys.stderr.write(
                "CV4 W3 observe error (rate-limited): shadow write failed for "
                "%s (real scan write proceeded)\n" % store)
    except Exception as e:
        sys.stderr.write("CV4 W3 observe error (rate-limited): %r "
                         "(real scan write proceeded)\n" % e)


def save_index(index: dict, *, enforce_invariants: bool = True):
    if enforce_invariants:
        violations = _preflight_invariants(index)
        if violations:
            raise IndexWriteRefused(
                "REFUSED: this write would cross agent identities — "
                + "; ".join(violations)
                + ". NOTHING WAS WRITTEN.")
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    _cv4_observe_session_index("sessions", None)  # CV4 W3: maintenance observe
    # Atomic write
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, default=str))
    tmp.rename(INDEX_FILE)


def load_registry() -> dict:
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"agents": {}}


def cwd_to_project_dir(cwd: str) -> str:
    """Convert a cwd to its Claude project dir name, exactly as Claude Code does.

    /home/<user>/scripts/agent-orchestra -> -home-<user>-scripts-agent-orchestra
    (leading dash is kept; dots also become dashes)
    """
    return re.sub(r"[/.]", "-", cwd)


def scan_project_dir(project_path: Path) -> list[dict]:
    """Scan a Claude project dir for session metadata from JSONL files."""
    sessions = []
    for jf in sorted(project_path.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True):
        session_id = None
        cwd = None
        first_user_msg = None
        last_modified = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc).isoformat()

        try:
            with open(jf, "r") as f:
                for i, line in enumerate(f):
                    if i > 50:  # Don't read entire file
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Extract session ID
                    if not session_id and d.get("sessionId"):
                        session_id = d["sessionId"]

                    # Extract cwd
                    if not cwd and d.get("cwd"):
                        cwd = d["cwd"]

                    # Extract first user message for context
                    if not first_user_msg and d.get("type") == "user":
                        msg = d.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                for c in content:
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        first_user_msg = c["text"][:300]
                                        break
                            elif isinstance(content, str):
                                first_user_msg = content[:300]

                    if session_id and cwd and first_user_msg:
                        break
        except (OSError, PermissionError):
            continue

        if session_id:
            sessions.append({
                "session_id": session_id,
                "jsonl_file": str(jf),
                "project_dir": str(project_path),
                "cwd": cwd,
                "first_message": first_user_msg,
                "last_modified": last_modified,
            })
    return sessions


def _live_claude_sessions() -> dict:
    """Snapshot tmux sessions whose pane foreground process is a claude/node proc.

    Returns {session_name: True}. A bare `tmux ls` name is NOT enough — a pane can
    outlive its claude process (crash, or dropped to a bash shell); trusting such a
    stale pane would confirm-first onto a dead agent. Gate on the foreground command.
    """
    live = {}
    r = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return live
    for line in r.stdout.strip().split("\n"):
        if not line or "\t" not in line:
            continue
        name, cmd = line.split("\t", 1)
        if cmd.strip() in ("claude", "node"):
            live[name] = True
    return live


def _newest_jsonl_sid(project_dir: str):
    """Return the sessionId of the most-recently-modified *.jsonl in project_dir,
    or None. Used to detect a fork (compaction/crash-resume made a newer transcript
    than the agent's registered one)."""
    try:
        p = Path(project_dir)
        jsonls = sorted(p.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        for jf in jsonls:
            if jf.name.startswith("agent-"):
                continue  # subagent/warmup transcripts, not a resumable agent session
            return jf.stem
    except Exception:
        pass
    return None


def _boundary_re(term: str):
    """Hyphen-safe exact-token matcher. Plain \\b treats '-' as a boundary, so
    \\bintent-audience-refresh\\b WRONGLY matches intent-audience-refresh-2. The
    lookarounds forbid an adjacent word-char OR hyphen, so a -<suffix> sibling
    no longer matches while the exact self does."""
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.IGNORECASE)


def _confirm_registered_sid(agent_id, agent_config, prior_index, live_sessions, all_sessions):
    """Change A: authoritative-sid confirm-first. Return a session dict iff the
    agent's REGISTERED sid (prior index or resume_command) is safe to trust:
    transcript exists, is the NEWEST jsonl in its project dir, and the agent has a
    LIVE claude tmux pane. Else None → fall through to content matching.

    This is a match SOURCE only; the caller still runs it through _dedup, so it can
    never exempt an entry from conflict resolution.
    """
    prev = prior_index.get(agent_id) or {}
    sid = prev.get("session_id")
    if not sid:
        m = re.search(
            r"--resume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            prev.get("resume_command") or agent_config.get("resume_command") or "",
        )
        sid = m.group(1) if m else None
    if not sid:
        return None

    tmux_name = agent_config.get("tmux_session") or agent_id
    if tmux_name not in live_sessions:
        return None  # not a live claude pane → don't trust; re-derive

    # find the matching scanned session for this sid and confirm it is the newest
    for s in all_sessions:
        if s.get("session_id") == sid:
            newest = _newest_jsonl_sid(s.get("project_dir", ""))
            if newest and newest != sid:
                return None  # a newer transcript forked (compaction/crash) → stale
            return s
    return None


def _is_foreign_init(first_message: str, agent_id: str, agent_config: dict,
                     registered_names: set) -> bool:
    """Change C: True iff LINE 1 of the transcript is DEMONSTRABLY another registered
    agent's init header. Anchored to line 1 + a hyphen-safe terminator so a valid
    self-init that merely NAMES a parent/peer in prose ("spawned by orchestra-builder")
    is NOT vetoed. Never vetoes the agent's OWN name."""
    if not first_message or not registered_names:
        return False
    line1 = first_message.splitlines()[0] if first_message else ""
    my_names = {agent_id, (agent_config.get("name") or agent_id)}
    for other in registered_names:
        if other in my_names or not other:
            continue
        # "# INIT — <other>" or "You are <other>" / "You are **<other>**" as the header
        pat = re.compile(
            r"^\s*(#\s*INIT\b[^\n]*?|you\s+are\s+\*{0,2})" + re.escape(other) + r"(?![\w-])",
            re.IGNORECASE,
        )
        if pat.search(line1):
            return True
    return False


def _resolve_by_declaration(agent_id: str, candidates: list,
                            registered_names: set = None):
    """Pick the candidate whose transcript DECLARES it is `agent_id`.

    Returns the match, or None when no candidate declares this agent (which
    includes the honest case where nothing declares anything — unverifiable
    is never a pass, ia's rule 6). Candidates that declare a DIFFERENT known
    agent are vetoed outright: that transcript belongs to someone else, and
    handing it over is the crossing itself.

    Substance guard (INV5): a stub never beats a substantial transcript. The
    shipped fixture is a 3-line/3-second aborted compaction stub that was
    NEWER than the 710-line head it would have replaced.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import sid_invariants as SI
    except ImportError:
        return None
    try:
        registry = (load_registry() or {}).get("agents", {}) or {}
    except Exception:
        registry = {}
    known = set(registered_names or ()) | set(registry)
    known.add(agent_id)

    # DERIVED NEVER OVERWRITES DECLARED. The registry DECLARES which session
    # is canonical for this agent; this scan only DERIVES a guess. During a
    # rotation window both generations declare the same lineage and the newer
    # one is the successor — moving canonical's row to it before
    # promote_successor completes is the rotation-window identity hazard
    # (a pane becomes canonical before the agent learns it is). Verified by
    # effect: mid-rotation this path proposed gm -> gm-gen14 while the
    # registry still declared gen-13. So if the registry's sid is present and
    # still declares this lineage, it WINS.
    declared_sid = (registry.get(agent_id) or {}).get("session_id")
    if declared_sid:
        for s in candidates:
            if s.get("session_id") != declared_sid:
                continue
            path = (s.get("jsonl_file") or s.get("conversation_path")
                    or s.get("path"))
            if not path or not os.path.exists(path):
                break
            d = SI.declared_identity(path, known)
            if d and (d == agent_id or SI.same_lineage(d, agent_id, registry)):
                return s
            break

    mine = []
    for s in candidates:
        path = (s.get("jsonl_file") or s.get("conversation_path")
                or s.get("path"))
        if not path or not os.path.exists(path):
            continue
        decl = SI.declared_identity(path, known)
        if decl is None:
            continue                 # unverifiable is never a pass (rule 6)
        # An init declares the GENERATIONAL name while the row is keyed by the
        # canonical one (live: the transcript says "orchestra-builder-g11",
        # the key is "orchestra-builder"; both carry lineage_root
        # "orchestra-builder"). Accept a declared LINEAGE sibling; a
        # declaration outside the lineage is someone else's transcript and is
        # vetoed by omission — that veto IS the anti-crossing rule.
        if decl == agent_id or SI.same_lineage(decl, agent_id, registry):
            mine.append((SI.transcript_substance(path), s))
    if not mine:
        return None
    # DECLARATION decides WHOSE transcript this is (the anti-crossing
    # property). It does NOT decide which of an agent's OWN sessions is
    # current: a lineage accumulates generations, and ranking those by size
    # would resurrect the biggest HISTORICAL transcript — verified by effect
    # on live data, where substance-ranking picked an old generation over the
    # running session. So: substance is a GATE (a stub never wins while a
    # substantial sibling exists — INV5's aborted-compaction fixture), and
    # recency then chooses among the agent's own non-stub sessions, which is
    # the one place recency is legitimate because ownership is already fixed.
    non_stub = [t for t in mine if not t[0]["is_stub"]]
    pool = non_stub or mine
    pool.sort(key=lambda t: (t[1].get("last_modified") or ""), reverse=True)
    return pool[0][1]


def match_agent_to_sessions(agent_id: str, agent_config: dict, all_sessions: list[dict],
                            prior_index: dict = None, live_sessions: dict = None,
                            registered_names: set = None) -> dict | None:
    """Find the best session match for an agent based on cwd and prompt content.

    When multiple agents share a CWD (e.g. ~/scripts/agent-orchestra), we must
    disambiguate by checking if the session's first user message mentions this
    specific agent. If we can't disambiguate, return None rather than assigning
    a wrong session — fresh spawn is better than wrong-context resume.

    Order (congruence DEC-1786280521): A confirm-first (registered sid, newest jsonl,
    live claude pane) → exact-cwd filter → shared_cwd fast-path → B word-boundary
    scoring → C foreign-init veto. A is a match source only (never dedup-exempt).
    """
    prior_index = prior_index or {}
    live_sessions = live_sessions or {}
    registered_names = registered_names or set()

    # A. Authoritative-sid confirm-first (applies even for a unique cwd — better than
    #    "newest anything", which can pick a fork/throwaway transcript).
    confirmed = _confirm_registered_sid(
        agent_id, agent_config, prior_index, live_sessions, all_sessions)
    if confirmed is not None:
        return confirmed
    agent_cwd = agent_config.get("cwd", "")
    if not agent_cwd:
        return None

    # Expand ~ in cwd
    if agent_cwd.startswith("~"):
        agent_cwd = os.path.expanduser(agent_cwd)
    # Normalize /root → the actual home dir (e.g. when running as a different user than the seat cwd was recorded under)
    agent_cwd = agent_cwd.replace("/root/", os.path.expanduser("~") + "/")

    candidates = []
    for s in all_sessions:
        s_cwd = s.get("cwd", "")
        if not s_cwd:
            continue
        # EXACT cwd match only. Parent/child matching caused cross-agent transcript
        # theft: a session in agent-orchestra/ (GM) matched an agent whose cwd is
        # agent-orchestra/dashboard/ (chat-dev), so resume opened the wrong agent's
        # conversation. claude --resume also requires the transcript to live in the
        # project dir of the EXACT cwd, so non-exact matches can never resume anyway.
        if s_cwd == agent_cwd:
            candidates.append(s)

    if not candidates:
        return None

    # Count how many registered agents share this CWD
    # (checked by caller via all_sessions, but we can check registry too)
    shared_cwd_count = 0
    try:
        reg = load_registry()
        for aid, acfg in reg.get("agents", {}).items():
            acwd = acfg.get("cwd", "")
            if acwd.startswith("~"):
                acwd = os.path.expanduser(acwd)
            acwd = acwd.replace("/root/", os.path.expanduser("~") + "/")
            if acwd == agent_cwd:
                shared_cwd_count += 1
    except Exception:
        shared_cwd_count = 1

    # If this CWD is unique to this agent, simple — most recent session wins
    if shared_cwd_count <= 1:
        candidates.sort(key=lambda s: s.get("last_modified", ""), reverse=True)
        return candidates[0]

    # Shared CWD — DECLARATION FIRST (gm msg_557a9257, P0: this path crossed
    # gm <-> orchestra-builder three times in one night).
    #
    # Why the scoring below was never enough: it matches a MENTION, and an
    # init routinely mentions another agent. orchestra-builder's own init
    # opens "[SUPERVISOR gm gen-13] You are orchestra-builder-g11" — it
    # mentions `gm` while DECLARING `orchestra-builder`, so word-boundary
    # scoring hands ob's transcript to gm, and symmetrically. The DEC this
    # path was hardened for was dispositioned by inspecting the pipeline's
    # structure rather than testing the two agents that actually share this
    # cwd; the defect stayed live.
    #
    # A declaration is decidable and a mention is not, so ask the transcript
    # what it says it IS, using the SAME predicate the audit uses (one rule,
    # one home — a second copy is how the class regrows).
    declared = _resolve_by_declaration(agent_id, candidates, registered_names)
    if declared is not None:
        return declared

    # Look for agent_id, agent name, or prompt file name in the first user message
    agent_name = agent_config.get("name", agent_id)
    prompt_file = agent_config.get("system_prompt", "")
    prompt_basename = os.path.basename(prompt_file).replace(".md", "") if prompt_file else ""

    # Build search terms unique to this agent
    search_terms = [agent_id]
    if agent_name and agent_name != agent_id:
        search_terms.append(agent_name.lower())
    if prompt_basename and prompt_basename != agent_id:
        search_terms.append(prompt_basename)

    # Pre-compile hyphen-safe matchers for this agent's terms (B).
    term_res = [(t, _boundary_re(t)) for t in search_terms if t]

    # Score candidates by how well they match this specific agent
    scored = []
    for s in candidates:
        msg = s.get("first_message") or ""
        # C. foreign-init veto — if LINE 1 is demonstrably ANOTHER registered agent's
        #    init, skip this candidate entirely (don't let "spawned by X" / "you are
        #    the next GM" score it for the wrong agent).
        if _is_foreign_init(msg, agent_id, agent_config, registered_names):
            continue
        # B. word-boundary exact-name scoring (NOT substring).
        score = sum(1 for _t, rx in term_res if rx.search(msg))
        if score > 0:
            scored.append((score, s))

    if scored:
        # highest score, then most recent (both descending)
        scored.sort(key=lambda x: (x[0], x[1].get("last_modified", "")), reverse=True)
        return scored[0][1]

    # No disambiguation possible — return None rather than guessing wrong
    return None


def extract_model(conv_path: str) -> str | None:
    """Last real model used in a transcript (reads the tail; model is on every
    assistant turn). Needed because resume without --model silently falls back
    to the default model — post-reboot 2026-07-13 the fleet came back on
    wrong models and the operator had to order manual archaeology."""
    try:
        with open(conv_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    model = None
    for line in tail.split("\n"):
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        msg = d.get("message")
        m = msg.get("model") if isinstance(msg, dict) else None
        if m and m != "<synthetic>":
            model = m
    return model


def get_handoff_summary(agent_id: str) -> str | None:
    """Get conversation summary from handoff file if available."""
    md_file = HANDOFF_DIR / f"{agent_id}.md"
    json_file = HANDOFF_DIR / f"{agent_id}.json"

    if json_file.exists():
        try:
            data = json.loads(json_file.read_text())
            summary = data.get("summary", {})
            working_on = summary.get("working_on", [])
            if working_on:
                return "; ".join(working_on[:3])
        except (json.JSONDecodeError, OSError):
            pass

    if md_file.exists():
        try:
            text = md_file.read_text()[:500]
            # Extract first meaningful line
            for line in text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("---"):
                    return line[:200]
        except OSError:
            pass

    return None


# Infrastructure tmux sessions — never register as agents
INFRA_SESSIONS = {"dashboard", "custom-llm", "api-server", "combo-proxy", "telegram-router"}


# --- identity-store cutover seam (INERT until the operator-armed) --------------------
# Under cutover (DEC-1788346974 (b)+(b1)) reconcile_registry may NOT auto-mint a
# registry entry for a live raw-tmux session: that fabricates a PARTIAL identity
# (no generation/model/runtime — the exact splinter the store abolishes). It
# becomes OBSERVER-ONLY: alarm + skip, take NO fleet action (b1: no auto-quiesce).
# A cheap flag-FILE check gates it; the store is imported ONLY on the armed path.
def _cutover_active() -> bool:
    # Resolve the flag path at CALL time from the (test-injectable) ORCHESTRA_DIR
    # module global — NOT a frozen import-time constant — so the check is hermetic
    # against the live armed flag (a test's monkeypatched ORCHESTRA_DIR reaches it).
    # Still a cheap flag-FILE check that imports nothing new on the flag-off path.
    return (ORCHESTRA_DIR / "state" / "identity-store-cutover.flag").exists() \
        or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"


def _adopt_recipe(name: str, pane_cwd: str, missing) -> str:
    """One-paste operator recipe to adopt this session as a COMPLETE identity."""
    fields = " ".join(f"--{f} <{f}>" for f in missing)
    return (f"python3 scripts/identity_store/db_cli.py adopt_identity "
            f"--tmux {name} --cwd {pane_cwd or str(ORCHESTRA_DIR)} {fields}").strip()


# A surviving pane whose name matches the rotation alias conventions (`<root>-g<N>`
# provisional or `<root>-gen<N>` archive) but is absent from the store is an
# ORPHANED rotation pane — a crashed/rolled-back rotation left it behind. Flagging
# it gives gm/the operator the desired visibility (RED#5), distinct from a raw-tmux spawn.
_ROTATION_ALIAS_RE = re.compile(r"-g(?:en)?\d+$")


def _unregistered_alarm(name: str, pane_cwd: str, required) -> dict:
    """RED#1 payload: name the session + the missing _REQUIRED_ADOPT fields + recipe.
    RED#5: classify a surviving rotation-alias pane as an orphaned rotation."""
    return {
        "kind": "unregistered-live-session",
        "session": name,
        "tmux": name,
        "cwd": pane_cwd,
        "missing_required": list(required),
        "adopt_recipe": _adopt_recipe(name, pane_cwd, required),
        "orphan_rotation": bool(_ROTATION_ALIAS_RE.search(name)),
    }


# H9: the observer runs from the */10 cron as a FRESH process each pass, so the
# re-page ceiling can't live in memory — it is a durable JSON ledger keyed by
# session (last-paged epoch). Page once per session, then at most once per window.
_ALARM_REPAGE_CEILING_S = 86400  # 24h


def _alarm_ledger_path() -> Path:
    return ORCHESTRA_DIR / "state" / "identity-store-unregistered-alarms.json"


def _default_page(event: dict) -> None:
    """Terminal anomaly page (stderr + best-effort gm msg). Best-effort at the
    edge; the throttle above guarantees it fires at most once per session/window."""
    print(f"UNREGISTERED-LIVE-SESSION (cutover, no mint): {json.dumps(event)}",
          file=sys.stderr)
    try:
        subprocess.run(
            ["python3", str(ORCHESTRA_DIR / "msg_store.py"), "send",
             "--from", "session-index", "--to", "gm", "--type", "task",
             "--subject", f"unregistered live session: {event.get('session')}",
             "--body", json.dumps(event)],
            timeout=10, check=False)
    except Exception:
        pass


def _throttled_alarm(event: dict, *, page=None, ledger_path=None, now=None,
                     ceiling_s: int = _ALARM_REPAGE_CEILING_S) -> bool:
    """Dedup-by-session + bounded re-page ceiling (H9). Page a session at most once
    per `ceiling_s` window; persist the last-paged epoch in a durable JSON ledger so
    the out-of-lock */10 cron never re-pages a storm. Returns True iff it paged."""
    ledger_path = ledger_path or _alarm_ledger_path()
    now = time.time() if now is None else now
    sess = event.get("session")
    ledger = {}
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text())
        except Exception:
            ledger = {}
    last = ledger.get(sess)
    if last is not None and (now - last) < ceiling_s:
        return False  # within the window -> deduped, no page
    (page or _default_page)(event)
    ledger[sess] = now
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger))
    tmp.rename(ledger_path)
    return True


def _default_alarm(event: dict) -> None:
    """Live-path alarm: dedup + bounded re-page (RED#2) via the durable ledger."""
    _throttled_alarm(event)


# SYNC window (RED#4): rotate_agent registers the provisional to the store (Step-3)
# BEFORE spawning the pane (Step-4), so the store row exists before the pane. This
# grace is the defensive belt: an unknown session younger than the window may be
# mid-registration, so hold the alarm one cycle rather than fire a false anomaly.
_SYNC_GRACE_S = 120


def _session_age(name: str):
    """Seconds since the tmux session was created, or None if undeterminable
    (None => do NOT suppress; surfacing beats hiding)."""
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", name, "#{session_created}"],
            capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None
        return max(0.0, time.time() - int(out.stdout.strip()))
    except Exception:
        return None


def _store_known_names() -> set:
    """The set of identity names the store recognizes — canonical roots,
    ``<root>-gen<N>`` archives, and ``<root>-g<N>`` provisional aliases — derived
    via the faithful projector so it matches the projected registry membership
    EXACTLY (keys on ``generations`` INCLUDING non-canonical rows: the provisional
    window, DEC-1788342210 Part B). Empty when the prod DB is ABSENT (INERT) or
    unreadable, so every live session is then treated as an anomaly."""
    db_path = ORCHESTRA_DIR / "state" / "orchestra-registry.db"
    if not db_path.exists():
        return set()
    try:
        from scripts.identity_store import orchestra_db, projector
        conn = orchestra_db.get_connection(str(db_path))
        try:
            snap = projector._read_faithful_snapshot(conn)
            return set(projector._build_faithful_registry(snap)["agents"])
        finally:
            conn.close()
    except Exception:
        return set()


def reconcile_registry(alarm=None, store_lookup=None, session_age=None) -> int:
    """Auto-register live tmux sessions missing from registry.json.

    Unregistered agents (spawned via raw tmux) were invisible to recovery,
    reincarnation, and the GM's roster. Registers with safe defaults:
    T2, vps, always_on=False, cwd from the session's active pane.

    Under the identity-store cutover this becomes OBSERVER-ONLY (DEC-1788346974
    (b)+(b1)): no mint, alarm + skip, no fleet-affecting action. `alarm` and
    `store_lookup` are injectable for the RED proofs; the live path resolves them
    lazily on the armed branch only.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}|#{pane_current_path}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if out.returncode != 0:
        return 0

    registry = load_registry()
    agents = registry.setdefault("agents", {})
    known = set(agents.keys()) | {a.get("tmux_session", "") for a in agents.values()}

    cutover = _cutover_active()
    required = ()
    if cutover:
        # lazy-import the store ONLY on the armed path (INERT-safe order — the
        # flag-off path imports nothing new).
        from scripts.identity_store import orchestra_db
        required = orchestra_db._REQUIRED_ADOPT
        if alarm is None:
            alarm = _default_alarm
        if store_lookup is None:
            # resolve the store membership ONCE per pass (not per session).
            store_lookup = _store_known_names().__contains__
        if session_age is None:
            session_age = _session_age

    added = 0
    for line in out.stdout.strip().split("\n"):
        if "|" not in line:
            continue
        name, pane_cwd = line.split("|", 1)
        if not name or name in known or name in INFRA_SESSIONS:
            continue
        if cutover:
            # (b)+(b1): never mint a partial identity. A session already known to
            # the store (canonical or provisional) is NOT an anomaly — skip quietly
            # (RED#3). Otherwise ALARM + SKIP; take NO fleet action.
            if store_lookup(name):
                continue
            # sync window (RED#4): a just-spawned session may be mid-registration —
            # hold the alarm one cycle rather than fire a false anomaly.
            age = session_age(name)
            if age is not None and age < _SYNC_GRACE_S:
                continue
            alarm(_unregistered_alarm(name, pane_cwd, required))
            continue
        agents[name] = {
            "name": name,
            "tier": "T2",
            "machine": "vps",
            "cwd": pane_cwd or str(ORCHESTRA_DIR),
            "tmux_session": name,
            "system_prompt": "",
            "always_on": False,
            "auto_registered": now_iso(),
        }
        print(f"AUTO-REGISTERED: {name} (cwd: {pane_cwd})")
        added += 1

    if added:
        registry["last_updated"] = now_iso()
        _cv4_observe_session_index("registry", None)  # CV4 W3: maintenance observe
        tmp = REGISTRY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(registry, indent=2))
        tmp.rename(REGISTRY_FILE)
    return added


_SID_RE = re.compile(
    r"--resume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def _recover_sid(entry: dict, agent_id: str, prior_index: dict):
    """Recover a falsy session_id without fabricating one. Returns a sid or None.

    Order (congruence DEC-1786278696, a→b): prior on-disk index is FRESHER — this
    scanner refreshes it every 10m — so try it first; resume_command is a stale-prone
    last resort (written only by spawn scripts, absent on most entries, NOT refreshed
    on a /model switch). Never invents a conversation_path from a bare sid.
    """
    if entry.get("session_id"):
        return entry["session_id"]
    prev = (prior_index.get(agent_id) or {}).get("session_id")
    if prev:
        return prev
    m = _SID_RE.search(entry.get("resume_command") or "")
    return m.group(1) if m else None


def _registry_statuses() -> dict:
    """agent_id -> registry status (F3, DEC-1787043333). Fail-open: any read
    error returns {} and arbitration behaves exactly as before."""
    try:
        agents = load_registry().get("agents", {})
        return {k: (v.get("status") or "") for k, v in agents.items()
                if isinstance(v, dict)}
    except Exception as e:
        # gm vote bind (DEC-1787043333): fail-open must be LOUD — a silent {}
        # degrades arbitration to identity-blind without anyone knowing.
        print(f"WARNING: registry unreadable for sid arbitration ({e}) — "
              f"identity-blind strip-all dedup this cycle")
        return {}


def sid_assignable(agent_id: str, statuses: dict) -> bool:
    """F3: a registry-retired/parked agent must never be (re)assigned a sid —
    the 07:02 fork's recover pass handed the live canonical sid to a retired
    alias. Unknown agents (not in statuses) keep current behavior (fail-open)."""
    return statuses.get(agent_id) not in ("retired", "parked")


def dedup_with_status(idx: dict, statuses: dict, registry_agents: dict = None):
    """B7-scan tiered dedup (DELTA E + D4, gm commission msg_c6486125;
    replaces both the identity-blind strip-all AND the manufactured-ambiguity
    class that destroyed the gm gen-16 promotion):

      RELEASE   claimants that cannot support a sid (sid_assignable's own
                set: retired/parked) are stripped FIRST and never count —
                long-dead generations claiming live sids were the fuel
      SOLO      <=1 supportable claimant left: no contest (also the
                promote-fast rollback window — a solo quiescent predecessor
                is untouched by cron)
      ACTIVE    exactly one online/active claimant keeps (E.6-measured,
                preserved verbatim)
      DECLARED  else exactly one claimant EXACTLY declared by its own
                transcript wins outright (real declared_identity)
      HOLD      else: genuinely undecidable — every prior value HELD, loud
                report, nothing cleared. The scan must never write identity
                BACKWARDS on ambiguity it cannot resolve (fencing principle;
                'ambiguous, cleared from all' reseated the canonical T0 row
                on an undeclared corpse and froze the index for 440 agents).

    Returns (n_conflicts, cleared_sids); cleared_sids = strip-ALL clears
    only, which post-B7 can occur on no path — held sids are NOT cleared, so
    recover suppression never fires on a held row."""
    claims = {}
    for aid, e in idx.items():
        sid = e.get("session_id")
        if sid:
            claims.setdefault(sid, []).append(aid)
    n = 0
    cleared = set()

    def _strip(aid):
        idx[aid].pop("session_id", None)
        idx[aid].pop("conversation_path", None)
        idx[aid]["resumable"] = False

    for sid, claimants in claims.items():
        if len(claimants) <= 1:
            continue
        # ---- RELEASE
        supportable = []
        for aid in claimants:
            if sid_assignable(aid, statuses):
                supportable.append(aid)
            else:
                _strip(aid)
                print(f"DEDUP: released stale claim on {sid[:8]} from "
                      f"'{aid}' (status={statuses.get(aid)!r} cannot support "
                      f"a sid — D4 fuel)")
        # ---- SOLO
        if len(supportable) <= 1:
            if len(supportable) < len(claimants):
                n += 1          # a release happened; report as a conflict handled
            continue
        n += 1
        # ---- ACTIVE (keeper election requires TRULY-active status: a
        # quiescent claimant keeps BOOKKEEPING via sid_assignable but never
        # WINS a contest)
        active = [a for a in supportable
                  if statuses.get(a) in ("online", "active")]
        if len(active) == 1:
            keeper = active[0]
            for aid in supportable:
                if aid != keeper:
                    _strip(aid)
            print(f"DEDUP: session {sid[:8]} claimed by {len(claimants)} agents — "
                  f"kept on sole registry-active '{keeper}', stripped the rest")
            continue
        # ---- DECLARED (real predicate, invoked not reimplemented — A7).
        # CANONICAL-SEAT clause (g13 msg_5f9ced74, dry-run: bare exact
        # equality would strip 9/19 live rows including BOTH canonical
        # seats): a correctly-promoted canonical row holds a sid whose
        # transcript declares the GENERATIONAL name, so exact equality can
        # never hold precisely where the seat is healthy. A claimant
        # QUALIFIES if its transcript exactly declares it, OR it is the
        # REGISTRY-DECLARED holder of this sid and the declaration names a
        # member of its lineage (the fencing conjunct: the verb's own write
        # breaks the tie). Exactly one qualified claimant wins; the
        # seat-vs-its-own-alias case yields TWO qualified -> HOLD, which
        # strips nothing.
        try:
            import sid_invariants as _SI
            known = set(idx.keys()) | set(statuses.keys()) \
                | set((registry_agents or {}).keys())
            declared = []
            for aid in supportable:
                path = (idx[aid].get("conversation_path")
                        or idx[aid].get("jsonl_file") or "")
                if not path or not os.path.exists(path):
                    continue
                decl = _SI.declared_identity(path, known)
                if decl is None:
                    continue
                if decl == aid:
                    declared.append(aid)
                elif registry_agents is not None:
                    row = registry_agents.get(aid) or {}
                    if row.get("session_id") == sid \
                            and _SI.same_lineage(decl, aid, registry_agents):
                        declared.append(aid)
        except Exception as e:
            declared = []
            print(f"WARNING: declaration tier unavailable ({e}) — "
                  f"falling through to HOLD")
        if len(declared) == 1:
            keeper = declared[0]
            for aid in supportable:
                if aid != keeper:
                    _strip(aid)
            print(f"DEDUP: session {sid[:8]} contested — kept on "
                  f"exactly-declared '{keeper}', stripped the rest")
            continue
        # ---- HOLD
        print(f"DEDUP: session {sid[:8]} claimed by {len(claimants)} agents "
              f"({', '.join(claimants[:4])}...) — UNDECIDABLE, all prior "
              f"values HELD (never cleared-from-all); needs operator/gm "
              f"attention")
    return n, cleared


_VARIANT_RE = re.compile(r"(\[[^\]]+\])\s*$")


def reconcile_model(declared, derived):
    """DERIVED never overwrites DECLARED intent (gm ruling msg_5994c971, found
    by readback during the 2026-08-18 fleet model switch).

    The transcript's `message.model` is the API id — it carries the BASE model
    but NEVER the CLI variant suffix ('[1m]'), because the variant is a launch
    FLAG the transcript cannot see. Overwriting the row with the raw derived
    value silently stripped '[1m]' from every agent on each */10 scan
    (orchestra-builder + gm clobbered within one minute of a verified write);
    a bare model re-forks an agent to ~200k context instead of 1M — the operator's
    every-model-is-[1m] law.

    Rule (hardened by effect — see below): the DECLARED BASE always wins; the
    variant suffix is enriched from whichever side has one. The transcript is
    HISTORY, the row is INTENT: a just-switched agent that has not yet taken a
    turn still has pre-switch assistant messages in its tail, so trusting the
    derived base REVERTED two live agents to claude-fable-5[1m] — the exhausted
    pool — during this very fix's verification scan. A genuine mid-session
    /model switch therefore leaves the row stale until an actuator updates it
    (safe, and the divergence is recorded as model_observed at the call site).

    Generalizes: derive the observable part, never let history overwrite intent."""
    if not derived:
        return declared
    if not declared:
        return derived
    base = _VARIANT_RE.sub("", declared).strip()
    variant = _VARIANT_RE.search(declared) or _VARIANT_RE.search(derived)
    return f"{base}{variant.group(1)}" if variant else base


def reconcile_sids(index: dict, prior_index: dict) -> int:
    """Post-loop sid reconciliation. Returns the count of deduped conflicts.

    Two ordered passes:
      1. DEDUP — one-session-one-agent: if a sid is claimed by >1 agent the matches
         are ambiguous, so strip it from ALL claimants (fresh spawn beats wrong-context
         resume). One sid was once assigned to 12 agents.
      2. RECOVER — for any agent whose session_id is now falsy (match miss this cycle,
         or dedup above), restore it via _recover_sid — but ONLY if the recovered sid is
         not already held by another agent, so recovery can never re-create a conflict
         dedup just resolved. conversation_path is intentionally left as-is; `resumable`
         (already computed per-entry) stays honest (False) until a real match heals it.
    """
    # F3 (DEC-1787043333): status-aware arbitration — module-level
    # dedup_with_status/sid_assignable; registry read fail-open to {} keeps
    # legacy strip-all behavior when the registry is unreadable.
    statuses = _registry_statuses()
    # canonical-seat clause needs the raw rows (lineage + declared-holder);
    # fail-open to None keeps the exact-only tier when the registry is out
    try:
        _reg_agents = (load_registry() or {}).get("agents") or None
    except Exception:
        _reg_agents = None
    deduped, cleared_conflicts = dedup_with_status(index, statuses,
                                                   registry_agents=_reg_agents)

    held = {e.get("session_id") for e in index.values() if e.get("session_id")}
    recovered = 0
    for agent_id, entry in index.items():
        if entry.get("session_id"):
            continue
        # F3: retired/parked agents never receive a recovered sid (the 07:02
        # fork's exact vector — the alias won the canonical's live sid here).
        if not sid_assignable(agent_id, statuses):
            continue
        sid = _recover_sid(entry, agent_id, prior_index)
        # never resurrect a sid dedup just cleared as a genuine multi-agent conflict,
        # and never create a NEW duplicate (sid already held by another agent).
        if sid and sid not in held and sid not in cleared_conflicts:
            entry["session_id"] = sid
            held.add(sid)
            recovered += 1
            print(f"RECOVER: {agent_id} session_id restored to {sid[:8]} "
                  f"(match miss; recovered from prior index/resume_command)")
    if recovered:
        print(f"Recovered {recovered} session_id(s) that this scan failed to re-match")
    return deduped


def scan_all():
    """Full scan: discover sessions from ~/.claude/projects/ and match to agents."""
    # First, pull any live-but-unregistered tmux sessions into the registry
    # so they get indexed below.
    reconcile_registry()

    registry = load_registry()
    agents = registry.get("agents", {})
    index = load_index()
    # Untouched snapshot of the prior on-disk index — used to recover a session_id
    # that this scan fails to re-match (a distinct object; `index` is mutated below).
    prior_index = load_index()

    # Collect all sessions across all project dirs
    all_sessions = []
    if CLAUDE_PROJECTS.exists():
        for pdir in CLAUDE_PROJECTS.iterdir():
            if pdir.is_dir():
                sessions = scan_project_dir(pdir)
                all_sessions.extend(sessions)

    print(f"Found {len(all_sessions)} sessions across {len(list(CLAUDE_PROJECTS.iterdir()))} project dirs")

    # Snapshot once (threaded into the matcher): live claude panes + all registered
    # agent names (for the foreign-init veto). Fail-safe: an empty snapshot only makes
    # the matcher re-derive (never a false confirm).
    live_sessions = _live_claude_sessions()
    registered_names = set(agents.keys()) | {
        (cfg.get("name") or aid) for aid, cfg in agents.items()
    }

    # Also check agent state files for any session data already recorded
    matched = 0
    for agent_id, agent_config in agents.items():
        best = match_agent_to_sessions(
            agent_id, agent_config, all_sessions,
            prior_index=prior_index, live_sessions=live_sessions,
            registered_names=registered_names)

        # Read existing state file for supplementary data
        state_file = STATE_DIR / f"{agent_id}.json"
        state = {}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        entry = index.get(agent_id, {})

        # Merge data: session scan > state file > existing index > registry
        agent_cwd = agent_config.get("cwd", "")
        if agent_cwd.startswith("~"):
            agent_cwd = os.path.expanduser(agent_cwd)
        agent_cwd = agent_cwd.replace("/root/", os.path.expanduser("~") + "/")

        # LIVE-AGENT GUARD (apr_206a15a0, the operator 2026-08-15): the cwd-matcher
        # misassigns sessions for agents sharing one cwd (known bug,
        # agent-recovery.sh:210) and was clobbering LIVE agents' records with
        # stale/foreign session data every 10-min cron scan — making running
        # agents invisible on freshness-sorted surfaces (gm-gen7-prime,
        # ob gen-4, 58/60 fleet sweep 2026-08-14). For an agent whose tmux
        # session is LIVE, a scan match may only FILL GAPS or move last_active
        # FORWARD — never regress it. Dead/unknown agents keep full overwrite
        # (the scan is their only recovery path).
        tmux_name = agent_config.get("tmux_session", agent_id)
        is_live = tmux_name in live_sessions
        if best:
            existing_last = entry.get("last_active") or ""
            regress = is_live and existing_last and str(best["last_modified"]) < str(existing_last)
            if not regress:
                entry["session_id"] = best["session_id"]
                entry["project_dir"] = best["project_dir"]
                entry["conversation_path"] = best["jsonl_file"]
                entry["last_active"] = best["last_modified"]
                if best.get("cwd"):
                    entry["cwd"] = best["cwd"]
                model = extract_model(best["jsonl_file"])
                if model:
                    # derived-vs-declared: intent wins, variant enriched
                    _declared = entry.get("model")
                    entry["model"] = reconcile_model(_declared, model)
                    # observability: keep the transcript's own view when it
                    # disagrees, so a real mid-session /model switch is VISIBLE
                    # rather than silently discarded (or silently applied).
                    if _declared and model != entry["model"]:
                        entry["model_observed"] = model
            matched += 1
        else:
            # Use state/registry as fallback
            if not entry.get("session_id") and state.get("session_id"):
                entry["session_id"] = state["session_id"]
            if not entry.get("cwd"):
                entry["cwd"] = state.get("cwd") or agent_cwd

        # Always set these from registry
        entry["prompt_file"] = agent_config.get("system_prompt", "")
        entry["tmux_session"] = agent_config.get("tmux_session", agent_id)
        entry["machine"] = agent_config.get("machine", "vps")
        entry["tier"] = agent_config.get("tier", "T2")
        entry["name"] = agent_config.get("name", agent_id)

        if not entry.get("cwd"):
            entry["cwd"] = agent_cwd

        # Get handoff summary
        summary = get_handoff_summary(agent_id)
        if summary:
            entry["conversation_summary"] = summary

        # Resumable requires: transcript exists AND lives in the project dir of the
        # agent's cwd (claude --resume resolves sessions from the launch cwd's slug;
        # a transcript anywhere else silently opens a wrong/new conversation).
        conv_path = entry.get("conversation_path", "")
        expected_dir = str(CLAUDE_PROJECTS / cwd_to_project_dir(entry.get("cwd") or agent_cwd))
        entry["resumable"] = bool(
            conv_path
            and Path(conv_path).exists()
            and str(Path(conv_path).parent) == expected_dir
        )

        index[agent_id] = entry

    deduped = reconcile_sids(index, prior_index)

    save_index(index)
    print(f"Indexed {len(index)} agents ({matched} matched, {deduped} shared-session conflicts cleared)")
    return index


def update_agent(agent_id: str, **kwargs):
    """Update a single agent's entry in the index."""
    index = load_index()
    entry = index.get(agent_id, {})

    for key, val in kwargs.items():
        if val is not None:
            entry[key] = val

    entry["last_active"] = now_iso()

    # Check resumable
    conv_path = entry.get("conversation_path", "")
    entry["resumable"] = bool(conv_path and Path(conv_path).exists())

    index[agent_id] = entry
    save_index(index)
    return entry


def lookup_agent(agent_id: str) -> dict | None:
    """Fast lookup of agent session data."""
    index = load_index()
    entry = index.get(agent_id)
    if entry:
        # Verify session file still exists
        conv_path = entry.get("conversation_path", "")
        entry["resumable"] = bool(conv_path and Path(conv_path).exists())
    return entry


def find_latest_session_for_cwd(cwd: str) -> dict | None:
    """Find the most recent session matching a given cwd without full scan."""
    if not CLAUDE_PROJECTS.exists():
        return None

    # Convert cwd to project dir pattern
    proj_name = cwd_to_project_dir(cwd)
    candidates = []

    for pdir in CLAUDE_PROJECTS.iterdir():
        if not pdir.is_dir():
            continue
        # Check if this project dir matches the cwd
        if proj_name in pdir.name or pdir.name in proj_name:
            sessions = scan_project_dir(pdir)
            for s in sessions:
                if s.get("cwd") == cwd:
                    candidates.append(s)

    if candidates:
        candidates.sort(key=lambda s: s.get("last_modified", ""), reverse=True)
        return candidates[0]
    return None


def print_list():
    """Print all indexed agents."""
    index = load_index()
    if not index:
        print("No agents indexed. Run: python3 session-index.py scan")
        return

    fmt = "{:<25} {:<12} {:<8} {:<40} {}"
    print(fmt.format("AGENT", "SESSION", "RESUME?", "CWD", "SUMMARY"))
    print("-" * 120)
    for agent_id in sorted(index.keys()):
        e = index[agent_id]
        sid = (e.get("session_id") or "")[:10]
        resume = "YES" if e.get("resumable") else "no"
        cwd = (e.get("cwd") or "")[-38:]
        summary = (e.get("conversation_summary") or "")[:40]
        print(fmt.format(agent_id, sid, resume, cwd, summary))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "scan":
        scan_all()

    elif cmd == "update" and len(sys.argv) >= 3:
        agent_id = sys.argv[2]
        kwargs = {}
        i = 3
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg.startswith("--") and i + 1 < len(sys.argv):
                key = arg[2:].replace("-", "_")
                kwargs[key] = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        entry = update_agent(agent_id, **kwargs)
        print(json.dumps(entry, indent=2))

    elif cmd == "lookup" and len(sys.argv) >= 3:
        agent_id = sys.argv[2]
        entry = lookup_agent(agent_id)
        if entry:
            print(json.dumps(entry, indent=2))
        else:
            print(f"No index entry for {agent_id}")
            sys.exit(1)

    elif cmd == "reconcile":
        n = reconcile_registry()
        print(f"Auto-registered {n} live tmux sessions")

    elif cmd == "list":
        print_list()

    else:
        print(__doc__)
        sys.exit(1)
