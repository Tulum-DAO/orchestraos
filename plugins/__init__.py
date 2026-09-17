"""Channel plugins — optional inbound/outbound chat channels (Telegram today, WhatsApp next).

Core never imports a plugin module directly. It asks this registry "which channel is
configured" and gets back a module-like object or None, so a disabled or uninstalled
plugin is simply absent from the import graph (docs/tracks/06-telegram-whatsapp-plugins.md).

Each plugin owns its own `[plugins.<name>]` config section and reads its secrets from the
environment only (never the toml). A plugin module exposes:
  is_enabled(raw_config) -> bool
  status(raw_config)     -> dict   # for `orchestra doctor`: {"ok": bool, "level": "OK|INFO|MISSING|WARN", "detail": str, "fix": str|None}
  send_text(text, chat_id=None) -> bool
"""
from __future__ import annotations

import importlib
from typing import Optional

KNOWN = ("telegram",)


def load(name: str):
    """Import plugins.<name>; None when the package is not installed."""
    try:
        return importlib.import_module(f"plugins.{name}")
    except ImportError:
        return None


def configured_channel(raw: Optional[dict]):
    """The first enabled channel plugin, or None (core's 'no chat channel' state)."""
    raw = raw or {}
    for name in KNOWN:
        mod = load(name)
        if mod is not None and mod.is_enabled(raw):
            return mod
    return None
