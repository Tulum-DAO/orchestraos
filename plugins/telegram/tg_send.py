#!/usr/bin/env python3
"""tg_send.py — outbound Telegram for OrchestraOS (the `notify` side of plugins.telegram).

CLI (what the gm seat runs to answer the operator, see prompts/gm.md TELEGRAM CHANNEL):
    python3 plugins/telegram/tg_send.py "<text>"                 # to the remembered operator chat
    python3 plugins/telegram/tg_send.py --chat 123456 "<text>"   # explicit chat id
    echo "<text>" | python3 plugins/telegram/tg_send.py          # stdin
Exit 0 only when every chunk came back ok:true (Telegram answers HTTP 200 with ok:false on
soft failures — the body is checked, never just the status code).

Library (the interface track 6 moves scripts/approval_notify.py's _tg_* functions behind):
    send_text(text, chat_id=None) -> bool
    send_photo(path, caption=None, chat_id=None) -> bool
    send_card(card, chat_id=None) -> dict|None     # a pending approval row -> message with inline buttons
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plugins import telegram as _tg  # noqa: E402

API = "https://api.telegram.org"
MAXLEN = 3900          # under Telegram's 4096 hard limit, room for a chunk header
TIMEOUT = 20

# Injected by tests: api(method, payload) -> dict  (the Bot API JSON response)
_api_override: Optional[Callable[[str, dict], dict]] = None


def _api(method: str, payload: dict, files: Optional[dict] = None) -> dict:
    if _api_override is not None:
        return _api_override(method, payload)
    token = _tg.bot_token()
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN is not set"}
    url = f"{API}/bot{token}/{method}"
    try:
        if files:
            body, ctype = _multipart(payload, files)
            req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": str(e)}


def _multipart(fields: dict, files: dict):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, path in files.items():
        p = Path(path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{p.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += p.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _target(chat_id: Optional[int]) -> Optional[int]:
    if chat_id is not None:
        return int(chat_id)
    env = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return _tg.remembered_chat_id()


def _chunks(text: str) -> list:
    text = text or ""
    if len(text) <= MAXLEN:
        return [text]
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > MAXLEN and buf:
            out.append(buf); buf = ""
        while len(line) > MAXLEN:
            out.append(line[:MAXLEN]); line = line[MAXLEN:]
        buf += line
    if buf:
        out.append(buf)
    n = len(out)
    return [f"[{i}/{n}]\n{c}" for i, c in enumerate(out, 1)] if n > 1 else out


def send_text(text: str, chat_id: Optional[int] = None, reply_markup: Optional[dict] = None) -> bool:
    chat = _target(chat_id)
    if chat is None:
        print("tg_send: no chat id — message the bot once from your phone (the router remembers it) "
              "or pass --chat / TELEGRAM_CHAT_ID", file=sys.stderr)
        return False
    ok = True
    parts = _chunks(text)
    for i, part in enumerate(parts):
        payload = {"chat_id": chat, "text": part, "disable_web_page_preview": True}
        if reply_markup and i == len(parts) - 1:
            payload["reply_markup"] = reply_markup
        r = _api("sendMessage", payload)
        if not r.get("ok"):
            print(f"tg_send: sendMessage failed: {r.get('description')}", file=sys.stderr)
            ok = False
    return ok


def send_photo(path: str, caption: Optional[str] = None, chat_id: Optional[int] = None) -> bool:
    chat = _target(chat_id)
    if chat is None or not Path(path).is_file():
        return False
    payload = {"chat_id": chat}
    if caption:
        payload["caption"] = caption[:1024]
    r = _api("sendPhoto", payload, files={"photo": path})
    return bool(r.get("ok"))


# ---- decision cards with inline buttons -------------------------------------------------
# callback_data is at most 64 bytes: "a|<card id>|<verb or option n>"

def card_buttons(card: dict) -> dict:
    cid = card["id"]
    kind = card.get("kind") or "approval"
    rows = []
    menu = card.get("menu")
    if isinstance(menu, str):
        try:
            menu = json.loads(menu)
        except ValueError:
            menu = None
    options = (menu or {}).get("options") if isinstance(menu, dict) else None
    if kind == "menu" and options:
        for o in options:
            n = str(o.get("n") or len(rows) + 1)
            rows.append([{"text": f"{n}. {str(o.get('label') or '')[:40]}", "callback_data": f"a|{cid}|o{n}"}])
    elif kind == "human_task":
        rows.append([{"text": "Done", "callback_data": f"a|{cid}|done"},
                     {"text": "Can't", "callback_data": f"a|{cid}|cant"},
                     {"text": "Snooze", "callback_data": f"a|{cid}|snooze"}])
    else:
        rows.append([{"text": "Approve", "callback_data": f"a|{cid}|approve"},
                     {"text": "Deny", "callback_data": f"a|{cid}|deny"}])
    return {"inline_keyboard": rows}


def card_text(card: dict) -> str:
    kind = card.get("kind") or "approval"
    head = {"menu": "DECISION", "human_task": "WAITING ON YOU", "questionnaire": "QUESTIONNAIRE"}.get(kind, "APPROVAL")
    lines = [f"{head} — {card.get('from_agent', '?')}  ·  {card['id']}", "", str(card.get("question") or card.get("summary") or "")]
    menu = card.get("menu")
    if isinstance(menu, str):
        try:
            menu = json.loads(menu)
        except ValueError:
            menu = None
    if isinstance(menu, dict) and menu.get("options"):
        lines += ["", "Options:"] + [f"  {o.get('n', i)}. {o.get('label', '')}" for i, o in enumerate(menu["options"], 1)]
    if kind == "questionnaire":
        lines += ["", "Open the OrchestraOS dashboard to fill it in."]
    return "\n".join(lines)


def send_card(card: dict, chat_id: Optional[int] = None) -> Optional[dict]:
    """Push one pending card; returns the sent message (for later editing) or None."""
    chat = _target(chat_id)
    if chat is None:
        return None
    payload = {"chat_id": chat, "text": card_text(card)[:4000], "disable_web_page_preview": True}
    if (card.get("kind") or "approval") != "questionnaire":
        payload["reply_markup"] = card_buttons(card)
    r = _api("sendMessage", payload)
    return r.get("result") if r.get("ok") else None


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="tg_send.py", description=__doc__.split("\n")[0])
    ap.add_argument("text", nargs="?", help="message text (or pipe it on stdin)")
    ap.add_argument("--chat", type=int, default=None, help="chat id (default: the remembered operator chat)")
    ap.add_argument("--photo", default=None, help="send this image file, text becomes the caption")
    ap.add_argument("--from", dest="label", default=None, help="prefix the text with [label]")
    ns = ap.parse_args(argv)
    text = ns.text if ns.text is not None else sys.stdin.read()
    text = text.rstrip("\n")
    if ns.label:
        text = f"[{ns.label}] {text}"
    if ns.photo:
        return 0 if send_photo(ns.photo, caption=text or None, chat_id=ns.chat) else 1
    if not text.strip():
        print("tg_send: empty message", file=sys.stderr)
        return 2
    return 0 if send_text(text, chat_id=ns.chat) else 1


if __name__ == "__main__":
    sys.exit(main())
