#!/usr/bin/env python3
"""Send brief status updates to the operator via Telegram."""
import json
import os
import sys
import requests
from pathlib import Path
from datetime import datetime

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR") or Path(__file__).resolve().parent)
ENV_FILE = ORCHESTRA_DIR / ".env"
ACTIVITY_FILE = ORCHESTRA_DIR / "activity.jsonl"


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def send_brief(agent_id: str, stage: str, message: str):
    """Send a brief to the operator via Telegram.

    stage: 'ack' | 'checkpoint' | 'result' | 'blocker'
    """
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("SHAW_TELEGRAM_ID", "")

    # Also try the persisted chat ID file
    if not chat_id:
        chat_id_file = ORCHESTRA_DIR / ".shaw_chat_id"
        if chat_id_file.exists():
            chat_id = chat_id_file.read_text().strip()

    if not token or not chat_id:
        return False

    icons = {"ack": "\U0001f4cb", "checkpoint": "\U0001f504", "result": "\u2705", "blocker": "\U0001f6ab"}
    icon = icons.get(stage, "\U0001f4cc")

    text = f"{icon} *{agent_id}* \u2014 {stage}\n{message}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        pass

    # Also log to activity
    try:
        entry = json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "agent": agent_id,
            "event": f"brief_{stage}",
            "detail": message[:200],
        })
        with open(ACTIVITY_FILE, "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass

    return True


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 brief.py <agent-id> <stage> <message>")
        print("  stage: ack | checkpoint | result | blocker")
        sys.exit(1)
    send_brief(sys.argv[1], sys.argv[2], " ".join(sys.argv[3:]))
