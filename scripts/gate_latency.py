#!/usr/bin/env python3
"""gate_latency.py — wall-time of hooks/gate-hook.py over the gate fixtures, in a SCRATCH data dir (plan §6, R7b).

Replays every call in scripts/fixtures/gate/calls.jsonl (placeholders expanded with gate_fixture_harvest.expand)
as PreToolUse input to `<python> hooks/gate-hook.py`, one subprocess per call, the way Claude Code runs it, and
reports p50 / p95 / max per scenario:

  gate_off      GATE_OFF present (budget p95 <= 25 ms)
  no_hit        mode enforce, the probe agent on Supervised (Watch closely): the calls that logged nothing
                (budget p95 <= 45 ms)
  let_them_run  mode enforce, the seeded defaults (Let them run): the calls with hits (budget p95 <= 60 ms) and
                every call (budget p95 <= 60 ms)
  held          mode enforce, Supervised, the gateway URL on a closed port, so no card is ever filed: the calls the
                gate held (Watch closely -> deny "gateway down"; budget p95 <= 400 ms)

It never touches a live install: the data dir must be a scratch dir (default: a fresh temp dir), and the live data
dir (~/.orchestra, or the install's configured one) or anything inside it is refused.

    python3 scripts/gate_latency.py [--data-dir DIR] [--python PY] [--json] [--check]

--check exits 1 when any budget is missed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOOK = os.path.join(REPO, "hooks", "gate-hook.py")
FIXTURES = os.path.join(HERE, "fixtures", "gate", "calls.jsonl")
sys.path.insert(0, HERE)

PERSON = "operator"
AGENT = "probe"
BUDGETS = {"gate_off": 25, "no_hit": 45, "let_them_run.hits": 60, "let_them_run.all": 60, "held": 400}


def _live_dirs() -> list:
    out = [os.path.realpath(os.path.expanduser("~/.orchestra"))]
    try:
        import approval_config
        out.append(os.path.realpath(str(approval_config.LIVE_ORCHESTRA_DIR)))
    except Exception:  # noqa: BLE001
        pass
    return out


def refuse_live(data_dir: str) -> str | None:
    """Why this data dir is refused (the live one or inside it), or None."""
    real = os.path.realpath(data_dir)
    for live in _live_dirs():
        if real == live or real.startswith(live.rstrip("/") + "/"):
            return f"refusing the live data dir {live}: gate_latency.py runs in a scratch dir only"
    return None


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * q))], 1)


def _stats(xs):
    return {"n": len(xs), "p50": _pct(xs, 0.5), "p95": _pct(xs, 0.95), "max": round(max(xs), 1) if xs else None}


class Scratch:
    def __init__(self, data_dir, py):
        import gate_config as GCF
        from gate_fixture_harvest import FAKE_CWD
        self.GCF, self.data, self.py = GCF, data_dir, py
        os.makedirs(os.path.join(data_dir, "state", "attention"), exist_ok=True)
        with open(os.path.join(data_dir, "registry.json"), "w") as fh:
            json.dump({"agents": {AGENT: {"cwd": FAKE_CWD, "runtime": "claude", "tmux_session": AGENT}}}, fh)
        os.environ["APPROVAL_DDL_ARMED"] = "all"
        os.environ["ORCHESTRA_DIR"] = data_dir
        from approval_schema import ApprovalStore
        from supervision_store import SupervisionStore
        db = os.path.join(data_dir, "state", "tasks.db")
        ApprovalStore(db_path=db).migrate()
        self.ss = SupervisionStore(db_path=db)
        self.ss.migrate()
        self.ss.seed(PERSON, [FAKE_CWD])
        self.supervised = next(r["id"] for r in self.ss.roles(PERSON) if r.get("starter") == "supervised")
        self.port = _closed_port()
        GCF.write_gate(data_dir, mode="enforce", person=PERSON, gateway_url=f"http://127.0.0.1:{self.port}",
                       loopback_hosts=["127.0.0.1", "localhost", "::1"],
                       default={"level": "let_them_run", "high_risk": [], "hold_wait_s": 0}, agents={}, gen=1)
        self.env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        self.env.update({"ORCHESTRA_DIR": data_dir, "ORCHESTRA_AGENT_ID": AGENT, "APPROVAL_DDL_ARMED": "all",
                         "GATE_CARDS_NOTIFY": "0"})
        self.env.pop("PYTEST_CURRENT_TEST", None)

    def supervise(self, on: bool):
        self.ss.assign(PERSON, "agent", AGENT, self.supervised if on else None, by=PERSON)

    def kill(self, on: bool):
        p = self.GCF.paths(self.data)["kill"]
        if on:
            open(p, "w").close()
        elif os.path.exists(p):
            os.unlink(p)

    def log_len(self) -> int:
        try:
            with open(self.GCF.paths(self.data)["log"]) as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    def last_log(self) -> dict:
        r = self.GCF.read_log(self.data, limit=1)
        return r[-1] if r else {}

    def run(self, inp) -> tuple:
        before = self.log_len()
        t0 = time.monotonic()
        r = subprocess.run([self.py, HOOK], input=json.dumps(inp), capture_output=True, text=True, env=self.env,
                           timeout=60)
        ms = (time.monotonic() - t0) * 1000
        logged = self.log_len() > before
        return ms, logged, (self.last_log().get("decision") if logged else None), r.stdout.strip()


def calls():
    from gate_fixture_harvest import expand
    out = []
    with open(FIXTURES) as fh:
        for i, ln in enumerate(fh):
            if not ln.strip():
                continue
            c = json.loads(ln)
            out.append({"hook_event_name": "PreToolUse", "session_id": "lat", "tool_use_id": f"lat{i}",
                        "cwd": c.get("cwd"), "tool_name": c["tool"], "tool_input": expand(c["input"])})
    return out


def measure(data_dir, py) -> dict:
    box = Scratch(data_dir, py)
    cs = calls()
    res = {}

    box.kill(True)
    res["gate_off"] = _stats([box.run(c)[0] for c in cs])
    box.kill(False)

    box.supervise(True)
    no_hit, held, held_dec = [], [], {}
    for c in cs:
        ms, logged, dec, _out = box.run(c)
        if not logged:
            no_hit.append(ms)
        else:
            held.append(ms)
            held_dec[dec] = held_dec.get(dec, 0) + 1
    res["no_hit"] = _stats(no_hit)
    res["held"] = dict(_stats(held), decisions=held_dec)

    box.supervise(False)
    hits, every = [], []
    for c in cs:
        ms, logged, _dec, _out = box.run(c)
        every.append(ms)
        if logged:
            hits.append(ms)
    res["let_them_run.hits"] = _stats(hits)
    res["let_them_run.all"] = _stats(every)
    res["calls"] = len(cs)
    return res


def misses(res) -> list:
    out = []
    for k, b in BUDGETS.items():
        p95 = (res.get(k) or {}).get("p95")
        if p95 is not None and p95 > b:
            out.append(f"{k}: p95 {p95} ms > {b} ms")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gate_latency.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", help="a scratch data dir (created; default: a fresh temp dir, removed after)")
    ap.add_argument("--python", default=sys.executable, help="the interpreter the hook row runs (default: this one)")
    ap.add_argument("--json", action="store_true", help="print the raw result as JSON")
    ap.add_argument("--check", action="store_true", help="exit 1 when any p95 budget is missed")
    ns = ap.parse_args(argv)
    made = ns.data_dir is None
    data_dir = ns.data_dir or tempfile.mkdtemp(prefix="gate-latency-")
    why = refuse_live(data_dir)
    if why:
        print(why, file=sys.stderr)
        return 2
    if not made and os.path.exists(os.path.join(data_dir, "state", "tasks.db")):
        print(f"refusing {data_dir}: it already has a ledger (give an empty scratch dir)", file=sys.stderr)
        return 2
    try:
        res = measure(os.path.abspath(data_dir), ns.python)
    finally:
        if made:
            shutil.rmtree(data_dir, ignore_errors=True)
    if ns.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"gate-hook latency over {res['calls']} fixture calls (ms, wall time per subprocess)")
        for k in ("gate_off", "no_hit", "let_them_run.hits", "let_them_run.all", "held"):
            s = res[k]
            extra = f"  decisions={s['decisions']}" if "decisions" in s else ""
            print(f"  {k:18s} n={s['n']:4d}  p50={s['p50']}  p95={s['p95']}  max={s['max']}  "
                  f"(budget p95 <= {BUDGETS[k]}){extra}")
    bad = misses(res)
    for m in bad:
        print("MISS " + m, file=sys.stderr)
    return 1 if (ns.check and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
