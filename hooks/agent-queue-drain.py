#!/usr/bin/env python3
"""agent-queue-drain — fleet-wide Stop-hook mail digest (2026-08-18,
Generalized fleet-wide digest; gm rides it too.

At every Stop: resolve this tmux session to its canonical registry agent (alias
chains included), and if allowlisted, check for pending self-bound mail older
than AGE_GATE_S — including mail addressed to retired lineage aliases (the
gm-gen12 alias-mail class). If present: BLOCK the stop once with a
[QUEUE-DIGEST] listing and stamp surfaced_by on the listed rows so the router's
digest-gate (_hook_surfaced, message-router.py:689) never re-presents them.
The hook NEVER claims/delivers — the agent reads via store and acks after
processing; held_message rows are the boundary lane's and are never touched.

Safety set (per-agent): 30s age-gate · per-agent marker file (unchanged-head = no
re-block) · fail-open on ANY error · stop_hook_active guard · LIMIT. Public default:
ON for every resolved seat, gm included (no allowlist file needed; write
<data>/state/queue-drain-allowlist.json {"enabled":true,"default_on":false,"agents":[...],
"exclude":[...]} to narrow it). All paths env-overridable (AQD_*) for hermetic tests.
"""
import json
import os
import sqlite3
import sys
import time

# DATA dir (registry.json, state/tasks.db, marker files). `orchestra init` bakes
# ORCHESTRA_DIR into the installed hook command; the fallback is the default data dir.
ORCH = os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or os.path.expanduser("~/orchestra")
AGE_GATE_S = 30
MAX_LINES = 12
QUERY_LIMIT = 50


def _p(env, key, default):
    return (env or {}).get(key) or os.environ.get(key) or default


def resolve_canonical(session, registry, max_hops=10):
    """tmux session name -> (canonical_agent_id, alias_set) or None.
    Match on registry key OR record.tmux_session; follow succeeded_by chains
    (cycle-safe). alias_set = every key whose chain terminates at canonical."""
    agents = (registry or {}).get("agents") or {}
    start = None
    if session in agents:
        start = session
    else:
        for k, rec in agents.items():
            if isinstance(rec, dict) and rec.get("tmux_session") == session:
                start = k
                break
    if start is None:
        return None
    canon, seen = start, {start}
    for _ in range(max_hops):
        nxt = (agents.get(canon) or {}).get("succeeded_by")
        if not nxt or nxt in seen or nxt not in agents:
            if nxt in seen:
                return None            # cycle — refuse rather than guess
            break
        seen.add(nxt)
        canon = nxt
    aliases = {canon}
    for k in agents:
        cur, hop, chain = k, 0, {k}
        while hop < max_hops:
            nxt = (agents.get(cur) or {}).get("succeeded_by")
            if not nxt or nxt in chain or nxt not in agents:
                break
            cur = nxt
            chain.add(cur)
            hop += 1
        if cur == canon:
            aliases.add(k)
    return canon, aliases


def _stamp_surfaced(db_path, ids, canon, batch_id=None, processed_ts=None):
    """Best-effort surfaced_by stamp (router digest-gate contract). Dict-merge
    like msg_store.stamp_metadata; never raises.

    queued-native-render B2 (DEC-1789392107605493): also stamps batch_id +
    processed_ts WRITE-ONCE (setdefault) so a persistent row's batch identity is
    fixed at its FIRST surfacing and never migrates on a partial-ack overlapping
    re-drain ({A,B,C} -> {C,D} would otherwise re-batch C). surfaced_by stays an
    overwrite (each drain re-asserts the router gate); batch fields do not."""
    try:
        c = sqlite3.connect(db_path, timeout=3)
        c.row_factory = sqlite3.Row
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        for mid in ids:
            try:
                row = c.execute("SELECT metadata FROM messages WHERE id=?", [mid]).fetchone()
                if row is None:
                    continue
                try:
                    md = json.loads(row["metadata"]) if row["metadata"] else {}
                    if not isinstance(md, dict):
                        md = {}
                except (ValueError, TypeError):
                    md = {}
                md.update({"surfaced_by": f"stop-hook:{canon}", "hook_surfaced_at": now})
                if batch_id is not None:
                    md.setdefault("batch_id", batch_id)          # WRITE-ONCE — no cross-batch migration
                if processed_ts is not None:
                    md.setdefault("processed_ts", processed_ts)  # WRITE-ONCE — batch's first-surface time
                c.execute("UPDATE messages SET metadata=? WHERE id=?", [json.dumps(md), mid])
            except sqlite3.Error:
                continue
        c.commit()
        c.close()
    except Exception:  # noqa: BLE001 — stamping is best-effort, digest still valid
        pass


def run(payload, env=None):
    """Core, hermetic-testable. Returns the block dict or None (= exit 0)."""
    try:
        if (payload or {}).get("stop_hook_active"):
            return None
        # allowlist gate (absent/disabled/malformed -> inert)
        allow_path = _p(env, "AQD_ALLOWLIST",
                        os.path.join(ORCH, "state", "queue-drain-allowlist.json"))
        # Public default: no allowlist file = drain ON for every resolved seat. A present
        # file is honored exactly (enabled=false = inert; malformed = inert, fail-open).
        if os.path.exists(allow_path):
            try:
                allow = json.loads(open(allow_path).read())
            except Exception:
                return None
            if not (isinstance(allow, dict) and allow.get("enabled")):
                return None
        else:
            allow = {"enabled": True, "default_on": True}
        # session -> canonical (registry)
        session = _p(env, "AQD_TMUX_SESSION", "") or _p(env, "AQD_SESSION", "")
        if not session:
            try:
                import subprocess
                session = subprocess.run(["tmux", "display-message", "-p", "#S"],
                                         capture_output=True, text=True,
                                         timeout=3).stdout.strip()
            except Exception:
                return None
        reg_path = _p(env, "AQD_REGISTRY", os.path.join(ORCH, "registry.json"))
        try:
            registry = json.loads(open(reg_path).read())
        except Exception:
            return None
        resolved = resolve_canonical(session, registry)
        if not resolved:
            return None
        canon, aliases = resolved
        # public: gm rides this same drain (the operator's separate gm-queue-drain.py is
        # a pre-cutover artifact and is not shipped).
        # DEFAULT-ON (2026-08-25): the drain
        # fires for EVERY resolved canonical Claude agent — Stop hooks only run for
        # Claude seats (gemini/service never trigger it) and retired aliases resolve
        # via succeeded_by, so the population is self-bounding with NO per-agent list
        # to maintain. `enabled` (above) is the master kill-switch; `exclude` is a
        # denylist for the rare agent we want left on pure router drip-feed. The old
        # `agents` allowlist is honored as a fallback iff no default-on flag is set.
        if allow.get("default_on", True):
            if canon in (allow.get("exclude") or []):
                return None
        elif canon not in (allow.get("agents") or []):
            return None
        # pending self-bound mail past the age gate (held rows = boundary lane's)
        db_path = _p(env, "AQD_DB", os.path.join(ORCH, "state", "tasks.db"))
        cutoff = time.time() - AGE_GATE_S
        try:
            c = sqlite3.connect(db_path, timeout=3)
            ph = ",".join("?" * len(aliases))
            # Answer fast-lane (2026-08-26, after
            # apr_2870afa0 stranded): type='approval_resolved' rows BYPASS the
            # age gate — an operator answer must surface at the very next turn
            # boundary (the push lane already tried and failed/held; there is
            # no router fast-path worth waiting 120s for). All other mail
            # keeps the gate.
            rows = c.execute(
                f"SELECT id, from_agent, subject, created_at FROM messages "
                f"WHERE to_agent IN ({ph}) AND status='pending' "
                f"AND type != 'held_message' "
                f"AND (type = 'approval_resolved' OR strftime('%s', created_at) < ?) "
                # Answers sort FIRST (claude-reviewer fold): under a 50+ row
                # backlog, plain created_at ASC would truncate the NEWEST rows
                # — i.e. exactly the fresh answer — out of the LIMIT window.
                f"ORDER BY (type = 'approval_resolved') DESC, created_at "
                f"LIMIT {QUERY_LIMIT}",
                list(aliases) + [str(int(cutoff))]).fetchall()
            c.close()
        except Exception:
            return None
        if not rows:
            marker = os.path.join(_p(env, "AQD_MARKER_DIR", os.path.join(ORCH, "state")),
                                  f".agent-queue-drain-marker.{canon}")
            try:
                os.path.exists(marker) and os.remove(marker)
            except OSError:
                pass
            return None
        # Loop-brake on the FULL pending id SET, not the newest id (AGY fold,
        # DEC-1787727130): with newest-id anchoring, an un-acked fast-lane
        # answer row would keep the head unchanged while an OLDER ordinary row
        # later graduates the age gate — falsely suppressed forever. Any set
        # change (new, graduated, acked) re-blocks; an identical set never does.
        import hashlib
        head = hashlib.sha256(",".join(r[0] for r in rows).encode()).hexdigest()
        marker = os.path.join(_p(env, "AQD_MARKER_DIR", os.path.join(ORCH, "state")),
                              f".agent-queue-drain-marker.{canon}")
        try:
            if os.path.exists(marker) and open(marker).read().strip() == head:
                return None        # unchanged pending SET: never re-block (loop brake)
            open(marker, "w").write(head)
        except OSError:
            return None
        # queued-native-render B2: derive this drain's batch identity from the
        # pending id-SET hash (head) + drain time, then stamp WRITE-ONCE. A row
        # already carrying a batch_id (surfaced in a prior drain) keeps it.
        proc_ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        batch_id = hashlib.sha256(f"{head}|{proc_ts}".encode()).hexdigest()[:16]
        _stamp_surfaced(db_path, [r[0] for r in rows], canon, batch_id, proc_ts)
        lines = [
            f"  {r[0]} | {r[1]} | {(r[2] or '(no subject)')[:60]} — python3 msg_store.py get --id {r[0]} ; python3 msg_store.py ack --id {r[0]}"
            for r in rows[:MAX_LINES]
        ]
        more = f" (+{len(rows) - MAX_LINES} more)" if len(rows) > MAX_LINES else ""
        return {"decision": "block",
                "reason": (f"[QUEUE-DIGEST] {len(rows)} pending message(s) for {canon} "
                           f"older than {AGE_GATE_S}s{more} — batch-process now (read "
                           f"each via python3 msg_store.py get --id <id>, ack after processing via "
                           f"python3 msg_store.py ack --id <id>, stale-banner "
                           f"discipline applies):\n" + "\n".join(lines))}
    except Exception:  # noqa: BLE001 — a mail hook must never wedge a session
        return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    out = run(payload, env=None)
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
