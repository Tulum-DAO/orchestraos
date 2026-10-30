"""--test on `orchestra spawn` and `orchestra agent create`: a throwaway seat recorded on its row (test: true), a T2
worker reporting to the integrator (not a registered seat), refused for gm, an existing real seat or a pm; the
retired session record keeps the flag. Temp checkout and data dir, fake tmux, nothing launched."""
import json
import os
import sys
from pathlib import Path

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import seats as SE
from orchestra_cli.tests.test_runtime_pick import box, codex_logged_in, reg  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import tier_rule as T  # noqa: E402


def _ready(box, tmp_path, monkeypatch):
    box.installed.add("codex"); codex_logged_in(box)
    log = tmp_path / "tmux.log"
    bin_ = tmp_path / "fakebin"; bin_.mkdir()
    (bin_ / "tmux").write_text(f'#!/bin/bash\necho "$*" >> "{log}"\nexit 0\n'); (bin_ / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ.get('PATH', '')}")
    monkeypatch.setattr(SE, "_pane_alive", lambda s: True)
    return log


def test_tier_rule_test_seat():
    assert T.tier_for("qa-demo", None, False, None, test=True) == ("T2", T.INTEGRATOR, None)
    assert T.tier_for("qa-demo", "integrator", False, None, test=True) == ("T2", "integrator", None)
    assert T.tier_for("qa-demo", None, False, None, "T2", test=True)[0] == "T2"
    with pytest.raises(T.TierRefused):
        T.tier_for("qa-demo", "pm-demo", True, "T1", test=True)
    with pytest.raises(T.TierRefused):
        T.tier_for("qa-demo", None, False, None, "T1", test=True)
    # Without --test nothing changes: no parent is a T1, an unregistered parent is refused.
    assert T.tier_for("qa-demo", None, False, None) == ("T1", None, None)
    with pytest.raises(T.TierRefused):
        T.tier_for("qa-demo", "integrator", False, None)
    assert T.is_test({"test": True}) and not T.is_test({"test": "yes"}) and not T.is_test(None)


def test_tier_rule_cli_test_flag(capsys):
    assert T.main(["decide", "qa-demo", "--test", "--fields"]) == 0
    assert capsys.readouterr().out.strip().split("\x1f")[:3] == ["T2", "integrator", "worker"]
    assert T.main(["decide", "qa-demo", "--test", "--role", "pm"]) == 3


def test_spawn_test_records_the_seat_as_a_test_seat(box, tmp_path, monkeypatch):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    row = reg(box)["qa-demo"]
    assert row["test"] is True and row["tier"] == "T2" and row["reports_to"] == "integrator"
    assert row["role"] == "worker"
    # A plain spawn is unchanged.
    assert M.main(["spawn", "dev-demo", "--runtime", "codex"]) == 0
    assert "test" not in reg(box)["dev-demo"] and reg(box)["dev-demo"]["tier"] == "T1"


def test_agent_create_test_with_a_cwd(box, tmp_path, monkeypatch):
    _ready(box, tmp_path, monkeypatch)
    work = tmp_path / "scratch"; work.mkdir()
    assert M.main(["agent", "create", "qa-demo", "--runtime", "codex", "--role", "worker",
                   "--cwd", str(work), "--test"]) == 0
    row = reg(box)["qa-demo"]
    assert row["test"] is True and row["reports_to"] == "integrator" and row["cwd"] == str(work)


def test_test_flag_refusals(box, tmp_path, monkeypatch, capsys):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "dev-demo", "--runtime", "codex"]) == 0
    assert M.main(["spawn", "dev-demo", "--runtime", "codex", "--test"]) == 2     # a real seat stays real
    assert "dev-demo is a real seat's name" in capsys.readouterr().err
    assert "test" not in reg(box)["dev-demo"]
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test", "--role", "pm"]) == 2
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test", "--parent", "dev-demo"]) == 2
    assert "qa-demo" not in reg(box)
    # Respawning an existing test seat with --test again is fine (idempotent).
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0


def test_retire_keeps_the_test_flag_on_the_session_record(box, tmp_path, monkeypatch):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    assert M.main(["retire", "qa-demo", "--yes", "--reason", "proof done"]) == 0
    assert "qa-demo" not in reg(box)
    s = json.loads((box.data / "state" / "agent-sessions.json").read_text())["qa-demo"]
    assert s["status"] == "retired" and s["test"] is True


# --- "test" belongs to the lineage ------------------------------------------------------------------

def test_a_retired_test_seat_respawned_without_test_is_refused(box, tmp_path, monkeypatch, capsys):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    assert M.main(["retire", "qa-demo", "--yes", "--reason", "proof done"]) == 0
    capsys.readouterr()
    assert M.main(["spawn", "qa-demo", "--runtime", "codex"]) == 2
    assert "qa-demo is a test seat; spawn it with --test, or use a new name" in capsys.readouterr().err
    assert "qa-demo" not in reg(box)
    work = tmp_path / "w"; work.mkdir()
    assert M.main(["agent", "create", "qa-demo", "--runtime", "codex", "--role", "worker", "--cwd", str(work)]) == 2
    assert "is a test seat" in capsys.readouterr().err


def test_a_retired_test_seat_respawned_with_test_stays_a_test_seat(box, tmp_path, monkeypatch):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    assert M.main(["retire", "qa-demo", "--yes", "--reason", "proof done"]) == 0
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    row = reg(box)["qa-demo"]
    assert row["test"] is True and row["tier"] == "T2" and row["reports_to"] == "integrator"


def test_a_live_test_seat_spawned_again_without_test_is_refused(box, tmp_path, monkeypatch, capsys):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    capsys.readouterr()
    assert M.main(["spawn", "qa-demo", "--runtime", "codex"]) == 2
    assert "qa-demo is a test seat" in capsys.readouterr().err


def test_a_real_seats_name_with_test_is_refused_live_or_retired(box, tmp_path, monkeypatch, capsys):
    _ready(box, tmp_path, monkeypatch)
    assert M.main(["spawn", "dev-demo", "--runtime", "codex"]) == 0
    assert M.main(["retire", "dev-demo", "--yes", "--reason", "done"]) == 0
    capsys.readouterr()
    assert M.main(["spawn", "dev-demo", "--runtime", "codex", "--test"]) == 2
    assert "dev-demo is a real seat's name; spawn the test seat with a new name" in capsys.readouterr().err
    work = tmp_path / "w2"; work.mkdir()
    assert M.main(["agent", "create", "dev-demo", "--runtime", "codex", "--role", "worker",
                   "--cwd", str(work), "--test"]) == 2
    # The real seat itself comes back as itself.
    assert M.main(["spawn", "dev-demo", "--runtime", "codex"]) == 0
    assert "test" not in reg(box)["dev-demo"]


def test_lineage_flag_reads_the_retired_record(box, tmp_path, monkeypatch):
    _ready(box, tmp_path, monkeypatch)
    from orchestra_cli import settings as S
    st = S.load_settings()
    assert SE.lineage_test_flag(st, "pm-demo") is None                      # never used
    assert M.main(["spawn", "qa-demo", "--runtime", "codex", "--test"]) == 0
    assert SE.lineage_test_flag(st, "qa-demo", reg(box)["qa-demo"]) is True
    assert M.main(["retire", "qa-demo", "--yes", "--reason", "x"]) == 0
    assert SE.lineage_test_flag(st, "qa-demo") is True                      # retired: the session record says so


def test_a_refused_agent_create_writes_nothing(box, tmp_path, monkeypatch, capsys):
    _ready(box, tmp_path, monkeypatch)
    (box.root / "prompts" / "_sample-template.md").write_text("You are {YOUR_ID}, a sample seat.\n")
    work = tmp_path / "w3"; work.mkdir()
    args = ["agent", "create", "qa-demo", "--runtime", "codex", "--role", "worker", "--cwd", str(work),
            "--template", "prompts/_sample-template.md"]
    assert M.main(args + ["--test"]) == 0
    assert M.main(["retire", "qa-demo", "--yes", "--reason", "proof done"]) == 0
    (box.root / "prompts" / "qa-demo.md").unlink()                       # the retire checklist removes it
    capsys.readouterr()
    assert M.main(args) == 2                                              # without --test: refused
    assert "is a test seat" in capsys.readouterr().err
    assert not (box.root / "prompts" / "qa-demo.md").exists()             # nothing rendered
    assert M.main(args + ["--test"]) == 0                                 # so the right call still works
    assert (box.root / "prompts" / "qa-demo.md").exists()
