"""P1b Voice Layer Dispatcher — stateless deep brain for Arturo (DEC-1788772980798256, SPEC v2).

Invariant: a live voice call may wait on state reads and on ephemeral spawns — NEVER on a
standing agent. Behind ARTURO_DISPATCHER=1 (default OFF = proxy byte-identical):

  D1 deep_query — a transient, sessionless CLI analyst subprocess (never a tmux pane, never a
     registry row). BoundedSemaphore(1); hard wall 12.0s; start_new_session=True + killpg
     SIGKILL on timeout (no orphaned grandchildren); /proc/meminfo MemAvailable<500MB precheck
     aborts the spawn. Every failure degrades to the async_task(gm_command) tier-3 path.
  D2 read-only analyst — prompt-only invocation (no tools, no --dangerously-skip-permissions,
     hydration content is read HERE from allowlisted roots and baked into the prompt); raw
     output capped at 3000 chars.
  D3 ask_gm — a thin alias over the hardened async_task(gm_command) pipeline in the proxy
     (inherits _TG_OUTBOX dedupe, verified tg-notify delivery, provenance tag + role-inversion
     guard). Under the flag the model-facing sync gm_command tool is retired from TOOLS; the
     internal execute_tool branch stays for async reuse.
  D4 first-person persona — spoken templates below never attribute to a third-person "GM".
  D5 :5071 only; ships build-and-hold, restarted in the same single the operator-word window as P1.
"""
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("arturo-dispatcher")

FLAG = "ARTURO_DISPATCHER"
DEEP_QUERY_WALL_S = 12.0
MEM_FLOOR_MB = 500
OUTPUT_CAP = 3000
HYDRATION_FILE_CAP = 1500
MEMINFO_PATH = "/proc/meminfo"
ANALYST_CMD_ENV = "ARTURO_DEEP_QUERY_CMD"     # shlex-split override; default below
DEFAULT_ANALYST_CMD = ["claude", "-p", "--model", "claude-haiku-4-5"]

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))

# D4 spoken templates — first person, no third-person GM attribution (test-pinned).
SPOKEN_DEEP_FALLBACK = ("Let me investigate and think through that. I'll text you on Telegram "
                        "as soon as I have the complete breakdown.")
SPOKEN_ASK_GM_ACK = ("On it. I'll text you on Telegram as soon as it's done.")

_DQ_SEM = threading.BoundedSemaphore(1)


def _reset_for_tests():
    global _DQ_SEM
    _DQ_SEM = threading.BoundedSemaphore(1)


def enabled():
    return os.environ.get(FLAG, "") == "1"


def mem_available_mb(path=None):
    """MemAvailable from /proc/meminfo in MB; None if unreadable (caller fails open on read)."""
    try:
        for line in open(path or MEMINFO_PATH):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


# ---- tool surface (D3 retire + new defs) ----

DEEP_QUERY_DEF = {
    "type": "function",
    "function": {
        "name": "deep_query",
        "description": ("Think deeply about a hard question using your own fast ephemeral reasoning "
                        "(hydrated with live fleet state + your semantic memory). Use for analysis "
                        "questions: 'what's blocking X', 'why did Y fail', 'should we do Z'. Answers "
                        "in a few seconds; if it needs longer you'll automatically text the operator the full "
                        "breakdown on Telegram. NEVER mention this plumbing aloud."),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "The specific analysis question, with any context the operator gave."},
            },
            "required": ["question"],
        },
    },
}

ASK_GM_DEF = {
    "type": "function",
    "function": {
        "name": "ask_gm",
        "description": ("Hand an EXECUTIVE ACTION to your own deeper self to execute in the background "
                        "(deploys, file writes, git operations, reprioritizations, multi-step tasks). "
                        "Returns instantly; the result is texted to the operator on Telegram with verified "
                        "delivery. Speak in the first person only ('On it, I'll text you')."),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {"type": "string",
                            "description": "The action to execute, specific and complete."},
                "summary": {"type": "string",
                            "description": "Short label for the Telegram notification."},
            },
            "required": ["request"],
        },
    },
}


def transform_tools(tools):
    """Pure: drop the model-facing sync gm_command def, append deep_query + ask_gm."""
    out = [t for t in tools if t.get("function", {}).get("name") != "gm_command"]
    out.append(DEEP_QUERY_DEF)
    out.append(ASK_GM_DEF)
    return out


# ---- D2 hydration (read-only, allowlisted, baked into the prompt) ----

def _read_capped(path, cap=HYDRATION_FILE_CAP):
    try:
        return Path(path).read_text()[:cap]
    except Exception:
        return ""


def hydrate(question):
    """Allowlisted context for the analyst prompt: semantic-memory pointers (when the P1
    embedder is warm — never load a model inside the deep_query wall), root-visibility,
    host telemetry. Read-only; content is baked into the prompt so the analyst needs no tools."""
    parts = []
    try:
        from services.arturo import semantic_recall as _sr
        if _sr.enabled() and _sr._model_ready.is_set():
            rp = _sr.recall_preamble(question, budget_ms=1000)
            if rp:
                parts.append(rp)
    except Exception:
        pass
    rv = _read_capped(ORCHESTRA_DIR / "state" / "root-bridge" / "root-visibility.json")
    if rv:
        parts.append("ROOT-VISIBILITY (kernel truth):\n" + rv)
    ht = _read_capped(ORCHESTRA_DIR / "state" / "host-telemetry.json")
    if ht:
        parts.append("HOST TELEMETRY:\n" + ht)
    return "\n\n".join(parts)


def _analyst_cmd():
    env_cmd = os.environ.get(ANALYST_CMD_ENV, "")
    return shlex.split(env_cmd) if env_cmd else list(DEFAULT_ANALYST_CMD)


def build_prompt(question, hydration=""):
    return (
        "You are a one-shot read-only fleet analyst for OrchestraOS. Answer the question "
        "concisely (voice context: <=6 sentences), grounded ONLY in the context below. "
        "If the context is insufficient, say what specifically is missing.\n\n"
        f"QUESTION: {question}\n\n{hydration}"
    )


# ---- D1 bounded subprocess ----

def run_analyst(question, cmd=None, wall_s=None, mem_floor_mb=None, hydration=None):
    """(ok, text). ok=False (text='') on: semaphore busy, memory floor, spawn error, timeout,
    non-zero exit, empty output — the caller degrades to the tier-3 async path. The child gets
    its own session (PGID); on timeout the WHOLE group is SIGKILLed (no leaked grandchildren)."""
    wall = DEEP_QUERY_WALL_S if wall_s is None else float(wall_s)
    floor = MEM_FLOOR_MB if mem_floor_mb is None else mem_floor_mb
    avail = mem_available_mb()
    if avail is not None and avail < floor:
        log.warning(f"deep_query: MemAvailable {avail}MB < floor {floor}MB — no spawn, degrading")
        return False, ""
    if not _DQ_SEM.acquire(blocking=False):
        log.info("deep_query: analyst already in flight (semaphore) — degrading")
        return False, ""
    try:
        prompt = build_prompt(question, hydration if hydration is not None else hydrate(question))
        try:
            p = subprocess.Popen(_analyst_cmd() if cmd is None else cmd,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True,
                                 start_new_session=True)
        except Exception as e:
            log.error(f"deep_query spawn failed: {e}")
            return False, ""
        try:
            out, err = p.communicate(input=prompt, timeout=wall)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception as ke:
                log.error(f"deep_query killpg failed: {ke}")
            p.wait()
            log.warning(f"deep_query: wall {wall}s exceeded — process group killed, degrading")
            return False, ""
        if p.returncode != 0 or not (out or "").strip():
            log.warning(f"deep_query: analyst rc={p.returncode} out={len(out or '')}ch — degrading")
            return False, ""
        return True, out.strip()[:OUTPUT_CAP]
    finally:
        _DQ_SEM.release()
