"""Audit-file -> focus-store importer + CLI (WS1).

`import_from_audit` is the idempotent, re-runnable glue: read the latest fleet
audit, parse its focus-cluster tables, upsert the focus entities into the thin
store. Re-run whenever the audit refreshes.
"""
import argparse
import glob
import os
import re
from typing import Optional

from scripts.focus_registry.parse import parse_audit
from scripts.focus_registry.store import DEFAULT_STORE, import_focuses, load_store

_ORCHESTRA_DIR = os.environ.get(
    "ORCHESTRA_DIR", os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
)
_DEFAULT_DOCS = os.path.join(_ORCHESTRA_DIR, "docs")
_AUDIT_DATE_RE = re.compile(r"FLEET_AUDIT_(\d{4}-\d{2}-\d{2})\.md$")


def latest_audit_path(docs_dir: str = _DEFAULT_DOCS) -> Optional[str]:
    """Newest dated FLEET_AUDIT_YYYY-MM-DD.md in docs_dir, or None."""
    candidates = glob.glob(os.path.join(docs_dir, "FLEET_AUDIT_*.md"))
    dated = [(m.group(1), p) for p in candidates if (m := _AUDIT_DATE_RE.search(p))]
    if not dated:
        return None
    return max(dated, key=lambda t: t[0])[1]


def import_from_audit(audit_path: str, store_path: str = DEFAULT_STORE) -> dict:
    with open(audit_path) as fh:
        focuses = parse_audit(fh.read())
    return import_focuses(focuses, store_path, source=audit_path)


def _cmd_import(args) -> int:
    audit = args.audit or latest_audit_path()
    if not audit:
        print("no FLEET_AUDIT_*.md found; pass --audit")
        return 1
    store = import_from_audit(audit, args.store)
    print(f"imported {len(store['entities'])} focuses from {audit} -> {args.store}")
    return 0


def _cmd_list(args) -> int:
    store = load_store(args.store)
    for e in sorted(store.get("entities", {}).values(), key=lambda x: x["attrs"].get("category", "")):
        a = e["attrs"]
        print(
            f"[{a.get('category',''):16}] {e['canonical']:34} "
            f"{str(a.get('pct_done'))+'%':5} rel={a.get('relevance')} imp={a.get('importance')} "
            f"owner={e.get('owner')} status={e.get('status')}"
        )
    return 0


def _cmd_supervise(args) -> int:
    """WS4 OBSERVE beat: propose feeding idle owned focuses their next item.

    LOG-ONLY / DRY-RUN — drives NO agent, sends nothing, installs no cron. Reads the
    focus store + an idle signal (--source events = the hook-event bus; --source
    status = a read-only agent-status pass) + a backlog (--backlog JSON, else empty).
    With no backlog wired the beat honestly yields 0 proposals (Task-8). --log appends
    the report as one JSONL line (the observe proposal log)."""
    import json as _json
    import subprocess
    import time
    from datetime import datetime, timezone, timedelta
    from scripts.focus_registry.supervisor import dry_run, dry_run_from_events

    store = load_store(args.store)
    items_by_focus = {}
    if args.backlog:
        with open(args.backlog) as fh:
            items_by_focus = _json.load(fh)

    meta = {"observe_ts": time.time(), "source": args.source, "drives_live_agent": False}

    if args.source == "events":
        from scripts.lineage_daemon import bus
        # hot window (default 7d) of the event stream; missing dir -> [] (honest).
        days = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(args.hot_days)]
        rd = bus.read_events(args.events_dir, days=days)
        events = rd.get("events", [])
        meta["events_read"] = len(events)
        meta["events_skipped"] = rd.get("skipped_lines", 0)
        meta["events_dir_exists"] = os.path.isdir(args.events_dir)
        report = dry_run_from_events(store, events, items_by_focus)
    else:
        status_list = []
        try:
            out = subprocess.check_output(
                ["python3", "scripts/agent-status.py", "--all"], cwd=_ORCHESTRA_DIR, text=True)
            status_list = _json.loads(out)
        except Exception as e:
            print(f"(agent-status unavailable: {e}; treating all owners as non-idle)")
        report = dry_run(store, status_list, items_by_focus)

    report.update(meta)
    report["backlog_wired"] = bool(items_by_focus)

    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        with open(args.log, "a") as fh:
            fh.write(_json.dumps(report) + "\n")
        print(f"observe report appended to {args.log}")
    print(_json.dumps(report, indent=2))
    return 0


def _cmd_drift(args) -> int:
    from scripts.focus_registry.resolve import is_drift

    store = load_store(args.store)
    drifting = [a for a in args.agents if is_drift(f"agent:{a}" if not a.startswith("agent:") else a, store)]
    if drifting:
        print("DRIFT (attached to no active focus):")
        for a in drifting:
            print(f"  - {a}")
    else:
        print("no drift among the given agents")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Focus registry (WS1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import", help="import focuses from the latest fleet audit")
    p_imp.add_argument("--audit", help="path to a FLEET_AUDIT_*.md (default: latest in docs/)")
    p_imp.add_argument("--store", default=DEFAULT_STORE)
    p_imp.set_defaults(func=_cmd_import)

    p_list = sub.add_parser("list", help="list tracked focuses (dashboard source)")
    p_list.add_argument("--store", default=DEFAULT_STORE)
    p_list.set_defaults(func=_cmd_list)

    p_drift = sub.add_parser("drift", help="flag agents attached to no active focus")
    p_drift.add_argument("agents", nargs="+", help="agent names to check")
    p_drift.add_argument("--store", default=DEFAULT_STORE)
    p_drift.set_defaults(func=_cmd_drift)

    p_sup = sub.add_parser("supervise", help="WS4 OBSERVE beat (log-only proposals; drives nothing)")
    p_sup.add_argument("--backlog", help="JSON {focus_id: [items]} backlog source (default: none)")
    p_sup.add_argument("--source", choices=["status", "events"], default="status",
                       help="idle signal: agent-status (default) or the hook-event bus stream")
    p_sup.add_argument("--events-dir",
                       default=os.path.join(_ORCHESTRA_DIR, "state", "event-stream"),
                       help="bus event-stream dir (for --source events)")
    p_sup.add_argument("--hot-days", type=int, default=7, help="event hot-window in days")
    p_sup.add_argument("--log", help="append the observe report as one JSONL line to this path")
    p_sup.add_argument("--store", default=DEFAULT_STORE)
    p_sup.set_defaults(func=_cmd_supervise)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
