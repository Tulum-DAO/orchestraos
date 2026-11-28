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


def _default_data_dir():
    """The install's data dir when no ORCHESTRA_DIR was baked into the hook command: the ONE default,
    orchestra_cli.settings.data_dir (data-dir sweep S5), imported from this hook's checkout only then."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.append(root)
    from orchestra_cli.settings import data_dir
    return str(data_dir())


# DATA dir (registry.json, state/tasks.db, marker files). `orchestra init` bakes
# ORCHESTRA_DIR into the installed hook command; the fallback is the default data dir.
ORCH = os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or _default_data_dir()
AGE_GATE_S = 30
MAX_LINES = 12
QUERY_LIMIT = 50

# A promote_notice under PROMOTE_YIELD_S old is pending for this seat: yield the turn boundary (no digest)
# so the pane goes idle and the router types the wake in on its next pass with delivered_at set. Listing it
# here instead had the seat ack it from the store, so the router never delivered it (gm msg_5ad16a17).
# One range read on idx_messages_inbox (to_agent, status, created_at); any error falls back to the digest.
# Bounded twice: the notice must be under PROMOTE_YIELD_S old (and not over 60 s in the future), and one
# continuous yield episode per seat ends PROMOTE_YIELD_S after it began (`episode` marker file, monotonic
# clock), whatever the stamps or the wall clock say. A marker that cannot be written, or reads non-finite or in the future, means no yield.
PROMOTE_YIELD_S = 600


def _promote_wake_pending(conn, aliases, now=None, episode=None):
    try:
        from datetime import datetime, timezone
        t = time.time() if now is None else now
        lo = datetime.fromtimestamp(t - PROMOTE_YIELD_S, timezone.utc).isoformat()
        hi = datetime.fromtimestamp(t + 60, timezone.utc).isoformat()   # a future stamp never extends it
        names = sorted(aliases)
        pending = conn.execute(
            f"SELECT 1 FROM messages WHERE to_agent IN ({','.join('?' * len(names))}) "
            "AND status='pending' AND created_at >= ? AND created_at <= ? AND type='promote_notice' LIMIT 1",
            names + [lo, hi]).fetchone() is not None
        if not pending:
            if episode and os.path.exists(episode):
                os.remove(episode)
            return False
        if episode:
            # Timed on the system-wide monotonic clock (shared by every process, immune to wall-clock
            # changes) and keyed to the boot, since that clock restarts at boot.
            try:
                boot = open("/proc/sys/kernel/random/boot_id").read().strip()
            except OSError:
                boot = "-"
            m = time.monotonic()
            try:
                b, began = open(episode).read().split()
                began = float(began)
                if b != boot:
                    raise ValueError("another boot")
            except (OSError, ValueError):
                began = m
                with open(episode, "w") as fh:
                    fh.write(f"{boot} {m}")
            return 0 <= m - began < PROMOTE_YIELD_S      # NaN/inf or a start in the future: no yield
        return True
    except Exception:  # noqa: BLE001 — an unreadable store must never cost the seat its digest
        return False


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



# ---------------------------------------------------------------------------------------------------------------------
# ACK-CONTINUE (Shaw focus #3, gm msg_8d8d1220). A seat whose work takes more than a few seconds first sends a FINAL reply
# that is a one-line acknowledgement ending with the marker line below; the operator sees it at once. This hook then
# blocks the stop with "continue", so the seat starts the work in the next turn with no router hold. A progress reply may
# end with the marker again; the answer does not. Decided from the session's own transcript, before any registry lookup,
# so it works for any Claude session; capped per session so a reply that always carries the marker cannot loop forever.
# ---------------------------------------------------------------------------------------------------------------------
ACK_MARKER = "\u2937 continuing"
ACK_CONTINUE_CAP = 12


def _last_assistant_text(transcript_path, max_bytes=262144):
    """The text of the session's final reply, or None (the last transcript turn is not an assistant text message)."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return None
    for raw in reversed(lines):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        kind = obj.get("type") if isinstance(obj, dict) else None
        if kind == "user":
            return None
        if kind != "assistant":
            continue
        parts = (obj.get("message") or {}).get("content") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(texts) if texts else None
    return None



def _ack_log(path, payload, n, capped, env=None):
    """One line per continue (and per cap): UTC time, seat, session, consecutive count, capped. Never raises."""
    try:
        seat = (env or {}).get("AQD_TMUX_SESSION") or os.environ.get("AQD_TMUX_SESSION", "")
        if not seat and os.environ.get("TMUX") and os.environ.get("TMUX_PANE"):
            import subprocess
            seat = subprocess.run(["tmux", "display-message", "-p", "-t", os.environ["TMUX_PANE"], "#S"],
                                  capture_output=True, text=True, timeout=2).stdout.strip()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} seat={seat or '-'} "
                     f"session={str((payload or {}).get('session_id') or '-')[:36]} n={n} capped={'yes' if capped else 'no'}\n")
    except Exception:  # noqa: BLE001
        pass


def ack_log_path():
    return os.environ.get("AQD_ACK_LOG") or os.path.join(
        os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra"), "logs", "ack-continue.log")


def ack_continue(payload, state_dir, tag, env=None):
    """{"decision": "block", "reason": "continue"} when the final reply ends with ACK_MARKER, else None. Never raises."""
    try:
        # The CLI hands the final reply to Stop hooks (last_assistant_message). The transcript is only a fallback:
        # at the moment the hook runs, its last line can still be the user's message (seen in the lab, 2026-11-22).
        text = (payload or {}).get("last_assistant_message")
        if not isinstance(text, str):
            text = _last_assistant_text((payload or {}).get("transcript_path") or "")
        sid = str((payload or {}).get("session_id") or "nosession")
        sid = "".join(c for c in sid if c.isalnum() or c in "-_")[:80] or "nosession"
        counter = os.path.join(state_dir, f".ack-continue.{tag}.{sid}")
        lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
        if not lines or lines[-1] != ACK_MARKER:
            if os.path.exists(counter):
                os.remove(counter)
            return None
        try:
            n = int(open(counter).read().strip() or 0)
        except (OSError, ValueError):
            n = 0
        if n >= ACK_CONTINUE_CAP:
            os.remove(counter)
            _ack_log(ack_log_path(), payload, n, True, env)
            return None
        os.makedirs(state_dir, exist_ok=True)
        with open(counter, "w") as fh:
            fh.write(str(n + 1))
        _ack_log(ack_log_path(), payload, n + 1, False, env)
        return {"decision": "block", "reason": "continue"}
    except Exception:  # noqa: BLE001 -- a mail hook must never wedge a session
        return None


def run(payload, env=None):
    """Core, hermetic-testable. Returns the block dict or None (= exit 0)."""
    try:
        ack = ack_continue(payload, _p(env, "AQD_MARKER_DIR", os.path.join(ORCH, "state")), "orchestraos", env)
        if ack:
            return ack
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
            if _promote_wake_pending(c, aliases, episode=os.path.join(
                    _p(env, "AQD_MARKER_DIR", os.path.join(ORCH, "state")), f".promote-yield.{canon}")):
                c.close()
                return None
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


# ---------------------------------------------------------------------------------------------------------------------
# HELD-ROW KICK (gm G1 ruling msg_fbd39704). A person's message the gateway HELD for this seat (a type='held_message'
# row: watch_gateway._hold_for_boundary, metadata.held_for_boundary) is delivered by the router, never by this digest.
# Nothing told the router that the turn had ended, so the row waited for the next beat. At a Stop that really ends the
# turn (this hook is not blocking it), one indexed query asks whether such a row is pending for THIS seat; only then a
# detached router pass is started. It never prints, never raises and never waits: what the hook prints is unchanged.
# ---------------------------------------------------------------------------------------------------------------------
def _seat_targets(env):
    """(db_path, every to_agent this seat answers to: its tmux session, its canonical id and the aliases), or None."""
    session = _p(env, "AQD_TMUX_SESSION", "") or _p(env, "AQD_SESSION", "")
    if not session:
        import subprocess
        session = subprocess.run(["tmux", "display-message", "-p", "#S"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
    if not session:
        return None
    targets = {session}
    try:
        registry = json.loads(open(_p(env, "AQD_REGISTRY", os.path.join(ORCH, "registry.json"))).read())
        resolved = resolve_canonical(session, registry)
        if resolved:
            targets |= {resolved[0]} | set(resolved[1])
    except Exception:  # noqa: BLE001 — no registry: the session name alone (the gateway addresses held rows by it)
        pass
    return _p(env, "AQD_DB", os.path.join(ORCH, "state", "tasks.db")), targets


def _held_row_pending(db_path, targets):
    """One indexed query (idx_messages_type on type, status): a pending held row for any of `targets`."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    try:
        ph = ",".join("?" * len(targets))
        return c.execute(f"SELECT 1 FROM messages WHERE type = 'held_message' AND status = 'pending' "
                         f"AND to_agent IN ({ph}) LIMIT 1", list(targets)).fetchone() is not None
    finally:
        c.close()


def _router_kick(db_path):
    """A detached `message-router.py --cron` for the installed bus: msg_store.kick_router_after_send from this
    hook's own checkout (its guards: installed bus only, never under pytest or MSG_STORE_NO_KICK, flock-serialised)."""
    root = os.environ.get("ORCHESTRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.append(root)
    from msg_store import kick_router_after_send
    kick_router_after_send(db_path)


def kick_if_held(env=None):
    """True when a router pass was requested. Never raises."""
    try:
        seat = _seat_targets(env)
        if not seat or not _held_row_pending(*seat):
            return False
        _router_kick(seat[0])
        return True
    except Exception:  # noqa: BLE001 — a kick must never cost the hook anything
        return False


def handle(payload, env=None):
    """The Stop: the digest (run), then — only when the turn really ends — the held-row kick."""
    out = run(payload, env=env)
    if not out:
        kick_if_held(env)
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    out = handle(payload, env=None)
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
