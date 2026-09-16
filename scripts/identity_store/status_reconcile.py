"""canonical.status truth reconcile — Identity Layer v1 item (a) (gm-specified, mechanical).

INVARIANT enforced: ``canonical.status=='online'`` <=> the row's ``tmux_session`` exists
AND its pane pid has >=1 live CHILD process (by effect, NEVER by name pattern). Otherwise
``'parked'`` (resumable; generations/lineages/sids untouched, resume_command preserved).

WHY: ``'online'`` is written only at insert/promote (orchestra_db.py); nothing ever sets a
seat offline, so the roster accretes phantom-canonical rows (an online seat with no live
process — the class that wedged ios-watch-dev and that every live-detector trips on). This
is the missing offline writer:
    online && !live  -> 'parked'
    parked && live   -> 'online'   (the ios-watch-dev-gen16 recovery class; reported)

A seat with an attached tmux CLIENT is LOGGED and SKIPPED (never flip a seat a human is on).
A seat whose lineage ``machine`` is not this host is SKIPPED (its liveness can't be judged
locally — never wrongly park a seat running elsewhere).

Writes are DB-first via the sanctioned ``identity_writer.update_registry_agent`` — BOTH the
typed ``canonical.status`` AND the source_record agent doc's ``status`` (the faithful
projector serves the doc verbatim, projector.py:419), so the flat roster follows and M1
stays 0-diff — then a single ``project_now``. Dry-run by default; ``--apply`` writes.
"""
import argparse
import json
import os
import subprocess

from scripts.identity_store import identity_writer, orchestra_db, status_vocab


def _default_has_session(tmux):
    return subprocess.run(["tmux", "has-session", "-t", tmux],
                          capture_output=True).returncode == 0


def _default_pane_has_child(tmux):
    """By effect: the pane's shell pid has >=1 child (the CLI/agent). A husk pane whose
    agent exited leaves the bare shell with no children => NOT live."""
    r = subprocess.run(["tmux", "list-panes", "-t", tmux, "-F", "#{pane_pid}"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return False
    for pp in r.stdout.split():
        kids = subprocess.run(["pgrep", "-P", pp.strip()], capture_output=True, text=True)
        if kids.stdout.strip():
            return True
    return False


def _default_list_clients(tmux):
    return subprocess.run(["tmux", "list-clients", "-t", tmux],
                          capture_output=True, text=True).stdout.strip()


# Service seats are NOT tmux-agent processes (item-b addendum, gm by effect): their
# liveness is a per-seat DECLARED probe — a listening port, or (for consumers with no
# port) a live pane process. A service NOT in this table resolves to parked with a LOUD
# reason=service:no-probe (never guessed online). Fill by effect from `ss -ltnp` + panes.
SERVICE_PROBES = {
    "proxy": {"port": 8091},                    # docker-proxy host-port 8091
    "cartesia-arturo-service": {"port": 5052},  # python3 LISTEN 127.0.0.1:5052
    "jarvis-service": {"port": 5060},           # python3 LISTEN 127.0.0.1:5060 (pane IS the proc)
    "jarvis-v2-mock": {"port": 5091},           # node LISTEN 127.0.0.1:5091
    "watch-gateway": {"port": 9091},            # python3 LISTEN 0.0.0.0:9091
    "lwe-feedback": {"pane_process": True},     # consumer, live pane child
    "pocket-service": {"pane_process": True},   # watchdog.sh, live pane child
    "pocket-webhook": {"pane_process": True},   # webhook, live pane child
    # reclassified from runtime=claude (gm msg_799b33cf); ports matched by effect from
    # `ss -ltnp` pid-tree at reclassification (2026-09-15).
    "arturo-proxy": {"port": 5071},             # python3 arturo-proxy.py LISTEN 127.0.0.1:5071
    "fable5-serve": {"port": 5174},             # node/vite dev LISTEN 127.0.0.1:5174
    "second-brain-svc": {"port": 7373},         # node server.js LISTEN 127.0.0.1:7373
}


def _default_port_listening(port):
    r = subprocess.run(["ss", "-ltn"], capture_output=True, text=True)
    return any(f":{port} " in ln or ln.rstrip().endswith(f":{port}")
               for ln in r.stdout.splitlines())


def service_is_live(seat, *, port_listening_fn=None, pane_has_child_fn=None):
    """Service liveness by DECLARED probe. Returns (is_live, reason). A seat with no
    declared probe => (False, 'service:no-probe') so it parks LOUD, never guessed online."""
    port_listening = port_listening_fn or _default_port_listening
    pane_has_child = pane_has_child_fn or _default_pane_has_child
    probe = SERVICE_PROBES.get(seat)
    if probe is None:
        return (False, "service:no-probe")
    if "port" in probe:
        return (port_listening(probe["port"]), f"service:port:{probe['port']}")
    if probe.get("pane_process"):
        return (pane_has_child(seat), "service:pane_process")
    return (False, "service:no-probe")


def _db_path(od):
    return os.path.join(od, "state", "orchestra-registry.db")


def reconcile(orchestra_dir, *, apply=False, local_machine="vps",
              has_session_fn=None, pane_has_child_fn=None, list_clients_fn=None,
              port_listening_fn=None):
    has_session = has_session_fn or _default_has_session
    pane_has_child = pane_has_child_fn or _default_pane_has_child
    list_clients = list_clients_fn or _default_list_clients
    port_listening = port_listening_fn or _default_port_listening

    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        rows = conn.execute(
            "SELECT c.root AS root, c.tmux_session AS tmux, c.status AS status, "
            "l.runtime AS runtime, l.machine AS machine, "
            "g.promoted_at AS promoted_at, g.retired_at AS gen_retired "
            "FROM canonical c "
            "JOIN generations g ON g.id = c.generation_id "
            "LEFT JOIN lineages l ON l.root = c.root "
            "WHERE g.retired_at IS NULL").fetchall()
        # separate anomaly: a canonical row pointing at a RETIRED generation.
        retired_gen_canonical = [r["root"] for r in conn.execute(
            "SELECT c.root AS root FROM canonical c JOIN generations g "
            "ON g.id = c.generation_id WHERE g.retired_at IS NOT NULL")]
    finally:
        conn.close()

    changes = []            # (root, old, new)
    attached_skipped, nonlocal_skipped, flagged_retired = [], [], []
    service_no_probe = []   # service seats with no declared probe (-> parked, LOUD)
    by_legacy = {}          # old_status -> {"target":.., "count":.., "examples":[..]}

    for r in rows:
        root, tmux, status = r["root"], r["tmux"], r["status"]
        rt, machine = r["runtime"], r["machine"]
        if machine and machine != local_machine:
            nonlocal_skipped.append(root)
            continue
        if status == "retired":
            # a canonical row must NOT carry 'retired' (gm: flag, never auto-remap).
            flagged_retired.append(root)
            continue
        if tmux and list_clients(tmux):
            attached_skipped.append(root)
            continue
        if rt == "service":
            # item-b addendum: a service seat is NOT a tmux-agent — liveness is a
            # per-seat DECLARED probe (port / pane-process). Unknown probe -> parked, LOUD.
            live, svc_reason = service_is_live(
                root, port_listening_fn=port_listening, pane_has_child_fn=pane_has_child)
            if svc_reason == "service:no-probe":
                service_no_probe.append(root)
        else:
            live = bool(tmux) and has_session(tmux) and pane_has_child(tmux)
        target = status_vocab.resolve(
            status, is_live=live, gen_promoted=r["promoted_at"] is not None)
        if target != status:
            changes.append((root, status, target))
            b = by_legacy.setdefault(status, {"target": None, "count": 0, "examples": []})
            b["count"] += 1
            if len(b["examples"]) < 3:
                b["examples"].append(root)
            # record the concrete resolved target(s) seen for this legacy value
            b["target"] = target if b["target"] in (None, target) else "by-liveness"

    to_parked = [root for root, _o, n in changes if n == "parked"]
    to_online = [root for root, _o, n in changes if n == "online"]
    to_provisional = [root for root, _o, n in changes if n == "provisional"]

    if apply and changes:
        by_target = {}
        for root, _o, n in changes:
            by_target.setdefault(n, []).append(root)
        for new_status, roots in by_target.items():
            _apply(orchestra_dir, roots, new_status)
        identity_writer.project_now(orchestra_dir)

    return {"to_parked": to_parked, "to_online": to_online,
            "to_provisional": to_provisional,
            "changes": [{"root": r, "old": o, "new": n} for r, o, n in changes],
            "by_legacy": by_legacy,
            "attached_skipped": attached_skipped, "nonlocal_skipped": nonlocal_skipped,
            "flagged_retired": flagged_retired, "service_no_probe": service_no_probe,
            "retired_gen_canonical": retired_gen_canonical,
            "applied": bool(apply), "scanned": len(rows)}


def _apply(orchestra_dir, roots, new_status):
    """Flip status DB-first: typed canonical.status + the source_record agent doc's status
    (read-modify-write, COMPLETE doc — the doc is served verbatim by the faithful projector)."""
    if not roots:
        return
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        docs = {r["key"]: json.loads(r["payload_json"]) for r in conn.execute(
            "SELECT key, payload_json FROM source_records "
            "WHERE file='registry.json' AND kind='agent'")}
    finally:
        conn.close()
    status_vocab.assert_valid(new_status)   # never persist a non-enum status
    for root in roots:
        doc = dict(docs.get(root) or {"name": root, "lineage_root": root})
        doc["status"] = new_status
        # update_registry_agent writes canonical.status (fields) AND the full doc (full_record)
        # in one txn under the cutover; project_now regenerates the flat afterwards.
        identity_writer.update_registry_agent(
            orchestra_dir, root, {"status": new_status}, full_record=doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="canonical.status truth reconcile (dry-run default)")
    ap.add_argument("--dir", default=os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
    ap.add_argument("--apply", action="store_true", help="write the flips (default: dry-run)")
    ap.add_argument("--local-machine", default="vps")
    a = ap.parse_args(argv)
    rep = reconcile(a.dir, apply=a.apply, local_machine=a.local_machine)
    out = {
        "applied": rep["applied"], "scanned": rep["scanned"],
        "total_changes": len(rep["changes"]),
        "to_parked": len(rep["to_parked"]), "to_online": len(rep["to_online"]),
        "to_provisional": len(rep["to_provisional"]),
        "by_legacy": rep["by_legacy"],
        "attached_skipped": rep["attached_skipped"],
        "nonlocal_skipped": rep["nonlocal_skipped"],
        "flagged_retired_status_canonical": {"count": len(rep["flagged_retired"]),
                                             "examples": rep["flagged_retired"][:10]},
        "service_no_probe": rep["service_no_probe"],
        "retired_gen_canonical": rep["retired_gen_canonical"],
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
