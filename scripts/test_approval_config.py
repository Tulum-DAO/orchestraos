import os, sys, importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_config_exposes_all_symbols():
    import approval_config as c
    for name in ["DB_PATH","NTFY_BASE","NTFY_APPROVALS_TOPIC","NTFY_ANSWERS_TOPIC",
                 "NTFY_TOKEN_FILE","DEFAULT_OPTIONS","EXPIRY_HOURS","WATCHDOG_MINUTES",
                 "WATCHDOG_MAX_ATTEMPTS","CURSOR_FILE"]:
        assert hasattr(c, name), f"missing {name}"
    assert c.DEFAULT_OPTIONS == ["approve","deny","hold"]
    assert c.EXPIRY_HOURS == 24
    assert c.WATCHDOG_MINUTES == 5
    assert c.WATCHDOG_MAX_ATTEMPTS == 3

def test_ntfy_token_missing_file_returns_empty(monkeypatch, tmp_path):
    import approval_config as c
    monkeypatch.setattr(c, "NTFY_TOKEN_FILE", tmp_path / "nope-does-not-exist")
    assert c.ntfy_token() == ""   # OSError branch -> "" not a crash

def test_orchestra_dir_env_override(monkeypatch):
    # DB_PATH derives from ORCHESTRA_DIR; changing env + reimport should move it.
    monkeypatch.setenv("ORCHESTRA_DIR", "/tmp/xyz-orch")
    import approval_config as c
    importlib.reload(c)
    assert str(c.DB_PATH) == "/tmp/xyz-orch/state/tasks.db"
    # restore for other tests
    monkeypatch.delenv("ORCHESTRA_DIR", raising=False)
    importlib.reload(c)
