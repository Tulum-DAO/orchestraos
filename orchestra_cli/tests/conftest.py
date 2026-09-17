"""Every CLI test runs against a throwaway Claude config dir: `orchestra init` installs hooks
into $CLAUDE_CONFIG_DIR/settings.json, and a test must never touch the developer's real one
(2026-09-17: two test runs installed rows pointing at pytest tmp dirs into the live file and
blocked every tool call on the host)."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    yield
