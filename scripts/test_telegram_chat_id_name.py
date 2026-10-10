"""The Telegram chat id setting is ORCHESTRA_TELEGRAM_CHAT_ID (gm). The old operator-named key is still read so
existing installs keep working. One order everywhere: the new name (env, then the file), then the old name (env,
then the file)."""
import importlib.util
import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD = "SHAW_TELEGRAM_ID"
NEW = "ORCHESTRA_TELEGRAM_CHAT_ID"


def _fn(script):
    m = re.search(r"^tg_chat_id\(\) \{\n.*?^\}\n", (ROOT / script).read_text(), re.S | re.M)
    assert m, f"{script} has no tg_chat_id()"
    return m.group(0)


def test_both_shell_senders_carry_the_same_lookup():
    assert _fn("scripts/tg-notify.sh") == _fn("scripts/agent-recovery.sh")


def _sh(tmp_path, file_text, env=None):
    f = tmp_path / ".env.telegram"
    f.write_text(file_text)
    e = {k: v for k, v in os.environ.items() if k not in (OLD, NEW)}
    e.update(env or {})
    r = subprocess.run(["bash", "-c", _fn("scripts/tg-notify.sh") + f'tg_chat_id "{f}"'],
                       capture_output=True, text=True, env=e)
    return r.stdout if r.returncode == 0 else None


@pytest.mark.parametrize("file_text,env,want", [
    (f"{OLD}=111\n", {}, "111"),                                   # an existing install keeps working
    (f'{OLD}=111\n{NEW}="222"\n', {}, "222"),                      # the new name wins; quotes stripped
    (f"{OLD}=111\n", {OLD: "333"}, "333"),                         # env before the file, old name
    (f"{NEW}=222\n", {OLD: "333"}, "222"),                         # any new-name source beats the old name
    ("", {NEW: "444", OLD: "333"}, "444"),
    ("", {}, None),                                                # nothing set -> no id (the caller skips)
])
def test_shell_lookup_order(tmp_path, file_text, env, want):
    assert _sh(tmp_path, file_text, env) == want


def test_router_lookup_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.delenv(OLD, raising=False)
    monkeypatch.delenv(NEW, raising=False)
    spec = importlib.util.spec_from_file_location("message_router_tgid", ROOT / "scripts" / "message-router.py")
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    monkeypatch.setattr(mr, "ORCHESTRA_DIR", tmp_path)
    env = tmp_path / ".env.telegram"

    def tg_id():
        mr.TG_ID = None
        mr.load_env_telegram()
        return mr.TG_ID
    env.write_text(f"TELEGRAM_BOT_TOKEN=t\n{OLD}=111\n")
    assert tg_id() == "111"
    env.write_text(f'TELEGRAM_BOT_TOKEN=t\n{OLD}=111\n{NEW}="222"\n')
    assert tg_id() == "222"
    monkeypatch.setenv(NEW, "444")
    assert tg_id() == "444"


def test_nothing_reads_the_old_name_alone():
    """Every shipped file that still reads the old name reads the new one first."""
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert len(files) > 300
    lonely = []
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        if name.startswith("test_") or rel.startswith("docs/") or not rel.endswith((".py", ".sh", ".ts", ".js")):
            continue
        try:
            text = (ROOT / rel).read_text(errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        if re.search(r"SHAW_TELEGRAM(_CHAT)?_ID", text) and NEW not in text:
            lonely.append(rel)
    assert not lonely, f"reads the old Telegram chat id name without {NEW}: {lonely}"
