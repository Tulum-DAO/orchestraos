"""The opt-in status line: writes the context reading every reader finds, chains a user's own line
without changing it, and comes out byte-for-byte."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
import install as H  # noqa: E402

SID = "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb"
PAYLOAD = {"session_id": SID, "model": {"display_name": "Opus 5.5"},
           "workspace": {"current_dir": "/work/project"},
           "context_window": {"remaining_percentage": 61}}


def _run_shim(tmpdir, stdin: bytes, *args, settings_cmd=None):
    env = dict(os.environ, TMPDIR=str(tmpdir), ORCHESTRA_PY=sys.executable)
    if settings_cmd:          # the exact command line the installer writes, through sh like Claude Code
        return subprocess.run(["sh", "-c", settings_cmd], input=stdin, capture_output=True, env=env, timeout=30)
    return subprocess.run(["sh", str(REPO / H.STATUSLINE_SHIM), *args], input=stdin,
                          capture_output=True, env=env, timeout=30)


# --- condition 1: the writer and every reader agree on the path, off /tmp too -------------------

def test_writer_and_every_reader_agree_on_a_non_tmp_tmpdir(tmp_path, monkeypatch):
    tmpdir = tmp_path / "var-folders-T"           # a macOS-style per-user TMPDIR, not /tmp
    tmpdir.mkdir()
    r = _run_shim(tmpdir, json.dumps(PAYLOAD).encode())
    assert r.returncode == 0
    written = tmpdir / f"claude-ctx-{SID}.json"
    assert written.is_file(), list(tmpdir.iterdir())
    d = json.loads(written.read_text())
    assert (d["remaining_percentage"], d["used_pct"]) == (61, 49)      # 39% of the window, 49% of the budget

    monkeypatch.setenv("TMPDIR", str(tmpdir))
    import context_reading
    from lineage_daemon.wal import ctx_adapters
    assert Path(context_reading.bridge_path(SID)) == written
    assert Path(ctx_adapters.ctx_bridge_path(SID)) == written
    frac, fresh = ctx_adapters.read_ctx_detectorfile("seat", sid=SID)  # the rotation engine's reader
    assert fresh and abs(frac - 0.49) < 1e-9
    src = (REPO / "scripts/lineage_daemon/wal/bg_beat.py").read_text()
    assert "detector_dir = ctx_bridge_dir()" in src and '"/tmp" if detector_dir is None' not in src, \
        "the rotation beat's default dir is the shared resolver"


def test_the_minimal_line_shows_the_window_figure_labeled_used(tmp_path):
    r = _run_shim(tmp_path, json.dumps(PAYLOAD).encode())
    assert r.stdout.decode().strip() == "Opus 5.5 │ project ███░░░░░░░ 39% used"


def test_a_redraw_inside_the_cache_window_does_not_rerun_python(tmp_path):
    _run_shim(tmp_path, json.dumps(PAYLOAD).encode())
    written = tmp_path / f"claude-ctx-{SID}.json"
    first = written.stat().st_mtime_ns
    later = dict(PAYLOAD, context_window={"remaining_percentage": 20})
    r = _run_shim(tmp_path, json.dumps(later).encode())
    assert r.stdout.decode().strip().endswith("39% used"), "served from the 30 s cache"
    assert written.stat().st_mtime_ns == first


def test_bad_input_still_prints_a_line_and_exits_0(tmp_path):
    for stdin in (b"", b"not json", b"[]", json.dumps({"session_id": "../../etc"}).encode()):
        r = _run_shim(tmp_path, stdin)
        assert r.returncode == 0 and r.stdout.strip(), stdin
    assert not list(tmp_path.glob("claude-ctx-*")), "no session id, no reading"


# --- condition 2: chain mode ---------------------------------------------------------------------

USER_SCRIPT = """import hashlib, sys
data = sys.stdin.buffer.read()
sys.stdout.buffer.write(b"MINE " + hashlib.sha256(data).hexdigest().encode() + b" \\x1b[32mgreen\\x1b[0m\\n  second line")
"""


def _user_statusline(tmp_path):
    script = tmp_path / "user_line.py"
    script.write_text(USER_SCRIPT)
    return {"type": "command", "command": f'"{sys.executable}" "{script}"', "padding": 2}


def test_the_chained_command_gets_identical_stdin_and_its_output_is_unchanged(tmp_path):
    original = _user_statusline(tmp_path)
    stdin = json.dumps(PAYLOAD).encode() + b"\n\n"          # trailing newlines must survive too
    want = subprocess.run(["sh", "-c", original["command"]], input=stdin, capture_output=True).stdout
    assert want.startswith(b"MINE " + hashlib.sha256(stdin).hexdigest().encode())
    cmd = H._statusline_command(REPO, original)
    r = _run_shim(tmp_path, stdin, settings_cmd=cmd)
    assert r.stdout == want
    assert (tmp_path / f"claude-ctx-{SID}.json").is_file(), "and the reading is written"


def test_a_missing_checkout_falls_back_to_the_users_own_line(tmp_path):
    original = _user_statusline(tmp_path)
    cmd = H._statusline_command(tmp_path / "gone", original)
    stdin = json.dumps(PAYLOAD).encode()
    r = _run_shim(tmp_path, stdin, settings_cmd=cmd)
    assert r.stdout.startswith(b"MINE " + hashlib.sha256(stdin).hexdigest().encode())


def _settings(tmp_path, obj):
    p = tmp_path / "claude-config" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")   # the installer's format
    return p


def test_install_then_remove_restores_settings_byte_for_byte(tmp_path):
    original = _user_statusline(tmp_path)
    p = _settings(tmp_path, {"model": "opus", "statusLine": original, "permissions": {"allow": ["Bash(ls:*)"]},
                             "note": "ünïcode"})
    before = p.read_bytes()
    rep = H.install_statusline(settings_path=p, repo_root=REPO, data_dir=tmp_path / "data", chain=True)
    assert rep["state"] == "chained", rep
    assert H.statusline_status(settings_path=p)["state"] == "chained"
    assert H.install_statusline(settings_path=p, repo_root=REPO, data_dir=tmp_path / "data")["state"] == "chained", \
        "re-install keeps the chain"
    assert H.remove_statusline(settings_path=p)["restored"] is True
    assert p.read_bytes() == before


def test_install_into_a_file_without_a_status_line_then_remove_is_byte_for_byte(tmp_path):
    p = _settings(tmp_path, {"model": "opus", "hooks": {}})
    before = p.read_bytes()
    assert H.install_statusline(settings_path=p, repo_root=REPO, data_dir=tmp_path / "data")["state"] == "installed"
    assert H.remove_statusline(settings_path=p)["removed"] is True
    assert p.read_bytes() == before


def test_a_status_line_that_is_not_ours_is_never_replaced_without_a_yes(tmp_path):
    p = _settings(tmp_path, {"statusLine": _user_statusline(tmp_path)})
    before = p.read_bytes()
    rep = H.install_statusline(settings_path=p, repo_root=REPO, data_dir=tmp_path / "data")
    assert rep.get("state") == "theirs" and "not replaced" in rep["error"]
    assert p.read_bytes() == before
    assert H.remove_statusline(settings_path=p) == {"removed": False, "state": "theirs"}
    assert p.read_bytes() == before


def test_a_scratch_checkout_never_installs_into_the_real_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(H.ALLOW_TEMP_ENV, raising=False)
    scratch = (tmp_path / "scratchpad").resolve()
    scratch.mkdir()
    monkeypatch.setattr(H, "_temp_roots", lambda: [scratch])
    clone = scratch / "clone"
    for rel in (H.STATUSLINE_SHIM, "hooks/statusline.py"):
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, clone / rel)
    real = home / ".claude" / "settings.json"
    real.write_text(json.dumps({"model": "keep-me"}))
    before = real.read_bytes()
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    rep = H.install_statusline(settings_path=real, repo_root=clone, data_dir=tmp_path / "d")
    assert "under a temp dir" in rep["error"] and real.read_bytes() == before
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    rep = H.install_statusline(settings_path=real, repo_root=REPO, data_dir=tmp_path / "d")
    assert "from inside a test" in rep["error"] and real.read_bytes() == before
