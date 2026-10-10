"""CODE vs DATA in the rotation chain (data-dir sweep S2).

cron_beat hands every rotation / blue-green step `orchestra_dir` = ORCHESTRA_DIR, the DATA dir under
`orchestra up` and scripts/run-beat.sh. The scripts those steps run are CODE and live in the checkout;
under the data dir they do not exist, so every armed step failed with ENOENT (and some failed silently:
the escalation card's subprocess.run has no check, the S3 correction send swallowed it). Each test
below points the data dir at an EMPTY temp dir and checks the code comes from the checkout while the
data dir still reaches the child.
"""
import os
import pathlib
import re
import subprocess

from scripts.lineage_daemon import code_root as CR

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_code_path_is_the_checkout_and_reads_orchestra_root_per_call(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    assert pathlib.Path(CR.code_path("spawn-agent.sh")) == ROOT / "spawn-agent.sh"
    assert (ROOT / "spawn-agent.sh").is_file()
    monkeypatch.setenv("ORCHESTRA_ROOT", str(tmp_path))
    assert CR.code_path("msg_store.py") == str(tmp_path / "msg_store.py")


def test_child_env_pins_the_data_dir_and_the_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", "/somewhere/else")
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    env = CR.child_env(str(tmp_path))
    assert env["ORCHESTRA_DIR"] == env["ORCH_DIR"] == str(tmp_path)
    assert env["ORCHESTRA_ROOT"] == str(ROOT)
    assert CR.child_env(None, {"ORCHESTRA_DIR": "/kept"})["ORCHESTRA_DIR"] == "/kept"


def test_escalation_card_runs_the_checkouts_approval_py(tmp_path):
    from scripts.lineage_daemon import completion_arm_seams as A
    data = tmp_path / "data"
    data.mkdir()
    fired = []
    esc = A.build_escalate_fn(str(data), runtime_dir=str(tmp_path / "rt"),
                              request_fn=lambda argv: fired.append(argv))
    esc("seat-x", "seat-x-g2", {"reason": "never-progressed-after-context-assist"})
    assert fired, "the card was not requested"
    assert pathlib.Path(fired[0][1]) == ROOT / "scripts" / "approval.py"
    assert pathlib.Path(fired[0][1]).is_file()


def test_s3_correction_send_runs_the_checkouts_msg_store_with_the_data_dir(monkeypatch, tmp_path):
    from scripts.lineage_daemon import s3_live_seams as S
    seen = {}

    def _fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", _fake_run)
    S._inject_correction("seat-x-g2", "resume", "n1", orchestra_dir=str(tmp_path),
                         nudge_fn=lambda *a, **k: None)
    assert pathlib.Path(seen["cmd"][1]) == ROOT / "msg_store.py"
    assert seen["env"]["ORCHESTRA_DIR"] == str(tmp_path)


def test_green_reap_runs_the_checkouts_spawn_script(monkeypatch, tmp_path):
    from scripts.lineage_daemon.wal import bg_beat, spawn_green
    seen = {}

    def _fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(spawn_green, "_tmux_has_session", lambda s: True)
    monkeypatch.setattr(subprocess, "run", _fake_run)
    bg_beat._default_reap_pane_fn(str(tmp_path))("seat-x-g-green")
    assert seen["cmd"][:2] == [str(ROOT / "spawn-agent.sh"), "--kill"]
    assert seen["env"]["ORCHESTRA_DIR"] == str(tmp_path)


def test_safety_recheck_loads_park_idle_from_the_checkout(monkeypatch, tmp_path):
    import importlib.util
    from scripts.lineage_daemon import execute as EX
    seen = []

    def _spec(name, path, *a, **k):
        seen.append(path)
        raise RuntimeError("stop here")      # fail-closed path: the recheck must not proceed
    monkeypatch.setattr(importlib.util, "spec_from_file_location", _spec)
    cat, _reason = EX.default_safety_recheck("seat-x", orchestra_dir=str(tmp_path))
    assert seen and pathlib.Path(seen[0]) == ROOT / "scripts" / "park-idle.py"
    assert cat != "SUPERSEDED_SAFE"


def test_graduation_loads_park_idle_from_the_checkout(monkeypatch):
    import importlib.util
    from scripts.lineage_daemon import graduation_executors as GE
    seen = []

    def _spec(name, path, *a, **k):
        seen.append(path)
        raise RuntimeError("stop here")
    monkeypatch.setattr(importlib.util, "spec_from_file_location", _spec)
    try:
        GE._load_park_idle()
    except RuntimeError:
        pass
    assert pathlib.Path(seen[0]) == ROOT / "scripts" / "park-idle.py"


def test_spawn_adopt_finds_prompts_in_the_checkout(monkeypatch, tmp_path):
    from scripts.identity_store import spawn_adopt as SA
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    prompt = next((ROOT / "prompts").glob("*.md"))
    assert SA._default_system_prompt(str(tmp_path), prompt.stem) == f"prompts/{prompt.name}"


# A code file the rotation chain runs, built from a DATA-dir variable. `od`, `orchestra_dir`, `orch` and
# ORCHESTRA_DIR are the data dir throughout scripts/lineage_daemon (cron_beat passes ORCHESTRA_DIR down).
_DATA_VARS = r"(?:od|orchestra_dir|orch|ORCHESTRA_DIR)"
_CODE_FILES = r"(?:spawn-agent\.sh|msg_store\.py|scripts\"?|registry-update\.py|sessions-update\.py|" \
              r"park-idle\.py|agent-status\.py|approval\.py|quota_oracle\.py)"
_BAD = [
    re.compile(_DATA_VARS + r'\s*\+\s*"/' + _CODE_FILES),
    re.compile(r"os\.path\.join\(\s*" + _DATA_VARS + r'\s*,\s*"' + _CODE_FILES),
]


def test_no_rotation_module_builds_a_code_path_from_the_data_dir():
    hits = []
    for p in sorted((ROOT / "scripts" / "lineage_daemon").rglob("*.py")):
        if p.name.endswith("_test.py") or p.name.startswith("test_"):
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if any(b.search(line) for b in _BAD):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "code resolved from the data dir (use code_root.code_path):\n" + "\n".join(hits)
