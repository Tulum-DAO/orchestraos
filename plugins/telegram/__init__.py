"""plugins.telegram — the operator's phone as a chat channel for the gm seat.

  router.py   inbound: long-polls the Bot API, drops each operator message into the gm
              seat's msg_store inbox (from_agent=telegram, chat id in metadata), pushes
              pending decision cards to the phone with inline buttons and lands button
              taps through scripts/approval.py answer.
  tg_send.py  outbound: `python3 plugins/telegram/tg_send.py "<text>"` — what gm runs to
              answer; the send_text/send_photo/send_card interface track 6 builds on.

Config: [plugins.telegram] enabled = true/false, allowed_chat_ids = [123, ...] (empty =
first chat that talks to the bot is remembered and becomes the operator). Secret: the
TELEGRAM_BOT_TOKEN environment variable (or a `TELEGRAM_BOT_TOKEN=...` line in
<data dir>/.env.telegram, the file scripts/tg-notify.sh also reads). Never the toml.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

NAME = "telegram"


def _section(raw: Optional[dict]) -> dict:
    return ((raw or {}).get("plugins") or {}).get("telegram") or {}


def is_enabled(raw: Optional[dict]) -> bool:
    return bool(_section(raw).get("enabled", False))


def allowed_chat_ids(raw: Optional[dict]) -> list:
    ids = _section(raw).get("allowed_chat_ids") or []
    out = []
    for v in ids:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def data_dir() -> Path:
    return Path(os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra"))


def state_dir() -> Path:
    return data_dir() / "state" / "telegram"


def bot_token() -> str:
    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if tok:
        return tok
    env_file = data_dir() / ".env.telegram"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def remembered_chat_id() -> Optional[int]:
    try:
        return int((state_dir() / "chat-id").read_text().strip())
    except (OSError, ValueError):
        return None


def remember_chat_id(chat_id: int) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    tmp = state_dir() / "chat-id.tmp"
    tmp.write_text(str(int(chat_id)))
    os.replace(tmp, state_dir() / "chat-id")


def status(raw: Optional[dict]) -> dict:
    """One `orchestra doctor` row. Disabled is INFO (a clean 'channel off' state), not a failure."""
    if not is_enabled(raw):
        return {"ok": True, "level": "INFO", "detail": "disabled ([plugins.telegram] enabled = false)",
                "fix": "set enabled = true and export TELEGRAM_BOT_TOKEN to use your phone as the gm channel"}
    if not bot_token():
        return {"ok": False, "level": "MISSING", "detail": "enabled but no TELEGRAM_BOT_TOKEN in the environment",
                "fix": "create a bot with @BotFather, then export TELEGRAM_BOT_TOKEN=... (or put it in <data>/.env.telegram)"}
    allow = allowed_chat_ids(raw)
    chat = remembered_chat_id()
    who = (f"{len(allow)} allowed chat id(s)" if allow else
           (f"open; remembered operator chat {chat}" if chat else "open; the first chat that messages the bot becomes the operator"))
    return {"ok": True, "level": "OK", "detail": f"token set; {who}", "fix": None}


def send_text(text: str, chat_id: Optional[int] = None) -> bool:
    from . import tg_send
    return tg_send.send_text(text, chat_id=chat_id)
