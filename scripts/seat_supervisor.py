#!/usr/bin/env python3
"""seat_supervisor.py: one warm headless Claude process per switched-on seat.

The supervisor owns ONE `claude -p` stream-json process resumed on the seat's session, with the
flags spawn gives the TUI (bypass, --model, --settings /tmp/agent-perms-<seat>.settings.json, the
same environment) plus the three the stream needs:
  --replay-user-messages        every message written to stdin is echoed back once the CLI takes
                                it: the "delivered" witness;
  --permission-prompt-tool stdio  AskUserQuestion and whatever bypass still asks arrive as
                                control requests on stdout, HELD here until someone answers;
  --include-partial-messages    live text deltas.
Manual permission mode is never used (every inbox read would become a question).

The process is kept alive between turns (a cold start costs ~6 s to first token, warm ~2 s) and
started again when it exits. Everything goes to an append-only event log,
<data dir>/state/headless/<seat>/events.jsonl: supervisor lifecycle, every stdout event, every
stdin write, stderr, and each message's state change.

Message states (one id = one message, never written twice to one process):
  accepted   taken by the supervisor while no process was running; written when one starts
  queued     written to stdin
  delivered  the CLI's replay echo of it was seen
  answered   the turn that took it ended (a result event)
  interrupted  delivered, but its turn did not finish: the process ended mid-turn (a crash, a
             kill, a stop) or an interrupt cut it. The words reached the agent; the cut turn is
             not resumed and the message is not resent, so this is an end state.
  unknown    the process ended after the write and before the echo: resent ONCE to the next
             process (at most MAX_ATTEMPTS writes in all, then failed)
  failed     gave up; the caller sees it in status
A send with an id the supervisor already knows writes nothing and returns the known state, so a
router retry or a gateway resend can never double a message.

Our own id per message (message_uuid, on by default): each stdin message carries a `uuid` made from
its message id (the same uuid on a resend), and the replay echo is matched by that uuid when the CLI
echoes it back; otherwise by its text, then by order. Each delivered record says which matched
(echo_match: uuid | text | order), and status counts them, which is how a test seat proves whether
the CLI echoes our uuid. Off for a seat: "message_uuid": false on its headless-seats.json entry
(`seat_supervisor.py message-uuid off <seat>`), or HEADLESS_MESSAGE_UUID=0; read at start.

A message whose turn was cut (interrupted) is also listed in interrupted.json beside the log, the
small file the API's chat reads to show that turn as interrupted in the agent's thread.

Dev (the seat's terminal): {"cmd":"dev"} closes the headless process (from idle only) and runs the
TUI, `claude --resume <session>`, as this supervisor's foreground child on the pane's terminal, with this
same environment (spawn's env and the pane's TMUX_PANE). While it runs, a `dev` marker beside the log tells
every reader (scripts/headless_switch.py) that the pane is a TUI again: mail and the dashboard type into it
and the detector reads its screen, as for any tmux seat. When the TUI exits ({"cmd":"chat"} types /exit
into it, or the person exits it in the terminal), the terminal comes back, the marker goes, and the headless
process starts again on the same session.

Held requests become cards (scripts/headless_cards.py; on by default, "cards": false on the seat's
headless-seats.json entry or HEADLESS_CARDS=0 turns it off): each one is filed once as a 4c card, and the
card's answer comes back as {"cmd":"resolve","request_id","card_id","outcome"}, which writes the control
response once (a second resolve of the same card is `already`, never a second write). A request that ends
any other way (answered here with `answer`, cancelled by the CLI, or the process ended) withdraws its card.

The local interface is a unix socket, <data dir>/state/headless/<seat>/sock (dir 0700, socket
0600): one JSON line in, one JSON line out. Chosen over a localhost port because the file mode is
the access check (only this user), nothing is reachable from the tailnet, and the path follows
from the seat name with no port to allocate. Commands:
  {"cmd":"send","id":..,"text":..,"allow_waiting":false}   {"cmd":"message","id":..}
  {"cmd":"interrupt"}   {"cmd":"pending"}   {"cmd":"answer","request_id":..,"response":{..}}
  {"cmd":"status"}      {"cmd":"stop","tui":false}          {"cmd":"restart"}
`status` also carries what agent-status reads instead of the pane: how long ago the last stdout
event came, how long the turn has run, the tools still in flight, the held requests in summary,
the model, and the context in use (the main chain's last usage over the window from the last
result's modelUsage; None until a result has named the window).

Where it runs: in the seat's own tmux pane, in place of the TUI (`pane-line` prints the command,
`takeover` types it into a pane that is back at its shell). The pane's process then carries
spawn's environment and the pane's TMUX_PANE, which the headless process and its hooks inherit,
and the pane runs no second `claude` on the session. Anything typed into the pane is logged and
ignored. `stop --tui` hands the pane back: the headless process is closed and the supervisor
execs `claude --resume <session>` (the TUI) in its own place, with the same environment.

CLI:
  seat_supervisor.py run <seat> [--cwd D] [--model M] [--session SID] [--claude-bin B]
  seat_supervisor.py switch on|off <seat>
  seat_supervisor.py pane-line <seat>          seat_supervisor.py takeover <seat>
  seat_supervisor.py send <seat> <text> [--id ID]
  seat_supervisor.py status|pending|interrupt|restart <seat>
  seat_supervisor.py answer <seat> <request_id> '<response json>'
  seat_supervisor.py stop <seat> [--tui]
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.append(_p)

import headless_switch as HS  # noqa: E402

#: Writes of one message (first + resends) before it is given up as failed.
MAX_ATTEMPTS = 3
#: A process that exits within this many seconds of starting counts as a fast failure.
FAST_EXIT_S = 10.0
#: Fast failures in a row before the supervisor stops restarting (state "failed").
MAX_FAST_EXITS = 5
BACKOFF_MAX_S = 60.0
#: How long `stop` waits for the CLI to exit after stdin closes, then after SIGTERM.
STOP_GRACE_S = 10.0
STDERR_KEEP = 2000
#: Cut turns kept in interrupted.json for the chat.
INTERRUPTED_KEEP = 50
#: Namespace for our per-message uuids: uuid5(namespace, message id), so a resend carries the same one.
MESSAGE_UUID_NS = uuid.UUID("0a0a0a0a-0000-4000-8000-000000000001")
#: The stdout reader's line limit: a tool result can be one very long line.
LINE_LIMIT = 64 * 1024 * 1024

# Kept equal to spawn-agent.sh's INSTALLER_SESSION_ENV (orchestra_cli/settings.py is the source).
try:
    from orchestra_cli.settings import INSTALLER_SESSION_ENV  # noqa: E402
except Exception:  # noqa: BLE001 — a stripped checkout still scrubs the known names
    INSTALLER_SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                             "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED",
                             "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                             "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID",
                             "CLAUDE_EFFORT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
                             "CODEX_THREAD_ID", "GEMINI_CLI")


def _now_iso() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


# --- what to run ----------------------------------------------------------------------------------

def claude_bin() -> str:
    pinned = os.environ.get("ORCHESTRA_CLAUDE_BIN", "")
    if pinned and os.access(pinned, os.X_OK):
        return pinned
    import shutil
    return shutil.which("claude") or "claude"


def settings_file(seat: str, cwd: str, orch_dir) -> str | None:
    """The seat's permission settings, generated the way spawn does (same path, same rules).
    The existing file is used when the generator cannot run."""
    path = f"/tmp/agent-perms-{seat}.settings.json"
    try:
        out = subprocess.run([sys.executable, os.path.join(_HERE, "spawn_permission_rules.py"),
                              seat, cwd, "--orchestra-dir", str(orch_dir)],
                             capture_output=True, text=True, timeout=20)
        made = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and made and os.path.isfile(made[-1]):
            return made[-1]
    except (OSError, subprocess.SubprocessError):
        pass
    return path if os.path.isfile(path) else None


def headless_argv(bin_: str, *, session_id: str, model: str | None, settings: str | None) -> list:
    """The headless command line. Bypass stays; manual permission mode is never used."""
    argv = [bin_, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--include-partial-messages", "--verbose", "--replay-user-messages",
            "--permission-prompt-tool", "stdio", "--dangerously-skip-permissions"]
    if model:
        argv += ["--model", model]
    if settings:
        argv += ["--settings", settings]
    argv += ["--resume", session_id]
    assert "--permission-mode" not in argv
    return argv


def tui_argv(bin_: str, *, session_id: str, model: str | None, settings: str | None) -> list:
    """The pane's interactive command line, as spawn builds it."""
    argv = [bin_, "--dangerously-skip-permissions"]
    if model:
        argv += ["--model", model]
    argv += ["--resume", session_id]
    if settings:
        argv += ["--settings", settings]
    return argv


def synthetic_pane_id(seat: str) -> str:
    """The pane id a seat's hooks key on when its supervisor runs with no tmux pane: the hooks
    write state/agent-events/panes/<id without %>.json, and agent-status reads that file back
    through the supervisor's status (pane_id), never through tmux."""
    return "%hl-" + "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in seat)


def seat_env(base: dict, orch_dir, pane_id: str | None, session: str | None = None) -> dict:
    """The environment spawn installs in a seat's pane (spawn-agent.sh, "Install env into the
    pane"): the installer session's variables scrubbed; data dir, checkout and (when set) config
    and Claude config dir; the normal-screen switch; plus the seat pane's TMUX_PANE, which the
    hooks and approval.py's authorship check read, and the seat's session for the Stop hook's
    mail digest (hooks/agent-queue-drain.py reads AQD_TMUX_SESSION before asking tmux)."""
    env = dict(base)
    for k in INSTALLER_SESSION_ENV:
        env.pop(k, None)
    env["ORCHESTRA_DIR"] = env["ORCH_DIR"] = str(orch_dir)
    env.setdefault("ORCHESTRA_ROOT", _ROOT)
    env["CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN"] = "1"
    if pane_id:
        env["TMUX_PANE"] = pane_id
    if session:
        env["AQD_TMUX_SESSION"] = session
    return env


def registry_row(seat: str, orch_dir) -> dict:
    """The seat's registry row, identity store first (as red_alert reads it), registry.json next."""
    try:
        from scripts.identity_store import resolver
        row = resolver.registry_agent_db(str(orch_dir), seat, alarm=lambda m: None)
        if isinstance(row, dict) and row:
            return row
    except Exception:  # noqa: BLE001 — the flat file is a valid fallback
        pass
    try:
        doc = json.loads((Path(orch_dir) / "registry.json").read_text())
        row = doc.get("agents", doc).get(seat)
        return row if isinstance(row, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _tmux(*args, timeout=5):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)


def pane_id_of(session: str, tmux=_tmux) -> str | None:
    try:
        r = tmux("display-message", "-t", f"={session}:", "-p", "#{pane_id}")
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out.startswith("%") else None


def held_summary(h: dict) -> dict:
    """A held control request in a few words: the question and its options for AskUserQuestion,
    else the tool and the one input field that says what it would do."""
    req = h.get("request") or {}
    tool = req.get("tool_name") or req.get("subtype") or ""
    inp = req.get("input") if isinstance(req.get("input"), dict) else {}
    out = {"request_id": h.get("request_id"), "tool": tool, "seen_at": h.get("seen_at"),
           **({"card_id": h["card_id"]} if h.get("card_id") else {})}
    qs = inp.get("questions") if tool == "AskUserQuestion" else None
    if isinstance(qs, list) and qs and isinstance(qs[0], dict):
        q = qs[0]
        out.update(kind="question", question=str(q.get("question") or "")[:300],
                   header=q.get("header"), parts=len(qs),
                   options=[str(o.get("label")) for o in q.get("options") or [] if isinstance(o, dict)][:10])
    else:
        what = next((str(inp[k]) for k in ("command", "file_path", "url", "description", "path")
                     if inp.get(k)), "")
        out.update(kind="permission", question=f"Allow {tool}?" + (f" {what[:200]}" if what else ""))
    return out


# --- the supervisor -------------------------------------------------------------------------------

class Supervisor:
    def __init__(self, seat: str, *, argv_for, env: dict, cwd: str, home: Path,
                 session_id: str, tui_argv_for=None, interactive: bool = False,
                 backoff_base: float = 1.0, message_uuid: bool = True,
                 session_name: str | None = None, cards=None, model_arg: str | None = None):
        self.seat = seat
        self.argv_for = argv_for            # session_id -> argv
        self.tui_argv_for = tui_argv_for
        self.env = env
        self.cwd = cwd
        self.home = Path(home)
        self.session_id = session_id
        self.interactive = interactive
        self.backoff_base = backoff_base
        self.t0 = time.monotonic()
        self.proc = None
        self.gen = 0
        self.restarts = 0
        self.fast_exits = 0
        self.started_at = None
        self.state = "starting"
        self.turn_open = False
        self.last_event = None
        self.last_event_at = None           # monotonic time of the last stdout event
        self.turn_started_at = None
        self.tools_open: dict = {}          # tool_use id -> tool name, main chain, this turn
        self.last_tool = ""
        self.model = None
        self.ctx_tokens = None
        self.ctx_window = None
        self.ctx_wall = None
        self._interrupt_pending = False
        # A bool, or a callable read at each process start (so a CLI restart picks up a change).
        self._message_uuid_src = message_uuid
        self.message_uuid = bool(message_uuid() if callable(message_uuid) else message_uuid)
        self.echo_matches = {"uuid": 0, "text": 0, "order": 0}
        # Held requests as cards: an object with file(seat, session, request_id, request) -> card id,
        # withdraw(card_id), withdraw_seat(seat, session); None = no cards.
        self.session_name = session_name or seat
        self.cards = cards
        self.resolved: dict = {}            # request_id -> card_id it was answered from
        self.model_arg = model_arg          # the --model each (re)start passes; set_model changes it
        self.dev = False                    # the TUI owns the pane's terminal
        self.tui = None
        self._dev_exit = asyncio.Event()    # set when the TUI is gone (the headless process may start)
        self._input_transport = None
        self._ctl_waiters: dict = {}        # our control request id -> future of its response
        self.messages: dict = {}            # id -> record, in send order
        self.held: dict = {}                # request_id -> control request
        self.stopping = False
        self.to_tui = False
        self.failed = False
        self._restart_now = asyncio.Event()
        self._restart_requested = False
        self._write_lock = asyncio.Lock()
        self._log_fh = None
        self._server = None
        self._last_shown = None

    # --- log ---
    def log(self, kind: str, sync: bool = False, **fields):
        rec = {"t": int((time.monotonic() - self.t0) * 1000), "wall": _now_iso(), "gen": self.gen,
               "kind": kind, **fields}
        self._log_fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._log_fh.flush()
        if sync:
            os.fsync(self._log_fh.fileno())

    def _msg_state(self, m: dict, state: str, **extra):
        m["state"] = state
        m.update(extra)
        self.log("msg", sync=True, id=m["id"], state=state, attempts=m.get("attempts", 0),
                 **({"text": m["text"]} if state == "accepted" else {}), **extra)
        if state == "interrupted":
            self._note_interrupted(m, extra.get("reason") or extra.get("result") or "")

    def _note_interrupted(self, m: dict, reason: str):
        """Keep the last INTERRUPTED_KEEP cut turns in interrupted.json (atomic), for the chat."""
        p = self.home / "interrupted.json"
        try:
            doc = json.loads(p.read_text())
            items = doc.get("items") if isinstance(doc, dict) else None
            items = items if isinstance(items, list) else []
        except (OSError, ValueError):
            items = []
        items = [x for x in items if isinstance(x, dict) and x.get("id") != m["id"]]
        items.append({"id": m["id"], "text": (m.get("text") or "")[:2000], "at": _now_iso(),
                      "reason": str(reason)[:200], "session_id": self.session_id})
        tmp = p.with_name(".interrupted.json.tmp")
        try:
            tmp.write_text(json.dumps({"seat": self.seat, "items": items[-INTERRUPTED_KEEP:]}, indent=1) + "\n")
            os.replace(tmp, p)
        except OSError as e:
            self.log("err", text=f"interrupted.json not written: {e}")

    def load_messages(self):
        """Rebuild message states from the log (a supervisor restart). A message whose last
        state was queued (written, no echo) belonged to a process that is gone: unknown."""
        path = self.home / "events.jsonl"
        if not path.exists():
            return
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if ('"usage"' in line or '"modelUsage"' in line) and '"kind":"out"' in line:
                    try:
                        rec = json.loads(line)
                        self._note_usage(rec.get("ev") or {}, wall=rec.get("wall"))
                    except ValueError:
                        pass
                    continue
                if '"msg"' not in line:          # cheap skip of the (many) event lines
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "msg" or not r.get("id"):
                    continue
                m = self.messages.setdefault(r["id"], {"id": r["id"], "text": None})
                if r.get("text") is not None:
                    m["text"] = r["text"]
                m["state"] = r.get("state")
                m["attempts"] = r.get("attempts", 0)
        for m in list(self.messages.values()):
            if m.get("state") == "queued":
                m["state"] = "unknown"
            elif m.get("state") == "delivered":
                self._msg_state(m, "interrupted", reason="supervisor restarted mid-turn")

    # --- pane display ---
    def show(self):
        line = f"[headless {self.seat}] {self.state}"
        if self.proc is not None and self.proc.returncode is None:
            line += f" · pid {self.proc.pid}"
        line += f" · session {str(self.session_id)[:8]} · {time.strftime('%H:%M:%S')}"
        if self.interactive and line != self._last_shown and not self.dev:
            self._last_shown = line
            print(line, flush=True)

    def _set_state(self):
        if self.dev:
            st = "dev"
        elif self.failed:
            st = "failed"
        elif self.stopping:
            st = "stopping"
        elif self.proc is None or self.proc.returncode is not None:
            st = "restarting"
        elif self.held:
            st = "waiting"
        elif self.turn_open:
            st = "working"
        else:
            st = "idle"
        if st != self.state:
            self.state = st
            self.log("sup", what="state", state=st)
            self.show()

    # --- process ---
    async def start_proc(self):
        self.gen += 1
        if callable(self._message_uuid_src):
            try:
                self.message_uuid = bool(self._message_uuid_src())
            except Exception:  # noqa: BLE001 — an unreadable setting keeps the last one
                pass
        argv = self.argv_for(self.session_id, self.model_arg)
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=self.cwd, env=self.env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=LINE_LIMIT)
        self.started_at = time.monotonic()
        self.turn_open = False
        self.log("sup", sync=True, what="start", pid=self.proc.pid, argv=argv, cwd=self.cwd,
                 message_uuid=self.message_uuid)
        self._set_state()
        # Words taken while no process ran, then the ones a dead process may never have read.
        for m in list(self.messages.values()):
            if m.get("state") == "accepted":
                await self._write_message(m)
        for m in list(self.messages.values()):
            if m.get("state") == "unknown":
                if m.get("attempts", 0) >= MAX_ATTEMPTS or m.get("text") is None:
                    self._msg_state(m, "failed", reason="no echo after the allowed writes")
                else:
                    self.log("sup", what="resend", id=m["id"])
                    await self._write_message(m)

    async def _write(self, obj: dict):
        """True written; None nothing written (no process); False the write broke part way,
        so the CLI may or may not have read it."""
        async with self._write_lock:
            p = self.proc
            if p is None or p.returncode is not None or p.stdin is None or p.stdin.is_closing():
                return None
            try:
                p.stdin.write((json.dumps(obj) + "\n").encode())
                await p.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                self.log("in-failed", obj=obj)
                return False
            self.log("in", obj=obj)
            return True

    async def _write_message(self, m: dict) -> bool:
        obj = {"type": "user", "message": {"role": "user", "content": m["text"]},
               "parent_tool_use_id": None, "session_id": self.session_id}
        if self.message_uuid:
            m["uuid"] = m.get("uuid") or str(uuid.uuid5(MESSAGE_UUID_NS, m["id"]))
            obj["uuid"] = m["uuid"]
        wrote = await self._write(obj)
        if wrote is None:
            if m.get("state") != "accepted":
                self._msg_state(m, "accepted")   # nothing written: waits for the next process
            return False
        m["attempts"] = m.get("attempts", 0) + 1
        if wrote:
            m["gen"] = self.gen
            self._turn_begins()
            self._msg_state(m, "queued")
            self._set_state()
            return True
        self._msg_state(m, "unknown")
        return False

    async def read_stdout(self, proc):
        while True:
            try:
                line = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError) as e:
                self.log("err", text=f"stdout line over the limit: {e}")
                continue
            if not line:
                return
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                self.log("raw", text=raw[:STDERR_KEEP])
                continue
            self.log("out", ev=ev)
            self.on_event(ev)

    async def read_stderr(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            self.log("err", text=line.decode("utf-8", errors="replace")[:STDERR_KEEP])

    def on_event(self, ev: dict):
        typ = ev.get("type")
        self.last_event_at = time.monotonic()
        self.last_event = {"type": typ, "subtype": ev.get("subtype")
                           or (ev.get("event") or {}).get("type")
                           or (ev.get("request") or {}).get("subtype"), "wall": _now_iso()}
        if typ == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
            self.model = ev.get("model") or self.model
            if ev["session_id"] != self.session_id:
                self.log("sup", what="session", old=self.session_id, new=ev["session_id"])
                self.session_id = ev["session_id"]
                self._save_seat()
        elif typ == "user" and ev.get("isReplay"):
            self._on_replay(ev)
        elif typ in ("stream_event", "assistant"):
            self._turn_begins()
            if typ == "assistant" and ev.get("parent_tool_use_id") is None:
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                        self.tools_open[b["id"]] = b.get("name") or ""
                        self.last_tool = b.get("name") or ""
                self._note_usage(ev)
        elif typ == "user" and ev.get("parent_tool_use_id") is None:
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.tools_open.pop(b.get("tool_use_id"), None)
        elif typ == "result":
            self.turn_open = False
            self.turn_started_at = None
            self.tools_open.clear()
            self._note_usage(ev)
            end = "interrupted" if self._interrupt_pending else "answered"
            self._interrupt_pending = False
            for m in self.messages.values():
                if m.get("state") == "delivered":
                    self._msg_state(m, end, result=ev.get("subtype"),
                                    is_error=bool(ev.get("is_error")))
        elif typ == "control_request":
            rid = ev.get("request_id")
            if rid:
                # HELD: nothing here answers a question or a permission on the agent's behalf.
                self.held[rid] = {"request_id": rid, "request": ev.get("request"),
                                  "seen_at": _now_iso()}
                if self.cards is not None and (ev.get("request") or {}).get("subtype") == "can_use_tool":
                    asyncio.ensure_future(self._file_card(rid))
        elif typ == "control_response":
            resp = ev.get("response") or {}
            fut = self._ctl_waiters.pop(resp.get("request_id"), None)
            if fut is not None and not fut.done():
                fut.set_result(resp)
        elif typ == "control_cancel_request":
            if self._release_held(ev.get("request_id")):
                self.log("sup", what="request-cancelled", request_id=ev.get("request_id"))
        self._set_state()

    async def _file_card(self, rid):
        """One card for a held request (off the loop: a ledger write and a push)."""
        h = self.held.get(rid)
        if h is None:
            return
        loop = asyncio.get_event_loop()
        try:
            card = await loop.run_in_executor(None, self.cards.file, self.seat, self.session_name, rid,
                                              h.get("request") or {})
        except Exception as e:  # noqa: BLE001 — no card: the request stays held and visible in status
            self.log("err", text=f"card not filed for {rid}: {e!r}")
            return
        if rid in self.held:
            self.held[rid]["card_id"] = card
            self.log("sup", sync=True, what="card", request_id=rid, card_id=card)
        else:                                # it ended while the card was being filed
            await self._withdraw(card)

    async def _withdraw(self, card_id):
        if not card_id or self.cards is None:
            return
        try:
            await asyncio.get_event_loop().run_in_executor(None, self.cards.withdraw, card_id)
            self.log("sup", what="card-withdrawn", card_id=card_id)
        except Exception as e:  # noqa: BLE001
            self.log("err", text=f"card {card_id} not withdrawn: {e!r}")

    def _release_held(self, rid, by_card=None) -> bool:
        """Drop a held request; its card is withdrawn unless the card itself answered it."""
        h = self.held.pop(rid, None) if rid else None
        if h is None:
            return False
        card = h.get("card_id")
        if card and card != by_card:
            asyncio.ensure_future(self._withdraw(card))
        return True

    def _turn_begins(self):
        if self.turn_started_at is None:
            self.turn_started_at = time.monotonic()
        self.turn_open = True

    def _note_usage(self, ev: dict, wall="now"):
        """Context in use: the main chain's last assistant usage (what the next call sends:
        input + cache writes + cache reads + this reply), and the window from a result's
        modelUsage (the model that carried the most input: side calls run on a small model)."""
        typ = ev.get("type")
        if typ == "assistant" and ev.get("parent_tool_use_id") is None:
            u = (ev.get("message") or {}).get("usage") or {}
            parts = [u.get(k) for k in ("input_tokens", "cache_creation_input_tokens",
                                        "cache_read_input_tokens", "output_tokens")]
            if any(isinstance(x, int) for x in parts):
                self.ctx_tokens = sum(x for x in parts if isinstance(x, int))
                self.ctx_wall = _now_iso() if wall == "now" else wall
        elif typ == "result":
            mu = ev.get("modelUsage") or {}
            best = None
            for v in mu.values() if isinstance(mu, dict) else ():
                if not isinstance(v, dict) or not isinstance(v.get("contextWindow"), int):
                    continue
                weight = sum(v.get(k) or 0 for k in ("inputTokens", "cacheReadInputTokens",
                                                     "cacheCreationInputTokens"))
                if best is None or weight > best[0]:
                    best = (weight, v["contextWindow"])
            if best and best[1] > 0:
                self.ctx_window = best[1]

    def _on_replay(self, ev: dict):
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text")
        queued = [m for m in self.messages.values()
                  if m.get("state") == "queued" and m.get("gen") == self.gen]
        echo_uuid = ev.get("uuid")
        how, hit = "uuid", next((m for m in queued if echo_uuid and m.get("uuid") == echo_uuid), None)
        if hit is None:
            how, hit = "text", next((m for m in queued if m.get("text") == content), None)
        if hit is None and queued:
            how, hit = "order", queued[0]
        if hit is None:
            self.log("sup", what="replay-unmatched", uuid=echo_uuid)
            return
        self.echo_matches[how] += 1
        self._msg_state(hit, "delivered", cli_uuid=echo_uuid, echo_match=how)

    async def run_proc_once(self):
        await self.start_proc()
        proc = self.proc
        await asyncio.gather(self.read_stdout(proc), self.read_stderr(proc))
        code = await proc.wait()
        lived = time.monotonic() - (self.started_at or time.monotonic())
        self.log("sup", sync=True, what="exit", pid=proc.pid, code=code, lived_s=round(lived, 2))
        for m in self.messages.values():
            if m.get("state") == "queued" and m.get("gen") == self.gen:
                self._msg_state(m, "unknown")
            elif m.get("state") == "delivered":
                # Taken, but the turn never finished: the next process does not resume it.
                self._msg_state(m, "interrupted", reason=f"process exited (code {code})")
        self.turn_started_at = None
        self.tools_open.clear()
        self._interrupt_pending = False
        for rid in list(self.held):
            self.log("sup", what="request-abandoned", request_id=rid)
            self._release_held(rid)
        self.turn_open = False
        return lived

    async def supervise(self):
        delay = self.backoff_base
        while not self.stopping:
            lived = await self.run_proc_once()
            self._set_state()
            if self.stopping:
                break
            if self.dev:
                # Closed for Dev: no restart, no backoff, no count, until the TUI is gone.
                await self._dev_exit.wait()
                if self.stopping:
                    break
                continue
            self.restarts += 1
            if lived < FAST_EXIT_S:
                self.fast_exits += 1
            else:
                self.fast_exits, delay = 0, self.backoff_base
            if self.fast_exits >= MAX_FAST_EXITS:
                self.failed = True
                self.log("sup", sync=True, what="failed", fast_exits=self.fast_exits)
                self._set_state()
                self._restart_now.clear()
                await self._restart_now.wait()       # a `restart` command clears the failure
                self.failed, self.fast_exits, delay = False, 0, self.backoff_base
                continue
            if self._restart_requested:          # asked for: no backoff, no fast-exit count
                self._restart_requested = False
                self.fast_exits = max(0, self.fast_exits - (1 if lived < FAST_EXIT_S else 0))
                continue
            self.log("sup", what="restart-wait", seconds=delay)
            self._restart_now.clear()
            try:
                await asyncio.wait_for(self._restart_now.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(BACKOFF_MAX_S, delay * 2)

    async def stop_proc(self):
        p = self.proc
        if p is None or p.returncode is not None:
            return
        try:
            p.stdin.close()                    # the CLI's own clean exit (~0.4 s measured)
        except Exception:  # noqa: BLE001
            pass
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                try:
                    os.killpg(p.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                await asyncio.wait_for(p.wait(), timeout=STOP_GRACE_S)
                return
            except asyncio.TimeoutError:
                continue

    # --- interface ---
    async def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "status":
            return {"ok": True, **self.status()}
        if cmd == "send":
            return await self.cmd_send(req)
        if cmd == "message":
            m = self.messages.get(str(req.get("id") or ""))
            if m is None:
                return {"ok": False, "reason": "unknown-id"}
            return {"ok": True, "id": m["id"], "state": m.get("state"), "attempts": m.get("attempts", 0)}
        if cmd == "pending":
            return {"ok": True, "pending": list(self.held.values())}
        if cmd == "answer":
            rid = str(req.get("request_id") or "")
            if rid not in self.held:
                return {"ok": False, "reason": "no-such-request"}
            resp = req.get("response")
            if not isinstance(resp, dict):
                return {"ok": False, "reason": "response must be an object"}
            ok = bool(await self._write({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": resp}}))
            if ok:
                self._release_held(rid)
                self.log("sup", what="answered", request_id=rid)
                self._set_state()
            return {"ok": ok, **({} if ok else {"reason": "process-not-running"})}
        if cmd == "resolve":
            return await self.cmd_resolve(req)
        if cmd == "interrupt":
            rid = f"int-{uuid.uuid4().hex[:12]}"
            if self.turn_open:
                self._interrupt_pending = True
            ok = bool(await self._write({"type": "control_request", "request_id": rid,
                                         "request": {"subtype": "interrupt"}}))
            return {"ok": ok, "request_id": rid, **({} if ok else {"reason": "process-not-running"})}
        if cmd == "dev":
            return await self.cmd_dev(req)
        if cmd == "chat":
            return await self.cmd_chat(req)
        if cmd == "set_model":
            return await self.cmd_set_model(req)
        if cmd == "stop":
            if req.get("tui") and not (self.interactive and self.tui_argv_for):
                return {"ok": False, "reason": "--tui needs the supervisor running in the seat's pane"}
            self.to_tui = bool(req.get("tui"))
            asyncio.get_event_loop().call_soon(lambda: asyncio.ensure_future(self.stop()))
            return {"ok": True, "stopping": True, "tui": self.to_tui}
        if cmd == "restart":
            if self.failed:
                self._restart_now.set()          # the failed wait ends; a fresh process starts
            else:
                self._restart_requested = True
                await self.stop_proc_for_restart()
            return {"ok": True}
        return {"ok": False, "reason": f"unknown cmd {cmd!r}"}

    # --- Dev: the TUI in the pane, same environment -------------------------------------------------

    def _dev_marker(self):
        return self.home / "dev"

    def _tty_handoff(self, pgid):
        """Make `pgid` the pane terminal's foreground group (SIGTTOU ignored while we do it)."""
        old = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            os.tcsetpgrp(0, pgid)
        except OSError as e:
            self.log("err", text=f"terminal hand-over failed: {e}")
        finally:
            signal.signal(signal.SIGTTOU, old)

    async def cmd_dev(self, req: dict) -> dict:
        if not (self.interactive and self.tui_argv_for):
            return {"ok": False, "reason": "dev needs the supervisor running in the seat's pane"}
        if self.dev:
            return {"ok": True, "already": True, "tui_pid": self.tui.pid if self.tui else None}
        if self.state != "idle":
            return {"ok": False, "reason": "busy", "state": self.state}
        t0 = time.monotonic()
        self.dev = True
        self._dev_exit.clear()
        self._set_state()
        p = self.proc
        if p is not None and p.returncode is None:
            try:
                p.stdin.close()                # the CLI's own clean exit
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(p.wait(), timeout=STOP_GRACE_S)
            except asyncio.TimeoutError:
                await self.stop_proc()
        stopped_ms = int((time.monotonic() - t0) * 1000)
        self._dev_marker().write_text(json.dumps({"since": _now_iso(), "session_id": self.session_id}) + "\n")
        if self._input_transport is not None:
            self._input_transport.pause_reading()
        for fd in (0, 1, 2):
            try:
                os.set_blocking(fd, True)      # the TUI reads and writes this terminal itself
            except OSError:
                pass
        argv = self.tui_argv_for(self.session_id, self.model_arg)
        try:
            self.tui = await asyncio.create_subprocess_exec(*argv, cwd=self.cwd, env=self.env,
                                                            stdin=None, stdout=None, stderr=None,
                                                            process_group=0)
        except OSError as e:
            self._dev_done()
            return {"ok": False, "reason": f"the TUI did not start: {e}"}
        self._tty_handoff(self.tui.pid)
        self.log("sup", sync=True, what="dev", tui_pid=self.tui.pid, argv=argv, stopped_ms=stopped_ms,
                 tui_started_ms=int((time.monotonic() - t0) * 1000))
        asyncio.ensure_future(self._watch_tui(self.tui, t0))
        return {"ok": True, "tui_pid": self.tui.pid, "stopped_ms": stopped_ms}

    async def _watch_tui(self, tui, t0):
        code = await tui.wait()
        self._tty_handoff(os.getpgrp())
        self.log("sup", sync=True, what="dev-exit", tui_pid=tui.pid, code=code,
                 dev_s=round(time.monotonic() - t0, 2))
        self._dev_done()

    def _dev_done(self):
        try:
            self._dev_marker().unlink()
        except FileNotFoundError:
            pass
        for fd in (0,):
            try:
                os.set_blocking(fd, False)
            except OSError:
                pass
        if self._input_transport is not None:
            self._input_transport.resume_reading()
        self.tui = None
        self.dev = False
        self._set_state()
        self._dev_exit.set()               # the loop starts the headless process again

    async def cmd_chat(self, req: dict, timeout: float = 20.0) -> dict:
        """Back to chat: /exit typed into the TUI (the TUI's own clean exit, its transcript intact),
        then the headless process on the same session. The caller checks the TUI is not mid-turn."""
        if not self.dev:
            return {"ok": True, "already": True}
        pane = self.env.get("TMUX_PANE") or ""
        if not pane or pane.startswith("%hl-"):
            return {"ok": False, "reason": "no tmux pane to type /exit into"}
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        for keys in (("C-u",), ("-l", "/exit"), ("-H", "0d")):
            r = await loop.run_in_executor(None, lambda k=keys: subprocess.run(
                ["tmux", "send-keys", "-t", pane, *k], capture_output=True, text=True, timeout=5))
            if r.returncode != 0:
                return {"ok": False, "reason": f"send-keys failed: {(r.stderr or '').strip()[:200]}"}
            await asyncio.sleep(0.15)
        try:
            await asyncio.wait_for(self._dev_exit.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "reason": "the TUI did not exit (is a menu or a turn open in it?)"}
        exit_ms = int((time.monotonic() - t0) * 1000)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.state not in ("idle", "working", "waiting"):
            await asyncio.sleep(0.1)
        self.log("sup", sync=True, what="chat", exit_ms=exit_ms,
                 headless_ms=int((time.monotonic() - t0) * 1000), state=self.state)
        return {"ok": True, "exit_ms": exit_ms, "headless_ms": int((time.monotonic() - t0) * 1000),
                "state": self.state, "pid": self.proc.pid if self.proc else None}

    async def control(self, request: dict, timeout: float = 15.0):
        """Send one control request to the CLI and wait for its control_response: the response
        dict, None on timeout, False when nothing could be written."""
        rid = f"ctl-{uuid.uuid4().hex[:12]}"
        fut = asyncio.get_event_loop().create_future()
        self._ctl_waiters[rid] = fut
        if not await self._write({"type": "control_request", "request_id": rid, "request": request}):
            self._ctl_waiters.pop(rid, None)
            return False
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._ctl_waiters.pop(rid, None)
            return None

    async def cmd_set_model(self, req: dict) -> dict:
        """Switch the seat's model, between turns only (a busy seat answers busy, and the gateway
        defers the switch to the turn's end as it does for a pane). First the CLI's own set_model
        control request: no restart, no cold start. If the CLI refuses it or does not answer, the
        CLI is restarted on --model (a cold start once, the session resumed). Every later restart
        uses the new model too."""
        model = str(req.get("model") or "").strip()
        if not model:
            return {"ok": False, "reason": "model required"}
        if self.state != "idle":
            return {"ok": False, "reason": "busy", "state": self.state}
        resp = await self.control({"subtype": "set_model", "model": model})
        self.model_arg = model
        self._save_seat()
        if isinstance(resp, dict) and resp.get("subtype") == "success":
            self.model = model
            self.log("sup", sync=True, what="model", model=model, how="set_model")
            return {"ok": True, "model": model, "how": "set_model"}
        why = (resp or {}).get("error") if isinstance(resp, dict) else ("no answer" if resp is None else "not written")
        self.log("sup", sync=True, what="model", model=model, how="restart", why=str(why)[:200])
        self._restart_requested = True
        await self.stop_proc_for_restart()
        return {"ok": True, "model": model, "how": "restart", "why": str(why)[:200]}

    async def cmd_resolve(self, req: dict) -> dict:
        """A card's answer for a held request: the control response is written once. The same card
        again (a watchdog re-fire, a second surface) is `already`; a request this process does not
        hold (it ended, or another process asked it) is `no-such-request`."""
        rid = str(req.get("request_id") or "")
        card = req.get("card_id")
        if rid in self.resolved:
            return {"ok": True, "already": True, "card_id": self.resolved[rid]}
        h = self.held.get(rid)
        if h is None:
            return {"ok": False, "reason": "no-such-request"}
        import headless_cards
        resp = headless_cards.response_for(h.get("request") or {}, req.get("outcome") or {})
        ok = bool(await self._write({"type": "control_response", "response": {
            "subtype": "success", "request_id": rid, "response": resp}}))
        if not ok:
            return {"ok": False, "reason": "process-not-running"}
        self.resolved[rid] = card
        self._release_held(rid, by_card=card)
        self.log("sup", sync=True, what="resolved", request_id=rid, card_id=card,
                 behavior=resp.get("behavior"))
        self._set_state()
        return {"ok": True, "already": False}

    async def stop_proc_for_restart(self):
        p = self.proc
        if p is not None and p.returncode is None:
            try:
                p.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    async def cmd_send(self, req: dict) -> dict:
        text = req.get("text")
        mid = str(req.get("id") or "").strip()
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "reason": "empty"}
        if not mid:
            return {"ok": False, "reason": "id required"}
        known = self.messages.get(mid)
        if known is not None:
            # Never a second write for one id: the caller learns where the first one stands.
            return {"ok": True, "id": mid, "state": known.get("state"), "duplicate": True}
        if self.stopping:
            return {"ok": False, "reason": "stopping"}
        if self.dev:
            return {"ok": False, "reason": "dev"}
        if self.held and not req.get("allow_waiting"):
            return {"ok": False, "reason": "waiting", "pending": len(self.held)}
        m = {"id": mid, "text": text, "attempts": 0}
        self.messages[mid] = m
        self._msg_state(m, "accepted")         # durable before any write: a crash resends it
        if self.proc is not None and self.proc.returncode is None and not self.failed:
            await self._write_message(m)
        return {"ok": True, "id": mid, "state": m["state"], "duplicate": False}

    def status(self) -> dict:
        p = self.proc
        counts: dict = {}
        for m in self.messages.values():
            counts[m.get("state")] = counts.get(m.get("state"), 0) + 1
        mono = time.monotonic()
        age = (lambda t: round(mono - t, 1) if t is not None else None)
        live = p is not None and p.returncode is None
        return {"seat": self.seat, "state": self.state, "supervisor_pid": os.getpid(),
                "pid": p.pid if live else None,
                "session_id": self.session_id, "generation": self.gen, "restarts": self.restarts,
                "fast_exits": self.fast_exits,
                "last_event": self.last_event, "last_event_age_s": age(self.last_event_at),
                "turn_age_s": age(self.turn_started_at),
                "uptime_s": age(self.started_at) if live else None,
                "tools_open": sorted(set(self.tools_open.values())), "last_tool": self.last_tool,
                "held": len(self.held),
                "held_requests": [held_summary(h) for h in self.held.values()],
                "model": self.model, "model_arg": self.model_arg, "message_uuid": self.message_uuid,
                "pane_id": self.env.get("TMUX_PANE"),
                "dev": self.dev, "tui_pid": self.tui.pid if self.tui is not None else None,
                "echo_matches": dict(self.echo_matches),
                "context": {"tokens": self.ctx_tokens, "window": self.ctx_window, "at": self.ctx_wall},
                "messages": counts, "log": str(self.home / "events.jsonl")}

    async def _serve_conn(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            try:
                req = json.loads(line.decode() or "{}")
                out = await self.handle(req if isinstance(req, dict) else {})
            except ValueError:
                out = {"ok": False, "reason": "bad json"}
            writer.write((json.dumps(out) + "\n").encode())
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    def _save_seat(self):
        p = self.home / "seat.json"
        doc = {"seat": self.seat, "session_id": self.session_id, "cwd": self.cwd,
               **({"model": self.model_arg} if self.model_arg else {}), "updated": _now_iso()}
        tmp = p.with_name(".seat.json.tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n")
        os.replace(tmp, p)

    async def _pane_input(self):
        """Typing into the seat's pane reaches nothing: record it and say so."""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        self._input_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                return
            self.log("typed", text=line.decode("utf-8", errors="replace")[:500])
            print(f"[headless {self.seat}] this seat runs headless; typing here is not sent "
                  f"(use the dashboard, or `seat_supervisor.py stop {self.seat} --tui`)", flush=True)

    async def stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.log("sup", sync=True, what="stop", tui=self.to_tui)
        self._set_state()
        self._restart_now.set()
        self._dev_exit.set()
        await self.stop_proc()

    async def main(self):
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        lock_fh = open(self.home / "lock", "a+")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"{self.seat}: a supervisor is already running", file=sys.stderr)
            return 3
        self._log_fh = open(self.home / "events.jsonl", "a", encoding="utf-8")
        try:
            self._dev_marker().unlink()        # a Dev marker from an earlier supervisor: its TUI is not ours
        except FileNotFoundError:
            pass
        self.load_messages()
        self.log("sup", sync=True, what="supervisor-start", pid=os.getpid(), session_id=self.session_id,
                 cwd=self.cwd, carried=sum(1 for m in self.messages.values()
                                           if m.get("state") in ("accepted", "unknown")))
        self._save_seat()
        sock = self.home / HS.SOCK_NAME
        try:
            sock.unlink()
        except FileNotFoundError:
            pass
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._serve_conn, path=str(sock))
        finally:
            os.umask(old)
        os.chmod(sock, 0o600)
        self.log("sup", what="listening", sock=str(sock))
        if self.cards is not None:
            # A fresh supervisor holds no request: the seat's pending cards belong to a process that is gone.
            try:
                gone = await asyncio.get_event_loop().run_in_executor(
                    None, self.cards.withdraw_seat, self.seat, self.session_name)
                if gone:
                    self.log("sup", what="cards-withdrawn", cards=gone)
            except Exception as e:  # noqa: BLE001
                self.log("err", text=f"old cards not withdrawn: {e!r}")
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.stop()))
        for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP):
            # A key pressed in the pane must not stop (or suspend) the seat.
            loop.add_signal_handler(sig, lambda s=sig: self.log("sup", what="signal-ignored", signal=int(s)))
        tasks = []
        if self.interactive:
            tasks.append(asyncio.ensure_future(self._pane_input()))
        await self.supervise()
        for t in tasks:
            t.cancel()
        self._server.close()
        try:
            sock.unlink()
        except FileNotFoundError:
            pass
        self.log("sup", sync=True, what="supervisor-exit", tui=self.to_tui)
        self._log_fh.close()
        lock_fh.close()
        return 0

    def exec_tui(self):
        """Replace this process with the pane's TUI on the same session and environment. Called
        after the event loop has closed; the pane's stdin goes back to blocking first (the
        loop's reader made it non-blocking, which the TUI would trip on)."""
        argv = self.tui_argv_for(self.session_id, self.model_arg)
        for fd in (0, 1, 2):
            try:
                os.set_blocking(fd, True)
            except OSError:
                pass
        os.chdir(self.cwd)
        os.execvpe(argv[0], argv, self.env)


# --- CLI --------------------------------------------------------------------------------------------

def _orch_dir():
    return HS.data_dir()


def cmd_run(a) -> int:
    orch = _orch_dir()
    row = registry_row(a.seat, orch)
    runtime = (row.get("runtime") or "claude").strip().lower()
    if runtime not in HS.HEADLESS_RUNTIMES:
        print(f"{a.seat}: runtime {runtime} stays on tmux", file=sys.stderr)
        return 2
    home = HS.seat_dir(a.seat, orch)
    saved = {}
    try:
        saved = json.loads((home / "seat.json").read_text())
    except (OSError, ValueError):
        pass
    cwd = a.cwd or row.get("cwd") or saved.get("cwd")
    sid = a.session or saved.get("session_id") or row.get("session_id")
    model = a.model or row.get("model") or None
    if not cwd or not os.path.isdir(cwd):
        print(f"{a.seat}: no working folder (registry cwd={cwd!r})", file=sys.stderr)
        return 2
    if not sid:
        print(f"{a.seat}: no session id to resume (registry or --session)", file=sys.stderr)
        return 2
    bin_ = a.claude_bin or claude_bin()
    settings = None if a.no_settings else settings_file(a.seat, cwd, orch)
    session = row.get("tmux_session") or a.seat
    pane = os.environ.get("TMUX_PANE") or pane_id_of(session) or synthetic_pane_id(a.seat)
    env = seat_env(os.environ, orch, pane, session=session)
    interactive = sys.stdin.isatty()
    sup = Supervisor(a.seat, env=env, cwd=cwd, home=home, session_id=sid, interactive=interactive,
                     message_uuid=lambda: message_uuid_setting(a.seat, orch, a.no_message_uuid),
                     session_name=session, cards=cards_setting(a.seat, orch), model_arg=model,
                     argv_for=lambda s, m=None: headless_argv(bin_, session_id=s, model=m or model,
                                                              settings=settings),
                     tui_argv_for=lambda s, m=None: tui_argv(bin_, session_id=s, model=m or model,
                                                             settings=settings))
    code = asyncio.run(sup.main())
    if code == 0 and sup.to_tui:
        sup.exec_tui()
    return code


class LedgerCards:
    """The supervisor's card seam, on the approval ledger (scripts/headless_cards.py)."""

    @staticmethod
    def file(seat, session, request_id, request):
        import headless_cards
        return headless_cards.file_card(seat, session, request_id, request)

    @staticmethod
    def withdraw(card_id):
        import headless_cards
        return headless_cards.withdraw(card_id)

    @staticmethod
    def withdraw_seat(seat, session):
        import headless_cards
        return headless_cards.withdraw_seat(seat, session)


def cards_setting(seat: str, orch_dir):
    """Held requests as cards? On unless HEADLESS_CARDS=0 or the seat's entry says "cards": false."""
    if os.environ.get("HEADLESS_CARDS", "").strip() == "0":
        return None
    entry = HS.load_switch(orch_dir).get(seat)
    if isinstance(entry, dict) and entry.get("cards") is False:
        return None
    return LedgerCards()


def message_uuid_setting(seat: str, orch_dir, flag_off: bool = False) -> bool:
    """Send our own uuid with each message? On unless the flag, HEADLESS_MESSAGE_UUID=0, or the seat's
    headless-seats.json entry ("message_uuid": false) turns it off."""
    if flag_off or os.environ.get("HEADLESS_MESSAGE_UUID", "").strip() == "0":
        return False
    entry = HS.load_switch(orch_dir).get(seat)
    return not (isinstance(entry, dict) and entry.get("message_uuid") is False)


def pane_line(seat: str, orch_dir=None) -> str:
    """The line to type into the seat's pane shell: spawn's env prefix (spawn-agent.sh) around
    this supervisor, so the pane's process carries the same environment the TUI had."""
    orch = str(HS.data_dir(orch_dir))
    parts = ["env"] + [f"-u {v}" for v in INSTALLER_SESSION_ENV]
    parts += [f"ORCHESTRA_DIR={shlex.quote(orch)}", f"ORCH_DIR={shlex.quote(orch)}",
              f"ORCHESTRA_ROOT={shlex.quote(_ROOT)}"]
    for k in ("ORCHESTRA_CONFIG", "CLAUDE_CONFIG_DIR", "ORCHESTRA_CLAUDE_BIN"):
        if os.environ.get(k):
            parts.append(f"{k}={shlex.quote(os.environ[k])}")
    parts.append("CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1")
    nice = str(os.environ.get("ORCHESTRA_SEAT_NICE", "10")).strip()
    if nice.isdigit() and 0 < int(nice) <= 19:
        parts.append(f"nice -n {int(nice)}")
    parts += ["python3", shlex.quote(os.path.join(_HERE, "seat_supervisor.py")), "run",
              shlex.quote(seat)]
    return " ".join(parts)


SHELLS = frozenset({"bash", "zsh", "sh", "dash", "fish"})


def takeover(seat: str, orch_dir=None, tmux=_tmux) -> tuple[bool, str]:
    """Type the supervisor line into the seat's pane, only when the switch is on, the pane is
    back at its shell (no claude running there) and no supervisor answers."""
    if not HS.is_headless(seat, orch_dir=orch_dir):
        return False, "switch it on first (seat_supervisor.py switch on <seat>)"
    try:
        HS.status(seat, orch_dir=orch_dir)
        return False, "a supervisor is already running for this seat"
    except HS.SupervisorDown:
        pass
    r = tmux("display-message", "-t", f"={seat}:", "-p", "#{pane_current_command}")
    cur = (r.stdout or "").strip()
    if r.returncode != 0 or not cur:
        return False, "no pane for this seat"
    if cur not in SHELLS:
        return False, f"the pane is running {cur!r}, not a shell: exit the TUI first"
    line = pane_line(seat, orch_dir)
    if tmux("send-keys", "-t", f"={seat}:", "-l", line).returncode != 0:
        return False, "send-keys failed"
    tmux("send-keys", "-t", f"={seat}:", "Enter")
    return True, line


def _print(out):
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", True) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seat_supervisor.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("seat")
    r.add_argument("--cwd")
    r.add_argument("--model")
    r.add_argument("--session")
    r.add_argument("--claude-bin")
    r.add_argument("--no-settings", action="store_true")
    r.add_argument("--no-message-uuid", action="store_true",
                   help="match replay echoes by text only (no uuid of ours on each message)")
    mu = sub.add_parser("message-uuid", help="send our own uuid with each message (read at supervisor start)")
    mu.add_argument("onoff", choices=("on", "off"))
    mu.add_argument("seat")
    sw = sub.add_parser("switch")
    sw.add_argument("onoff", choices=("on", "off"))
    sw.add_argument("seat")
    sw.add_argument("--by", default=os.environ.get("USER", ""))
    for name in ("pane-line", "takeover", "status", "pending", "interrupt", "restart"):
        sub.add_parser(name).add_argument("seat")
    s = sub.add_parser("send")
    s.add_argument("seat")
    s.add_argument("text")
    s.add_argument("--id")
    an = sub.add_parser("answer")
    an.add_argument("seat")
    an.add_argument("request_id")
    an.add_argument("response")
    st = sub.add_parser("stop")
    st.add_argument("seat")
    st.add_argument("--tui", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "switch":
        orch = _orch_dir()
        runtime = (registry_row(a.seat, orch).get("runtime") or "").strip().lower()
        try:
            entry = HS.set_switch(a.seat, a.onoff == "on", runtime=runtime, by=a.by)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        return _print({"ok": True, "seat": a.seat, **entry})
    if a.cmd == "message-uuid":
        entry = HS.set_option(a.seat, "message_uuid", a.onoff == "on")
        return _print({"ok": True, "seat": a.seat, **entry,
                       "note": "read each time the CLI starts: `seat_supervisor.py restart <seat>` applies it"})
    if a.cmd == "pane-line":
        print(pane_line(a.seat))
        return 0
    if a.cmd == "takeover":
        ok, info = takeover(a.seat)
        return _print({"ok": ok, ("typed" if ok else "reason"): info})
    payload = {"cmd": a.cmd}
    if a.cmd == "send":
        payload.update(text=a.text, id=a.id or f"cli-{uuid.uuid4().hex[:12]}")
    elif a.cmd == "answer":
        payload.update(request_id=a.request_id, response=json.loads(a.response))
    elif a.cmd == "stop":
        payload["tui"] = a.tui
    try:
        return _print(HS.request(a.seat, payload))
    except HS.SupervisorDown as e:
        return _print({"ok": False, "reason": str(e)})


if __name__ == "__main__":
    sys.exit(main())
