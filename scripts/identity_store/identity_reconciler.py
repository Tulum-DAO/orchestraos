"""identity_reconciler — periodic DB-first liveness+sid+model pass (Identity Layer v1,
DEC-1789507884583046, consensus CONSENSUS_REACHED 2026-09-15).

Design anchor: .workspace/proposals/identity-reconciler-v1-DEC.md (sha256 5a65e49e...).
Grounding: /tmp/gm-mine-identity-reconciler.md (332/381 canonical online/parked rows are
NO_PROCESS phantoms; 32 inflated SID_MISMATCH from a one-level ``pgrep -P`` liveness
blind spot; real live workers ~30-35).

PRINCIPLE (DEC section 2): COMPOSE, don't build. This module is ONE periodic pass that
runs the EXISTING narrow reconcilers (status_reconcile's liveness invariant + apply path,
pre_arm_sid_map's #1 register-sid hook, model_reconcile, session_doc_reconcile) in the
correct order, adding exactly three guarantees they individually lack:
  (a) PERIODIC — a single ``--cron`` entry point safe to chain on the existing beat;
  (b) ROBUST LIVENESS — a full ``/proc`` ppid-chain walk to the pane pid (setsid-aware),
      not ``status_reconcile``'s one-level ``pgrep -P`` (the SID_MISMATCH blind spot);
  (c) FIRE-AWARE — never touches a seat whose ``state/wal/<root>.bg.json`` state is
      PREWARMING/READY/SWAPPING (the swap owns its sids).

ORDER (DEC section 3, load-bearing — model trusts the sid, so sid MUST run first):
  0 GATE -> 1 SKIP SET -> 2 LIVENESS(+hysteresis) -> 3 SID-RESOLVE -> 4 MODEL ->
  5 CROSS-FILE -> 6 PROJECT -> 7 SELF-CHECK(fleet guards) -> 8 EMIT (log line).

Dry-run by DEFAULT (mirrors every reconciler in this package). ``--cron`` is the ONLY
apply path. Never kills a process, never reaps a pane — retire/park is a DB status write
only (DEC section 4).
"""
import argparse
import json
import os
import subprocess
import sys
import time

# arm_hooks.py / ctx_adapters.py (composed in step 3) import each other via the
# BARE `lineage_daemon.wal.*` path (no `scripts.` prefix) — mirror
# pre_arm_sid_map.py's bootstrap so those lazy imports resolve regardless of how
# this module itself was invoked (`-m scripts.identity_store...` or by path).
_ORCH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_ORCH, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from scripts.identity_store import (
    cutover, freeze, identity_writer, model_reconcile, orchestra_db,
    session_doc_reconcile, status_reconcile,
)

_MIDFIRE_STATES = {"PREWARMING", "READY", "SWAPPING"}
_DEAD_PARK_THRESHOLD = 2   # consecutive dead passes before park/retire
_LOG_REL = os.path.join("logs", "identity-reconciler.log")
_HYST_REL = os.path.join("state", "identity-reconciler-hysteresis.json")
_ALARM_DEDUPE_REL = os.path.join("state", "identity-reconciler-alarm-dedupe.json")
_ALARM_TTL_S = 24 * 3600   # re-alarm an unchanged (seat, reason, state) after 24h

FLEET_GUARD_TESTS = [
    "scripts/identity_store/test_fleet_archive_status_cross_file.py",
    "scripts/identity_store/test_fleet_canonical_status_enum.py",
    "scripts/identity_store/test_fleet_canonical_status_matches_process.py",
    "scripts/identity_store/test_fleet_generation_model_known_when_live.py",
    "scripts/identity_store/test_fleet_lineage_runtime_no_mismatch.py",
    "scripts/identity_store/test_fleet_parked_canonical_cross_file.py",
]


def _db_path(od):
    return os.path.join(od, "state", "orchestra-registry.db")


def _wal_dir(od):
    return os.path.join(od, "state", "wal")


# ---------------------------------------------------------------------------
# Step 0 — cutover gate. NEVER a silent no-op: cutover off -> LOUD msg_store alarm
# to gm + non-zero exit.
# ---------------------------------------------------------------------------

def _default_msg_store_send(orchestra_dir, subject, body):
    subprocess.run(
        [sys.executable, os.path.join(orchestra_dir, "msg_store.py"), "send",
         "--from", "identity-reconciler", "--to", "gm", "--type", "task",
         "--subject", subject, "--body", body],
        cwd=orchestra_dir, capture_output=True, text=True, timeout=30, check=False)


def _append_log_line(orchestra_dir, text, *, log_path=None):
    path = log_path or os.path.join(orchestra_dir, _LOG_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(text + "\n")


def loud_alarm(orchestra_dir, detail, *, send_fn=None, log_path=None):
    """The GATE-0 / ambiguous-sid / guard-red alarm: LOUD to gm's msg_store inbox
    (ALWAYS — this is the un-deduped primitive; dedupe is applied by the CALLER,
    see ``sid_resolve_pass``'s ``_notice`` helper, never here). Also appended to
    ``logs/identity-reconciler.log`` so the audit trail is complete regardless of
    whether a caller chose to page or log-only. Injectable for RED proof; never
    raises (best-effort page, like monitor.page)."""
    text = f"[identity-reconciler] {detail}"
    print(text, flush=True)
    try:
        _append_log_line(orchestra_dir, text, log_path=log_path)
    except OSError:  # noqa: BLE001 -- logging must never crash the pass
        pass
    send = send_fn or (lambda subj, body: _default_msg_store_send(orchestra_dir, subj, body))
    try:
        send("identity-reconciler ALARM", text)
    except Exception:  # noqa: BLE001 -- alarm delivery must never crash the pass
        pass


def log_only(orchestra_dir, detail, *, log_path=None):
    """A fail-closed notice DEDUPED away this pass: still fully audited to
    ``logs/identity-reconciler.log`` (and stdout), just NOT paged to gm."""
    text = f"[identity-reconciler] {detail} (SUPPRESSED: duplicate alarm, dedupe window active)"
    print(text, flush=True)
    try:
        _append_log_line(orchestra_dir, text, log_path=log_path)
    except OSError:  # noqa: BLE001
        pass


def gate_cutover(orchestra_dir, *, send_fn=None):
    """Step 0. Returns True iff the pass may proceed. Blocks (barrier) for the
    freeze window first; if cutover is inactive after that, LOUD + refuse."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        loud_alarm(
            orchestra_dir,
            "cutover is OFF — every identity_writer op is a no-op. Refusing to run a "
            "pass that would silently do nothing. Arm the cutover flag first.",
            send_fn=send_fn)
        return False
    return True


# ---------------------------------------------------------------------------
# Step 2a — the setsid-aware FULL /proc ppid-chain walk (the correctness fix over
# status_reconcile's one-level `pgrep -P`). Injected into status_reconcile.reconcile
# as its `pane_has_child_fn` so we COMPOSE the skip-set/service-probe/status_vocab
# machinery rather than re-implement it.
# ---------------------------------------------------------------------------

def _all_pids():
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                stat = fh.read().decode(errors="replace")
        except OSError:
            continue
        # comm may contain spaces/parens; split on the LAST ')' per `man proc`.
        rp = stat.rfind(")")
        if rp == -1:
            continue
        fields = stat[rp + 2:].split()
        try:
            ppid = int(fields[1])
        except (IndexError, ValueError):
            continue
        out[int(pid)] = ppid
    return out


def _pane_pids(tmux):
    r = subprocess.run(["tmux", "list-panes", "-t", tmux, "-F", "#{pane_pid}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [int(p) for p in r.stdout.split() if p.strip().isdigit()]


def full_tree_pane_has_child(tmux, *, pane_pids_fn=None, all_pids_fn=None):
    """By-effect liveness, setsid-aware: does the pane pid have >=1 LIVE DESCENDANT
    anywhere in the /proc ppid-chain (not just a direct child)? A `setsid`'d process
    reparents to pid 1 in some flows but keeps its ORIGINAL parent as an ancestor
    link in the child-discovery direction we walk here (child->parent scan, BFS
    forward from the pane pid through every pid whose ppid chain passes through
    it) — this is what a one-level `pgrep -P` (status_reconcile's default) misses:
    a grandchild whose immediate parent already exited/reparented is still found
    because we match on the FULL ppid chain, not just direct parentage.
    """
    pane_pids_fn = pane_pids_fn or _pane_pids
    all_pids_fn = all_pids_fn or _all_pids
    panes = pane_pids_fn(tmux)
    if not panes:
        return False
    child_of = all_pids_fn()   # pid -> ppid, snapshot once per call
    roots = set(panes)
    # BFS: a pid is a descendant if its ppid chain reaches a root within a bounded
    # depth (avoids infinite loops on a corrupted/racing ppid cycle).
    for pid, ppid in child_of.items():
        if pid in roots:
            continue
        seen = {pid}
        cur = ppid
        depth = 0
        while cur and cur not in seen and depth < 64:
            if cur in roots:
                return True
            seen.add(cur)
            cur = child_of.get(cur)
            depth += 1
    return False


# ---------------------------------------------------------------------------
# Step 1 — skip set additions status_reconcile doesn't know about: provisional
# aliases (bg lane) and mid-fire bg_state (PREWARMING/READY/SWAPPING).
# ---------------------------------------------------------------------------

def _bg_state_of(wal_dir, root, *, read_fn=None):
    """The seat's Blue-Green state machine phase, or None if unreadable/absent
    (SOLO is the quiescent default and is NOT a skip condition)."""
    if read_fn is not None:
        return read_fn(root)
    path = os.path.join(wal_dir, f"{root}.bg.json")
    try:
        with open(path) as fh:
            return (json.load(fh) or {}).get("state")
    except (OSError, ValueError):
        return None


def compute_midfire_skip(orchestra_dir, roots, *, wal_dir=None, bg_state_fn=None):
    """Roots whose bg_state is mid-swap — the fire-skip (DEC section 1, item 2):
    'the swap owns its sids — reconciler must not race a fire'."""
    wal_dir = wal_dir or _wal_dir(orchestra_dir)
    skipped = []
    for root in roots:
        st = _bg_state_of(wal_dir, root, read_fn=bg_state_fn)
        if st in _MIDFIRE_STATES:
            skipped.append(root)
    return skipped


# ---------------------------------------------------------------------------
# Hysteresis state (park/retire only after 2 consecutive dead passes; recover in 1).
# ---------------------------------------------------------------------------

def _hyst_path(orchestra_dir):
    return os.path.join(orchestra_dir, _HYST_REL)


def load_hysteresis(orchestra_dir):
    try:
        with open(_hyst_path(orchestra_dir)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_hysteresis(orchestra_dir, data):
    p = _hyst_path(orchestra_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# ALARM DEDUPE (gm follow-up, post-first-live-pass): the routine fail-closed
# sid-resolve notices (UNRESOLVABLE / AMBIGUOUS / resolver error) re-fire
# IDENTICALLY every pass on a */15 cron — the known sentinel-re-fires-identical-
# beats class (7 alarms/pass -> ~28/hour flooding gm). Persist a per-(seat,
# reason-class, state) last-alarmed timestamp; emit the msg_store alarm only when
# the KEY is new (first sighting OR the seat/reason/sid-pair state CHANGED — a
# changed state is a DIFFERENT key by construction) or the prior alarm for that
# EXACT key is older than 24h; otherwise log-only (logs/identity-reconciler.log
# still gets the line via the caller's own print). Guard-red and the cutover gate
# are explicitly NEVER deduped (gm: "always LOUD") — this mechanism is ONLY wired
# into the routine per-seat sid-resolve notices in ``sid_resolve_pass``.
# ---------------------------------------------------------------------------

def _alarm_dedupe_path(orchestra_dir):
    return os.path.join(orchestra_dir, _ALARM_DEDUPE_REL)


def load_alarm_dedupe(orchestra_dir):
    try:
        with open(_alarm_dedupe_path(orchestra_dir)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_alarm_dedupe(orchestra_dir, data):
    p = _alarm_dedupe_path(orchestra_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def should_alarm(dedupe, key, *, now=None, ttl_s=_ALARM_TTL_S):
    """True iff this EXACT (seat, reason-class, state) key should page gm now —
    first sighting, or the prior alarm for this key is older than ``ttl_s``.
    Mutates ``dedupe`` in place (stamps/refreshes the key) whenever it returns
    True; a suppressed hit leaves the stored timestamp untouched (so the 24h
    window is measured from the LAST alarm, not the last suppressed check)."""
    now = now if now is not None else time.time()
    prev = dedupe.get(key)
    if prev is None or (now - prev.get("ts", 0)) > ttl_s:
        dedupe[key] = {"ts": now}
        return True
    return False


# ---------------------------------------------------------------------------
# Step 2 — liveness pass. Reuses status_reconcile.reconcile()'s skip-set / service-
# probe / status_vocab machinery WHOLESALE, injecting the full-tree walk as its
# pane_has_child_fn — then layers hysteresis + provisional/mid-fire skip + the
# park-vs-retire (resume_command) decision on top, since status_reconcile itself
# only ever writes 'parked' (never 'retired') and has no hysteresis at all.
# ---------------------------------------------------------------------------

def liveness_pass(orchestra_dir, *, apply, wal_dir=None,
                  has_session_fn=None, pane_has_child_fn=None, list_clients_fn=None,
                  port_listening_fn=None, bg_state_fn=None, local_machine="vps",
                  send_fn=None):
    wal_dir = wal_dir or _wal_dir(orchestra_dir)
    has_session = has_session_fn or status_reconcile._default_has_session
    pane_has_child = pane_has_child_fn or full_tree_pane_has_child
    list_clients = list_clients_fn or status_reconcile._default_list_clients
    port_listening = port_listening_fn or status_reconcile._default_port_listening

    # status_reconcile.reconcile() is still called (dry-run) to reuse its
    # attached-client / non-local-machine / retired-canonical skip computation
    # UNCHANGED — but its own to_parked/to_online is a STATUS-CHANGE diff (a
    # seat already correctly 'parked' with no live process never appears there),
    # which would blind hysteresis to the escalate-to-retire case (an
    # already-parked phantom with no resume_command). So liveness itself is
    # re-derived directly below, over the FULL row population, using the SAME
    # injected primitives (composition, not duplication of policy).
    base = status_reconcile.reconcile(
        orchestra_dir, apply=False, local_machine=local_machine,
        has_session_fn=has_session, pane_has_child_fn=pane_has_child,
        list_clients_fn=list_clients, port_listening_fn=port_listening)

    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        rows = {r["root"]: dict(r) for r in conn.execute(
            "SELECT c.root AS root, c.tmux_session AS tmux, c.status AS status, "
            "c.generation_id AS gid, l.runtime AS runtime, l.machine AS machine, "
            "g.resume_command AS resume_command "
            "FROM canonical c JOIN generations g ON g.id = c.generation_id "
            "LEFT JOIN lineages l ON l.root = c.root "
            "WHERE g.retired_at IS NULL")}
    finally:
        conn.close()

    excluded = (set(base["attached_skipped"]) | set(base["nonlocal_skipped"])
               | set(base["flagged_retired"]))
    all_roots = set(rows) - excluded
    midfire = set(compute_midfire_skip(orchestra_dir, all_roots, wal_dir=wal_dir,
                                       bg_state_fn=bg_state_fn))
    provisional = {r for r in all_roots if rows[r]["status"] == "provisional"}
    skip = midfire | provisional
    candidates = all_roots - skip

    hyst = load_hysteresis(orchestra_dir)
    dead1, confirmed_park, confirmed_retire, recovered = [], [], [], []

    for root in sorted(candidates):
        r = rows[root]
        if r["runtime"] == "service":
            live, _reason = status_reconcile.service_is_live(
                root, port_listening_fn=port_listening, pane_has_child_fn=pane_has_child)
        else:
            live = bool(r["tmux"]) and has_session(r["tmux"]) and pane_has_child(r["tmux"])

        if live:
            hyst.pop(root, None)
            if r["status"] != "online":
                recovered.append(root)
            continue

        n = hyst.get(root, 0) + 1
        hyst[root] = n
        if n >= _DEAD_PARK_THRESHOLD:
            target = "parked" if r["resume_command"] else "retired"
            if target == "parked":
                if r["status"] != "parked":
                    confirmed_park.append(root)
            else:
                confirmed_retire.append(root)   # retire_agent is idempotent-safe
        else:
            dead1.append(root)

    if apply:
        if confirmed_park:
            status_reconcile._apply(orchestra_dir, confirmed_park, "parked")
        for root in confirmed_retire:
            session_record = _retired_session_record(orchestra_dir, root)
            identity_writer.retire_agent(
                orchestra_dir, root,
                reason="identity_reconciler: no-process x2, no resume_command",
                session_record=session_record)
        if recovered:
            status_reconcile._apply(orchestra_dir, recovered, "online")
        save_hysteresis(orchestra_dir, hyst)
        if confirmed_park or confirmed_retire or recovered:
            identity_writer.project_now(orchestra_dir)
    # dry-run: DO NOT persist the hysteresis counters either — a dry-run must
    # never have a side effect (including advancing the dead-count).

    return {
        "scanned": base["scanned"],
        "skipped_midfire": sorted(midfire),
        "skipped_provisional": sorted(provisional),
        "attached_skipped": base["attached_skipped"],
        "nonlocal_skipped": base["nonlocal_skipped"],
        "service_no_probe": base["service_no_probe"],
        "dead1": sorted(dead1),
        "confirmed_park": sorted(confirmed_park),
        "confirmed_retire": sorted(confirmed_retire),
        "recovered": sorted(recovered),
        "applied": bool(apply),
    }


def _retired_session_record(orchestra_dir, root):
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        row = conn.execute(
            "SELECT payload_json FROM source_records WHERE file='agent-sessions.json' "
            "AND kind='session' AND key=?", (root,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    doc = json.loads(row["payload_json"])
    doc["status"] = "retired"
    return doc


# ---------------------------------------------------------------------------
# Step 3 — sid-resolve. ONLINE LIVE seats only (post-liveness). Ambiguous or
# unresolvable => fail-closed, LOUD, no write (never guess).
# ---------------------------------------------------------------------------

def _foreign_generation_holder(orchestra_dir, root, canonical_gen_id, sid):
    """True iff ``sid`` is already attributed to SOME OTHER generation of ``root``
    (retired OR not — a live provisional/archive alias counts too). A rotated root
    has TWO live processes (the bare canonical pane + a deliberately-kept-alive
    ``<root>-gen<N>`` predecessor pane, or an equivalent per-provider archive) and the
    provider resolvers (transcript/store scans, gen-suffix tolerant) can match the
    PREDECESSOR's own boot-declaration instead of the canonical's — this is the
    'ambiguous' case the DEC's fail-closed rule covers: never adopt a sid that
    belongs to a DIFFERENT generation of the same lineage, no matter how it was
    found. Only a sid unseen by this lineage, or already the canonical's own, may
    be written."""
    if not sid:
        return False
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        row = conn.execute(
            "SELECT id FROM generations WHERE root=? AND session_id=?",
            (root, sid)).fetchone()
    finally:
        conn.close()
    return row is not None and row["id"] != canonical_gen_id


def sid_resolve_pass(orchestra_dir, *, apply, wal_dir=None, live_roots=None,
                     resolve_registry=None, register_sid_fn=None, send_fn=None,
                     foreign_holder_fn=None, now_fn=None, dedupe_state=None):
    """``live_roots`` are the confirmed-online, non-skipped seats from step 2
    (the ~30-35). ``resolve_registry`` defaults to CID_RESOLVER_REGISTRY.

    FAIL-CLOSED against the rotated-root ambiguity (gm GATE, by-effect finding):
    resolving a root's cid by NAME (as every provider resolver does — process
    cmdline / transcript boot-declaration / brain-dir match) cannot itself tell a
    canonical pane from a deliberately-kept-alive retired predecessor pane of the
    SAME root — so any resolved sid is cross-checked against THIS root's OWN
    generation history before it is ever considered a candidate write. A resolved
    sid that belongs to a different generation of this root (retired or not) is
    AMBIGUOUS, never a valid target, regardless of freshness.

    ALARM DEDUPE (gm follow-up): every routine fail-closed notice (error /
    unresolvable / ambiguous) is paged to gm ONLY when its (seat, reason-class,
    state) key is new or its last page is >24h old; otherwise it is still fully
    logged (``log_only``) but NOT re-paged — this is the ONLY dedupe surface
    (guard-red and the gate-0 alarm are never deduped, called elsewhere via the
    un-deduped ``loud_alarm`` directly). ``dedupe_state`` lets a caller (tests,
    or ``run_pass`` batching several sub-passes) supply/reuse an in-memory dict
    instead of a fresh load+save per call; when omitted this function loads and
    persists ``state/identity-reconciler-alarm-dedupe.json`` itself."""
    wal_dir = wal_dir or _wal_dir(orchestra_dir)
    if resolve_registry is None:
        from scripts.lineage_daemon.wal.ctx_adapters import CID_RESOLVER_REGISTRY
        resolve_registry = CID_RESOLVER_REGISTRY
    if register_sid_fn is None:
        from scripts.lineage_daemon.wal.arm_hooks import make_register_sid_fn
        register_sid_fn = make_register_sid_fn(orchestra_dir, wal_dir)
    foreign_holder = foreign_holder_fn or (
        lambda root, gid, sid: _foreign_generation_holder(orchestra_dir, root, gid, sid))
    now = now_fn() if now_fn else time.time()
    owns_dedupe = dedupe_state is None
    dedupe = load_alarm_dedupe(orchestra_dir) if owns_dedupe else dedupe_state
    suppressed = 0

    def _notice(root, key_suffix, detail):
        nonlocal suppressed
        key = f"{root}|{key_suffix}"
        if should_alarm(dedupe, key, now=now):
            loud_alarm(orchestra_dir, detail, send_fn=send_fn)
        else:
            suppressed += 1
            log_only(orchestra_dir, detail)

    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        rows = {r["root"]: dict(r) for r in conn.execute(
            "SELECT c.root AS root, c.generation_id AS generation_id, "
            "g.session_id AS session_id, l.runtime AS runtime "
            "FROM canonical c JOIN generations g ON g.id = c.generation_id "
            "LEFT JOIN lineages l ON l.root = c.root WHERE g.retired_at IS NULL")}
    finally:
        conn.close()

    fixed, unresolved, ambiguous_foreign, skipped_unknown_runtime = [], [], [], []
    for root in sorted(live_roots or []):
        r = rows.get(root)
        if r is None:
            continue
        runtime = r["runtime"]
        resolver = resolve_registry.get(runtime)
        if resolver is None:
            skipped_unknown_runtime.append(root)
            continue
        try:
            cid = resolver(root)
        except Exception as e:  # noqa: BLE001 -- a resolver crash is a fail-closed finding
            cid = None
            _notice(root, f"error|{e}", f"sid-resolve error for {root}: {e} — no write")
        if not cid:
            unresolved.append(root)
            _notice(root, "unresolvable",
                   f"sid-resolve UNRESOLVABLE/AMBIGUOUS for {root} (runtime={runtime}) "
                   f"— fail-closed, no write")
            continue
        if cid == r["session_id"]:
            continue   # already correct — no write needed
        # AMBIGUITY GATE: a stored sid that already belongs to a foreign
        # generation of this root is a corruption finding, never actionable
        # here; a RESOLVED sid that belongs to a foreign generation (the
        # rotated-root predecessor-pane collision) is the gm-reported bug —
        # both fail-closed, zero write, LOUD (subject to dedupe).
        if foreign_holder(root, r["generation_id"], cid) or foreign_holder(
                root, r["generation_id"], r["session_id"]):
            ambiguous_foreign.append(root)
            _notice(
                root, f"ambiguous|{cid}|{r['session_id']}",
                f"sid-resolve AMBIGUOUS for {root}: resolved sid {cid!r} (or stored "
                f"{r['session_id']!r}) belongs to a DIFFERENT generation of this root "
                f"(rotated-root predecessor-pane collision) — fail-closed, no write")
            continue
        fixed.append({"root": root, "old_sid": r["session_id"], "new_sid": cid})
        if apply:
            try:
                register_sid_fn(root)   # the sanctioned #1 hook: DB-first + flat
            except Exception as e:  # noqa: BLE001 -- an operational write failure is
                # NEVER deduped (distinct from the routine fail-closed notices above).
                loud_alarm(orchestra_dir,
                          f"sid-resolve register FAILED for {root}: {e}", send_fn=send_fn)

    if owns_dedupe:
        save_alarm_dedupe(orchestra_dir, dedupe)

    return {"applied": bool(apply), "fixed": fixed, "unresolved": unresolved,
            "ambiguous_foreign_generation": ambiguous_foreign,
            "skipped_unknown_runtime": skipped_unknown_runtime,
            "alarms_suppressed": suppressed}


# ---------------------------------------------------------------------------
# Step 7 — self-check: run the 6 fleet guards. LOUD alarm on any red; NEVER
# auto-revert (DEC section 3 item 7).
# ---------------------------------------------------------------------------

def run_fleet_guards(orchestra_dir, *, runner=None):
    # ORCH_GUARD_RUN=1 declares the guard-run context to scripts/conftest.py so its registry
    # fingerprint compare (a TEST-mutates-prod trap) does not fire on a live rotation /
    # regenerator write that lands mid-run (false guards_red page, gm msg_141aff5e).
    runner = runner or (lambda files: subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *files],
        cwd=orchestra_dir, capture_output=True, text=True, timeout=180,
        env={**os.environ, "ORCH_GUARD_RUN": "1"}))
    r = runner(FLEET_GUARD_TESTS)
    return {"returncode": r.returncode, "stdout": r.stdout[-4000:], "stderr": r.stderr[-2000:]}


# ---------------------------------------------------------------------------
# Step 8 — one summary line to logs/identity-reconciler.log.
# ---------------------------------------------------------------------------

def emit_log_line(orchestra_dir, summary, *, log_path=None):
    path = log_path or os.path.join(orchestra_dir, _LOG_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       **summary})
    with open(path, "a") as fh:
        fh.write(line + "\n")
    return line


# ---------------------------------------------------------------------------
# The pass — orchestrates steps 0..8 in DEC order.
# ---------------------------------------------------------------------------

def run_pass(orchestra_dir, *, apply=False, local_machine="vps", wal_dir=None,
            has_session_fn=None, pane_has_child_fn=None, list_clients_fn=None,
            port_listening_fn=None, bg_state_fn=None,
            resolve_registry=None, register_sid_fn=None,
            guard_runner=None, send_fn=None, skip_guards=False):
    report = {"applied": bool(apply)}

    # STEP 0 — gate.
    if not gate_cutover(orchestra_dir, send_fn=send_fn):
        report["gate_failed"] = True
        return report, 2

    # STEP 2 — liveness (order matters: sid trusts the post-liveness picture).
    live_rep = liveness_pass(
        orchestra_dir, apply=apply, wal_dir=wal_dir,
        has_session_fn=has_session_fn, pane_has_child_fn=pane_has_child_fn,
        list_clients_fn=list_clients_fn, port_listening_fn=port_listening_fn,
        bg_state_fn=bg_state_fn, local_machine=local_machine, send_fn=send_fn)
    report["liveness"] = live_rep

    # ONLINE-LIVE population for sid-resolve = every canonical root currently
    # 'online' minus everything the liveness pass just skipped/parked/retired.
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        online_roots = {r["root"] for r in conn.execute(
            "SELECT root FROM canonical WHERE status='online'")}
    finally:
        conn.close()
    excluded = (set(live_rep["skipped_midfire"]) | set(live_rep["skipped_provisional"])
               | set(live_rep["attached_skipped"]) | set(live_rep["nonlocal_skipped"])
               | set(live_rep["confirmed_park"]) | set(live_rep["confirmed_retire"]))
    live_roots = (online_roots | set(live_rep["recovered"])) - excluded

    # STEP 3 — sid-resolve.
    sid_rep = sid_resolve_pass(orchestra_dir, apply=apply, wal_dir=wal_dir,
                               live_roots=live_roots, resolve_registry=resolve_registry,
                               register_sid_fn=register_sid_fn, send_fn=send_fn)
    report["sid"] = sid_rep

    # STEP 4 — model (trusts the now-correct sids).
    model_rep = model_reconcile.reconcile(orchestra_dir, apply=apply)
    report["model"] = {"resolved_count": len(model_rep["resolved"]),
                       "unresolved_count": len(model_rep["unresolved"]),
                       "services_set_na": len(model_rep["services"])}

    # STEP 5 — cross-file (session doc vs canonical status).
    doc_rep = session_doc_reconcile.reconcile(orchestra_dir, apply=apply)
    report["session_doc"] = {"changed": doc_rep["changed"]}

    # STEP 6 — project (regenerator also catches it; this is the read-your-writes seam).
    if apply:
        identity_writer.project_now(orchestra_dir)

    # STEP 7 — self-check: the 6 fleet guards. LOUD on red; NEVER auto-revert.
    if not skip_guards:
        guard_rep = run_fleet_guards(orchestra_dir, runner=guard_runner)
        report["guards"] = {"returncode": guard_rep["returncode"]}
        if guard_rep["returncode"] != 0:
            loud_alarm(
                orchestra_dir,
                f"fleet guard RED after a pass (apply={apply}) — "
                f"rc={guard_rep['returncode']}. NOT auto-reverting. "
                f"stdout tail: {guard_rep['stdout'][-800:]}",
                send_fn=send_fn)
            report["guards"]["red"] = True

    # STEP 8 — one summary line.
    summary = {
        "applied": bool(apply),
        "scanned": live_rep["scanned"],
        "skipped": len(live_rep["skipped_midfire"]) + len(live_rep["skipped_provisional"])
                  + len(live_rep["attached_skipped"]) + len(live_rep["nonlocal_skipped"]),
        "dead1": len(live_rep["dead1"]),
        "parked": len(live_rep["confirmed_park"]),
        "retired": len(live_rep["confirmed_retire"]),
        "recovered": len(live_rep["recovered"]),
        "sid_fixed": len(sid_rep["fixed"]),
        "sid_unresolved": len(sid_rep["unresolved"]),
        "sid_ambiguous_foreign_generation": len(sid_rep["ambiguous_foreign_generation"]),
        "alarms_suppressed": sid_rep.get("alarms_suppressed", 0),
        "model_fixed": report["model"]["resolved_count"],
        "guards_red": report.get("guards", {}).get("red", False),
    }
    emit_log_line(orchestra_dir, summary)
    report["summary"] = summary
    return report, 0


def format_dry_run_report(report):
    """Per-seat proposed-action lines + counts, for --dry-run stdout."""
    lines = []
    live = report.get("liveness", {})
    for root in live.get("dead1", []):
        lines.append(f"  {root:30s} DEAD-1 (no live process; 1/{ _DEAD_PARK_THRESHOLD } "
                     f"consecutive passes — not yet parked/retired)")
    for root in live.get("confirmed_park", []):
        lines.append(f"  {root:30s} -> PARK (dead x2, resume_command present)")
    for root in live.get("confirmed_retire", []):
        lines.append(f"  {root:30s} -> RETIRE (dead x2, no resume_command)")
    for root in live.get("recovered", []):
        lines.append(f"  {root:30s} -> ONLINE (recovered, 1 live pass)")
    for item in report.get("sid", {}).get("fixed", []):
        lines.append(f"  {item['root']:30s} SID-FIX {item['old_sid']} -> {item['new_sid']}")
    for root in report.get("sid", {}).get("unresolved", []):
        lines.append(f"  {root:30s} SID-UNRESOLVABLE (LOUD, no write)")
    for root in report.get("sid", {}).get("ambiguous_foreign_generation", []):
        lines.append(f"  {root:30s} SID-AMBIGUOUS (resolved/stored sid belongs to a "
                     f"DIFFERENT generation of this root — rotated-root predecessor-pane "
                     f"collision; LOUD, no write)")
    out = ["identity_reconciler dry-run report", "=" * 40]
    out.extend(lines or ["  (no proposed actions)"])
    out.append("")
    out.append(json.dumps(report.get("summary", {}), indent=2))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
    ap.add_argument("--cron", action="store_true",
                    help="APPLY the pass (the only apply path). Default: dry-run.")
    ap.add_argument("--dry-run", action="store_true",
                    help="explicit dry-run (default behavior; accepted for clarity)")
    ap.add_argument("--local-machine", default="vps")
    ap.add_argument("--skip-guards", action="store_true",
                    help="skip the step-7 fleet-guard self-check (debugging only)")
    a = ap.parse_args(argv)
    apply = bool(a.cron)
    report, rc = run_pass(a.dir, apply=apply, local_machine=a.local_machine,
                          skip_guards=a.skip_guards)
    if rc != 0:
        print(json.dumps(report, indent=2))
        return rc
    if not apply:
        print(format_dry_run_report(report))
    else:
        print(json.dumps(report.get("summary", {}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
