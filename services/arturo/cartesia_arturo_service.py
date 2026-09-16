#!/usr/bin/env python3
"""cartesia_arturo_service.py — Arturo Voice Agent server on Cartesia Line with full tool execution & screen awareness.

Exposes an end-to-end conversational agent on port 5072 connected to Cartesia Sonic TTS/STT,
Gemini 2.5 Flash LLM, and the OrchestraOS Arturo tool suite (knowledge, screen context, shell, GM, transcripts).
"""

import os
import sys
import json
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("cartesia-arturo")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load secrets
secrets = {}
secrets_file = REPO_ROOT / ".env.secrets"
if secrets_file.exists():
    for line in secrets_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            secrets[k.strip()] = v.strip()

GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
CARTESIA_API_KEY = secrets.get("CARTESIA_API_KEY", os.environ.get("CARTESIA_API_KEY", ""))
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["CARTESIA_API_KEY"] = CARTESIA_API_KEY

from line.voice_agent_app import VoiceAgentApp
from line.llm_agent import LlmAgent, LlmConfig, end_call
from line.llm_agent.tools import ToolEnv
import importlib.util

# Load arturo-proxy module dynamically (handles hyphen in filename)
_arturo_proxy_path = Path(__file__).resolve().parent / "arturo-proxy.py"
_spec = importlib.util.spec_from_file_location("arturo_proxy_module", str(_arturo_proxy_path))
_arturo_mod = importlib.util.module_from_spec(_spec)
sys.modules["arturo_proxy_module"] = _arturo_mod
_spec.loader.exec_module(_arturo_mod)

execute_tool = _arturo_mod.execute_tool
build_context = _arturo_mod.build_context

# Tool definitions wrapping the Arturo execution engine

async def read_screen_context(ctx: ToolEnv) -> str:
    """See what the operator is looking at in the OrchestraOS app RIGHT NOW and pull that screen's live data. Call when the operator asks 'what am I looking at', 'what's on my screen', or refers to an on-screen element."""
    try:
        res = execute_tool("read_screen_context", {})
        return str(res)
    except Exception as e:
        return f"Error reading screen context: {e}"


async def knowledge(ctx: ToolEnv, query: str, category: str = None) -> str:
    """FAST instant lookup of people, projects, priorities, and fleet agents (<10ms). Use when the operator asks about a person, project, status, or what to focus on."""
    try:
        args = {"query": query}
        if category:
            args["category"] = category
        res = execute_tool("knowledge", args)
        return str(res)
    except Exception as e:
        return f"Error looking up knowledge: {e}"


async def gm_command(ctx: ToolEnv, command: str) -> str:
    """Send a command or instruction to the General Manager (gemini-gm) or orchestrate agents across the fleet."""
    try:
        res = execute_tool("gm_command", {"command": command})
        return str(res)
    except Exception as e:
        return f"Error executing GM command: {e}"


async def answer_menu(ctx: ToolEnv, session: str, option: str, confirm: bool = False, text: str = None) -> str:
    """Answer a decision menu an agent is PARKED on. Stage with confirm=False, ask the operator for verbal go, then call with confirm=True."""
    try:
        args = {"session": session, "option": option, "confirm": confirm}
        if text:
            args["text"] = text
        res = execute_tool("answer_menu", args)
        return str(res)
    except Exception as e:
        return f"Error answering menu: {e}"


async def run_shell(ctx: ToolEnv, command: str) -> str:
    """Execute a bash shell command on VPS or Mac (Mac SSH host from config)."""
    try:
        res = execute_tool("run_shell", {"command": command})
        return str(res)
    except Exception as e:
        return f"Error running shell command: {e}"


async def agent_status(ctx: ToolEnv, session: str = None) -> str:
    """Inspect active tmux sessions, processes, and agent health across the fleet."""
    try:
        args = {"session": session} if session else {}
        res = execute_tool("agent_status", args)
        return str(res)
    except Exception as e:
        return f"Error getting agent status: {e}"


async def view_transcript(ctx: ToolEnv, session: str, count: int = 10) -> str:
    """Read recent conversation turns and logs for a specific agent session."""
    try:
        res = execute_tool("view_transcript", {"session": session, "count": count})
        return str(res)
    except Exception as e:
        return f"Error retrieving transcript: {e}"


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

async def get_agent(env, call_request):
    log.info(f"Incoming call request: agent={call_request.agent.id if call_request.agent else 'default'}")
    
    # Enrich system prompt with dynamic surface and fleet context
    try:
        system_prompt = build_context("voice")
    except Exception as e:
        log.warning(f"Could not build context: {e}")
        system_prompt = "You are Arturo, AI operations commander and voice copilot for the operator."

    return LlmAgent(
        model="gemini/gemini-2.5-flash",
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
    port = int(os.environ.get("PORT", 5072))
    log.info(f"Starting Arturo Cartesia Voice Agent on port {port}...")
    app.run(host="0.0.0.0", port=port)
