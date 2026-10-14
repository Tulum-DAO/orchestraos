"""Grok Bot webhook: tell an outside bot app that its mailbox has new mail (plugins/grok_bot/README.md).

Not a chat channel: it never sends or reads message text, so it is not in plugins.KNOWN (which
`configured_channel` picks gm's chat channel from). It is listed in plugins.NOTIFIERS for its
`orchestra doctor` row. Off unless [plugins.grok_bot] enabled = true; the URL and key live in
<data dir>/state/grok-bot-webhook.env (mode 600), never in orchestra.toml.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_AGENT = "grok-bot"


def _section(raw: Optional[dict]) -> dict:
    return ((raw or {}).get("plugins") or {}).get("grok_bot") or {}


def is_enabled(raw: Optional[dict]) -> bool:
    return bool(_section(raw).get("enabled", False))


def agent(raw: Optional[dict]) -> str:
    return str(_section(raw).get("agent") or DEFAULT_AGENT)


def _env_path(raw: Optional[dict]) -> str:
    from orchestra_cli.settings import resolve_data_dir
    return os.path.join(str(resolve_data_dir(raw or {})), "state", "grok-bot-webhook.env")


def status(raw: Optional[dict]) -> dict:
    """One `orchestra doctor` row. Disabled is INFO, never a failure."""
    if not is_enabled(raw):
        return {"ok": True, "level": "INFO", "detail": "disabled ([plugins.grok_bot] enabled = false)",
                "fix": None}
    from .webhook import read_env, webhook_config
    path = _env_path(raw)
    env = read_env(path)
    if webhook_config(env) is None:
        url = (env.get("GROKBOT_WEBHOOK_URL") or "").strip()
        why = ("its URL is not https" if url and url != "REPLACE_ME" and not url.startswith("https://")
               else "no webhook URL and key set yet")
        return {"ok": False, "level": "WARN", "detail": f"enabled for {agent(raw)}, but {why}; nothing is posted",
                "fix": f"set the webhook URL and key your bot app gives you in {path} (mode 600)"}
    return {"ok": True, "level": "OK", "detail": f"posts to an https webhook when {agent(raw)} has new mail",
            "fix": None}
