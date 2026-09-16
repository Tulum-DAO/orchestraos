# gateway_conf.py — where the watch gateway lives + its bearer.
# B1: the gateway (scripts/watch_gateway.py gateway_token) reads its expected token from a
# FILE — WATCH_GATEWAY_TOKEN_FILE else ~/.config/jarvis/watch-gateway-token — NOT an env var
# and NOT .env.secrets. We MUST read the same file or every injection 401s.
import os
from pathlib import Path

AGENT_MESSAGE_URL = "http://127.0.0.1:9091/agent-message"


def _token_file() -> Path:
    return Path(os.environ.get("WATCH_GATEWAY_TOKEN_FILE",
                               os.path.expanduser("~/.config/jarvis/watch-gateway-token")))


def token() -> str:
    try:
        return _token_file().read_text().strip()
    except OSError:
        return ""
