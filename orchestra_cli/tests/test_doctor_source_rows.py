"""`orchestra doctor` prints one advisory row per prompt source Arturo reads (arturo:source:<name>):
fresh OK, stale WARN (age, window), missing INFO, unreadable WARN. Never required, never written."""
import json
import os
import sqlite3
import time

from orchestra_cli import doctor as D
from orchestra_cli import settings as S
from orchestra_cli.tests.test_doctor import _by_name, _probes, _repo


def _fence(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARTURO_STATE", str(tmp_path / "data" / "state" / "arturo"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert all(os.environ[k].startswith(str(tmp_path)) for k in ("ORCHESTRA_DIR", "ARTURO_STATE", "HOME"))


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_source_rows_each_status(tmp_path, monkeypatch):
    _fence(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    d = st.data_dir
    prof = d / "state" / "users" / "operator" / "profile.json"
    prof.parent.mkdir(parents=True)
    prof.write_text(json.dumps({"implicit_facts": []}))
    tg = d / "state" / "telegram-outbound.jsonl"
    tg.write_text("{}\n")
    _age(tg, 8 * 3600)
    (d / "state" / "learning.db").mkdir()            # a directory where the db should be
    # facts/facts_db.json is missing
    before = sorted(p.name for p in (d / "state").iterdir())
    rows = _by_name(D.run_doctor(st, _probes()))
    assert rows["arturo:source:profile"].status == D.OK and "fresh" in rows["arturo:source:profile"].detail
    assert rows["arturo:source:telegram"].status == D.WARN and "8h old, window 6h" in rows["arturo:source:telegram"].detail
    assert rows["arturo:source:telegram"].remedy
    assert rows["arturo:source:learning"].status == D.WARN and "unreadable" in rows["arturo:source:learning"].detail
    assert rows["arturo:source:facts"].status == D.INFO and "missing" in rows["arturo:source:facts"].detail
    assert all(not rows[f"arturo:source:{n}"].required for n in ("profile", "telegram", "learning", "facts"))
    assert D.exit_code(list(rows.values())) == 0
    assert sorted(p.name for p in (d / "state").iterdir()) == before      # advisory: wrote nothing


def test_source_rows_missing_everywhere_on_a_clean_install(tmp_path, monkeypatch):
    _fence(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    rows = _by_name(D.run_doctor(st, _probes()))
    for n in ("profile", "telegram", "learning", "facts"):
        assert rows[f"arturo:source:{n}"].status == D.INFO and "missing" in rows[f"arturo:source:{n}"].detail


def test_source_rows_absent_when_arturo_disabled(tmp_path, monkeypatch):
    _fence(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    (root / "orchestra.toml").write_text((root / "orchestra.toml").read_text() + '\n[arturo]\nenabled = false\n')
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    assert not any(c.name.startswith("arturo:source:") for c in D.run_doctor(st, _probes()))


def test_stale_learning_row_says_it_is_still_injected(tmp_path, monkeypatch):
    _fence(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    p = st.data_dir / "state" / "learning.db"
    c = sqlite3.connect(str(p)); c.execute("CREATE TABLE rules (rule_text TEXT, status TEXT)"); c.commit(); c.close()
    _age(p, 10 * 86400)
    row = _by_name(D.run_doctor(st, _probes()))["arturo:source:learning"]
    assert row.status == D.WARN and "10d old, window 7d" in row.detail and "as-of date" in row.detail
