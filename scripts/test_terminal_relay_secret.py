import importlib


def test_explicit_file_is_shared_and_existing_value_is_reused(tmp_path, monkeypatch):
    p = tmp_path / "relay-secret"
    p.write_text("already-there\n")
    monkeypatch.setenv("TERMINAL_RELAY_SECRET_FILE", str(p))
    mod = importlib.import_module("scripts.terminal_relay_secret")
    assert mod.path() == p
    assert mod.ensure() == "already-there"
    assert p.read_text() == "already-there\n"


def test_creator_race_reads_the_winning_file(tmp_path, monkeypatch):
    p = tmp_path / "relay-secret"
    monkeypatch.setenv("TERMINAL_RELAY_SECRET_FILE", str(p))
    mod = importlib.import_module("scripts.terminal_relay_secret")
    real_open = mod.os.open

    def race(path, flags, mode):
        p.write_text("winner\n")
        raise FileExistsError()

    monkeypatch.setattr(mod.os, "open", race)
    assert mod.ensure() == "winner"
    monkeypatch.setattr(mod.os, "open", real_open)
