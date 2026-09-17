#!/usr/bin/env python3
"""router.py — inbound Telegram for OrchestraOS (runs under `orchestra up` when
[plugins.telegram] enabled = true; stdlib only, no bot framework).

What it does, and nothing else:
  * long-polls the Bot API (getUpdates) with the token from TELEGRAM_BOT_TOKEN;
  * every operator message becomes a row in the gm seat's msg_store inbox —
    from_agent=telegram, subject = first line, metadata carries chat_id / message_id /
    username (+ downloaded photo/document/voice paths under <data>/state/uploads/telegram);
    gm answers with plugins/telegram/tg_send.py;
  * pushes each new pending decision card to the operator's chat with inline buttons and
    lands a button tap through `scripts/approval.py answer --surface phone
    --answered-by operator` — the same ONE core the dashboard and the watch use;
  * remembers the operator chat (<data>/state/telegram/chat-id) so outbound sends need no
    configuration beyond the token. With allowed_chat_ids set, everyone else is ignored.

State: <data>/state/telegram/{offset,chat-id,notified.json}. Logs to stdout (the supervisor
captures it). Never raises out of the loop: one bad update is logged and skipped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

CODE_ROOT = Path(os.environ.get("ORCHESTRA_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(CODE_ROOT))
from plugins import telegram as _tg          # noqa: E402
from plugins.telegram import tg_send         # noqa: E402

API = "https://api.telegram.org"
POLL_TIMEOUT = 25            # long-poll seconds
CARD_POLL_EVERY = 20         # seconds between pending-card sweeps
GM_SEAT = os.environ.get("ORCHESTRA_GM_SEAT", "gm")
PY = sys.executable or "python3"


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "[telegram]", msg, flush=True)


# ---- Bot API ---------------------------------------------------------------------------

def bot_api(method: str, payload: Optional[dict] = None, timeout: int = POLL_TIMEOUT + 10) -> dict:
    token = _tg.bot_token()
    url = f"{API}/bot{token}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": str(e)}


def download_file(file_id: str, dest_dir: Path, api: Callable = bot_api, fetch: Optional[Callable] = None) -> Optional[Path]:
    r = api("getFile", {"file_id": file_id})
    if not r.get("ok"):
        return None
    fpath = r["result"].get("file_path") or file_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{int(time.time())}-{Path(fpath).name}"
    try:
        if fetch is not None:
            dest.write_bytes(fetch(fpath))
        else:
            url = f"{API}/file/bot{_tg.bot_token()}/{fpath}"
            with urllib.request.urlopen(url, timeout=60) as src:
                dest.write_bytes(src.read())
        return dest
    except Exception as e:  # noqa: BLE001
        log(f"download failed: {e}")
        return None


# ---- state -------------------------------------------------------------------------------

class State:
    def __init__(self, base: Optional[Path] = None):
        self.dir = base or _tg.state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _read(self, name: str, default):
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, ValueError):
            return default

    def _write(self, name: str, value) -> None:
        tmp = self.dir / f"{name}.tmp"
        tmp.write_text(json.dumps(value))
        os.replace(tmp, self.dir / name)

    @property
    def offset(self) -> int:
        return int(self._read("offset", 0) or 0)

    @offset.setter
    def offset(self, v: int) -> None:
        self._write("offset", int(v))

    def notified(self) -> dict:
        return self._read("notified.json", {})

    def mark_notified(self, card_id: str, message: Optional[dict]) -> None:
        d = self.notified()
        d[card_id] = {"at": time.time(), "message_id": (message or {}).get("message_id"),
                      "chat_id": ((message or {}).get("chat") or {}).get("id")}
        if len(d) > 500:
            for k in sorted(d, key=lambda k: d[k]["at"])[: len(d) - 500]:
                d.pop(k, None)
        self._write("notified.json", d)


# ---- core seams (injectable for tests) ----------------------------------------------------

def deliver_to_gm(text: str, meta: dict, seat: str = GM_SEAT) -> str:
    """One msg_store row in the gm inbox. Import path = the checkout (code), DB = data dir."""
    import msg_store  # noqa: WPS433  (CODE_ROOT is on sys.path)
    subject = (text.strip().splitlines() or ["(media)"])[0][:120]
    return msg_store.MessageStore().send(
        from_agent="telegram", to_agent=seat, type="task_request", subject=subject,
        body=text, priority="high", source="telegram", metadata=dict(meta, channel="telegram"))


def pending_cards() -> list:
    """The canonical pending feed (same rows the dashboard reads)."""
    r = subprocess.run([PY, str(CODE_ROOT / "scripts" / "approval.py"), "pending", "--json"],
                       capture_output=True, text=True, timeout=30, env=dict(os.environ, ORCHESTRA_DIR=str(_tg.data_dir())))
    if r.returncode != 0:
        log(f"approval.py pending failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        return []
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def answer_card(card_id: str, verb: str) -> tuple:
    """Land a button tap through the ONE core. verb: approve|deny|done|cant|snooze|o<n>."""
    argv = [PY, str(CODE_ROOT / "scripts" / "approval.py"), "answer", "--id", card_id,
            "--surface", "phone", "--answered-by", "operator"]
    if verb.startswith("o") and verb[1:].isdigit():
        argv += ["--answer", "option", "--option-n", verb[1:]]
    else:
        argv += ["--answer", verb]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=dict(os.environ, ORCHESTRA_DIR=str(_tg.data_dir())))
    return r.returncode == 0, (r.stdout or r.stderr).strip()[-300:]


# ---- update handling -----------------------------------------------------------------------

def parse_callback(data: str) -> Optional[tuple]:
    parts = (data or "").split("|")
    if len(parts) == 3 and parts[0] == "a" and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None


class Router:
    def __init__(self, raw_config: dict, *, api: Callable = bot_api, deliver: Callable = deliver_to_gm,
                 answer: Callable = answer_card, pending: Callable = pending_cards,
                 state: Optional[State] = None, fetch: Optional[Callable] = None):
        self.raw = raw_config
        self.api = api
        self.deliver = deliver
        self.answer = answer
        self.pending = pending
        self.state = state or State()
        self.fetch = fetch
        self.allow = _tg.allowed_chat_ids(raw_config)
        self.uploads = _tg.data_dir() / "state" / "uploads" / "telegram"

    # auth: allowlist wins; otherwise the remembered chat, or the first chat that talks to us
    def authorized(self, chat_id: int) -> bool:
        if self.allow:
            return chat_id in self.allow
        known = _tg.remembered_chat_id()
        if known is None:
            _tg.remember_chat_id(chat_id)
            log(f"operator chat remembered: {chat_id}")
            return True
        return chat_id == known

    def operator_chat(self) -> Optional[int]:
        if self.allow:
            return self.allow[0]
        return _tg.remembered_chat_id()

    def handle_update(self, upd: dict) -> None:
        if "callback_query" in upd:
            self.handle_callback(upd["callback_query"])
        elif "message" in upd:
            self.handle_message(upd["message"])

    def handle_message(self, msg: dict) -> None:
        chat_id = int((msg.get("chat") or {}).get("id"))
        if not self.authorized(chat_id):
            log(f"ignored message from unauthorized chat {chat_id}")
            return
        _tg.remember_chat_id(chat_id) if not self.allow else None
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text in ("/start", "/chatid"):
            self.api("sendMessage", {"chat_id": chat_id,
                                     "text": f"Connected. This chat ({chat_id}) is the operator channel for the {GM_SEAT} seat. "
                                             "Send anything; decision cards arrive here with buttons."})
            return
        meta = {"chat_id": chat_id, "message_id": msg.get("message_id"),
                "username": ((msg.get("from") or {}).get("username")), "attachments": []}
        for kind, key in (("photo", "photo"), ("document", "document"), ("voice", "voice"), ("video", "video"), ("audio", "audio")):
            obj = msg.get(key)
            if not obj:
                continue
            if kind == "photo":
                obj = obj[-1]   # largest size
            path = download_file(obj["file_id"], self.uploads, api=self.api, fetch=self.fetch)
            if path:
                meta["attachments"].append({"kind": kind, "path": str(path), "name": obj.get("file_name")})
        if meta["attachments"]:
            text = (text + "\n\n" if text else "") + "Attachments:\n" + "\n".join(f"  {a['kind']}: {a['path']}" for a in meta["attachments"])
        if not text:
            return
        mid = self.deliver(text, meta)
        log(f"-> {GM_SEAT} inbox {mid} from chat {chat_id}: {text[:60]!r}")

    def handle_callback(self, cq: dict) -> None:
        cq_id = cq.get("id")
        chat_id = int(((cq.get("message") or {}).get("chat") or {}).get("id") or 0)
        if not self.authorized(chat_id):
            self.api("answerCallbackQuery", {"callback_query_id": cq_id, "text": "not authorized"})
            return
        parsed = parse_callback(cq.get("data", ""))
        if not parsed:
            self.api("answerCallbackQuery", {"callback_query_id": cq_id})
            return
        card_id, verb = parsed
        ok, detail = self.answer(card_id, verb)
        label = verb[1:] and f"option {verb[1:]}" if verb.startswith("o") and verb[1:].isdigit() else verb
        self.api("answerCallbackQuery", {"callback_query_id": cq_id,
                                         "text": f"{card_id}: {label} recorded" if ok else f"{card_id}: failed — {detail[:150]}",
                                         "show_alert": not ok})
        msg = cq.get("message") or {}
        if ok and msg.get("message_id"):
            self.api("editMessageText", {"chat_id": chat_id, "message_id": msg["message_id"],
                                         "text": (msg.get("text") or card_id) + f"\n\n✔ answered: {label}"})
        log(f"card {card_id} {label}: {'ok' if ok else 'FAILED ' + detail[:120]}")

    # ---- cards -> phone ----
    def push_pending_cards(self) -> int:
        chat = self.operator_chat()
        if chat is None:
            return 0
        seen = self.state.notified()
        n = 0
        for card in self.pending():
            cid = card.get("id")
            if not cid or cid in seen:
                continue
            if card.get("feature") == "demo":
                self.state.mark_notified(cid, None)     # demo fixtures never leave the machine
                continue
            sent = tg_send.send_card(card, chat_id=chat)
            self.state.mark_notified(cid, sent)
            n += 1
            log(f"card {cid} -> chat {chat}: {'sent' if sent else 'send FAILED'}")
        return n

    # ---- loop ----
    def poll_once(self) -> int:
        r = self.api("getUpdates", {"offset": self.state.offset, "timeout": POLL_TIMEOUT,
                                    "allowed_updates": ["message", "callback_query"]})
        if not r.get("ok"):
            log(f"getUpdates failed: {r.get('description')}")
            time.sleep(5)
            return 0
        updates = r.get("result") or []
        for upd in updates:
            try:
                self.handle_update(upd)
            except Exception as e:  # noqa: BLE001
                log(f"update {upd.get('update_id')} failed: {e!r}")
            self.state.offset = int(upd["update_id"]) + 1
        return len(updates)

    def run(self) -> None:
        log(f"router up: seat={GM_SEAT} data={_tg.data_dir()} allow={self.allow or 'open'}")
        me = self.api("getMe", {}, timeout=15) if self.api is bot_api else {"ok": True, "result": {"username": "test"}}
        if not me.get("ok"):
            log(f"getMe failed ({me.get('description')}); check TELEGRAM_BOT_TOKEN")
        else:
            log(f"bot @{me['result'].get('username')}")
        last_cards = 0.0
        while True:
            self.poll_once()
            if time.time() - last_cards >= CARD_POLL_EVERY:
                try:
                    self.push_pending_cards()
                except Exception as e:  # noqa: BLE001
                    log(f"card sweep failed: {e!r}")
                last_cards = time.time()


def load_raw_config() -> dict:
    try:
        from orchestra_cli.settings import load_settings
        return load_settings().raw
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    raw = load_raw_config()
    if not _tg.is_enabled(raw) and not os.environ.get("TELEGRAM_FORCE"):
        log("disabled ([plugins.telegram] enabled = false); exiting 0")
        return 0
    if not _tg.bot_token():
        log("no TELEGRAM_BOT_TOKEN; exiting 1")
        return 1
    Router(raw).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
