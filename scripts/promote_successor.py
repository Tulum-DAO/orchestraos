#!/usr/bin/env python3
# scripts/promote_successor.py
"""promote_successor — THE atomic canonical-identity promotion primitive.

Phase B3 (spec §6.6.1, critique failure mode #9 — the gm gen-9→10 debacle):
a canonical-name swap used to be hand-rolled across six state surfaces; on
2026-08-16 exactly one was updated properly (tmux rename), the registry row
came out half-empty and non-openable, state/agents/gm.json stayed 'stopped',
resume_command was wiped, and the operator was the only detector. This module is the
ONE way a successor takes over a canonical id. Manual rotations, WS3, and
gm-lineage swaps ALL call promote() — nobody hand-rolls a promotion again.

Under the shared registry writer flock (registry_lock), in ONE pass:
  * registry.json      — new canonical entry = COPY of the predecessor's full
                         field set + successor overrides. NEVER a half-empty
                         row: REQUIRED_FIELDS asserted post-merge or the whole
                         promotion refuses loudly (zero writes). Leftover
                         successor-alias rows (gm-gen10-style temp entries)
                         are retired.
  * agent-sessions.json — canonical entry online, session_id, tmux_session,
                         resumable=True, and resume_command WRITTEN (closes
                         the twice-flagged park-idle wipe).
  * state/agents/<id>.json — status online + session fields (created if
                         missing; unknown keys preserved).

All new file contents are computed and validated FIRST; only then are all
files written (atomic tmp→replace each). A refusal writes nothing. Partial
conditions (e.g. no session id resolvable) are logged loudly in the report.

CLI:
  python3 scripts/promote_successor.py gm gm-gen10 --session-id <sid> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from registry_lock import registry_lock  # noqa: E402
# The ONE shared seat-verb runtime resolver (spec §4.1 R4(b)): promote consumes
# the SAME object spawn does, so the R3 field-read asymmetry (promote read
# `runtime|provider`, spawn read only `runtime`) is unified. Re-exported at
# module scope precisely so a parity test can assert identity with spawn's source.
from runtime_signatures import (  # noqa: E402,F401
    resolve_runtime, RuntimeResolutionError)

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
REGISTRY = ORCHESTRA_DIR / "registry.json"
AGENT_SESSIONS = ORCHESTRA_DIR / "state" / "agent-sessions.json"
AGENTS_DIR = ORCHESTRA_DIR / "state" / "agents"
LOCK_FILE = ORCHESTRA_DIR / "state" / "registry.lock"
LOG_FILE = ORCHESTRA_DIR / "logs" / "promote-successor.log"

# --- identity-store cutover seam (INERT until the operator-armed) --------------------
# Cheap flag-file check FIRST: while the cutover is unarmed (default + current
# state) this imports nothing new and the 3-store promote is byte-identical. Only
# when armed do we lazily route the canonical Blue->Green repoint through the
# store (fail-closed if the lineage/canonical rows are absent — never write
# identity around the store). Surrounding choreography is untouched.
def _cutover_active() -> bool:
    # Resolve the flag path at CALL time from the (test-injectable) ORCHESTRA_DIR
    # module global so the check is hermetic against the live armed flag. Cheap
    # flag-FILE check; imports nothing new on the flag-off path.
    return (ORCHESTRA_DIR / "state" / "identity-store-cutover.flag").exists() \
        or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"


def _db_promote_swap(root: str, new_entry: dict, sess_entry: dict,
                     agent_state=None, archive_key=None, reg=None, meta=None,
                     aliases_retired=None) -> bool:
    if not _cutover_active():
        return False
    if str(ORCHESTRA_DIR) not in sys.path:
        sys.path.insert(0, str(ORCHESTRA_DIR))
    from scripts.identity_store import identity_writer
    green = {
        "generation": sess_entry.get("generation") or new_entry.get("generation"),
        "session_id": sess_entry.get("session_id"),
        "model": sess_entry.get("model"),
        "conversation_path": sess_entry.get("conversation_path"),
        "promoted_at": sess_entry.get("promoted_at"),
        "resume_command": sess_entry.get("resume_command"),
    }
    # DP-A2: persist the FULL records the 3-store promote would have written —
    # successor doc under root, successor session, the state blob, and the
    # predecessor <root>-gen<N> archive (registry + sessions) — atomically with the
    # canonical repoint, so project_faithful serves the successor under root and
    # KEEPS the predecessor archive (M3a). The projector regenerates the JSON.
    documents = [
        ("registry.json", "agent", root, new_entry),
        ("agent-sessions.json", "session", root, sess_entry),
    ]
    if agent_state is not None:
        documents.append(("state/agents", "state_agent", f"{root}.json", agent_state))
    if archive_key:
        arch_reg = (reg or {}).get("agents", {}).get(archive_key)
        if arch_reg is not None:
            documents.append(("registry.json", "agent", archive_key, arch_reg))
        arch_sess = (meta or {}).get(archive_key)
        if arch_sess is not None:
            documents.append(("agent-sessions.json", "session", archive_key, arch_sess))
    # F1: the promote/rotate SYNC path owns + runs its own effects choreography
    # (tmux rename / archive / kill, Steps 8-9) synchronously — it is NOT managed by
    # r-a-b's async resume-driver, so mark the swap terminal ('complete') and the
    # resume-driver hands off (no spurious re-run of effects it does not own).
    handled = identity_writer.swap_generation(str(ORCHESTRA_DIR), root, green,
                                              documents=documents,
                                              sync_effects_owner=True)
    # ITEM B (gm msg_b1429f36): finalize the swap-retired predecessor archive DB-first.
    # execute_swap stamps the blue gen's retired_at but leaves its resume_command NULL
    # and its runtime_state 'online' — so the archive read non-resumable + still-live.
    # Carry the resume_command the archive doc already holds (built at ~:1115) onto the
    # typed gen row, and flip its runtime_state to 'parked', keyed to the archived
    # (root, gen). The projector then decides the archive's status by resume_command,
    # identically in registry + sessions.
    if handled and archive_key:
        arch_doc = ((meta or {}).get(archive_key)
                    or (reg or {}).get("agents", {}).get(archive_key) or {})
        arch_gen = arch_doc.get("generation")
        arch_resume = arch_doc.get("resume_command")
        if arch_gen is not None:
            identity_writer.finalize_archive(str(ORCHESTRA_DIR), root, arch_gen,
                                             arch_resume)
    # ITEM A (gm msg_b1429f36): retire leftover successor-alias rows DB-FIRST. The
    # swap only repoints the ROOT canonical; each `<root>-gN` alias is its OWN
    # canonical row the swap never touches, so without this the store keeps the
    # alias status='online' (gen 1, sid None) with no pane while the flat says
    # 'retired' — flat/DB disagree and the next faithful projection can RESURRECT
    # it (the ":1499" 3-alias-resurrections class, previously patched flat-side
    # only). retire_agent drops the alias canonical row + stamps its generation
    # retired_at (resume path preserved) + leaves the lineage row; project_now
    # makes the flat follow (the flat edits at :1341-1365/:1506-1528 stay
    # belt-and-braces for the INERT, flag-off path).
    if handled and aliases_retired:
        for alias_key in aliases_retired:
            if alias_key == root:
                continue  # never retire the canonical root the swap just promoted
            identity_writer.retire_agent(
                str(ORCHESTRA_DIR), alias_key,
                reason=(f"alias canonical row retired DB-first by atomic "
                        f"promotion to '{root}'"))
        identity_writer.project_now(str(ORCHESTRA_DIR))
    return handled

# A canonical registry row missing ANY of these is non-openable — the exact
# half-empty-row failure the operator hit. Refuse the whole promotion instead.
REQUIRED_FIELDS = ("tier", "tmux_session", "cwd", "always_on", "name")

# Predecessor-transient fields that must NOT survive onto the promoted row.
_DROP_ON_PROMOTE = {"succeeded_by", "superseded_by", "retired_at", "retired_by",
                    "retired_note", "retired_reason", "status", "note",
                    "repinned_canonical_at"}

# T4 non-triviality floor: existence-tier only (quality belongs to a T8
# grader). A committed stub under this many non-blank lines is not
# absorption evidence.
READBACK_MIN_NONBLANK = 10

# G7 rotation trigger (the operator order 2026-08-19): a rotation may begin only when
# the PREDECESSOR's detector proves it is at/over the ceiling. A detector older
# than this is a claim about a PAST turn, not this one.
ROTATION_TRIGGER_PCT = 80
DETECTOR_STALE_S = 900

# Piece 1 (DEC-1787861488): G7 structured trigger evidence for completion-timed
# rotations. The hold STORE G7 re-reads (C1 — the promoter-passed dict is never
# trusted) is the fleet-beat daemon's persisted state; the hard hold-age ceiling
# (C2) aligns with hold_ledger.PARK_PROPOSAL_AGE_S — beyond 12h the ledger
# already treats the hold as stale (park-proposal territory), so machine
# evidence expires there too and the HUMAN override is required.
FLEET_BEAT_STATE = ORCHESTRA_DIR / "state" / "fleet-beat-state.json"
TRIGGER_EVIDENCE_MAX_AGE_S = 12 * 3600


def _readback_rel_path(successor_session: str) -> str:
    """THE pinned T4 location, resolved from the successor id (gm req 1 —
    never 'wherever the successor put it'). One canonical path is what makes
    an artifact discoverable: this lane's own g1->g2 readback was produced
    AND undiscoverable because handoff, orders and implementation named
    three different places (gm msg_21e9ca44). CANONICAL by gm ruling
    msg_91fdc1d7: it sits beside its rotation siblings (canary.json,
    comprehension.json, grade.json) under _handoffs_dir()."""
    return f"state/agent-handoffs/{successor_session}.readback.md"


def _readback_legacy_rel_path(successor_session: str) -> str:
    """MIGRATION-era legacy location (gm ruling msg_91fdc1d7): the convention
    was bifurcated in live use, so during migration the gate ACCEPTS this
    path with a warning and NEVER refuses on it — refusing mid-migration
    mechanises a false absence-accusation with the verb's authority. Remove
    when gm flips to refuse-on-absent post-migration."""
    return f"docs/READBACK_{successor_session}.md"


def _comprehension_rel_path(successor_session: str) -> str:
    """The grader's comprehension artifact, beside the readback (rotation
    siblings under state/agent-handoffs/). Written by the REAL grader
    (rotation_gate_manual) before promotion, on disk (not necessarily
    committed — it is a local grader receipt, read by effect)."""
    return f"state/agent-handoffs/{successor_session}.comprehension.json"


def _has_strict_pass_comprehension(successor_session: str) -> bool:
    """T4 SECOND LEG (gm msg_67c57526 #1). True IFF a grader-written
    comprehension.json for this successor is a STRICT PASS. The line-count
    floor is only an existence-tier PROXY for absorption; a real strict
    citation grade (rotation_gate_manual, result=PASS mode=strict) is stronger
    evidence than a line count, so it satisfies T4 even when the readback is
    terse (the live failure: a content-rich answer written as one long line
    per question strict-PASSed the grader yet tripped the >=10-non-blank floor).

    Positive-signal-only: absent / unreadable / non-dict / not-strict /
    not-PASS all return False -> the floor stands. This NEVER widens the
    absent/uncommitted paths (the caller only consults it after the readback
    is confirmed present)."""
    p = ORCHESTRA_DIR / _comprehension_rel_path(successor_session)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    mode = str(data.get("mode") or "").strip().lower()
    passed = (data.get("pass") is True
              or str(data.get("result") or "").strip().upper() == "PASS")
    return passed and mode == "strict"


class PromotionRefused(RuntimeError):
    """The promotion could not be made safe/complete — NOTHING was written."""


_VOUCH_FIELDS = ("init_ref", "operator_sid", "grade_linkage")


def _valid_mechanical_vouch(vouch, sid: str) -> bool:
    """A Stage-B identity vouch (comprehension.json `identity_vouch`) is a valid
    MECHANICAL stand-in for the human operator-assertion IFF it is a complete
    3-field block AND its operator_sid is exactly the sid being asserted (replay
    defense — a vouch minted for a DIFFERENT sid must never vouch this one).

    Positive-signal-only: anything less (missing field, blank, non-dict, sid
    mismatch, None) is NOT a vouch -> the caller falls through to the existing
    REFUSE. This never WIDENS acceptance beyond what a human operator-assertion
    already allowed in the `d is None` branch; it only removes the human from that
    one branch when the graded artifact positively vouches the SAME sid."""
    if not isinstance(vouch, dict):
        return False
    if any(not str(vouch.get(k) or "").strip() for k in _VOUCH_FIELDS):
        return False
    return str(vouch.get("operator_sid")).strip() == str(sid).strip()


def _assert_written_identity(sid: str, successor_session: str,
                             registry_agents: dict, warnings: list,
                             *, operator_asserted: bool = False,
                             mechanical_vouch=None) -> None:
    """B2 / STEP 10 — WRITTEN-IDENTITY assert (DEC-1787117309, gen-15's
    coherently-wrong finding): the three-store coherence assert passed on a
    value all stores agreed on that was NOT the successor's. Agreement was
    never the property; the independent signal is the transcript AT THE
    WRITTEN SID declaring the successor. Re-derived here from the transcript
    side (never a registry echo — A2.3), against the FINAL sid, whatever
    code path produced it. Defense-in-depth: today's resolver already
    enforces this; this assert survives every future path that sets a sid
    without it.

      exact declaration            -> pass, silent
      lineage-member declaration   -> pass, DISCLOSED (weaker basis)
      undeclared + operator sid    -> pass, DISCLOSED (the explicit escape
                                      hatch preserved — refusing it strands
                                      every undeclared lineage)
      undeclared, no operator      -> REFUSE (nothing vouches for the sid)
      declares a NON-lineage agent -> REFUSE (the crossing, at the write)
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sid_invariants as SI
    path = SI.find_transcript(sid)
    if not path:
        raise PromotionRefused(
            f"written-identity: sid {sid!r} has NO transcript in any project "
            f"dir at write time. NOTHING was written.")
    known = set(registry_agents) | {successor_session}
    d = SI.declared_identity(path, known)
    if d == successor_session:
        return
    if d is not None and SI.same_lineage(d, successor_session, registry_agents):
        warnings.append(
            f"written-identity: transcript at {sid} declares lineage member "
            f"{d!r}, not {successor_session!r} exactly — accepted on the "
            f"weaker basis, disclosed")
        return
    if d is None:
        # Stage C (DEC-1787687601): a MECHANICAL vouch (Stage-B identity_vouch,
        # complete + tied to THIS sid) stands in for the human operator-assertion
        # in this ONE branch (by-reference `Read /tmp/agent-init-*.md` init =>
        # transcript declares nothing). Positive-signal-only: a partial/forged/
        # sid-mismatched/missing vouch is NOT a vouch -> falls through to REFUSE.
        # This closes the declared_identity=None hole WITHOUT weakening the
        # primitive — it accepts nothing a human operator-assertion didn't already
        # accept here, it only removes the human when the graded artifact vouches
        # the SAME sid. The other branches (exact / lineage / non-lineage-crossing)
        # are untouched — a vouch can never rescue a CROSSED identity.
        if _valid_mechanical_vouch(mechanical_vouch, sid):
            warnings.append(
                f"written-identity: transcript at {sid} declares NOTHING — "
                f"accepted on a MECHANICAL identity-vouch (init_ref + operator_sid "
                f"matching + grade_linkage), no human operator needed, disclosed")
            return
        if operator_asserted:
            warnings.append(
                f"written-identity: transcript at {sid} declares NOTHING — "
                f"accepted solely on operator assertion, disclosed")
            return
        raise PromotionRefused(
            f"written-identity: transcript at {sid} declares NOTHING and no "
            f"operator asserted it — an unvouched sid must not become a "
            f"canonical identity. NOTHING was written.")
    raise PromotionRefused(
        f"written-identity: transcript at the written sid {sid} declares "
        f"{d!r} — NOT {successor_session!r} nor a lineage member. The stores "
        f"would have agreed on the wrong identity (gen-15's coherently-wrong "
        f"finding, STEP 10). NOTHING was written.")


def assert_stores_coherent(ids: dict) -> None:
    """Raise PromotionRefused unless all three stores name the SAME NON-NULL
    identity. gm's third-store finding (msg_a3e2a297): an absent/null field
    reads to a naive coherence check as 'no conflict' rather than 'no data' —
    `len(set(values)) != 1` PASSES on {None, None, None} because three nulls
    'agree'. no-data is never agreement. Pure + callable so the null-rejection
    is behaviourally testable, not an unreachable defensive branch."""
    vals = set(ids.values())
    if None in vals or "" in vals:
        raise PromotionRefused(
            f"CROSS-STORE IDENTITY INCOHERENT (no-data is not agreement): "
            f"{ids} — a store with a NULL/absent identity is missing data, "
            f"never 'no conflict'. NOTHING was written.")
    if len(vals) != 1:
        raise PromotionRefused(
            f"CROSS-STORE IDENTITY INCOHERENT: {ids} — an operation that "
            f"writes identity to three stores must prove they agree on ONE "
            f"non-null value before reporting success. NOTHING was written.")


def assert_conversation_path_names_sid(conversation_path, sid: str) -> None:
    """Raise PromotionRefused unless the sessions conversation_path names the
    resolved sid. The split-brain vector (the operator-routed gm gen-18 commission,
    2026-08-20): the sessions row is COPIED from the predecessor and only
    session_id is refreshed, so conversation_path silently keeps pointing at the
    DEAD predecessor transcript. `resolve_delivery_target` reads it, so a stale
    one routes live mail to a corpse. assert_stores_coherent guards only
    session_id — one member of the identity set standing for the set (law 16),
    which is why this needs its own guard rather than a wider tuple in that one.
    A null cp is permitted (the field may legitimately be unset on some rows);
    a PRESENT cp that names a DIFFERENT sid is the incoherence."""
    if conversation_path in (None, ""):
        return
    if sid and sid not in str(conversation_path):
        raise PromotionRefused(
            f"CONVERSATION_PATH / SID SPLIT-BRAIN: sessions conversation_path "
            f"{conversation_path!r} does not name the resolved sid {sid!r} — a "
            f"promotion that updates session_id but leaves conversation_path on "
            f"the predecessor routes live mail to a dead transcript. Derive "
            f"conversation_path from the sid. NOTHING was written.")


def _same_lineage_row(a: str, b: str, registry_agents: dict) -> bool:
    """Lineage relation via sid_invariants.same_lineage (parent is NOT identity,
    per 62b7a2da5) — used to scope the terminal-retire stamp to genuine
    predecessor archives, never an unrelated row that shares a sid by accident."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sid_invariants as SI
    return a == b or SI.same_lineage(a, b, registry_agents)


def _norm_runtime(runtime: str | None) -> str:
    """Normalize a declared runtime token for artifact resolution: agy ->
    gemini; anything falsy -> '' (unknown/undeclared)."""
    rt = (runtime or "").strip().lower()
    return "gemini" if rt == "agy" else rt


def _detector_path(sid: str, runtime: str | None = "claude") -> Path:
    """The predecessor's context detector file, written periodically by the
    session hook. PER-RUNTIME (spec §4.2): the artifact is namespaced by the
    seat's runtime — `/tmp/<runtime>-ctx-<sid>.json` carrying
    {used_pct, timestamp}. A claude seat resolves to the historical
    `/tmp/claude-ctx-<sid>.json` (byte-identical to before); a gemini seat to
    `/tmp/gemini-ctx-<sid>.json`. A MISSING detector is a VISIBLE unknown (the
    G7 gate REFUSES on it), never silently defaulted to claude's file. A
    function so tests can inject a scratch location (the /tmp path is real in
    production)."""
    rt = _norm_runtime(runtime) or "claude"
    return Path(f"/tmp/{rt}-ctx-{sid}.json")


class RuntimeRefused(ValueError):
    """A runtime is missing/unknown/non-resumable — refuse, never default."""


def resume_command_for(runtime: str, sid: str) -> str:
    """Build the resume command for a seat's runtime — PURE, and it RAISES on a
    missing/unknown/non-resumable runtime (spec §4.1). The historical inline
    branch (promote_successor.py:794-799) DEFAULTED to `claude --resume` when
    the runtime was missing; a Gemini seat resumed as claude is the silent
    seat-corruption "success that isn't" fleet defect. This kills that default:
    an unresolved runtime is refused loudly, never guessed.

      claude          -> claude --resume <sid> ...
      gemini / agy    -> agy --conversation <sid> ...
      codex           -> codex --yolo resume <sid>
      '' / None       -> RuntimeRefused (undeclared)
      service         -> RuntimeRefused (a process, not a resumable seat)
      other           -> RuntimeRefused
    """
    rt = (runtime or "").strip().lower()
    if not rt:
        raise RuntimeRefused(
            "resume_command_for: runtime is missing/empty — a missing runtime "
            "must REFUSE, never default to claude (spec §4.1)")
    if not sid:
        raise RuntimeRefused(
            f"resume_command_for: no session id for runtime {runtime!r}")
    if rt == "service":
        raise RuntimeRefused(
            "resume_command_for: runtime='service' is a process, not a "
            "resumable conversational seat (spec §4.1)")
    if rt == "claude":
        return f"claude --resume {sid} --dangerously-skip-permissions"
    if rt in ("gemini", "agy"):
        return f"agy --conversation {sid} --dangerously-skip-permissions"
    if rt == "codex":
        return f"codex --yolo resume {sid}"
    raise RuntimeRefused(
        f"resume_command_for: unknown runtime {runtime!r} has no "
        f"resume adapter — refuse, never default to claude (spec §4.1)")


def _transcript_search_runtimes(runtime: str | None) -> tuple:
    """Which provider transcript roots to search, resolved BY the seat's
    declared runtime (spec §4.2) — NO cross-provider precedence. A claude seat
    searches ~/.claude/projects only; a gemini seat searches the agy brain root
    only; a codex seat searches ~/.codex/sessions only. An UNDECLARED/unknown runtime
    (the un-backfilled legacy rows) falls back to the historical union so a legacy
    row is never stranded."""
    rt = _norm_runtime(runtime)
    if rt == "claude":
        return ("claude",)
    if rt == "gemini":
        return ("gemini",)
    if rt == "codex":
        return ("codex",)
    return ("claude", "gemini", "codex")   # undeclared/unknown: legacy union


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} [promote-successor] {msg}"
    print(line, file=sys.stderr)
    try:
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_strict(p: Path, what: str) -> dict:
    try:
        obj = json.loads(Path(p).read_text())
    except FileNotFoundError:
        raise PromotionRefused(f"{what} missing at {p} — refusing to promote "
                               f"from a blank surface")
    except (OSError, json.JSONDecodeError) as e:
        raise PromotionRefused(f"{what} unreadable at {p} ({e}) — refusing: a "
                               f"promotion must never overwrite state it "
                               f"cannot read")
    if not isinstance(obj, dict):
        raise PromotionRefused(f"{what} at {p} is not a JSON object — refusing")
    return obj


def _load_lenient(p: Path) -> dict:
    try:
        obj = json.loads(Path(p).read_text())
        return obj if isinstance(obj, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(p: Path, obj: dict) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, str(p))


def _cv4_observe_promote(triples):
    """CV4 W1 write-fence SHADOW routing (observe-only; NEVER alters/breaks the
    promote). W1 is THE 3-store rotation verb — highest blast radius — so this is
    called ONCE with all 3 store records as a group, IMMEDIATELY BEFORE the atomic
    commit block, so the three _atomic_write calls stay back-to-back and the
    atomic write ordering is not perturbed (a broken observe that aborted a promote
    would be catastrophic). Each observe_write RETURNS 1=logged / 0=failed /
    -1=disabled — we check the RETURN, not absence-of-exception (the W7 dead-except
    trap). rc==0 => ONE visible log line; the promote ALWAYS proceeds. The ENTIRE
    thing is wrapped so a broken/missing guard can never propagate into promote().
    Off-switches (kill-file + CV4_WRITE_FENCE_MODE env) live in write_fence.
    `triples` = [(store, key, record), ...]."""
    try:
        import importlib.util
        _wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "continuity", "write_fence.py")
        _spec = importlib.util.spec_from_file_location("cv4_write_fence", _wf_path)
        _wf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_wf)
        for store, key, record in triples:
            rc = _wf.observe_write(
                store=store, key=key,
                record=record if isinstance(record, dict) else {}, on_disk={},
                writer="promote-successor")
            if rc == 0:
                _log(f"CV4 W1 observe error (rate-limited): shadow write failed "
                     f"for {store}/{key} (promote proceeded)")
    except Exception as e:
        _log(f"CV4 W1 observe error (rate-limited): {e!r} (promote proceeded)")


def _pred_descriptor(canonical_id: str, pred: dict) -> str:
    from scripts.gen_resolve import resolve_predecessor_generation
    gen = resolve_predecessor_generation(
        canonical_id, pred.get("generation"), orchestra_dir=str(ORCHESTRA_DIR),
        alarm=lambda m: None)  # descriptor: accurate trace, no duplicate alarm
    return f"{canonical_id}(gen-{gen})" if gen is not None else canonical_id


def _cwd_to_project_dir(cwd: str) -> Path | None:
    if not cwd:
        return None
    p = os.path.expanduser(cwd).replace("/root/", str(Path.home()) + "/")
    return Path.home() / ".claude" / "projects" / p.replace("/", "-")


def _resolve_successor_sid(canonical_id: str, successor_session: str,
                           explicit_sid: str | None, registry_agents: dict):
    """Establish the SUCCESSOR'S identity from its OWN declaration.

    THE DEFECT THIS REPLACES (gm gen-14, msg_9364122b — hit live during its
    own promotion): `sid = session_id or sess_entry.get("session_id")`, where
    sess_entry is the CANONICAL id's existing row — i.e. the PREDECESSOR's.
    With successor rows at status=provisioning/session_id=None, promotion
    wrote the predecessor's sid as canonical. It inherited a FIELD instead of
    establishing WHO the successor is.

    Fourth plane of the orthogonality law: every guard we own inspects
    CONTENT (a field that contains a sid); almost none establishes
    AUTHORSHIP (whose declaration is it). Same law as INV6 and the
    save_index write gate: unverifiable is never a pass, so this REFUSES
    rather than guesses, and it NEVER falls back to the predecessor.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sid_invariants as SI

    known = set(registry_agents) | {successor_session, canonical_id}
    succ_row = registry_agents.get(successor_session) or {}

    def _declares_successor(path: str) -> bool:
        d = SI.declared_identity(path, known)
        if d is None:
            return False
        return (d == successor_session
                or SI.same_lineage(d, successor_session, registry_agents))

    if explicit_sid:
        path = SI.find_transcript(explicit_sid)
        if not path:
            raise PromotionRefused(
                f"--session-id {explicit_sid} resolves to NO transcript in any "
                f"project dir. Refusing rather than writing an identity that "
                f"cannot be resumed. NOTHING WAS WRITTEN.")
        d = SI.declared_identity(path, known)
        if d is not None and not _declares_successor(path):
            raise PromotionRefused(
                f"--session-id {explicit_sid} declares {d!r}, not "
                f"{successor_session!r} (nor a lineage sibling). This is the "
                f"crossing shape; refusing. NOTHING WAS WRITTEN.")
        return explicit_sid, ("declared" if d else "operator-asserted")

    # No explicit sid: find the transcript that DECLARES the successor.
    # §4.2 (all-model-parity): the transcript ROOTS are resolved BY the seat's
    # declared runtime — a claude seat searches ~/.claude/projects, a gemini
    # seat the agy brain root, with NO cross-provider precedence. Runtime is
    # read from the registry row (R1 backfill), never inferred from a pane. An
    # un-backfilled/unknown runtime keeps the historical union (claude, then
    # gemini) so a legacy row is never stranded.
    # Unified field-read (§4.1 R4(b)): read the `runtime` FIELD only — the
    # `provider` alias is dropped (redundant on every live row). Transcript-root
    # selection is a SEARCH heuristic, NOT the seat resume, so it stays field-only
    # (NO model-derivation): an un-backfilled row keeps the historical
    # claude→gemini UNION rather than narrowing to one root and stranding a
    # mis-placed transcript. Model-derivation belongs ONLY to the seat-resume
    # resolution below (resume_command_for), which delegates to resolve_runtime.
    succ_runtime = (_norm_runtime(succ_row.get("runtime"))
                    or _norm_runtime((registry_agents.get(canonical_id) or {})
                                     .get("runtime"))
                    or None)
    search = _transcript_search_runtimes(succ_runtime)

    # PRECEDENCE (defect #5, hit LIVE on gm gen-15→16, 2026-08-19): an EXACT
    # declaration (decl == successor name) beats any LINEAGE SIBLING,
    # unconditionally. A live predecessor declares the same lineage and
    # out-writes the newborn, so a recency sort over a sibling-inclusive pool
    # almost always hands the identity BACK to the predecessor — which the
    # consumption fix then faithfully writes to all three stores,
    # coherent-and-wrong. Recency may only order candidates AFTER ownership
    # is fixed. Siblings remain a FALLBACK for legitimate canonical-named
    # inits (no exact declaration exists anywhere).
    exact, sibling = [], []
    if "claude" in search:
        dirs = []
        pd = _cwd_to_project_dir(succ_row.get("cwd")
                                 or (registry_agents.get(canonical_id) or {}).get("cwd"))
        if pd and pd.is_dir():
            dirs.append(pd)
        root = Path.home() / ".claude" / "projects"
        dirs += [d for d in sorted(root.glob("*")) if d.is_dir() and d not in dirs]
        for d in dirs:
            for path in sorted(d.glob("*.jsonl")):
                decl = SI.declared_identity(str(path), known)
                if decl is None:
                    continue
                if decl == successor_session:
                    exact.append((SI.transcript_substance(str(path)), path))
                elif SI.same_lineage(decl, successor_session, registry_agents):
                    sibling.append((SI.transcript_substance(str(path)), path))
            if exact:
                break        # an exact declaration in the nearest dir wins outright

    # gemini brain: searched when the seat's runtime is gemini (no precedence),
    # or as the legacy-union fallback for an undeclared runtime (only if the
    # claude pass found no EXACT declaration — the historical behavior).
    gemini_brain = Path(os.environ.get("GEMINI_BRAIN_ROOT", os.path.expanduser("~/.gemini/antigravity-cli/brain")))
    if ("gemini" in search and not (exact and "claude" in search)
            and gemini_brain.is_dir()):
        for conv_dir in sorted(gemini_brain.glob("*")):
            for candidate in (conv_dir / ".system_generated" / "logs" / "transcript.jsonl", conv_dir / "transcript.jsonl"):
                if candidate.is_file():
                    decl = SI.declared_identity(str(candidate), known)
                    if decl is None:
                        continue
                    if decl == successor_session:
                        exact.append((SI.transcript_substance(str(candidate)), candidate))
                    elif SI.same_lineage(decl, successor_session, registry_agents):
                        sibling.append((SI.transcript_substance(str(candidate)), candidate))

    codex_sessions = Path(os.environ.get("CODEX_SESSIONS_ROOT", os.path.expanduser("~/.codex/sessions")))
    if ("codex" in search and not (exact and ("claude" in search or "gemini" in search))
            and codex_sessions.is_dir()):
        for candidate in sorted(codex_sessions.glob("**/*.jsonl")):
            if candidate.is_file():
                decl = SI.declared_identity(str(candidate), known)
                if decl is None:
                    continue
                if decl == successor_session:
                    exact.append((SI.transcript_substance(str(candidate)), candidate))
                elif SI.same_lineage(decl, successor_session, registry_agents):
                    sibling.append((SI.transcript_substance(str(candidate)), candidate))

    def _sid_from_path(p: Path) -> str:
        if p.name == "transcript.jsonl":
            if p.parent.name == "logs" and p.parent.parent.name == ".system_generated":
                return p.parent.parent.parent.name
            return p.parent.name
        return SI.session_uuid_from_stem(p.stem)

    pool = exact or sibling
    if pool:
        # substance is a GATE (a stub never wins while a real one exists);
        # recency then picks among the fixed-ownership pool
        non_stub = [f for f in pool if not f[0]["is_stub"]] or pool
        non_stub.sort(key=lambda f: f[1].stat().st_mtime, reverse=True)
        return (_sid_from_path(non_stub[0][1]),
                "declared" if exact else "declared-lineage-fallback")

    raise PromotionRefused(
        f"cannot establish {successor_session!r}'s OWN session identity: no "
        f"transcript in any project dir declares it. Refusing rather than "
        f"inheriting the predecessor's sid — that is how a promotion writes "
        f"the WRONG identity (gm msg_9364122b). Pass --session-id explicitly "
        f"if you can verify it yourself. NOTHING WAS WRITTEN.")


def _key1_evidence_gate(canonical_id: str, successor_session: str,
                        shadow_skip_reason: str | None) -> str:
    """KEY-1 EVIDENCE GATE (gm msg_e2bcc05f, the operator-found).

    A real rotation that leaves no Key-1 evidence must be IMPOSSIBLE, not
    merely discouraged — the shadow ledger sat at 0 rows through three
    rotations because the row lived in a supervisor's intentions.
    promote_successor is the single writer for canonical names, so it is the
    one place that KNOWS a rotation happened.

    Refuses BEFORE any write (nothing half-done). The escape hatch is a
    RECORDED skip reason, not a silent bypass — an audit trail, not an
    override. A stranded lineage must never be the price of bookkeeping,
    which is why this cannot deadlock a genuine emergency.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lineage_gate import shadow as SH
    rotation = f"{canonical_id} <- {successor_session}"
    if SH.has_evidence_for(rotation):
        return rotation
    if shadow_skip_reason:
        SH.append_row({"kind": "skip", "rotation": rotation,
                       "reason": shadow_skip_reason,
                       "recorded_by": "promote_successor"})
        return rotation
    raise PromotionRefused(
        f"KEY-1 EVIDENCE MISSING for rotation {rotation!r}: no shadow-ledger "
        f"row, and no --shadow-skip-reason given. Record the grader-vs-"
        f"supervisor verdicts (runbooks/lineage-gate-shadow-rotation.md), or "
        f"pass --shadow-skip-reason '<why this rotation is not a datum>'. "
        f"NOTHING WAS WRITTEN.")


def build_trigger_snapshot(canonical_id: str, *, now: float | None = None):
    """S1 HOLD-OPEN trigger snapshot (Piece 1, DEC-1787861488). Read the
    PREDECESSOR's detector NOW — at the moment the rotation decision fires and
    the hold opens — and, IFF it proves a fresh at-ceiling trigger, return the
    machine evidence dict the hold row records:

        {used_pct, detector_ts, detector_path, snapshot_at, predecessor_sid}

    G7 later validates this snapshot (re-read from the hold STORE, never the
    promoter's copy — C1) instead of demanding a same-turn detector re-read
    that a quiescent predecessor can never produce (the flagship's G7 TRIGGER
    STALE free-text override, now first-class).

    NEVER raises and NEVER fabricates: a missing/stale/sub-threshold detector
    returns None — the hold simply carries no evidence and the promote later
    requires the human override, exactly today's behavior. Positive-signal-only
    (the _valid_mechanical_vouch shape)."""
    t = time.time() if now is None else now
    try:
        reg = _load_strict(REGISTRY, "registry.json")
    except PromotionRefused:
        return None
    pred = (reg.get("agents", {}) or {}).get(canonical_id) or {}
    pred_sid = pred.get("session_id")
    if not pred_sid:
        return None
    pred_runtime = _norm_runtime(pred.get("runtime")) or None
    path = _detector_path(pred_sid, pred_runtime)
    try:
        det = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    ts = det.get("timestamp")
    used = det.get("used_pct")
    if not isinstance(ts, (int, float)) or (t - ts) > DETECTOR_STALE_S:
        return None
    if not isinstance(used, (int, float)) or used < ROTATION_TRIGGER_PCT:
        return None
    return {"used_pct": used, "detector_ts": float(ts),
            "detector_path": str(path), "snapshot_at": t,
            "predecessor_sid": pred_sid}


_EVIDENCE_FIELDS = ("used_pct", "detector_ts", "detector_path",
                    "snapshot_at", "predecessor_sid")


def _validate_trigger_evidence(canonical_id: str, successor_session: str,
                               passed: dict, warnings: list) -> None:
    """G7 evidence validation (Piece 1, peer conditions C1+C2 BINDING).

    C1 — the promoter-passed dict is NEVER the authority: the hold row is
    RE-READ from the fleet-beat store and its recorded snapshot is what gets
    validated; a passed dict that differs from the store is a forgery surface
    (the promoter's argv) and refuses. C2 — the evidence is VOID unless the
    registry predecessor sid at promote == the sid recorded at snapshot AND
    the hold is still unresolved AND the snapshot is within the hard
    TRIGGER_EVIDENCE_MAX_AGE_S ceiling (beyond it, the human override is
    required). Every refusal is pre-write."""
    try:
        state = json.loads(Path(FLEET_BEAT_STATE).read_text())
    except (OSError, ValueError) as e:
        raise PromotionRefused(
            f"G7 EVIDENCE UNVERIFIABLE: hold store {FLEET_BEAT_STATE} is "
            f"unreadable ({e}) — promoter-passed evidence is never trusted on "
            f"its own (C1); unverifiable is never a pass. "
            f"--rotation-trigger-override records an emergency reseat. "
            f"NOTHING was written.")
    holds = ((state.get("ledger") or {}).get("holds")) or []
    open_rows = [h for h in holds if isinstance(h, dict)
                 and h.get("canary") == canonical_id
                 and h.get("successor") == successor_session
                 and not h.get("resolved")]
    if not open_rows:
        raise PromotionRefused(
            f"G7 EVIDENCE VOID: no OPEN hold row for "
            f"{canonical_id!r} <- {successor_session!r} in {FLEET_BEAT_STATE} "
            f"— a resolved or absent hold cannot vouch a promote (C2), and "
            f"promoter-passed evidence alone is never trusted (C1). "
            f"--rotation-trigger-override records an emergency reseat. "
            f"NOTHING was written.")
    ev = open_rows[-1].get("trigger_evidence")
    if not isinstance(ev, dict):
        raise PromotionRefused(
            f"G7 EVIDENCE VOID: the open hold row for {canonical_id!r} <- "
            f"{successor_session!r} carries NO trigger_evidence — S1 never "
            f"recorded a verified snapshot into the store, so there is nothing "
            f"machine-checkable to promote on (C1). "
            f"--rotation-trigger-override records an emergency reseat. "
            f"NOTHING was written.")
    if any(passed.get(k) != ev.get(k) for k in _EVIDENCE_FIELDS):
        raise PromotionRefused(
            f"G7 EVIDENCE FORGED: promoter-passed trigger_evidence does not "
            f"match the STORE row for {canonical_id!r} <- "
            f"{successor_session!r} (C1 — the store is authoritative; a "
            f"promoter-invented dict is the argv forgery surface). "
            f"NOTHING was written.")
    snap_at, det_ts, used = (ev.get("snapshot_at"), ev.get("detector_ts"),
                             ev.get("used_pct"))
    if not all(isinstance(x, (int, float)) for x in (snap_at, det_ts, used)):
        raise PromotionRefused(
            f"G7 EVIDENCE MALFORMED: store snapshot for {canonical_id!r} has "
            f"non-numeric used_pct/detector_ts/snapshot_at — unverifiable is "
            f"never a pass. NOTHING was written.")
    age = time.time() - snap_at
    if age > TRIGGER_EVIDENCE_MAX_AGE_S:
        raise PromotionRefused(
            f"G7 EVIDENCE EXPIRED: hold-open snapshot for {canonical_id!r} is "
            f"{int(age)}s old (hard ceiling {TRIGGER_EVIDENCE_MAX_AGE_S}s, "
            f"C2) — machine evidence does not live forever; past the ceiling "
            f"the HUMAN --rotation-trigger-override is required. "
            f"NOTHING was written.")
    if snap_at - det_ts > DETECTOR_STALE_S:
        raise PromotionRefused(
            f"G7 EVIDENCE STALE-AT-SNAPSHOT: detector was "
            f"{int(snap_at - det_ts)}s old when the S1 snapshot was taken "
            f"(max {DETECTOR_STALE_S}s) — S1 never verified a fresh trigger, "
            f"so the snapshot proves a PAST turn, not the hold-open one. "
            f"NOTHING was written.")
    if used < ROTATION_TRIGGER_PCT:
        raise PromotionRefused(
            f"G7 EVIDENCE SUB-THRESHOLD: store snapshot used_pct={used} < "
            f"{ROTATION_TRIGGER_PCT} for {canonical_id!r} — the seat was "
            f"never at its ceiling (the g15 shape, machine-checked). "
            f"NOTHING was written.")
    reg = _load_strict(REGISTRY, "registry.json")
    pred_sid_now = ((reg.get("agents", {}) or {}).get(canonical_id)
                    or {}).get("session_id")
    if pred_sid_now != ev.get("predecessor_sid"):
        raise PromotionRefused(
            f"G7 EVIDENCE SID-CHANGED: registry predecessor sid for "
            f"{canonical_id!r} is now {pred_sid_now!r} but the snapshot was "
            f"taken against {ev.get('predecessor_sid')!r} (C2) — the seat "
            f"re-seated since hold-open; the old snapshot vouches a DIFFERENT "
            f"occupant. NOTHING was written.")
    warnings.append(
        f"G7 trigger met via HOLD-OPEN evidence for {canonical_id!r}: "
        f"used_pct={used} (>= {ROTATION_TRIGGER_PCT}) verified fresh at "
        f"snapshot_at={int(snap_at)} ({int(age)}s ago, ceiling "
        f"{TRIGGER_EVIDENCE_MAX_AGE_S}s), predecessor sid unchanged, hold "
        f"unresolved — store-re-read (C1), never the promoter's copy")


def _rotation_trigger_gate(canonical_id: str, override_reason: str | None,
                           warnings: list, *,
                           successor_session: str | None = None,
                           trigger_evidence: dict | None = None) -> None:
    """G7 — THE FIRST PRECONDITION of a promotion (the operator order 2026-08-19,
    spec G7). Read the PREDECESSOR's detector file and confirm used_pct >=
    ROTATION_TRIGGER_PCT before any execution detail. gm's premature g15
    promotion ran on ob's WORD ("I'm at 86") while its detector read 45 — a
    predecessor's self-report is a CLAIM TO CHECK, not the trigger.

    Refuses (before any write) on: a live predecessor whose detector is
    missing, unreadable, STALE, or below threshold. Exempt: a FIRST-seat
    promotion (canonical has no live predecessor sid) is an initial seat, not
    a rotation-at-ceiling — the gate must not deadlock a fresh install.
    Override: override_reason (the operator-explicit --rotation-trigger-override),
    recorded in warnings, never silent — the T4/Key-1 waiver shape."""
    # Override short-circuits BEFORE any store read — an emergency reseat must
    # not depend on the detector OR the registry being readable.
    if override_reason:
        warnings.append(
            f"G7 ROTATION-TRIGGER OVERRIDE for {canonical_id!r}: "
            f"{override_reason} — predecessor detector NOT required to be "
            f">= {ROTATION_TRIGGER_PCT}. Recorded, never silent.")
        return
    # Piece 1 (DEC-1787861488): structured hold-open evidence — the completion
    # daemon's machine path. Validated against the STORE row (C1/C2), never the
    # passed dict; on a valid snapshot the live-detector re-read below is
    # correctly SKIPPED (a quiescent predecessor's detector will never
    # re-stamp, and its used_pct cannot move). trigger_evidence=None keeps the
    # hand-driven live-detector path below byte-identical.
    if trigger_evidence is not None:
        _validate_trigger_evidence(canonical_id, successor_session or "",
                                   trigger_evidence, warnings)
        return
    reg = _load_strict(REGISTRY, "registry.json")
    pred = (reg.get("agents", {}) or {}).get(canonical_id) or {}
    pred_sid = pred.get("session_id")
    if not pred_sid:
        warnings.append(
            f"G7: no live predecessor sid for {canonical_id!r} — first-seat "
            f"promotion, not a rotation-at-ceiling; trigger gate not applied")
        return
    # PER-RUNTIME detector (spec §4.2): resolve the detector artifact by the
    # predecessor's DECLARED runtime, not a hardcoded claude path — a gemini
    # seat's detector lives at /tmp/gemini-ctx-<sid>.json. A missing detector
    # stays a VISIBLE unknown (this gate refuses on it below), never silently
    # read from claude's file.
    # Unified field-read (§4.1 R4(b)): read the `runtime` FIELD only, provider
    # alias dropped. Field-only (no model-derivation) — _detector_path maps an
    # empty/None runtime to claude's file exactly as the prior `... or "claude"`
    # did (behavior-preserving); the detector's ABSENCE is what this gate refuses
    # on below, not the runtime value.
    pred_runtime = _norm_runtime(pred.get("runtime")) or None
    path = _detector_path(pred_sid, pred_runtime)
    try:
        det = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise PromotionRefused(
            f"G7 TRIGGER UNVERIFIABLE: predecessor {canonical_id!r} (sid "
            f"{pred_sid}) has NO detector at {path}. A rotation's FIRST "
            f"precondition is a detector proving used_pct >= "
            f"{ROTATION_TRIGGER_PCT}; a self-reported number is a claim to "
            f"check, not the trigger. --rotation-trigger-override records an "
            f"emergency reseat. NOTHING was written.")
    except (OSError, ValueError) as e:
        raise PromotionRefused(
            f"G7 TRIGGER UNVERIFIABLE: predecessor detector at {path} is "
            f"unreadable ({e}) — unverifiable is never a pass. NOTHING was "
            f"written.")
    ts = det.get("timestamp")
    age = (time.time() - ts) if isinstance(ts, (int, float)) else None
    if age is None or age > DETECTOR_STALE_S:
        raise PromotionRefused(
            f"G7 TRIGGER STALE: predecessor {canonical_id!r} detector at "
            f"{path} is {'undated' if age is None else f'{int(age)}s old'} "
            f"(max {DETECTOR_STALE_S}s) — a stale detector is a claim about a "
            f"PAST turn, not this one. Re-read the detector in the same turn "
            f"or --rotation-trigger-override. NOTHING was written.")
    used = det.get("used_pct")
    if not isinstance(used, (int, float)) or used < ROTATION_TRIGGER_PCT:
        raise PromotionRefused(
            f"G7 TRIGGER NOT MET: predecessor {canonical_id!r} detector "
            f"used_pct={used} < {ROTATION_TRIGGER_PCT} at {path}. The seat is "
            f"NOT at its ceiling; this rotation is premature (the g15 shape: "
            f"promoted on a word of 86 while the detector read 45). "
            f"--rotation-trigger-override for an emergency reseat. NOTHING "
            f"was written.")
    warnings.append(f"G7 trigger met: predecessor {canonical_id!r} at "
                    f"used_pct={used} (>= {ROTATION_TRIGGER_PCT}), "
                    f"detector {int(age)}s fresh")


def _readback_gate(successor_session: str, waived_reason: str | None,
                   warnings: list) -> str | None:
    """T4 READBACK ENFORCEMENT (gm msg_4d5d5b9b, CAPTURE #4).

    promote() refuses unless a COMMITTED readback exists for the successor at
    the pinned path — existence, non-triviality, and a commit. The gate that
    defines the operator's ordering (T4: successor absorbs BEFORE the seat moves) had
    zero enforcement: this lane's own g1->g2 promotion completed while the
    successor was still composing its readback, and nothing refused
    (fixtures/lineage/readback-absence-specimen.json).

    Non-optional requirements (each from a live error):
      1. PIN the path — resolved from the successor id, one location.
      2. Every refusal PRINTS the path it looked at — a refusal that hides
         its evidence repeats the confident absence-accusation (gm's own,
         retracted msg_21e9ca44) with the verb's authority behind it.
      3. Tree-independent — `git log --all` over the shared object store, so
         a readback committed on a branch from a worktree (this lane's own
         rotation shape) never reads as missing.
    Waiver: a RECORDED reason, never silent (the key1 skip contract's shape).
    Existence-tier only: quality is T8's and belongs to a grader.
    Returns the vouching commit hash, or None when waived.
    """
    canonical = _readback_rel_path(successor_session)
    legacy = _readback_legacy_rel_path(successor_session)
    if waived_reason:
        warnings.append(
            f"T4 READBACK WAIVED for {successor_session!r}: {waived_reason} "
            f"— pinned path NOT verified: {canonical}")
        return None

    def _git(*args):
        return subprocess.run(["git", "-C", str(ORCHESTRA_DIR), *args],
                              capture_output=True, text=True)

    if _git("rev-parse", "--git-dir").returncode != 0:
        raise PromotionRefused(
            f"T4 READBACK UNVERIFIABLE for {successor_session!r}: looked at "
            f"{canonical} in {ORCHESTRA_DIR}, which is not a git repository — "
            f"a commit cannot be proven there, and unverifiable is never a "
            f"pass (INV6). --readback-waived-reason records an emergency "
            f"bypass. NOTHING was written.")

    def _latest_present(rel):
        # Most recent commit on ANY ref touching rel; worktrees share the
        # object store and refs, so a worktree-branch commit is found from
        # here. (NOT --diff-filter=d: verified by effect on git 2.43 that a
        # lowercase exclusion filter + pathspec returns NOTHING for a plainly
        # committed file — the must-not-fire fixture caught it. Deletion is
        # handled via `git show` failing instead.)
        rev = _git("log", "--all", "-n", "1", "--format=%H", "--", rel
                   ).stdout.strip()
        if not rev:
            return None, None
        show = _git("show", f"{rev}:{rel}")
        if show.returncode != 0:
            return rev, None          # last touch DELETED it
        return rev, show.stdout

    # Migration-era search order (gm ruling msg_91fdc1d7): canonical first;
    # the bifurcated legacy convention is ACCEPTED with a warning, never
    # refused, until gm flips to refuse-on-absent post-migration. BOTH probes
    # are kept (guard amendment msg_86068add): a refusal must explain each
    # path's TRUE state — discarding the legacy probe made the message claim
    # "no commit touches either" while two commits touched the legacy path.
    can_rev, can_content = _latest_present(canonical)
    leg_rev, leg_content = _latest_present(legacy)
    if can_content is not None:
        rel, rev, content = canonical, can_rev, can_content
    elif leg_content is not None:
        rel, rev, content = legacy, leg_rev, leg_content
        warnings.append(
            f"T4 readback found at NON-CANONICAL legacy path {legacy} "
            f"(@ {rev[:12]}) — accepted during migration (gm ruling "
            f"msg_91fdc1d7); migrate it to the canonical {canonical}")
    else:
        rel = rev = content = None

    if content is None:
        def _path_detail(path, prv):
            # prv = the probe's rev: set with content None means the last
            # commit touching `path` DELETED it; None means untouched.
            if prv:
                return f"{path}: committed then DELETED at {prv[:12]}"
            if (ORCHESTRA_DIR / path).exists():
                return (f"{path}: EXISTS on disk but is UNCOMMITTED — T4 "
                        f"requires a commit")
            return f"{path}: no commit on any ref touches it"
        details = "; ".join((_path_detail(canonical, can_rev),
                             _path_detail(legacy, leg_rev)))
        raise PromotionRefused(
            f"T4 READBACK MISSING for {successor_session!r}: looked across "
            f"ALL refs of {ORCHESTRA_DIR} (branches and worktrees included) — "
            f"{details}. The absorption gate (the operator's T4) has no readable "
            f"artifact — the successor must write and COMMIT its readback "
            f"first. --readback-waived-reason records an emergency bypass, "
            f"never silent. NOTHING was written.")
    nonblank = sum(1 for ln in content.splitlines() if ln.strip())
    if nonblank < READBACK_MIN_NONBLANK:
        # T4 SECOND LEG: a real STRICT-PASS comprehension grade outranks the
        # line-count proxy — two gates must not disagree about the SAME readback
        # (the citation grader strict-PASSed it; the line floor must not then
        # refuse it). The floor STAYS for the no-artifact path below.
        if _has_strict_pass_comprehension(successor_session):
            warnings.append(
                f"T4 readback SHORT ({nonblank} non-blank lines < floor "
                f"{READBACK_MIN_NONBLANK}) but a STRICT-PASS comprehension grade "
                f"(rotation_gate_manual) vouches absorption for "
                f"{successor_session!r} — accepted (a real strict grade outranks "
                f"a line-count proxy): {rel} @ {rev[:12]}")
            return rev
        raise PromotionRefused(
            f"T4 READBACK TRIVIAL for {successor_session!r}: {rel} at commit "
            f"{rev[:12]} has {nonblank} non-blank lines "
            f"(floor {READBACK_MIN_NONBLANK}) and NO strict-PASS comprehension "
            f"artifact ({_comprehension_rel_path(successor_session)}) vouches "
            f"it — a committed stub is not absorption evidence. NOTHING was "
            f"written.")
    warnings.append(f"T4 readback verified: {rel} @ {rev[:12]} "
                    f"({nonblank} non-blank lines)")
    return rev


def _create_predecessor_archive(agents, meta, pred_sid, pred_reg, pred_sess,
                                canonical_id, now, warnings):
    """gm commission 4e20a7516: in the canonical-IS-predecessor shape the verb
    overwrote the canonical row (which HELD pred_sid) and no separate row
    archived it — pred_sid would be claimed by ZERO rows (unresumable, the
    hand-restore gm did for gm-gen18 + ob-gen14). Create the <canon>-gen<N-1>
    archive.

    EXISTENCE-based trigger (gm refinement #1, NOT loop-empty): fire ONLY if
    pred_sid is claimed by no row in EITHER store. That single predicate gives
    idempotency (a prior archive already holds it) AND the alias-shape must-not-
    fire (a pre-existing alias holds it) for free — the stamp loop `continue`s on
    succeeded_by, so a loop-empty trigger would double-create on re-promote.

    FAIL-CLOSED: refuse the whole promotion (nothing written) if a coherent
    archive cannot be built — a promotion that loses its rollback IS the defect.
    Returns the archive key, or None if no archive was needed."""
    if not pred_sid:
        return None
    claimed = (any(isinstance(v, dict) and v.get("session_id") == pred_sid
                   for v in agents.values())
               or any(isinstance(v, dict) and v.get("session_id") == pred_sid
                      for v in meta.values()))
    if claimed:
        return None            # already archived (idempotent) or alias shape
    from scripts.gen_resolve import resolve_predecessor_generation
    pred_gen = resolve_predecessor_generation(
        canonical_id, pred_reg.get("generation"),
        orchestra_dir=str(ORCHESTRA_DIR))  # DEC-1788483120: DB-first (RED 1)
    if not isinstance(pred_gen, int):
        raise PromotionRefused(
            f"predecessor-archive FAIL-CLOSED: cannot derive a coherent archive "
            f"generation for predecessor sid {pred_sid} (its row has no integer "
            f"generation: {pred_gen!r}). A rotation must not lose its rollback — "
            f"restore the predecessor's generation on {canonical_id!r} and re-run. "
            f"NOTHING was written.")
    archive_key = f"{canonical_id}-gen{pred_gen}"
    existing = agents.get(archive_key)
    if isinstance(existing, dict) and existing.get("session_id") not in (None, pred_sid):
        raise PromotionRefused(
            f"predecessor-archive FAIL-CLOSED: archive key {archive_key!r} already "
            f"exists holding a DIFFERENT sid {existing.get('session_id')!r} (not the "
            f"predecessor sid {pred_sid}). Creating it would clobber another "
            f"identity — resolve the collision by hand and re-run. NOTHING was written.")
    # conversation_path: derive from pred_sid (INV3 derivation), preserved from
    # the predecessor's own sessions row; NEVER let it name the successor sid.
    _tp = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sid_invariants as _SI
        _tp = _SI.find_transcript(pred_sid)
    except Exception:
        _tp = None
    cp = str(_tp) if _tp else pred_sess.get("conversation_path")
    if cp and pred_sid not in str(cp):
        cp = None              # never let the archive cp name a different sid
    resume = pred_sess.get("resume_command") or pred_reg.get("resume_command")
    project_dir = (str(Path(cp).parent) if cp
                   else pred_sess.get("project_dir") or pred_reg.get("project_dir"))
    name = pred_reg.get("name") or f"{canonical_id} gen{pred_gen} (quiescent predecessor)"
    model = pred_reg.get("model")
    sid_source = pred_reg.get("sid_source") or pred_sess.get("sid_source") or "declared"
    agents[archive_key] = {
        "id": archive_key, "name": name,
        "tier": pred_reg.get("tier"), "machine": pred_reg.get("machine", "vps"),
        "tmux_session": archive_key, "cwd": pred_reg.get("cwd"),
        "status": "parked", "model": model,   # enum (item b): archived predecessor = parked (no live proc, resumable)
        "session_id": pred_sid, "resume_command": resume,
        "generation": pred_gen,
        "lineage_root": pred_reg.get("lineage_root") or canonical_id,
        "parent": canonical_id, "always_on": False,
        "succeeded_by": canonical_id, "superseded_at": now,
        "sid_source": sid_source,
    }
    arch_sess = {
        "status": "parked", "resumable": True, "session_id": pred_sid,
        "generation": pred_gen, "model": model, "resume_command": resume,
        "tmux_session": archive_key, "name": name,
        "succeeded_by": canonical_id, "superseded_at": now, "always_on": False,
        "last_active": now, "sid_source": sid_source,
    }
    if cp:
        arch_sess["conversation_path"] = cp
    if project_dir:
        arch_sess["project_dir"] = project_dir
    meta[archive_key] = arch_sess
    warnings.append(
        f"predecessor-archive CREATED: {archive_key!r} holds predecessor sid "
        f"{pred_sid} (quiescent, resumable, resume PRESERVED, always_on=False, "
        f"succeeded_by={canonical_id!r}) — canonical-IS-predecessor shape, no "
        f"existing row archived it")
    return archive_key


def derive_successor_generation(canonical_id, pred, *, explicit=None,
                                alias_value=None):
    """DEC-1788483120 (RED 7/9): successor generation, DB-first.

    Order: explicit generation= arg > alias row value > resolved predecessor
    generation (gen_resolve: DB primary, flat fallback, alarm on skew) + 1.
    Returns None when nothing authoritative exists (caller fail-closes) —
    NEVER fabricates. The complete.py:780 autonomous promote omits
    generation=, so this is the armed-path seam."""
    if isinstance(explicit, int):
        return explicit
    if isinstance(alias_value, int):
        return alias_value
    from scripts.gen_resolve import resolve_predecessor_generation
    pred_gen = resolve_predecessor_generation(
        canonical_id, pred.get("generation") if isinstance(pred, dict) else None,
        orchestra_dir=str(ORCHESTRA_DIR))
    return pred_gen + 1 if isinstance(pred_gen, int) else None


def promote(canonical_id: str, successor_session: str, *,
            session_id: str | None = None, model: str | None = None,
            generation: int | None = None, resume_command: str | None = None,
            dry_run: bool = False, shadow_skip_reason: str | None = None,
            require_key1_evidence: bool = True,
            readback_waived_reason: str | None = None,
            rotation_trigger_override: str | None = None,
            trigger_evidence: dict | None = None,
            rotation_store=None, rotation_id: str | None = None) -> dict:
    """Atomically promote `successor_session` to the canonical `canonical_id`.

    Returns a report dict naming every surface touched. Raises
    PromotionRefused (before any write) if the merged registry row would be
    half-empty, a surface is unreadable, or Key-1 evidence is missing.
    """
    # canonical-IS-successor is FORBIDDEN (gm ruling, 2026-08-21): one blessed
    # rotation path only. A seat may never be promoted into itself in place —
    # spawn the successor as its OWN alias (declaring its identity in transcript
    # entry 0), then promote that alias. An in-place blank seat is unverifiable
    # (gotcha-9: a session that declares nothing must be re-spawned, never
    # promoted in place). Structural precondition — refuses even under dry_run.
    if successor_session == canonical_id:
        raise PromotionRefused(
            f"in-place promotion FORBIDDEN: successor_session == canonical_id "
            f"({canonical_id!r}). The blessed rotation path is: spawn the "
            f"successor as a NEW alias via spawn-agent.sh so it declares its own "
            f"identity in transcript entry 0, THEN promote that alias. NOTHING "
            f"was written.")
    now = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []
    # G7 — THE FIRST PRECONDITION (the operator order): the predecessor's detector must
    # prove it is at its ceiling before ANY execution detail is touched. Before
    # key1 and T4 by design — a rotation that should not have started must be
    # refused on the trigger, not on a downstream gate.
    if not dry_run:
        _rotation_trigger_gate(canonical_id, rotation_trigger_override,
                               warnings, successor_session=successor_session,
                               trigger_evidence=trigger_evidence)
    if require_key1_evidence and not dry_run:
        _key1_evidence_gate(canonical_id, successor_session,
                            shadow_skip_reason)
    # T4 gate: BEFORE the lock and every write, dry-run exempt like key1
    # (a planning call must stay usable mid-rotation, before the readback
    # exists). A refusal here honours "NOTHING was written" by construction.
    if not dry_run:
        _readback_gate(successor_session, readback_waived_reason, warnings)

    with registry_lock(lock_path=LOCK_FILE):
        # ---- READ + COMPUTE everything first; write only after validation ----
        reg = _load_strict(REGISTRY, "registry.json")
        agents = reg.setdefault("agents", {})
        pred = agents.get(canonical_id) or {}
        # The predecessor's OLD sid, captured BEFORE new_entry overwrites it —
        # the terminal-retire step stamps its archive row superseded so
        # park-idle can reach the retire path (auditor diagnosis: a row with
        # succeeded_by=None is invisible to park-idle's superseded-safe path).
        pred_sid_before = pred.get("session_id")
        # Predecessor snapshot for the archive-CREATION path, captured BEFORE
        # new_entry overwrites the canonical row (gm commission 4e20a7516):
        # resume_command / generation / tier / lineage must be the PRESERVED
        # predecessor values, never reconstructed.
        pred_reg_snapshot = dict(pred)
        alias = agents.get(successor_session) if successor_session != canonical_id else None
        alias = alias if isinstance(alias, dict) else {}

        # THE CONSUMPTION (DEC-1787117309 A2.1 — root cause of the 08-19
        # double half-promotion, reproduced on BOTH canonical lineages):
        # the resolver used to run AFTER this row was finalized, and its
        # result was consumed by the sessions write ONLY — so the registry
        # row inherited the PREDECESSOR's session_id + resume_command from
        # the pred.items() copy below and the verb exited 0. Resolve FIRST;
        # the registry row consumes the resolved sid UNCONDITIONALLY. An
        # explicit --session-id reaches the resolver as its INPUT (validated
        # there against the transcript's own declaration), never as a bypass.
        sid, sid_source = _resolve_successor_sid(
            canonical_id, successor_session, session_id, agents)
        warnings.append(f"successor sid {sid} established by {sid_source} "
                        f"(never inherited from the predecessor row)")
        rc = resume_command
        if not rc and sid:
            # §4.1 refuse-on-missing-runtime: the historical branch defaulted
            # to `claude --resume` for ANY non-gemini value incl. a MISSING
            # runtime — a Gemini seat resumed as claude is the silent
            # seat-corruption fleet defect. Resolve the seat's runtime from the
            # DECLARED field first (R1 backfill); a service/codex row is
            # refused (not a Phase-1 resumable seat). An UNDECLARED row (the
            # 113 un-backfilled live rows) is inferred from its model — this is
            # NOT the hazard: a Gemini seat ALWAYS carries an explicit
            # runtime='gemini', so gemini can never fall through to claude.
            # §4.1 refuse-on-missing-runtime, UNIFIED field-read (§4.1 R4(b)):
            # the seat-resume runtime is resolved by the ONE shared resolver
            # (the SAME object spawn dispatch uses), replacing the old inline
            # `runtime|provider` chain — the `provider` alias is DROPPED (every
            # live row's provider is redundant with runtime). alias wins over
            # pred; resolve_runtime does field → POSITIVE-model-derive → REFUSE
            # zero-signal (the R1b-legal rule). A Gemini seat ALWAYS carries an
            # explicit runtime='gemini' so it can never fall through to claude;
            # a zero-signal seat REFUSES rather than resume a live Gemini pane as
            # claude (the §4.1 corruption).
            merged_rt = {"runtime": alias.get("runtime") or pred.get("runtime"),
                         "model": alias.get("model") or pred.get("model")}
            try:
                succ_runtime = resolve_runtime(merged_rt, agent_id=canonical_id,
                                               strict=True)
            except RuntimeResolutionError as e:
                raise PromotionRefused(
                    f"REFUSED promotion of {successor_session} -> {canonical_id}: "
                    f"{e}. Declare the seat's runtime via the locked R1b path "
                    f"(scripts/backfill_registry_runtime.py --declare <map.json> "
                    f"--registry <live-registry> --apply) then re-run. NOTHING "
                    f"was written.")
            if succ_runtime == "service":
                raise PromotionRefused(
                    f"REFUSED promotion of {successor_session} -> {canonical_id}: "
                    f"seat runtime={succ_runtime!r} is not a resumable "
                    f"seat (a service is a process) "
                    f"— a seat verb must REFUSE such a target, never default it "
                    f"to claude (spec §4.1). NOTHING was written.")
            try:
                rc = resume_command_for(succ_runtime, sid)
            except RuntimeRefused as e:
                raise PromotionRefused(
                    f"REFUSED promotion of {successor_session} -> {canonical_id}: "
                    f"{e}. NOTHING was written.")

        # registry: new canonical row = predecessor's full field set + overrides
        new_entry = {k: v for k, v in pred.items() if k not in _DROP_ON_PROMOTE}
        # DEC-1788483120: DB-first successor-gen derivation (RED 7/9) — the
        # autonomous complete.py promote omits generation=, and the flat pred
        # row may be a stale/minimal projection; never derive off flat alone.
        gen = derive_successor_generation(
            canonical_id, pred, explicit=generation,
            alias_value=alias.get("generation"))
        overrides = {
            "id": canonical_id,
            "tmux_session": canonical_id,
            "status": "online",
            "promoted_at": now,
            "promoted_by": "promote_successor",
            # UNCONDITIONAL — resolved, never inherited (the missed
            # consumption that made "promoted" mean "promoted somewhere")
            "session_id": sid,
            "resume_command": rc,
            # guard-required (f8642e5a5): HOW identity was established is
            # durable on the row — a 'declared-lineage-fallback' resolution
            # must never be indistinguishable from an exact declaration
            "sid_source": sid_source,
        }
        if pred:
            overrides["handoff_from"] = _pred_descriptor(canonical_id, pred)
        if gen is not None:
            overrides["generation"] = gen
        if model:
            overrides["model"] = model
        new_entry.update(overrides)
        new_entry.setdefault("name", pred.get("name") or alias.get("name"))

        missing = [f for f in REQUIRED_FIELDS
                   if f not in new_entry or new_entry[f] in (None, "")]
        if missing:
            raise PromotionRefused(
                f"REFUSED promotion of {successor_session} -> {canonical_id}: "
                f"merged registry row is missing required fields {missing} "
                f"(predecessor row gutted?). A half-empty canonical row is "
                f"exactly the gen-9→10 failure — fix the predecessor entry or "
                f"pass complete fields. NOTHING was written.")

        # retire leftover successor-alias rows pointing at the same identity
        aliases_retired = []
        for key, e in agents.items():
            if key == canonical_id or not isinstance(e, dict):
                continue
            if e.get("status") == "retired":
                continue
            if key == successor_session or e.get("tmux_session") == successor_session:
                e["status"] = "retired"
                e["retired_at"] = now
                # A demoted row is by definition NOT a T0 always-on seat; leaving
                # always_on absent/True makes pulse re-escalate a Level-A identity
                # invariant every cycle (gm gen-19 residue, 2026-08-21).
                e["always_on"] = False
                e["retired_note"] = (f"alias row superseded by atomic promotion "
                                     f"to canonical '{canonical_id}' (gen {gen})")
                # DELTA E.6 (gm-gen16, measured 07:30Z): the generational
                # registry row must NOT keep claiming the successor's sid —
                # two claimants at commit is the exact 'ambiguous, cleared
                # from all' input that let the */10 scan destroy the fleet's
                # first coherent promotion and freeze the index for 440
                # agents. Exactly one row claims a session the moment
                # promote() returns.
                e["session_id"] = None
                e["resume_command"] = None
                aliases_retired.append(key)

        # TERMINAL-RETIRE FOUNDATION (the operator directive 2026-08-19): stamp the
        # predecessor's ARCHIVE row succeeded_by=<canonical> so it becomes
        # reachable to park-idle's superseded-safe retire path. The archive is
        # a DIFFERENT row from the retired aliases: an in-lineage member still
        # holding the predecessor's OLD sid (the gm-gen16 shape). Without this
        # it keeps succeeded_by=None and park-idle's successor(entry)=None path
        # leaves it holding RAM forever (the pile-up gm swept by hand). Narrow
        # by construction: sid-match AND same-lineage AND not the canonical row
        # nor an alias this promotion already retired.
        predecessors_marked = []
        if pred_sid_before:
            for key, e in agents.items():
                if key == canonical_id or key in aliases_retired:
                    continue
                if not isinstance(e, dict) or e.get("session_id") != pred_sid_before:
                    continue
                if not _same_lineage_row(key, canonical_id, agents):
                    continue
                if e.get("succeeded_by"):
                    continue
                e["succeeded_by"] = canonical_id
                e["superseded_at"] = now
                # Same as the alias path: a superseded predecessor archive is not
                # an always-on seat (gm gen-19 residue). Stamp it so pulse's
                # Level-A identity invariant stops re-escalating the demoted row.
                e["always_on"] = False
                # D4 FIX (DEC-1787687601, ob sbd-3 by-effect): a predecessor
                # archive row that STILL carries tmux_session == canonical_id
                # resolves to the LIVE seat now held by the successor, so
                # reconcile_fleet_stores (`tmux_session or agent_id`, :145) stamps
                # the successor's live sid ONTO this rollback row — destroying the
                # rollback. Move it OFF the canonical seat to its OWN key (a dead
                # session name), so exactly one row (the canonical) points at the
                # live seat. sid is PRESERVED here (this is the RESUMABLE archive —
                # nulling it is the TERMINAL-retire row's job, a different stage).
                if e.get("tmux_session") == canonical_id:
                    e["tmux_session"] = key
                    warnings.append(
                        f"D4: archive row {key!r} moved off the canonical seat "
                        f"(tmux_session {canonical_id!r} -> {key!r}); sid "
                        f"{pred_sid_before} PRESERVED (resumable rollback)")
                predecessors_marked.append(key)
                warnings.append(
                    f"terminal-retire: archive row {key!r} (held predecessor "
                    f"sid {pred_sid_before}) stamped succeeded_by="
                    f"{canonical_id!r} — now reachable to park-idle")

        agents[canonical_id] = new_entry

        # THE KEY IS THE IDENTITY (gm msg_346b9af4, live the operator-facing bug): a
        # spread-copied row carries every embedded identity field, and a stale
        # embedded 'id' overrides the key in downstream spreads ({id, ...def}),
        # producing two rows claiming one identity. Force id == key on every
        # row this single-writer touches — an embedded copy of the key is
        # derived data pretending to be declared.
        for _k in [canonical_id, *aliases_retired]:
            _row = agents.get(_k)
            if isinstance(_row, dict) and _row.get("id") != _k:
                _row["id"] = _k
        reg["last_updated"] = now

        # agent-sessions.json: online + resume_command WRITTEN. The sid/rc
        # here are THE SAME resolved values the registry row consumed above —
        # one resolution, every store (IDENTITY IS ESTABLISHED, NEVER
        # INHERITED; the old inheritance line wrote the predecessor's sid
        # as the successor's identity twice on 2026-08-19).
        meta = _load_strict(AGENT_SESSIONS, "agent-sessions.json")
        # Predecessor sessions snapshot for the archive (preserved cp/resume),
        # captured BEFORE sess_entry is mutated into the successor row.
        pred_sess_snapshot = dict(meta.get(canonical_id) or {})
        sess_entry = dict(meta.get(canonical_id) or {})
        sess_entry.update({
            "status": "online",
            "tmux_session": canonical_id,
            "resumable": True,
            "last_active": now,
        })
        if sid:
            sess_entry["session_id"] = sid
            # COHERENCE (the operator-routed gm gen-18 fix, 2026-08-20): the sessions
            # row is copied from the predecessor, so three IDENTITY-BEARING
            # fields silently keep the dead predecessor's values unless derived
            # from the SAME resolved data the registry row consumed — "one
            # resolution, every store". Left stale on the real gm dry-run:
            # conversation_path (split-brain -> routes to a corpse), generation
            # (17 vs registry 18), model (opus-5 vs registry opus-4-8).
            #   conversation_path: prefer the transcript the resolver actually
            #   found for THIS sid; fall back to the existing project_dir. Never
            #   leave it naming a different sid.
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import sid_invariants as _SI
            _tp = _SI.find_transcript(sid)
            if _tp:
                sess_entry["conversation_path"] = str(_tp)
                # INV3 (auditor gen-3 guard, 2026-08-20): project_dir is copied
                # from the predecessor, whose dir may be a WORKTREE. Re-deriving
                # conversation_path to the successor transcript's MAIN dir while
                # leaving project_dir on the worktree makes the two disagree.
                # project_dir must follow the transcript we actually resolved.
                sess_entry["project_dir"] = str(Path(_tp).parent)
            elif sess_entry.get("project_dir"):
                sess_entry["conversation_path"] = str(
                    Path(sess_entry["project_dir"]) / f"{sid}.jsonl")
            elif sess_entry.get("conversation_path"):
                # cannot derive a new one and the old names the predecessor —
                # drop it rather than keep a split-brain pointer.
                warnings.append(
                    "conversation_path could not be re-derived from the new sid "
                    "(no transcript, no project_dir) — cleared the stale "
                    "predecessor pointer rather than route to a corpse")
                sess_entry["conversation_path"] = None
        else:
            warnings.append("no session_id available (none passed, none on the "
                            "existing sessions entry) — session_id NOT updated")
        # generation + model: match the registry row EXACTLY. These are single
        # resolved values (gen / new_entry['model']); reading them from the
        # canonical registry row rather than re-deriving keeps the two stores
        # coherent by construction, not by a later assert.
        if gen is not None:
            sess_entry["generation"] = gen
        if new_entry.get("model"):
            sess_entry["model"] = new_entry["model"]
        if rc:
            sess_entry["resume_command"] = rc
        else:
            warnings.append("resume_command could NOT be constructed (no "
                            "session_id) — the park-idle-wipe surface is still "
                            "open for this agent")
        meta[canonical_id] = sess_entry

        # F1 (DEC-1787043333): close the SESSIONS rows of every alias folded by
        # this promotion, in the SAME atomic write — the missing half that made
        # 3 alias-resurrections possible on 2026-08-18 (registry rows retired,
        # sessions rows left resumable+sid = the resurrection vector; the 07:02
        # fork resumed a stray onto the live canonical sid). Covers both the
        # aliases retired THIS run and a successor whose registry row was
        # already retired by an earlier partial pass. Canonical stays
        # resumable:True (never-prevent-resurrections law).
        sessions_aliases_closed = []
        for k in set(aliases_retired) | ({successor_session} - {canonical_id}):
            row = meta.get(k)
            if not isinstance(row, dict):
                continue
            if row.get("resumable") is False and row.get("status") == "retired":
                continue                      # already closed — idempotent
            row.update({
                "status": "retired",
                "resumable": False,
                # INV1 (auditor gen-3 guard, 2026-08-20): the registry side
                # nulls session_id + resume_command on the retired alias (695-6:
                # exactly one row claims a sid the moment promote returns). The
                # sessions side must match, or the retired sessions alias keeps
                # the sid and TWO sessions rows share it — the dedup-fuel /
                # resurrection vector this F1 block exists to close.
                "session_id": None,
                "resume_command": None,
                "retired_note": (f"alias sessions row closed by atomic promotion "
                                 f"to canonical '{canonical_id}' (gen {gen})"),
                "last_active": now,
            })
            meta[k] = row
            sessions_aliases_closed.append(k)

        # D4 twin (DEC-1787687601): keep the SESSIONS-store rows of the predecessor
        # archives moved off the canonical seat coherent with the registry side.
        # reconcile reads the registry tmux_session FIRST (so the registry move
        # already defeats the re-bind), but a sessions row still naming the
        # canonical seat is an incoherent pair that a sessions-first reader could
        # re-open — move it to its own key too. sid PRESERVED (resumable rollback).
        for key in predecessors_marked:
            twin = meta.get(key)
            if isinstance(twin, dict) and twin.get("tmux_session") == canonical_id:
                twin["tmux_session"] = key

        # CV4 predecessor-archive CREATION (gm commission 4e20a7516): if the
        # canonical row WAS the predecessor (gm/ob shape), pred_sid is now
        # claimed by no row — create its <canon>-gen<N-1> archive in BOTH stores
        # so the rollback survives without a hand-restore. Existence-based +
        # fail-closed; a raise here still honours "NOTHING was written" (all
        # writes happen after this block).
        predecessor_archived = _create_predecessor_archive(
            agents, meta, pred_sid_before, pred_reg_snapshot,
            pred_sess_snapshot, canonical_id, now, warnings)

        # state/agents/<id>.json: online + session fields, unknown keys kept
        state_path = Path(AGENTS_DIR) / f"{canonical_id}.json"
        agent_state = _load_lenient(state_path)
        agent_state.setdefault("agent_id", canonical_id)
        agent_state["status"] = "online"
        agent_state["tmux_session"] = canonical_id
        agent_state["last_updated"] = now
        if sid:
            agent_state["session_id"] = sid
        if gen is not None:
            agent_state["generation"] = gen

        # CROSS-STORE COHERENCE ASSERT (DEC-1787117309 §3 as corrected by my
        # COUNTER: three stores, not two; and BEFORE the write, not after —
        # refusing after a write cannot honour "NOTHING was written").
        # In-memory, on the prepared structures. The one way these can
        # diverge post-consumption-fix is an explicit --resume-command
        # naming a different sid; readers of registry.session_id include
        # the resolver fast path (session-index.py:328), the TRUE blast
        # radius (A11.3) — this assert protects consumers not yet found.
        _ids = {"registry": new_entry.get("session_id"),
                "sessions": sess_entry.get("session_id"),
                "state/agents": agent_state.get("session_id")}
        assert_stores_coherent(_ids)   # null-agreement rejected (gm finding)
        # conversation_path is NOT in assert_stores_coherent's tuple by design
        # (that guard is session_id only); its own guard covers the split-brain
        # vector so the set is not represented by one member (law 16).
        assert_conversation_path_names_sid(
            sess_entry.get("conversation_path"), sid)
        # generation + model must agree registry<->sessions (the gm gen-18
        # two-store disagreement: gen 17 vs 18, opus-5 vs opus-4-8). Derived
        # coherent above; this proves it before the write rather than trusting.
        for _f in ("generation", "model"):
            _r, _s = new_entry.get(_f), sess_entry.get(_f)
            if _r is not None and _s is not None and _r != _s:
                raise PromotionRefused(
                    f"CROSS-STORE {_f.upper()} INCOHERENT: registry {_r!r} vs "
                    f"sessions {_s!r} — the sessions row kept a stale {_f} while "
                    f"the registry took the new one. NOTHING was written.")
        for _store, _rc in (("registry", new_entry.get("resume_command")),
                            ("sessions", sess_entry.get("resume_command"))):
            if _rc and sid and sid not in _rc:
                raise PromotionRefused(
                    f"resume_command on the {_store} row does not name the "
                    f"resolved successor sid {sid!r}: {_rc!r} — a resume "
                    f"pointer at a different identity is the half-promotion "
                    f"wearing a new field. NOTHING was written.")
        # SINGLE-CLAIMANT clause (DELTA E.6 + D4 calibration): no OTHER
        # registry row in the prepared write may claim the promoted sid.
        # The successor's own alias was cleared above. Remaining claimants
        # are partitioned by the direction that must not fire (D4,
        # 2d5b0698a: long-dead generations still claim live sids — the log
        # shows gm__retired-gen8 + gm-gen10 counted as claimants of
        # gen-16's sid, so refusing on RETIRED rows would block legitimate
        # promotions on historical junk while leaving them is the dedup
        # fuel):
        #   RETIRED claimant -> RELEASE its stale claim in this same atomic
        #     write, visibly (report + warning) — never silently, never
        #     refused.
        #   ACTIVE claimant  -> a live CROSSED row this promotion does not
        #     own; writing over it knowingly recreates the two-claimant
        #     dedup bomb. REFUSE and name it.
        # B2 / STEP 10: the written sid's OWN transcript must vouch for the
        # successor — invoked with the FINAL sid, before any write.
        if sid:
            _assert_written_identity(
                sid, successor_session, agents, warnings,
                operator_asserted=(sid_source == "operator-asserted"))
        stale_claims_released = []
        if sid:
            for k, v in agents.items():
                if k == canonical_id or not isinstance(v, dict):
                    continue
                if v.get("session_id") != sid:
                    continue
                if v.get("status") == "retired":
                    v["session_id"] = None
                    v["resume_command"] = None
                    stale_claims_released.append(k)
                    warnings.append(
                        f"stale claim RELEASED: retired row {k!r} still "
                        f"claimed sid {sid} (D4 fuel) — cleared in this write")
                else:
                    raise PromotionRefused(
                        f"SINGLE-CLAIMANT violated: ACTIVE registry row {k!r} "
                        f"already claims the successor sid {sid!r} and is not "
                        f"an alias this promotion retires. That is a live "
                        f"crossed row (DELTA E: two claimants let the */10 "
                        f"scan null the canonical identity). Fix the crossing "
                        f"first. NOTHING was written.")

        report = {
            "canonical_id": canonical_id,
            "successor_session": successor_session,
            "dry_run": dry_run,
            "registry_entry": new_entry,
            "sessions_entry": sess_entry,
            "agent_state": agent_state,
            "aliases_retired": aliases_retired,
            "predecessors_marked": predecessors_marked,
            "predecessor_archived": predecessor_archived,
            "sessions_aliases_closed": sessions_aliases_closed,
            "stale_claims_released": stale_claims_released,
            "warnings": warnings,
            "written": [],
        }
        if dry_run:
            _log(f"DRY-RUN promotion {successor_session} -> {canonical_id}: "
                 f"no files written. plan={json.dumps(report, default=str)[:500]}")
            return report

        # CV4 W1 SHADOW observe — ONE grouped call BEFORE the commit block so the
        # three atomic writes below stay back-to-back (ordering not perturbed).
        _cv4_observe_promote([
            ("registry", canonical_id, new_entry),
            ("sessions", canonical_id, sess_entry),
            ("state/agents", canonical_id, agent_state),
        ])
        # ---- WRITE: all contents already computed + validated ----
        # cutover: the 3-store promote IS a canonical Blue->Green repoint — route
        # it through the store as ONE swap (fail-closed if rows absent). INERT:
        # _use_db is False while the flag is unarmed => the 3 legacy writes run
        # exactly as before. Choreography (ordering, downstream rename) untouched.
        _use_db = _db_promote_swap(canonical_id, new_entry, sess_entry,
                                   agent_state=agent_state,
                                   archive_key=predecessor_archived,
                                   reg=reg, meta=meta,
                                   aliases_retired=aliases_retired)
        if not _use_db:
            _atomic_write(REGISTRY, reg)
            report["written"].append(str(REGISTRY))
            _atomic_write(AGENT_SESSIONS, meta)
            report["written"].append(str(AGENT_SESSIONS))
            _atomic_write(state_path, agent_state)
            report["written"].append(str(state_path))
        else:
            report["written"].append(str(ORCHESTRA_DIR / "state" / "orchestra-registry.db"))

        if rotation_store and rotation_id and not dry_run:
            from scripts.continuity.rotation_store import RotationState
            rot = rotation_store.get_rotation(rotation_id)
            if rot and rot["state"] != RotationState.CUTOVER_COMMITTED.value:
                rotation_store.transition(rotation_id, rot["state_version"], RotationState.CUTOVER_COMMITTED, cutover_receipt_json={"report": report})

    for w in warnings:
        _log(f"WARNING {canonical_id}: {w}")
    _log(f"PROMOTED {successor_session} -> {canonical_id} (gen {gen}, "
         f"sid {sid or '?'}); aliases retired: {aliases_retired or 'none'}; "
         f"surfaces: {report['written']}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("canonical_id", help="the canonical identity being taken over (e.g. gm)")
    ap.add_argument("successor_session", help="the successor's current/temp session name (e.g. gm-gen10)")
    ap.add_argument("--session-id", default=None, help="the successor's claude session uuid")
    ap.add_argument("--model", default=None)
    ap.add_argument("--generation", type=int, default=None)
    ap.add_argument("--resume-command", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + print the full promotion report, write nothing")
    ap.add_argument("--shadow-skip-reason", default=None,
                    help="RECORD why this rotation is not a Key-1 datum. "
                         "Required when no shadow-ledger row exists for it — "
                         "an audit trail, never a silent bypass (gm "
                         "msg_e2bcc05f: a rotation leaving no Key-1 evidence "
                         "must be impossible, not discouraged)")
    ap.add_argument("--readback-waived-reason", default=None,
                    help="RECORD why this promotion proceeds without a "
                         "committed T4 readback at "
                         "state/agent-handoffs/<successor>.readback.md. "
                         "An audit trail, never a silent bypass — without it "
                         "the promotion REFUSES (gm msg_4d5d5b9b: T4 was the "
                         "only step of the operator's ordering with no enforcement)")
    ap.add_argument("--rotation-trigger-override", default=None,
                    help="RECORD why this promotion proceeds without the "
                         "predecessor's detector proving used_pct >= 85 "
                         "(G7). the operator-explicit emergency reseat — an audit "
                         "trail, never silent; without it a premature "
                         "rotation REFUSES (gm's g15 promotion ran on a "
                         "self-reported 86 while the detector read 45)")
    args = ap.parse_args(argv)
    try:
        report = promote(args.canonical_id, args.successor_session,
                         session_id=args.session_id, model=args.model,
                         generation=args.generation,
                         resume_command=args.resume_command,
                         dry_run=args.dry_run,
                         shadow_skip_reason=args.shadow_skip_reason,
                         readback_waived_reason=args.readback_waived_reason,
                         rotation_trigger_override=args.rotation_trigger_override)
    except PromotionRefused as e:
        _log(str(e))
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
