"""RED-first: an unattended spawn (`claude --dangerously-skip-permissions` in tmux) stops at
TWO first-run dialogs, each defaulting to "No, exit": the workspace-trust dialog (pre-seeded
in ~/.claude.json since 2026-09-05) and, on a fresh user, the Bypass Permissions acceptance
("Yes, I accept" writes skipDangerousModePermissionPrompt=true to ~/.claude/settings.json —
found by effect in the B3 container, 2026-09-17). Both must be pre-seeded, atomically,
without clobbering other settings. Hermetic: HOME is a temp dir."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ensure_cwd_trusted.py")


def _run(home, target):
    return subprocess.run([sys.executable, SCRIPT, target], env={**os.environ, "HOME": str(home)},
                          capture_output=True, text=True, timeout=30)


def test_preseeds_trust_and_bypass_acceptance_on_a_fresh_home(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    r = _run(home, str(tmp_path / "work"))
    assert r.returncode == 0, r.stderr
    cj = json.loads((home / ".claude.json").read_text())
    assert cj["projects"][str(tmp_path / "work")]["hasTrustDialogAccepted"] is True
    st = json.loads((home / ".claude" / "settings.json").read_text())
    assert st["skipDangerousModePermissionPrompt"] is True


def test_keeps_existing_settings_and_is_idempotent(tmp_path):
    home = tmp_path / "home"; (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark", "hooks": {"Stop": []}}))
    _run(home, str(tmp_path / "work"))
    st = json.loads((home / ".claude" / "settings.json").read_text())
    assert st == {"theme": "dark", "hooks": {"Stop": []}, "skipDangerousModePermissionPrompt": True}
    before = (home / ".claude" / "settings.json").stat().st_mtime_ns
    _run(home, str(tmp_path / "work"))
    assert (home / ".claude" / "settings.json").stat().st_mtime_ns == before, "no rewrite when already set"
