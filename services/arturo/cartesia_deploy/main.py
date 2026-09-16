#!/usr/bin/env python3
"""Cartesia Line Arturo Voice Agent — deployed with full tool execution & live surface context."""

import os
import json
import logging
import urllib.request
import urllib.error
from typing import Optional

from line.voice_agent_app import VoiceAgentApp
from line.llm_agent import LlmAgent, LlmConfig, end_call
from line.llm_agent.tools import ToolEnv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("arturo-cartesia")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BEARER_TOKEN = os.environ.get("CUSTOM_LLM_BEARER", "")
VPS_URL = os.environ.get("VPS_ARTURO_URL", "")

def _get_base_url() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.05)
    try:
        s.connect(("127.0.0.1", 5071))
        s.close()
        return "http://127.0.0.1:5071"
    except Exception:
        return VPS_URL

BASE_VPS_URL = _get_base_url()

def _call_vps_tool(name: str, args: dict) -> str:
    """Execute a tool via the Arturo tool runner on the VPS."""
    url = f"{BASE_VPS_URL}/tools/execute"
    payload = json.dumps({"name": name, "args": args}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BEARER_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return str(data.get("result", ""))
    except Exception as e:
        log.warning(f"Failed tool execution via {BASE_VPS_URL}: {e}")
    return f"Failed to execute tool {name}: Could not reach OrchestraOS backend."


def _fetch_vps_context() -> str:
    """Fetch live system prompt context from VPS."""
    url = f"{BASE_VPS_URL}/context"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {BEARER_TOKEN}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return str(data.get("context", ""))
    except Exception as e:
        log.warning(f"Failed to fetch context via {BASE_VPS_URL}: {e}")
    return ""


# Tool functions for Cartesia Line

async def read_screen_context(ctx: ToolEnv) -> str:
    """See what the operator is looking at in the OrchestraOS app RIGHT NOW and pull that screen's live data. Call when the operator asks 'what am I looking at', 'what's on my screen', or refers to an on-screen element."""
    return _call_vps_tool("read_screen_context", {})


async def knowledge(ctx: ToolEnv, query: str, category: Optional[str] = None) -> str:
    """FAST instant lookup of people, projects, priorities, and fleet agents (<10ms). Use when the operator asks about a person, project, status, or what to focus on."""
    args = {"query": query}
    if category:
        args["category"] = category
    return _call_vps_tool("knowledge", args)


async def gm_command(ctx: ToolEnv, command: str) -> str:
    """Send a command or instruction to the General Manager (gemini-gm) or orchestrate agents across the fleet."""
    return _call_vps_tool("gm_command", {"command": command})


async def answer_menu(ctx: ToolEnv, session: str, option: str, confirm: bool = False, text: Optional[str] = None) -> str:
    """Answer a decision menu an agent is PARKED on. Stage with confirm=False, ask the operator for verbal go, then call with confirm=True."""
    args = {"session": session, "option": option, "confirm": confirm}
    if text:
        args["text"] = text
    return _call_vps_tool("answer_menu", args)


async def run_shell(ctx: ToolEnv, command: str) -> str:
    """Execute a bash shell command on VPS or Mac (Mac SSH host from config)."""
    return _call_vps_tool("run_shell", {"command": command})


async def agent_status(ctx: ToolEnv, session: Optional[str] = None) -> str:
    """Inspect active tmux sessions, processes, and agent health across the fleet."""
    args = {"session": session} if session else {}
    return _call_vps_tool("agent_status", args)


async def view_transcript(ctx: ToolEnv, session: str, count: int = 10) -> str:
    """Read recent conversation turns and logs for a specific agent session."""
    return _call_vps_tool("view_transcript", {"session": session, "count": count})


ALL_TOOLS = [
    end_call,
    read_screen_context,
    knowledge,
    gm_command,
    answer_menu,
    run_shell,
    agent_status,
    view_transcript,
]

ARTURO_BASE_PROMPT = """# Identity
You are Arturo, the operator's executive AI chief of staff and voice copilot across the Orchestra agent fleet. You speak clearly, concisely, and naturally with a calm, capable tone.

# Speaking Style
- Keep responses brief: 1 to 3 sentences per turn for natural spoken conversation.
- Avoid markdown, bullet points, asterisks, or unreadable code syntax in spoken audio.
- Speak numbers, technical terms, and acronyms conversationally.
- Never give long monologues; converse back and forth interactively.
- Be direct, confident, and practical.

# Core Abilities & Tools
- You have real tools to inspect the operator's screen (read_screen_context), lookup knowledge (knowledge), run shell commands (run_shell), query agent statuses (agent_status), read transcripts (view_transcript), and manage GM actions (gm_command).
- When the operator asks what is on his screen or asks about current work, call read_screen_context immediately.
- When the operator asks to check on an agent or transcript, run agent_status or view_transcript.
- Always execute the appropriate tool rather than stating you lack capability.
"""

async def get_agent(env, call_request):
    log.info(f"Incoming call request: agent={call_request.agent.id if call_request.agent else 'default'}")
    live_ctx = _fetch_vps_context()
    if live_ctx:
        system_prompt = f"{ARTURO_BASE_PROMPT}\n\n# Live Fleet & Surface Context\n{live_ctx}"
    else:
        system_prompt = ARTURO_BASE_PROMPT

    return LlmAgent(
        model="gemini/gemini-2.0-flash",
        api_key=GEMINI_API_KEY,
        tools=ALL_TOOLS,
        config=LlmConfig(
            system_prompt=system_prompt,
            introduction="Hey the operator, Arturo here. What are we working on?",
            temperature=0.7,
        ),
    )

app = VoiceAgentApp(get_agent=get_agent)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
