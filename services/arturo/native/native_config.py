"""Native GPT-Live configuration (ported from the reviewed POC). The flag gates the service's live sessions; the relay
voice path and its vendor setting are untouched.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import protocol

FLAG = "ARTURO_NATIVE_GPTLIVE"
CLAIM_GATE_FLAG = "ARTURO_NATIVE_CLAIM_GATE"
PROMPT_FILE = Path(__file__).with_name("native_prompt.md")
BACKEND_MODEL = os.environ.get("ARTURO_NATIVE_BACKEND_MODEL", "gpt-6-luna")   # answered E2 (10-14): available
VOICE = os.environ.get("ARTURO_NATIVE_VOICE", "marin")


def enabled():
    """OFF unless explicitly "1"."""
    return os.environ.get(FLAG, "") == "1"


def claim_gate_enabled():
    """ON unless explicitly "0" (the escape hatch named on the restart card)."""
    return os.environ.get(CLAIM_GATE_FLAG, "1") != "0"


def prompts(path=PROMPT_FILE):
    """{'live': str, 'backend': str} from the '## LIVE' and '## BACKEND' sections of native_prompt.md."""
    text = Path(path).read_text(encoding="utf-8")
    parts = {}
    for name in ("LIVE", "BACKEND"):
        m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M)
        if not m or not m.group(1).strip():
            raise ValueError(f"native_prompt.md has no '## {name}' section")
        parts[name.lower()] = m.group(1).strip()
    return parts


def session_start_frame(tools, voice=None, context=""):
    """`context`: the thread above this call (call_context.opening), after both prompts so the rules come first."""
    p = prompts()
    live, backend = p["live"], p["backend"]
    if context and context.strip():
        live, backend = f"{live}\n\n{context.strip()}", f"{backend}\n\n{context.strip()}"
    return protocol.validate_client(protocol.session_start(
        instructions=live, tools=tools, backend_model=BACKEND_MODEL, backend_instructions=backend,
        voice=voice or VOICE, tool_choice="auto", parallel_tool_calls=False))


# ---- delegation settle/hold (gm msg_54cc2391). Shaw 10-14: "fast mode is awesome" -> FAST is the native default. ----
@dataclass(frozen=True)
class Settle:
    quiet_s: float = 0.8
    speak_or_s: float = 2.0
    hold_max_s: float = 8.0


SETTLE = Settle()
SETTLE_FAST = Settle(quiet_s=0.0, speak_or_s=0.0, hold_max_s=0.0)
