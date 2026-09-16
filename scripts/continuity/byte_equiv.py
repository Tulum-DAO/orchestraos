#!/usr/bin/env python3
"""Continuity v4 — W7 byte-equivalence harness (matrix global requirement).

Reproduces the state-snapshot PROJECTION (state-snapshot-agents.sh lines 43-66)
on SCRATCH stores, runs it twice — UNROUTED and ROUTED through the write_fence
projection observe — and proves the produced state/agents bytes are IDENTICAL
and that only telemetry keys changed. This is the reusable gate that must be
GREEN before ANY writer row is routed.

T4 discipline: refuses to run against the live orchestra tree; scratch only.
The routed path calls `write_fence.observe_projection`, a pure side-effect to
`cv4_write_fence_shadow`; if it ever perturbs a real write, this harness fails.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
from collections import namedtuple
from pathlib import Path

_here = os.path.dirname(os.path.abspath(__file__))
_TELEMETRY = ("last_snapshot_at", "was_running_at_snapshot", "last_seen_running")

Result = namedtuple(
    "Result", "byte_identical telemetry_only files diffs routed_records")


def _wf():
    spec = importlib.util.spec_from_file_location(
        "cv4_write_fence", os.path.join(_here, "write_fence.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _assert_not_live(root: str) -> None:
    live = Path(os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    ).resolve()
    if Path(root).resolve() == live:
        raise RuntimeError(
            f"byte_equiv refuses to run against the LIVE tree {live} (T4)")


def _agents_dir(root: str) -> str:
    return os.path.join(root, "state", "agents")


def _state_path(root: str, agent_id: str) -> str:
    p = os.path.join(_agents_dir(root), f"{agent_id}.json")
    if not os.path.exists(p):
        alt = os.path.join(root, "state", f"{agent_id}.json")
        if os.path.exists(alt):
            return alt
    return p


def _strip_telemetry(rec: dict) -> str:
    return json.dumps({k: v for k, v in rec.items() if k not in _TELEMETRY},
                      sort_keys=True)


def _project_tree(root, now_str, running, *, route, db=None, hook=None):
    """Mirror the cron projection over one scratch tree. Returns {file: record}.

    Routed path mirrors the revised .sh: per-agent CLASSIFY (pure, no DB),
    accumulate, then ONE batched flush per cycle (log_projection_batch)."""
    wf = _wf() if route else None
    with open(os.path.join(root, "registry.json")) as f:
        reg = json.load(f)
    out = {}
    batch = []
    expected = len(reg.get("agents", {}))
    for agent_id, cfg in reg.get("agents", {}).items():
        tmux = cfg.get("tmux_session", agent_id)
        sp = _state_path(root, agent_id)
        on_disk = {}
        if os.path.exists(sp):
            try:
                with open(sp) as f:
                    on_disk = json.load(f)
            except Exception:
                on_disk = {}
        is_running = tmux in running
        record = dict(on_disk)
        record["last_snapshot_at"] = now_str
        record["was_running_at_snapshot"] = is_running
        if is_running:
            record["last_seen_running"] = now_str
        if route:
            decision, reason = wf.classify_projection(on_disk, record)
            has_stamp = 1 if record.get("_fence") else 0
            batch.append(("state/agents", agent_id, "state-snapshot",
                          decision, reason, has_stamp))
        if hook is not None:
            record = hook(agent_id, record)
        with open(sp, "w") as f:
            json.dump(record, f, indent=2)
        out[os.path.basename(sp)] = record
    if route:
        wf.record_projection_cycle(now_str, expected, batch,
                                   db=db)   # one connection/transaction + cycle rec
    return out


def projection_byte_equiv(root, now_str, running, *, db=None,
                          _routed_write_hook=None) -> Result:
    """Run the projection UNROUTED vs ROUTED on scratch copies of `root` and
    compare. `_routed_write_hook(agent_id, record)->record` injects a buggy
    routed writer (tests only) to prove the harness can fail."""
    _assert_not_live(root)
    src = Path(root)
    unrouted = str(src.parent / (src.name + "__unrouted"))
    routed = str(src.parent / (src.name + "__routed"))
    for d in (unrouted, routed):
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(root, d)

    _project_tree(unrouted, now_str, running, route=False)
    routed_records = _project_tree(routed, now_str, running, route=True, db=db,
                                   hook=_routed_write_hook)

    files, diffs = [], []
    telemetry_only = True
    for fn in sorted(os.listdir(_agents_dir(routed))):
        if not fn.endswith(".json"):
            continue
        files.append(fn)
        rb = Path(_agents_dir(routed), fn).read_bytes()
        ub = Path(_agents_dir(unrouted), fn).read_bytes()
        if rb != ub:
            diffs.append(fn)
        # telemetry-only: routed non-telemetry content == the ORIGINAL source
        orig = json.loads(Path(_agents_dir(root), fn).read_bytes())
        routed_rec = json.loads(rb)
        if _strip_telemetry(orig) != _strip_telemetry(routed_rec):
            telemetry_only = False

    for d in (unrouted, routed):
        shutil.rmtree(d, ignore_errors=True)

    return Result(byte_identical=(len(diffs) == 0), telemetry_only=telemetry_only,
                  files=files, diffs=diffs, routed_records=routed_records)
