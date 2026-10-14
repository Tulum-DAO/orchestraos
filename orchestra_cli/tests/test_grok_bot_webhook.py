"""Grok Bot webhook (public port of the private grok-bot notifier; gm rulings msg_0324aa2a).

One https POST to the bot app's webhook when the agent's mailbox has NEW pending ids: ids and a
count, never text. Default off; run by `orchestra up` only with [plugins.grok_bot] enabled = true;
URL + key from a mode-600 file in the data dir, never orchestra.toml, never logged.
"""
import json
import os
import sqlite3
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from plugins.grok_bot import webhook as hook  # noqa: E402
import plugins.grok_bot as plugin  # noqa: E402


def _db(path, rows):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE messages (id TEXT, to_agent TEXT, status TEXT, tenant_id TEXT, created_at TEXT, "
              "subject TEXT, body TEXT)")
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    c.commit(); c.close()


def _box(tmp_path, url="https://bot.example/hook", key="k-secret"):
    state = tmp_path / "state"; state.mkdir()
    _db(state / "tasks.db", [("m1", "grok-bot", "pending", "operator", "1", "secret subject", "secret body"),
                              ("m2", "gm", "pending", "operator", "2", "x", "y")])
    p = hook.paths(str(tmp_path))
    hook.ensure_env_file(p["env"])
    if url:
        Path(p["env"]).write_text(f"GROKBOT_WEBHOOK_URL={url}\nGROKBOT_WEBHOOK_KEY={key}\n")
    return p


# --- config gate --------------------------------------------------------------------------------
def test_placeholders_and_http_are_not_configured():
    assert hook.webhook_config({}) is None
    assert hook.webhook_config({"GROKBOT_WEBHOOK_URL": "REPLACE_ME", "GROKBOT_WEBHOOK_KEY": "REPLACE_ME"}) is None
    assert hook.webhook_config({"GROKBOT_WEBHOOK_URL": "http://bot.example/h", "GROKBOT_WEBHOOK_KEY": "k"}) is None
    assert hook.webhook_config({"GROKBOT_WEBHOOK_URL": "https://bot.example/h", "GROKBOT_WEBHOOK_KEY": "k"}) == \
        ("https://bot.example/h", "k")


def test_env_file_is_created_mode_600_in_the_data_dir_and_never_overwritten(tmp_path):
    p = hook.paths(str(tmp_path))
    assert p["env"] == str(tmp_path / "state" / "grok-bot-webhook.env")
    hook.ensure_env_file(p["env"])
    assert os.stat(p["env"]).st_mode & 0o777 == 0o600
    Path(p["env"]).write_text("GROKBOT_WEBHOOK_URL=https://bot.example/h\nGROKBOT_WEBHOOK_KEY=k\n")
    hook.ensure_env_file(p["env"])
    assert "https://bot.example/h" in Path(p["env"]).read_text()


# --- one POST per new id, ids + count only, key never logged ----------------------------------------
def test_posts_new_ids_once_with_no_message_text(tmp_path, monkeypatch, capsys):
    p = _box(tmp_path)
    sent_payloads = []
    monkeypatch.setattr(hook, "post_once", lambda url, key, payload, timeout=15.0: sent_payloads.append((url, key, payload)) or 204)
    sent, owing = hook.check_once(p, "grok-bot", "operator", set())
    assert sent == {"m1"} and not owing
    sent, owing = hook.check_once(p, "grok-bot", "operator", sent)               # nothing new: no POST
    assert len(sent_payloads) == 1
    url, key, payload = sent_payloads[0]
    assert payload == {"agent": "grok-bot", "pending": 1, "new_ids": ["m1"]}      # never gm's mail
    body = json.dumps(payload)
    assert "secret subject" not in body and "secret body" not in body
    assert "k-secret" not in capsys.readouterr().out


def test_a_failed_post_is_retried_and_not_recorded(tmp_path, monkeypatch, capsys):
    p = _box(tmp_path)
    codes = iter([500, 500, 500, 204])
    monkeypatch.setattr(hook, "post_once", lambda *a, **k: next(codes))
    monkeypatch.setattr(hook.time, "sleep", lambda s: None)
    sent, owing = hook.check_once(p, "grok-bot", "operator", set())
    assert sent == set() and owing
    sent, owing = hook.check_once(p, "grok-bot", "operator", sent)
    assert sent == {"m1"} and not owing
    out = capsys.readouterr().out
    assert "status=500" in out and "k-secret" not in out


def test_nothing_is_posted_unconfigured_or_with_the_kill_file(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hook, "post_once", lambda *a, **k: calls.append(a) or 204)
    p = _box(tmp_path, url=None)                                            # placeholders
    assert hook.check_once(p, "grok-bot", "operator", set()) == (set(), False)
    Path(p["env"]).write_text("GROKBOT_WEBHOOK_URL=https://bot.example/h\nGROKBOT_WEBHOOK_KEY=k\n")
    Path(p["kill"]).write_text("")
    assert hook.check_once(p, "grok-bot", "operator", set()) == (set(), False)
    assert calls == []


def test_http_error_is_a_status_not_a_crash(monkeypatch):
    def boom(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 503, "no", hdrs=None, fp=None)
    monkeypatch.setattr(hook.urllib.request, "urlopen", boom)
    assert hook.post_once("https://bot.example/h", "k", {"a": 1}) == 503


def test_without_inotify_it_polls_instead_of_crashing(monkeypatch):
    def no_inotify(*a, **k):
        raise AttributeError("inotify_init1")
    monkeypatch.setattr(hook, "Inotify", no_inotify)
    w = hook.watcher("/tmp/x/tasks.db", poll_s=0.01)
    assert w.wait(5) == set(hook.DB_NAMES)


# --- default off, supervised only when enabled, doctor row, never a chat channel ------------------
def _settings(tmp_path, section=""):
    from orchestra_cli import settings as S
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(f'[data]\ndir = "{tmp_path / "data"}"\n{section}')
    return S.load_settings(config_path=cfg)


def test_the_supervisor_runs_it_only_when_enabled(tmp_path):
    from orchestra_cli import process_table as PT
    entry = lambda st: [e for e in PT.build_process_table(st) if e.name == "grok_bot_webhook"][0]
    off = entry(_settings(tmp_path))
    assert off.enabled is False
    on = entry(_settings(tmp_path, '[plugins.grok_bot]\nenabled = true\nagent = "my-bot"\n'))
    assert on.enabled is True and on.argv[-2:] == ["--agent", "my-bot"]


def test_doctor_row_off_unconfigured_and_ready(tmp_path):
    raw = {"data": {"dir": str(tmp_path)}}
    assert plugin.status(raw)["level"] == "INFO"
    raw["plugins"] = {"grok_bot": {"enabled": True}}
    row = plugin.status(raw)
    assert row["level"] == "WARN" and "your bot app gives you" in row["fix"]
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "grok-bot-webhook.env").write_text("GROKBOT_WEBHOOK_URL=http://x/h\nGROKBOT_WEBHOOK_KEY=k\n")
    assert "not https" in plugin.status(raw)["detail"]
    (tmp_path / "state" / "grok-bot-webhook.env").write_text("GROKBOT_WEBHOOK_URL=https://x/h\nGROKBOT_WEBHOOK_KEY=k\n")
    assert plugin.status(raw)["level"] == "OK"


def test_it_is_never_picked_as_the_chat_channel():
    import plugins
    raw = {"plugins": {"grok_bot": {"enabled": True}}}
    assert "grok_bot" not in plugins.KNOWN and "grok_bot" in plugins.NOTIFIERS
    assert plugins.configured_channel(raw) is None
