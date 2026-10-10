"""CODE vs DATA for alarms and manual tools (data-dir sweep S3).

Under `orchestra up` ORCHESTRA_DIR is the DATA dir (~/.orchestra by default); the checkout is elsewhere.
These alarms ran <data>/msg_store.py (or another script under the data dir), which does not exist on a
real install, inside a best-effort try/except, so the page to gm was lost without a trace. Each test
points ORCHESTRA_DIR at an EMPTY temp dir and checks the script that runs is the checkout's own.
"""
import importlib.util
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _capture(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    calls = []

    def _fake_run(cmd, *a, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", _fake_run)
    return data, calls


def _script_of(cmd):
    """The script a `python3 <script> ...` / `<script> ...` argv runs."""
    return pathlib.Path(cmd[1] if os.path.basename(cmd[0]).startswith("python") else cmd[0])


def test_session_index_page_runs_the_checkouts_msg_store(monkeypatch, tmp_path):
    data, calls = _capture(monkeypatch, tmp_path)
    si = _load("session_index_t", HERE / "session-index.py")
    si._default_page({"session": "x"})
    assert calls and _script_of(calls[0][0]) == ROOT / "msg_store.py"


def test_gen_resolve_alarm_runs_the_checkouts_msg_store(monkeypatch, tmp_path):
    data, calls = _capture(monkeypatch, tmp_path)
    gr = _load("gen_resolve_t", HERE / "gen_resolve.py")
    gr._default_alarm("skew")
    assert calls and _script_of(calls[0][0]) == ROOT / "msg_store.py"


def test_identity_store_alarms_run_the_checkouts_msg_store(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT))
    from scripts.identity_store import monitor, resolver, identity_reconciler
    data, calls = _capture(monkeypatch, tmp_path)
    monitor._default_gm_msg("page")
    resolver._default_alarm("skew")
    identity_reconciler._default_msg_store_send(str(data), "s", "b")
    assert len(calls) == 3
    for cmd, _kw in calls:
        assert _script_of(cmd) == ROOT / "msg_store.py", cmd
    # the reconciler passes its data dir explicitly: the child must act on THAT data dir
    assert calls[2][1]["env"]["ORCHESTRA_DIR"] == str(data)


def test_b_fire_watcher_pings_through_the_checkout(monkeypatch, tmp_path):
    data, calls = _capture(monkeypatch, tmp_path)
    bfw = _load("b_fire_watcher_t", HERE / "b_fire_watcher.py")
    bfw._notify_gm_fire("seat-x", "armed")
    scripts = [_script_of(c) for c, _ in calls]
    assert ROOT / "msg_store.py" in scripts
    assert ROOT / "scripts" / "tg-notify.sh" in scripts
    assert not any(str(s).startswith(str(data)) for s in scripts)


def test_sid_invariants_loads_message_router_from_the_checkout(tmp_path):
    # fresh interpreter, PYTHONPATH stripped, empty data dir: `orchestra up`'s PYTHONPATH cannot hide it
    data = tmp_path / "data"
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "ORCHESTRA_ROOT", "ORCH_DIR", "SID_ORCH")}
    env["ORCHESTRA_DIR"] = str(data)
    code = ("import importlib.util as u;"
            f"s=u.spec_from_file_location('si', {str(HERE / 'sid_invariants.py')!r});"
            "m=u.module_from_spec(s); s.loader.exec_module(m);"
            "r=m._load_resolvers(); print('delivery' in r and r['delivery'] is not None)")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.strip().endswith("True"), r.stdout + r.stderr[-800:]


def test_focus_importer_runs_agent_status_from_the_checkout():
    src = (HERE / "focus_registry" / "importer.py").read_text()
    assert '["python3", "scripts/agent-status.py"' not in src     # relative to cwd = the DATA dir
    assert '"scripts", "agent-status.py")' in src


def test_agent_recovery_runs_code_from_the_checkout():
    src = (HERE / "agent-recovery.sh").read_text()
    assert '"$SCRIPT_DIR/scripts/' not in src, "a script under the data dir"
    assert 'cd "$SCRIPT_DIR"' not in src, "python -m / sys.path 'scripts' relative to the data dir"
    assert 'CODE_DIR="${ORCHESTRA_ROOT:-' in src


# ---- static guard: no shipped Python builds a CODE path from a DATA-dir variable ----

_DATA = r"(?:ORCHESTRA_DIR|ORCH|ORCH_DIR|od|orchestra_dir|orch|_ORCHESTRA_DIR)"
_CODE = r"(?:msg_store\.py|message_bus\.py|spawn-agent\.sh|scripts)"
_BAD = [
    re.compile(_DATA + r"\s*\+\s*[\"'][^\"']*(?:msg_store\.py|message_bus\.py|spawn-agent\.sh|scripts/)"),
    re.compile(r"os\.path\.join\(\s*" + _DATA + r"\s*,\s*[\"']" + _CODE + r"[\"']"),
    re.compile(_DATA + r"\s*/\s*[\"']" + _CODE + r"[\"']"),
    re.compile(r"sys\.path\.insert\(\s*0\s*,\s*str\(\s*" + _DATA + r"\s*\)\s*\)"),
]


def test_no_shipped_python_builds_a_code_path_from_the_data_dir():
    hits = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT)
        if rel.parts[0] in ("node_modules", ".git") or "node_modules" in rel.parts:
            continue
        if p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py":
            continue
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if any(b.search(line) for b in _BAD):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, "code resolved from the data dir (resolve it from the checkout):\n" + "\n".join(hits)
