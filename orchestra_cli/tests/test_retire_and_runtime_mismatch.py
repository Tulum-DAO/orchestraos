"""A seat on the wrong runtime can be fixed (a new user's box, 2026-10-14).

register_seat keeps an existing row verbatim, so `orchestra spawn grok-gm --runtime grok` on a seat
already registered as claude passed the grok checks and then started it as claude again, with no
way out: there was no retire command. Now:
- spawn / agent create REFUSE an explicit --runtime that disagrees with the stored row, and say how
  to change it (nothing is written or launched);
- `orchestra retire <seat>` removes the row (no recovery brings it back), marks its session
  retired but keeps session id, resume command and transcript, and stops its tmux session; the
  seat can then be spawned again on any runtime. With the identity store armed it retires there too.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import seats as SE
from orchestra_cli.tests.test_runtime_pick import box, codex_logged_in, reg  # noqa: F401  (fixture)

CHECKOUT = Path(__file__).resolve().parents[2]


def _seed(data, seat="grok-gm", runtime="claude", transcript=None):
    row = {"name": seat, "runtime": runtime, "model": "claude-opus-5-5", "tier": "T0",
           "machine": "vps", "cwd": str(data), "tmux_session": seat, "system_prompt": "prompts/gm.md",
           "always_on": True, "generation": 1, "status": "online"}
    (data / "registry.json").write_text(json.dumps({"agents": {seat: row}}))
    (data / "state").mkdir(exist_ok=True)
    sess = {seat: {"session_id": "sid-1", "resume_command": "claude --resume sid-1", "resumable": True,
                   "status": "online", "tmux_session": seat,
                   "conversation_path": str(transcript) if transcript else ""}}
    (data / "state" / "agent-sessions.json").write_text(json.dumps(sess))
    return row


def _fake_tmux(tmp_path, monkeypatch):
    log = tmp_path / "tmux.log"
    bin_ = tmp_path / "fakebin"; bin_.mkdir()
    (bin_ / "tmux").write_text(f'#!/bin/bash\necho "$*" >> "{log}"\nexit 0\n'); (bin_ / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ.get('PATH', '')}")
    return log


def sessions(data):
    return json.loads((data / "state" / "agent-sessions.json").read_text())


# --- the runtime mismatch -----------------------------------------------------------------------
def test_spawn_with_another_runtime_than_the_stored_row_is_refused(box, capsys):
    box.installed.add("codex"); codex_logged_in(box)
    _seed(box.data)
    assert M.main(["spawn", "grok-gm", "--runtime", "codex"]) == 2
    err = capsys.readouterr().err
    assert "registered as claude" in err and "orchestra retire grok-gm" in err, err
    assert reg(box)["grok-gm"]["runtime"] == "claude"
    assert box.ran == []                                    # nothing launched


def test_spawn_with_the_same_runtime_or_none_still_works(box):
    box.installed.update({"claude", "codex"}); codex_logged_in(box)
    _seed(box.data, runtime="codex")
    assert M.main(["spawn", "grok-gm", "--runtime", "codex"]) == 0
    assert M.main(["spawn", "grok-gm"]) == 0
    assert reg(box)["grok-gm"]["runtime"] == "codex"


def test_agent_create_with_another_runtime_is_refused(box, capsys):
    box.installed.add("codex"); codex_logged_in(box)
    _seed(box.data, seat="w9")
    assert M.main(["agent", "create", "w9", "--runtime", "codex"]) == 2
    assert "orchestra retire w9" in capsys.readouterr().err
    assert reg(box)["w9"]["runtime"] == "claude" and box.ran == []


# --- retire (flat files) --------------------------------------------------------------------------
def test_retire_removes_the_row_keeps_the_session_and_transcript_then_respawn_on_a_new_runtime(box, tmp_path, monkeypatch, capsys):
    box.installed.add("codex"); codex_logged_in(box)
    tlog = _fake_tmux(tmp_path, monkeypatch)
    transcript = tmp_path / "sid-1.jsonl"; transcript.write_text('{"turn": 1}\n')
    _seed(box.data, transcript=transcript)
    assert M.main(["retire", "grok-gm", "--yes", "--reason", "wrong runtime"]) == 0
    assert "grok-gm" not in reg(box)
    s = sessions(box.data)["grok-gm"]
    assert s["status"] == "retired" and s["session_id"] == "sid-1"
    assert s["resume_command"] == "claude --resume sid-1" and s["retired_reason"] == "wrong runtime"
    assert transcript.read_text() == '{"turn": 1}\n'
    assert "kill-session -t =grok-gm" in tlog.read_text()
    out = capsys.readouterr().out
    assert "retired grok-gm" in out and "sid-1" in out
    assert M.main(["spawn", "grok-gm", "--runtime", "codex"]) == 0
    assert reg(box)["grok-gm"]["runtime"] == "codex"


def test_retire_an_unknown_seat_is_refused(box, capsys):
    _seed(box.data)
    assert M.main(["retire", "nobody", "--yes"]) == 2
    assert "no seat named nobody" in capsys.readouterr().err
    assert "grok-gm" in reg(box)


def test_retire_without_yes_and_no_terminal_changes_nothing(box, monkeypatch, capsys):
    _seed(box.data)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert M.main(["retire", "grok-gm"]) == 2
    assert "--yes" in capsys.readouterr().err
    assert "grok-gm" in reg(box) and sessions(box.data)["grok-gm"]["status"] == "online"


# --- retire with the identity store armed (what a fresh install has) ------------------------------
def test_retire_then_respawn_on_a_new_runtime_with_the_identity_store_armed(tmp_path, monkeypatch):
    from orchestra_cli import identity_cmd as IC
    from orchestra_cli import settings as S
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(f'[data]\ndir = "{data}"\n[notify]\nchannel = "none"\n')
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.setenv("ORCHESTRA_ROOT", str(CHECKOUT))
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    _fake_tmux(tmp_path, monkeypatch)
    _seed(data)
    IC.arm(CHECKOUT, data, start_regenerator=False)
    st = S.load_settings()

    def db_row():
        import sqlite3
        c = sqlite3.connect(str(data / "state" / "orchestra-registry.db"))
        try:
            canon = c.execute("SELECT generation_id FROM canonical WHERE root='grok-gm'").fetchone()
            doc = c.execute("SELECT payload_json FROM source_records WHERE file='registry.json' "
                            "AND kind='agent' AND key='grok-gm'").fetchone()
            return canon, (json.loads(doc[0]) if doc else None)
        finally:
            c.close()

    assert db_row()[0] is not None
    assert SE.retire_seat(st, "grok-gm", reason="wrong runtime")["armed"] is True
    canon, doc = db_row()
    assert canon is None and doc is None                      # out of the roster, no doc to resurrect
    assert "grok-gm" not in json.loads((data / "registry.json").read_text())["agents"]
    kept = sessions(data)["grok-gm"]                         # projected from the store's session doc
    assert kept["status"] == "retired" and kept["session_id"] == "sid-1"
    row = SE.register_seat(st, "grok-gm", gm=False, runtime="grok", model=None, tier="T0", prompt=None)
    assert row["runtime"] == "grok"
    canon, doc = db_row()
    assert canon is not None and doc["runtime"] == "grok"
    assert json.loads((data / "registry.json").read_text())["agents"]["grok-gm"]["runtime"] == "grok"
