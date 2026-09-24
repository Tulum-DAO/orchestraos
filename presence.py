#!/usr/bin/env python3
"""Check the operator's current presence status."""
import json
import sys
from datetime import datetime
from pathlib import Path

PRESENCE_FILE = Path.home() / "scripts" / "agent-orchestra" / "state" / "operator-presence.json"
HEARTBEAT_FILE = Path.home() / "scripts" / "agent-orchestra" / "state" / "mac-heartbeat.json"


def get_operator_presence() -> dict:
    """Returns {status, minutes_ago, behavior}"""
    # Check Telegram activity
    try:
        data = json.loads(PRESENCE_FILE.read_text())
        last_msg = datetime.fromisoformat(data["last_telegram_message"].replace("Z", "+00:00"))
        age_min = (datetime.now(last_msg.tzinfo) - last_msg).total_seconds() / 60
    except Exception:
        age_min = 999  # Unknown = treat as offline

    # Check Mac heartbeat
    mac_dead = False
    try:
        hb = json.loads(HEARTBEAT_FILE.read_text())
        if hb.get("consecutive_failures", 0) >= 3:
            mac_dead = True
    except Exception:
        pass

    if mac_dead:
        return {
            "status": "transit",
            "minutes_ago": age_min,
            "behavior": "Trigger transit mode. Mac is offline. Continue work on VPS.",
        }
    elif age_min < 5:
        return {
            "status": "active",
            "minutes_ago": round(age_min, 1),
            "behavior": "the operator is here. Be collaborative. Ask before big decisions. Quick responses.",
        }
    elif age_min < 30:
        return {
            "status": "away",
            "minutes_ago": round(age_min, 1),
            "behavior": "the operator stepped away. Work autonomously on assigned tasks. Send brief updates only.",
        }
    else:
        return {
            "status": "offline",
            "minutes_ago": round(age_min, 1),
            "behavior": "the operator is offline. Full autonomous mode. Process backlog. Compile report for return.",
        }


if __name__ == "__main__":
    result = get_operator_presence()
    if "--json" in sys.argv:
        print(json.dumps(result))
    else:
        print(f"the operator: {result['status'].upper()} ({result['minutes_ago']:.0f}m ago)")
        print(f"  -> {result['behavior']}")
