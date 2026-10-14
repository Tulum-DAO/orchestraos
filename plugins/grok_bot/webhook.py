#!/usr/bin/env python3
"""POST once when the Grok Bot's mailbox has new pending mail. No LLM, no timer wakes, no ack.

    python3 plugins/grok_bot/webhook.py [--agent grok-bot]

`orchestra up` runs this only when [plugins.grok_bot] enabled = true. It watches msg_store
(tasks.db) and, when the agent has pending message ids it has not reported yet, POSTs

    {"agent": "<agent>", "pending": <count>, "new_ids": [<message ids>]}

to the webhook URL your bot app gives you, with "Authorization: Bearer <key>". The payload never
carries a subject or a body: a leaked URL learns only that mail is waiting. The bot reads and
answers the mail itself.

Credentials: <data dir>/state/grok-bot-webhook.env (mode 600, created with REPLACE_ME
placeholders, never overwritten). While either value is still REPLACE_ME, the URL is not https, or
the file is readable by anyone but you, the process stays up and posts nothing. The key is never
logged, and a redirect is never followed. Delivery is at least once: an id is reported again if the
process dies between the POST and recording it, or if it becomes pending again later.
State (ids already posted): <data dir>/state/grok-bot-webhook.json.
Off: [plugins.grok_bot] enabled = false, or touch <data dir>/state/GROK_BOT_WEBHOOK_DISABLED.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from grok_mail_watch import (  # noqa: E402
    DB_NAMES,
    IN_CLOSE_WRITE,
    IN_CREATE,
    IN_MODIFY,
    IN_MOVED_TO,
    Inotify,
    pending_ids,
)

PLACEHOLDER = "REPLACE_ME"
ENV_TEMPLATE = (
    "# The webhook URL and key your bot app gives you. Placeholders = nothing is posted.\n"
    "GROKBOT_WEBHOOK_URL=REPLACE_ME\n"
    "GROKBOT_WEBHOOK_KEY=REPLACE_ME\n"
)


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} [grok-bot-webhook] {msg}",
          flush=True)


def data_dir() -> str:
    if os.environ.get("ORCHESTRA_DIR"):
        return os.environ["ORCHESTRA_DIR"]
    from orchestra_cli.settings import data_dir as _data_dir
    return str(_data_dir())


def paths(base: str | None = None) -> dict:
    state = os.path.join(base or data_dir(), "state")
    return {"env": os.path.join(state, "grok-bot-webhook.env"),
            "state": os.path.join(state, "grok-bot-webhook.json"),
            "kill": os.path.join(state, "GROK_BOT_WEBHOOK_DISABLED"),
            "db": os.environ.get("MSG_DB_PATH") or os.path.join(state, "tasks.db")}


def ensure_env_file(path: str) -> None:
    """Create a mode-600 placeholder file. Never overwrite an existing one."""
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, ENV_TEMPLATE.encode())
    finally:
        os.close(fd)


def loose_permissions(path: str) -> bool:
    """True when the credentials file is readable or writable by anyone but its owner."""
    try:
        return bool(os.stat(path).st_mode & 0o077)
    except OSError:
        return False


def read_env(path: str) -> dict:
    """The file's KEY=value pairs, or {} when it is missing or its permissions are looser than 600
    (the key would be exposed; nothing is posted until it is chmod 600)."""
    if loose_permissions(path):
        return {}
    vals = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return vals


def webhook_config(env: dict) -> tuple[str, str] | None:
    """(url, key) when both are set, not placeholders, and the URL is https; else None."""
    url = (env.get("GROKBOT_WEBHOOK_URL") or "").strip()
    key = (env.get("GROKBOT_WEBHOOK_KEY") or "").strip()
    if not url or not key or url == PLACEHOLDER or key == PLACEHOLDER:
        return None
    if not url.startswith("https://"):
        return None
    return url, key


def load_state(path: str) -> dict:
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, d: dict) -> None:
    if load_state(path) == d:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it could carry the bearer key to an http:// or another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post_once(url: str, key: str, payload: dict, timeout: float = 15.0) -> int:
    """Return the HTTP status, or 0 on a network error. Never logs the key. A redirect is not
    followed: its 3xx status is returned (not a success), so the ids stay unreported."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "orchestraos-grok-bot-webhook"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            resp.read()
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def post_with_retry(url: str, key: str, payload: dict, attempts: int = 3, sleep=time.sleep) -> bool:
    delay = 1.0
    for i in range(attempts):
        code = post_once(url, key, payload)
        if 200 <= code < 300:
            return True
        log(f"POST failed status={code} attempt={i + 1}/{attempts}")
        if i + 1 < attempts:
            sleep(delay)
            delay *= 2
    return False


class _Poll:
    """Where inotify is unavailable (macOS): wake every `every` seconds and re-count."""

    def __init__(self, every: float):
        self.every = every

    def wait(self, timeout: float) -> set:
        time.sleep(min(timeout, self.every))
        return set(DB_NAMES)


def watcher(db: str, poll_s: float = 10.0):
    try:
        return Inotify(os.path.dirname(db), IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE)
    except (OSError, AttributeError):
        log(f"inotify unavailable; re-counting every {poll_s:.0f}s")
        return _Poll(poll_s)


def check_once(p: dict, agent: str, tenant: str, sent: set, attempts: int = 3) -> tuple[set, bool]:
    """One pass: post the ids not reported yet. Returns (sent ids, still owing a POST)."""
    cfg = webhook_config(read_env(p["env"]))
    if cfg is None or os.path.exists(p["kill"]):
        return sent, False
    ids = pending_ids(p["db"], agent, tenant)
    sent = sent & set(ids)
    fresh = [i for i in ids if i not in sent]
    if not fresh:
        save_state(p["state"], {"sent": sorted(sent)})
        return sent, False
    url, key = cfg
    ok = post_with_retry(url, key, {"agent": agent, "pending": len(ids), "new_ids": fresh},
                         attempts=attempts)
    if ok:
        sent = sent | set(fresh)
        log(f"posted {len(fresh)} new id(s), {len(ids)} pending")
    else:
        log("POST not accepted; ids stay unsent and will be retried")
    save_state(p["state"], {"sent": sorted(sent)})
    return sent, not ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="POST a webhook when the Grok Bot's mailbox has new mail")
    ap.add_argument("--agent", default="grok-bot")
    ap.add_argument("--tenant", default=os.environ.get("ORCHESTRA_TENANT") or "operator")
    ap.add_argument("--debounce-s", type=float, default=2.0)
    ap.add_argument("--safety-s", type=float, default=120.0)
    ap.add_argument("--retry-s", type=float, default=30.0, help="re-check this often while a POST is owed")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)

    p = paths()
    ensure_env_file(p["env"])
    sent = set(load_state(p["state"]).get("sent") or [])
    if loose_permissions(p["env"]):
        log(f"{p['env']} is readable by others: chmod 600 it; nothing is posted until then")
    elif webhook_config(read_env(p["env"])) is None:
        log(f"webhook not configured: set the URL and key your bot app gives you in {p['env']}")
    else:
        log(f"watching {p['db']} for {a.agent} (pid {os.getpid()})")
    if a.once:
        try:
            _, owing = check_once(p, a.agent, a.tenant, sent, a.attempts)
        except sqlite3.Error as e:
            log(f"db read failed: {e}")
            return 1
        return 1 if owing else 0
    w = watcher(p["db"])
    owing = False
    while True:
        names = w.wait(a.retry_s if owing else a.safety_s)
        if names and not (names & DB_NAMES) and not owing:
            continue
        if names:
            time.sleep(a.debounce_s)
        try:
            sent, owing = check_once(p, a.agent, a.tenant, sent, a.attempts)
        except sqlite3.Error as e:
            log(f"db read failed: {e}")
            owing = False


if __name__ == "__main__":
    sys.exit(main())
