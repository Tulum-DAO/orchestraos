#!/usr/bin/env python3
"""
Unified Conversation Log — single append-only JSONL for all the operator interactions.

All channel adapters (Telegram, Jarvis voice, Dashboard, GM chat) call log_message()
to record interactions. The GM review cycle, scoring engine, and pattern detectors
all read from this single file.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
UNIFIED_LOG = ORCHESTRA_DIR / "state" / "unified-conversation.jsonl"


def log_message(channel: str, role: str, content: str,
                content_type: str = "text", image_path: str = None,
                agent_context: str = None, metadata: dict = None) -> dict:
    """Append a message to the unified conversation log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "role": role,
        "content": content,
        "content_type": content_type,
    }
    if image_path:
        entry["image_path"] = image_path
    if agent_context:
        entry["agent_context"] = agent_context
    if metadata:
        entry["metadata"] = metadata

    UNIFIED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIFIED_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry


def read_recent(n: int = 50, channel: str = None) -> list[dict]:
    """Read the last N entries from the unified log. Optionally filter by channel."""
    if not UNIFIED_LOG.exists():
        return []

    lines = UNIFIED_LOG.read_text().strip().split("\n")
    entries = []
    for line in lines[-(n * 2):]:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if channel and entry.get("channel") != channel:
                continue
            entries.append(entry)
        except json.JSONDecodeError:
            continue

    return entries[-n:]


def read_since(since_ts: str, channel: str = None) -> list[dict]:
    """Read all entries since a given ISO timestamp."""
    if not UNIFIED_LOG.exists():
        return []

    entries = []
    for line in UNIFIED_LOG.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("ts", "") > since_ts:
                if channel and entry.get("channel") != channel:
                    continue
                entries.append(entry)
        except json.JSONDecodeError:
            continue

    return entries
