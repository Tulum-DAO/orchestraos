"""python -m orchestra_cli — see orchestra_cli/__init__.py for the command list."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import settings as S


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="orchestra", description="OrchestraOS install / doctor / supervisor")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="create data dir, orchestra.toml, venv, npm installs, builds (idempotent)")
    i.add_argument("--data-dir", help="where state/logs/registry live (default: [data] dir in orchestra.toml, else ~/.orchestra)")
    i.add_argument("--no-venv", action="store_true", help="skip python venv + pip")
    i.add_argument("--no-npm", action="store_true", help="skip npm install (and builds)")
    i.add_argument("--no-build", action="store_true", help="skip api/dashboard builds")
    i.add_argument("--demo", action="store_true",
                   help="seed three fixture seats and one card of each kind (approval, menu, questionnaire, human task)")

    d = sub.add_parser("doctor", help="check CLIs+auth, ports, tmux, config keys, builds, rotation beat")
    d.add_argument("--json", action="store_true")

    u = sub.add_parser("up", help="run gateway + api + dashboard + arturo + beats under one supervisor")
    u.add_argument("--dry-run", action="store_true", help="print the process table and exit")
    u.add_argument("-d", "--detach", action="store_true", help="run the supervisor in the background")

    sub.add_parser("down", help="stop the running supervisor and its children")
    s = sub.add_parser("status", help="show the supervisor's process table")
    s.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def _settings() -> S.Settings:
    return S.load_settings()


def cmd_init(ns) -> int:
    from .init_cmd import render_report, run_init
    root = S.repo_root_from_env()
    report = run_init(root, data_dir=Path(ns.data_dir) if ns.data_dir else None,
                      skip_npm=ns.no_npm, skip_venv=ns.no_venv, skip_build=ns.no_build, demo=ns.demo)
    print(render_report(report))
    failed = [r for r in report if not r.did and ("failed" in r.detail)]
    st = _settings()
    print(f"\ndata dir: {st.data_dir}\nconfig:   {st.config_path}\nnext:     orchestra doctor && orchestra up")
    return 1 if failed else 0


def cmd_doctor(ns) -> int:
    from . import doctor as D
    st = _settings()
    checks = D.run_doctor(st, D.default_probes(st))
    print(D.render_json(checks) if ns.json else D.render_table(checks))
    return D.exit_code(checks)


def cmd_up(ns) -> int:
    from . import process_table as PT
    from . import supervisor as SV
    st = _settings()
    table = PT.build_process_table(st)
    if ns.dry_run:
        print(f"config: {st.config_path}\ndata:   {st.data_dir}\n")
        print(PT.render_table(table))
        return 0
    if not st.config_exists:
        print(f"no config at {st.config_path} — run `orchestra init` first", file=sys.stderr)
        return 2
    missing = S.missing_required(st.raw)
    if missing:
        print("orchestra.toml missing required keys: " + ", ".join(missing), file=sys.stderr)
        return 2
    status = SV.read_status(st.data_dir)
    if status["running"]:
        print(f"supervisor already running (pid {status['pid']}); use `orchestra status` / `orchestra down`",
              file=sys.stderr)
        return 1
    if ns.detach:
        (st.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        log = open(st.data_dir / "logs" / "supervisor.log", "ab")
        child = subprocess.Popen([sys.executable, "-m", "orchestra_cli", "up"], cwd=str(st.repo_root),
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, env=dict(os.environ, ORCHESTRA_ROOT=str(st.repo_root)))
        print(f"supervisor started in background (pid {child.pid}); logs: {st.data_dir / 'logs'}")
        return 0
    env = S.child_env(st)
    (st.data_dir / "logs").mkdir(parents=True, exist_ok=True)

    def _log(msg):
        print(f"[orchestra] {msg}", flush=True)

    sup = SV.Supervisor(table, data_dir=st.data_dir, base_env=env, log=_log)
    _log(f"up: data={st.data_dir} dashboard=http://{st.dashboard_host}:{st.dashboard_port}")
    sup.run()
    return 0


def cmd_down(ns) -> int:
    from . import supervisor as SV
    st = _settings()
    if SV.stop_running(st.data_dir):
        print("supervisor stopped")
        return 0
    print("no running supervisor (or it did not exit in time)")
    return 1


def cmd_status(ns) -> int:
    from . import supervisor as SV
    st = _settings()
    status = SV.read_status(st.data_dir)
    if ns.json:
        print(json.dumps(status, indent=1))
    else:
        print(f"supervisor: {'running pid ' + str(status['pid']) if status['running'] else 'not running'}")
        for name, ch in status["children"].items():
            when = f":{ch.get('port')}" if ch.get("kind") == "service" else f"every {ch.get('interval')}s"
            print(f"  {name:18} {ch.get('status', '?'):10} pid={ch.get('pid') or '-':<7} {when:12} restarts={ch.get('restarts', 0)}")
    return 0 if status["running"] else 3


def main(argv=None) -> int:
    ns = parse_args(argv)
    return {"init": cmd_init, "doctor": cmd_doctor, "up": cmd_up, "down": cmd_down, "status": cmd_status}[ns.command](ns)


if __name__ == "__main__":
    sys.exit(main())
