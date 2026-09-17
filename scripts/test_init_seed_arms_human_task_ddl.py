"""By effect, hermetic: the schema seed `orchestra init` runs (same command, same env) leaves
approval_requests with the gated columns present, so approval_resume's snooze sweep and the
human-task surface work on a fresh data dir with no per-minute error (gm msg_3f772533)."""
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_seed_command_from_init_creates_gated_columns(tmp_path, monkeypatch):
    from orchestra_cli import init_cmd as I
    # hermetic: no operator arming sentinel (~/runtime/APPROVAL_DDL_ARMED_*) and no ambient env
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    calls = []

    def run(argv, cwd=None, env=None):
        calls.append((argv, cwd, env))
        return subprocess.run(list(argv), cwd=cwd, env=env, capture_output=True, text=True).returncode

    report = I.run_init(__import__("pathlib").Path(ROOT), data_dir=data, run=run,
                        skip_npm=True, skip_venv=True, config_path=tmp_path / "orchestra.toml")
    done = {r.step: r for r in report}
    assert done["seed:approval-schema"].did is True, done["seed:approval-schema"].detail
    seed = [c for c in calls if "ApprovalStore" in " ".join(c[0])]
    assert len(seed) == 1
    conn = sqlite3.connect(data / "state" / "tasks.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(approval_requests)")}
    assert {"snoozed_until", "block_task", "blocks_what", "answer_surface", "answered_by"} <= cols
    # and a second seed run is silent + idempotent
    r = subprocess.run(list(seed[0][0]), cwd=seed[0][1], env=seed[0][2], capture_output=True, text=True)
    assert r.returncode == 0 and "DDL PENDING" not in r.stderr, r.stderr
