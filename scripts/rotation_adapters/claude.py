"""Claude adapter, briefing mode: a fresh `claude -p` (stream-json) with the seat's own command line, a session id we
choose (--session-id) so the packet file can be named before the session is bound, and no warm turn.

The headless CLI emits its `system/init` (model, tools, MCP status, skills...) only after its first input, so a fresh
session has nothing to compare before promotion. `probe()` runs the identical command line once more with
`--no-session-persistence` and one tiny neutral input, in the warm-up's fenced environment, captures that init, and is
killed: its turn is never part of the seat's context and nothing of it is saved. The real session's own init is
compared again when its first input arrives.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import uuid

from .base import Adapter, SessionHandle

#: How long a fresh process may take for its MCP servers to connect before its first input (remote connectors take
#: tens of seconds after start; a first input before then runs without them). The CLI is asked for the status meanwhile
#: (control request mcp_status) and the wait ends as soon as the servers that matter are connected.
MCP_SETTLE_S = 30.0
MCP_SETTLE_MAX_S = 60.0
MCP_POLL_S = 2.0
#: Tools that exist only through MCP servers: compared through their server's status, not by name.
MCP_TOOLS = frozenset({"ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResource"})


def settle_seconds(env=os.environ) -> float:
    try:
        v = float(env.get("HEADLESS_ROTATE_MCP_SETTLE_S") or MCP_SETTLE_S)
    except ValueError:
        v = MCP_SETTLE_S
    return max(0.0, min(MCP_SETTLE_MAX_S, v))


def mcp_statuses(resp: dict) -> dict | None:
    """{name: status} from a control response to mcp_status, or None when it carries none."""
    r = (resp or {}).get("response") if isinstance((resp or {}).get("response"), dict) else resp
    servers = (r or {}).get("mcpServers") or (r or {}).get("mcp_servers")
    if not isinstance(servers, list):
        return None
    return {str(x.get("name")): str(x.get("status")) for x in servers if isinstance(x, dict)}


#: The probe's one input: neutral, cheap, asks for nothing.
PROBE_INPUT = "Reply with the single word OK."
PROBE_TIMEOUT_S = 120.0
LINE_LIMIT = 64 * 1024 * 1024


def fresh_argv(argv: list) -> list:
    """The seat's command line without --resume (and without a session id): a new conversation, same flags."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in ("--resume", "--session-id"):
            skip = True
            continue
        out.append(a)
    return out


def fenced_env(env: dict, seat: str) -> dict:
    """The probe's environment: the seat's own, minus every way a hook could take it for the seat."""
    e = dict(env)
    for k in ("TMUX", "TMUX_PANE"):
        e.pop(k, None)
    e["AQD_TMUX_SESSION"] = f"headless-probe.{seat}"
    e["ORCH_SELF_ROTATE"] = "off"
    e["ORCHESTRA_HEADLESS_WARMUP"] = "1"
    return e


def normalise(init: dict | None) -> dict:
    """An init event as compared: order and per-run noise dropped. An MCP server compares by name and whether it is
    connected (a status word like needs-auth is the same capability as before, connected or not)."""
    if not isinstance(init, dict):
        return {}
    srt = lambda v: sorted(str(x if not isinstance(x, dict) else x.get("name")) for x in v) \
        if isinstance(v, list) else None
    mcp = init.get("mcp_servers")
    mcp = ({str(s.get("name")): ("connected" if s.get("status") == "connected" else "not-connected")
            for s in mcp if isinstance(s, dict)} if isinstance(mcp, list) else None)
    tools = srt([t for t in init.get("tools") or [] if not str(t).startswith("mcp__") and t not in MCP_TOOLS]) \
        if isinstance(init.get("tools"), list) else None
    return {"model": init.get("model"), "effort": init.get("effort"), "tools": tools,
            "permission_mode": init.get("permissionMode"), "skills": srt(init.get("skills")),
            "agents": srt(init.get("agents")), "commands": srt(init.get("slash_commands")),
            "cwd": init.get("cwd"), "mcp_servers": mcp, "output_style": init.get("output_style")}


class ClaudeAdapter(Adapter):
    runtime = "claude"
    seed_mode = "briefing"
    startup_slot = "append-system-prompt-file"

    def __init__(self, *, argv_for, env: dict, cwd: str, model_arg=None, session_id=None, log=None):
        self.argv_for = argv_for            # (session_id, model) -> the seat's own command line
        self.env = env                      # the CLI's environment for this seat (auto-compact off already decided)
        self.cwd = cwd
        self.model_arg = model_arg
        self.base_session = session_id
        self.log = log or (lambda *a, **k: None)
        self._usage = None

    def command_line(self, prompt_file=None) -> list:
        """The seat's own command line for a new conversation; with the context file in the startup slot, fixed for
        the conversation's life (snapshot on: a later resume keeps it whatever it is given)."""
        argv = fresh_argv(self.argv_for(self.base_session, self.model_arg))
        if prompt_file and self.startup_slot:
            argv += ["--append-system-prompt-file", str(prompt_file), "--system-prompt-snapshot", "on"]
        return argv

    async def _spawn(self, argv, env):
        return await asyncio.create_subprocess_exec(
            *argv, cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=LINE_LIMIT)

    async def start_session(self, seat: str, seed: dict) -> SessionHandle:
        sid = str((seed or {}).get("session_id") or uuid.uuid4())
        argv = self.command_line((seed or {}).get("prompt_file")) + ["--session-id", sid]
        proc = await self._spawn(argv, self.env)
        return SessionHandle(runtime=self.runtime, session_id=sid, proc=proc, argv=argv, started_at=time.time())

    async def probe(self, seat: str, timeout: float = PROBE_TIMEOUT_S, settle_s: float = 0.0, need=()) -> dict:
        """The identical command line once more, unsaved, with one neutral input: its init and what it cost (for a
        runtime with no startup slot; Claude uses fork()). {"init", "usage", "error", "argv", "pid", "reply"}"""
        return await self._unsaved_turn(seat, self.command_line(), PROBE_INPUT, timeout, settle_s, need)

    async def fork(self, seat: str, prompt_file, question: str, timeout: float = PROBE_TIMEOUT_S,
                   settle_s: float = 0.0, need=()) -> dict:
        """A copy of the new conversation, the IDENTICAL command line and startup context, unsaved and fenced: it
        answers `question` (the continuity check) and shows its init, then is killed. The real conversation started
        afterwards never sees the check."""
        return await self._unsaved_turn(seat, self.command_line(prompt_file), question, timeout, settle_s, need)

    async def _unsaved_turn(self, seat, base_argv, text, timeout, settle_s=0.0, need=()):
        argv = list(base_argv) + ["--no-session-persistence"]
        proc = await self._spawn(argv, fenced_env(self.env, seat))
        out = {"init": None, "usage": None, "error": None, "argv": argv, "pid": proc.pid, "reply": ""}
        texts = []

        async def deny(ev):
            proc.stdin.write((json.dumps({"type": "control_response", "response": {
                "subtype": "success", "request_id": ev["request_id"],
                "response": {"behavior": "deny", "message": "Not available."}}}) + "\n").encode())
            await proc.stdin.drain()

        async def run():
            out["settle"] = await settle(proc, need, settle_s, on_request=deny)
            proc.stdin.write((json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                          "parent_tool_use_id": None, "session_id": ""}) + "\n").encode())
            await proc.stdin.drain()
            while True:
                line = await proc.stdout.readline()
                if not line:
                    out["error"] = out["error"] or "the probe ended before its turn did"
                    return
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                typ = ev.get("type")
                if typ == "system" and ev.get("subtype") == "init":
                    out["init"] = ev
                elif typ == "assistant" and ev.get("parent_tool_use_id") is None:
                    for b in (ev.get("message") or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                            texts.append(b["text"])
                elif typ == "control_request" and ev.get("request_id"):
                    await deny(ev)
                elif typ == "result":
                    out["usage"] = {k: ev.get(k) for k in ("usage", "total_cost_usd", "duration_ms", "modelUsage")}
                    if ev.get("is_error"):
                        out["error"] = f"the probe's turn ended in error ({ev.get('subtype')})"
                    return

        async def drain_err():
            while await proc.stderr.readline():
                pass
        errs = asyncio.ensure_future(drain_err())
        try:
            await asyncio.wait_for(run(), timeout=timeout + settle_s)
        except asyncio.TimeoutError:
            out["error"] = f"the probe did not finish within {int(timeout)} s"
        except (BrokenPipeError, ConnectionResetError) as e:
            out["error"] = f"the probe's input broke: {e}"
        finally:
            await self._kill(proc)
            errs.cancel()
        out["reply"] = "\n\n".join(texts)
        return out

    def capabilities(self, handle=None, init: dict | None = None) -> dict:
        """The normalised init (pass the init event; the session's own arrives with its first input)."""
        return normalise(init)

    def set_usage(self, used, window):
        self._usage = {"used": used, "window": window} if used is not None and window else None

    def usage(self) -> dict | None:
        return self._usage

    async def retire(self, handle: SessionHandle) -> int | None:
        return await self._kill(handle.proc)

    @staticmethod
    async def _kill(proc) -> int | None:
        if proc is None:
            return None
        if proc.returncode is not None:
            return proc.returncode
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                return await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                continue
        return proc.returncode


async def settle(proc, need, settle_s: float, on_request=None, poll_s: float = MCP_POLL_S) -> dict:
    """Before a fresh process's first input: wait until the MCP servers in `need` report connected (asked with the
    control request mcp_status every poll_s), or settle_s has passed. Reads the process's stdout meanwhile (nothing
    else is reading it yet). {"waited_s", "by": "none"|"status"|"time", "statuses"}"""
    t0 = time.monotonic()
    need = set(need or ())
    if not need or settle_s <= 0:
        return {"waited_s": 0.0, "by": "none", "statuses": None}
    deadline = t0 + settle_s
    statuses, asking, supported = None, None, True
    while time.monotonic() < deadline:
        if supported and asking is None:
            asking = f"settle-{uuid.uuid4().hex[:10]}"
            proc.stdin.write((json.dumps({"type": "control_request", "request_id": asking,
                                          "request": {"subtype": "mcp_status"}}) + "\n").encode())
            await proc.stdin.drain()
            asked_at = time.monotonic()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.05, min(poll_s, deadline - time.monotonic())))
        except asyncio.TimeoutError:
            line = None
        if line == b"":
            break                                          # the process ended: the caller sees it
        if line:
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {}
            if ev.get("type") == "control_request" and ev.get("request_id") and on_request is not None:
                await on_request(ev)
            elif ev.get("type") == "control_response":
                resp = ev.get("response") or {}
                if resp.get("request_id") == asking:
                    got = mcp_statuses(resp)
                    if resp.get("subtype") != "success" or got is None:
                        supported = False                  # this CLI does not say: wait out the time
                    else:
                        statuses = got
                        if all(statuses.get(n) == "connected" for n in need):
                            return {"waited_s": round(time.monotonic() - t0, 2), "by": "status", "statuses": statuses}
                    asking = None
        if asking is not None and time.monotonic() - asked_at >= poll_s:
            asking = None                                  # no answer: ask again
    return {"waited_s": round(time.monotonic() - t0, 2), "by": "time", "statuses": statuses}
