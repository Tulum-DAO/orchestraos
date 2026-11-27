"""The native session's tool boundary: ARTURO'S OWN TOOLS, run through the proxy's execute_tool under the caller's
verified principal (gm msg_0aec5c71: "existing tools only, no new powers"; Shaw 10-15: all tools).

What a native caller can do is exactly what the same caller can do on the relay voice path today:
  - a verified fleet/owner principal gets Arturo's tool list MINUS kill_agent (fleet NO KILLS rule, Shaw 09-18);
  - anyone else gets the proxy's non-fleet allowlist (execute_tool refuses the rest anyway).
On top of the existing layer (each has a test and a sabotage):
  I1  the model never chooses its principal: principal-shaped argument keys are refused, and execute() takes the
      principal only from the SessionBinding the server made from the gateway's verified stamp;
  I2  a duplicate tool_call_id never runs its side effect twice (ToolLedger.run_once);
  I3  spawn_agent is capped (3 per session, 10 per principal per day) and refused unless Arturo's own spoken words in
      this turn read the spawn back first (the seat name, or the task's key words) - checked from the transcript,
      never trusted from the model;
  I4  any kill / restart / signal path is refused with "needs a card"; async_task and run_command are not offered (they
      can kill under another name); inject_message never types a command into a pane;
  I5  the relay's G1' fence: after a tool result in a turn, only lookups run until the caller speaks again (text that
      came back in a result cannot steer an action), and execute_tool sees the fence too.
  I6  the spending group (services/arturo/spending_tools.py, generated from the tracker's tools.json): fleet/owner
      callers only; its lookups run under the fence, its writes do not; every call goes through the ledger (at most
      once per call_id); a guard's confirm/allow_duplicate is honoured only on a LATER caller turn of this call whose
      words say yes (never the model's own say-so); a successful spending write backs a "saved"/"updated" claim.
"""
import importlib.util
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import protocol
from .tool_ledger import ToolLedger

PRINCIPAL_KEYS = frozenset({"principal", "user", "user_id", "tenant", "tenant_id", "device", "device_id",
                            "as_user", "on_behalf_of", "owner", "caller", "identity", "scope", "scopes"})
# Never offered and refused if asked for (NO KILLS: a kill/restart/signal of a process the caller did not spawn needs
# Shaw's card). kill_agent is the only such tool in Arturo's list today; the name rule catches a later one.
REFUSED_TOOLS = frozenset({"kill_agent"})
_KILL_NAME = re.compile(r"(kill|restart|signal|terminate|reboot)", re.I)
NEEDS_A_CARD = "That needs a card: stopping or restarting a seat is Shaw's decision, not a voice action."
# Not offered on native voice (independent review B1/B2, 10-15): each can kill or restart past every check here.
# async_task runs ANY tool by name (async_task(kill_agent)); run_command is a raw shell (tmux kill-server, pkill).
# gm_command stays: it asks gm, and gm is bound by the same NO KILLS rule.
NOT_ON_VOICE = frozenset({"async_task", "run_command"})
# inject_message types into a seat's pane: a slash command (/exit, /clear) or a shell escape (!cmd) is a control action,
# not a message.
_PANE_COMMAND = re.compile(r"^\s*[/!]")
# Tools that start a seat. research spawns a research-NNNN seat (async_task(spawn_agent)), so it gets the same spoken
# read-back and counts against the same caps (re-review N1).
SPAWN_TOOLS = frozenset({"spawn_agent", "research"})
SPAWN_PER_SESSION = 3
SPAWN_PER_DAY = 10
# execute_tool returns text; these prefixes are its failure shapes (spawn_agent "FAILED", the seat-name guard "NOT RUN",
# the non-fleet allowlist "REFUSED"/"I can't"), so the ledger never records a failure as a success.
# Matched at the START only: success lines echo model text (a seat named error-fixer, a task "fix the failed build", gm's
# reply), so a failure word anywhere would mark real successes failed (re-review N3b). These are the proxy's failure
# shapes: "GM error: ...", "GM timed out ...", "Blocked: ... Not injected", "Failed to inject ...", "Couldn't answer
# the menu ...", "Injected into 'x' on vps but COULD NOT VERIFY ...", "Session 'x' not found ..." (re-review N3).
_FAIL_PREFIX = re.compile(r"^\s*(FAILED|NOT RUN|REFUSED|Refused|ERROR|Error|I can't|Session '[^']*' already exists|"
                          r"Session '[^']*' not found|GM error|GM timed out|Blocked|BLOCKED|Failed|Couldn't|Unknown tool|"
                          r"No response|Telegram error|Injected into '[^']*' on \S+ but COULD NOT VERIFY)")


@dataclass(frozen=True)
class SessionBinding:
    """Made by the server from the gateway's VERIFIED stamp, never from model output."""
    principal: str                      # "fleet" | "owner:<device id>"
    device: str
    conversation_id: str
    allowed: frozenset = field(default_factory=frozenset)

    @property
    def fleet(self):
        return self.principal == "fleet" or self.principal.startswith("owner:")


def new_conversation_id():
    return f"native-{uuid.uuid4().hex[:12]}"


def _norm(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def read_back(spoken, args):
    """True when Arturo's spoken words this turn name what he is about to spawn: the seat name (hyphens may be spoken
    as spaces) or at least 3 of the task's words of 4+ letters (or all of them when it has fewer)."""
    said = f" {_norm(spoken)} "
    name = _norm(args.get("session_name", ""))
    if name and f" {name} " in said:
        return True
    words = {w for w in _norm(args.get("task") or args.get("query") or "").split() if len(w) >= 4}
    if not words:
        return False
    hit = sum(1 for w in words if f" {w} " in said)
    return hit >= min(3, len(words))


def _load_proxy():
    """The live arturo-proxy module, imported the way cartesia_arturo_service.py does. Importing it starts no daemon
    while ARTURO_STREAM_RELAY / ARTURO_WARM_VOICES_CACHE / ARTURO_FACTS_RECALL are unset (the native service's run
    script never sets them; a test pins that no thread starts)."""
    path = Path(__file__).resolve().parents[1] / "arturo-proxy.py"
    spec = importlib.util.spec_from_file_location("arturo_proxy_for_native", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ProxyExecutor:
    """Runs one tool through the proxy's execute_tool as the given principal; also lists the proxy's tools."""

    def __init__(self, mod=None):
        self._mod = mod

    @property
    def mod(self):
        if self._mod is None:
            self._mod = _load_proxy()
        return self._mod

    @property
    def non_fleet(self):
        return self.mod._NON_FLEET_ALLOWED

    def specs(self, fleet):
        """{name: (description, parameters)} for the tools this caller class may use on the relay path today."""
        out = {}
        for t in self.mod.TOOLS:
            fn = t.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            if not fleet and name not in self.mod._NON_FLEET_ALLOWED:
                continue
            out[name] = (fn.get("description", ""), fn.get("parameters") or {"type": "object", "properties": {}})
        return out

    def __call__(self, name, args, principal, fenced=False, user_turns=None, call=None):
        turn = {"principal": principal, "voice": True, "fenced": bool(fenced)}
        for k in ("conversation_id", "thread_id"):            # gm_command tags its message to gm with these
            if call and call.get(k):
                turn[k] = str(call[k])
        return self.mod._as_turn(turn, self.mod.execute_tool, name, args, user_turns=user_turns)


class ArturoTools:
    def __init__(self, ledger: ToolLedger, executor=None, spoken_fn=None, clock=time.time,
                 spawn_per_session=SPAWN_PER_SESSION, spawn_per_day=SPAWN_PER_DAY, spending=None):
        self.ledger = ledger
        self.spending = spending                 # spending_tools.SpendingTools, or None (no spending group)
        self.executor = executor or ProxyExecutor()
        self.spoken_fn = spoken_fn or (lambda conversation_id: "")
        self.user_fn = lambda conversation_id: ""
        self.thread_fn = lambda conversation_id: ""   # the chat thread the call was placed from (the server sets it)
        self._fenced = set()                     # conversations whose current turn has had a tool result
        self._turn_start = {}                    # conversation -> when the caller's current turn began (new_turn)
        self.clock = clock
        self.spawn_per_session, self.spawn_per_day = spawn_per_session, spawn_per_day
        self.trace = []

    # ---- what the session declares -------------------------------------------------------------------------
    def specs(self, binding):
        out = {n: s for n, s in self.executor.specs(binding.fleet).items()
               if n not in REFUSED_TOOLS and n not in NOT_ON_VOICE and not _KILL_NAME.search(n)}
        if self.spending is not None and binding.fleet:
            # private finances: verified fleet/owner callers only. A name the proxy already has is never shadowed.
            out.update({n: s for n, s in self.spending.specs().items()
                        if n not in out and not _KILL_NAME.search(n)})
        return out

    def _spending_read(self, name):
        return self.spending is not None and self.spending.owns(name) and self.spending.is_read(name)

    def new_turn(self, conversation_id):
        """The caller spoke again: the fence lifts (the session calls this)."""
        self._fenced.discard(conversation_id)
        self._turn_start[conversation_id] = self.ledger.clock()

    def declared_tools(self, binding):
        """delegation.responses.tools. strict=False: Arturo's schemas mark optional properties, which strict mode
        cannot express."""
        return [protocol.function_tool(n, d, p, strict=False) for n, (d, p) in sorted(self.specs(binding).items())]

    def bind(self, principal, device, conversation_id=None):
        b = SessionBinding(principal=principal, device=device, conversation_id=conversation_id or new_conversation_id())
        return SessionBinding(principal=b.principal, device=b.device, conversation_id=b.conversation_id,
                              allowed=frozenset(self.specs(b)))

    # ---- the boundary ------------------------------------------------------------------------------------------
    def execute(self, binding, tool_call_id, name, arguments_json):
        """The one entry point for a model-requested call. Returns the JSON-able function_call_output. Never raises for
        a model error: the model gets a refusal it can say out loud."""
        def refuse(code, detail):
            self.trace.append({"call_id": tool_call_id, "name": name, "decision": "refused", "error": code,
                               "conversation_id": getattr(binding, "conversation_id", None)})
            return {"ok": False, "error": code, "detail": detail}

        if not isinstance(binding, SessionBinding):
            raise TypeError("execute() needs the server-made SessionBinding")
        if not tool_call_id or not isinstance(tool_call_id, str):
            return refuse("NO_CALL_ID", "a tool call needs a stable call_id")
        if name in REFUSED_TOOLS or _KILL_NAME.search(name or ""):
            return refuse("NEEDS_A_CARD", NEEDS_A_CARD)
        if name not in binding.allowed:
            return refuse("TOOL_NOT_ALLOWED", f"{name!r} is not available on this call")
        try:
            args = json.loads(arguments_json or "{}")
        except (TypeError, ValueError):
            return refuse("BAD_ARGUMENTS", "arguments are not valid JSON")
        if not isinstance(args, dict):
            return refuse("BAD_ARGUMENTS", "arguments must be a JSON object")
        # top-level keys only: the proxy's tools take no principal argument at any depth (it comes from _TEAM_TURN)
        if PRINCIPAL_KEYS & {k.lower() for k in args}:
            return refuse("PRINCIPAL_IN_ARGS", "the caller identity comes from the session, not from arguments")
        if name == "inject_message" and _PANE_COMMAND.match(str(args.get("message", ""))):
            return refuse("NOT_A_MESSAGE", "Typing a command into a seat is not a voice action.")
        fenced = binding.conversation_id in self._fenced
        if fenced and name not in self.executor.non_fleet and not self._spending_read(name):
            return refuse("FENCED", "After a lookup in this turn, actions wait for Shaw's next words. Tell him what you "
                          "found and ask.")
        if self.spending is not None and self.spending.owns(name):
            return self._spending_execute(binding, tool_call_id, name, args)
        if name in SPAWN_TOOLS:
            why = self._spawn_soft_refusal(binding, args)
            if why:
                return refuse(*why)
        if name == "answer_menu" and args.get("confirm") and not self._staged_menu_choice(binding, args):
            return refuse("CONFIRM_FIRST", "Stage the choice first (confirm=false), read it back to Shaw, and confirm "
                          "only after he says yes in his next turn.")

        def effect():
            text = str(self.executor(name, args, binding.principal, fenced=fenced,
                                     user_turns=[self.user_fn(binding.conversation_id)],
                                     call={"conversation_id": binding.conversation_id,
                                           "thread_id": self.thread_fn(binding.conversation_id)}))
            ok = action_ok(name, text)
            seat = args.get("session_name") if (name == "spawn_agent" and ok) else None
            # one receipt per spawn: a seat name can be reused (killed last week, spawned again today)
            task_id = f"{seat}#{tool_call_id}" if seat else None
            out = {"ok": ok, "result": text[:4000]}
            if task_id:
                out.update(task_id=task_id, seat=seat, receipt="accepted",
                           say="The seat was started and given the task. The task itself is not done yet.")
            return out, task_id

        precheck = (lambda conn: self._spawn_cap_refusal(conn, binding)) if name in SPAWN_TOOLS else None
        result, dup = self.ledger.run_once_external(tool_call_id, binding.conversation_id, binding.principal, name,
                                                    args, effect, precheck=precheck)
        if result.get("error") in ("SPAWN_CAP",):
            self.trace.append({"call_id": tool_call_id, "name": name, "decision": "refused", "error": "SPAWN_CAP",
                               "conversation_id": binding.conversation_id})
            return result
        self._fenced.add(binding.conversation_id)        # a result is in this turn now (G1')
        self.trace.append({"call_id": tool_call_id, "name": name, "decision": "duplicate" if dup else "executed",
                           "ok": result.get("ok"), "conversation_id": binding.conversation_id})
        return result

    def _spending_execute(self, binding, tool_call_id, name, args):
        """One spending tool, at most once per call_id (the ledger). The confirmation scope is this call + principal;
        the turn is the caller's current turn (new_turn), so a guard's question is answered on a later turn only."""
        conv = binding.conversation_id
        turn = self._turn_start.get(conv)

        live = {}

        def effect():
            live["out"] = self.spending.call(name, args, scope=f"{binding.principal}|{conv}",
                                             turn_key=("turn", turn), caller_words=self.user_fn(conv))
            # The ledger row keeps no amount, merchant, note or date: arg keys and the outcome only (G6). A repeated
            # call_id gets this stored stub back; the model already has the full result from the first run.
            return self.spending.ledger_result(live["out"]), None

        result, dup = self.ledger.run_once_external(tool_call_id, conv, binding.principal, name,
                                                    self.spending.ledger_args(args), effect)
        self._fenced.add(conv)
        self.trace.append({"call_id": tool_call_id, "name": name, "decision": "duplicate" if dup else "executed",
                           "ok": result.get("ok"), "conversation_id": conv})
        return live.get("out", result)

    def _spawn_soft_refusal(self, binding, args):
        if not read_back(self.spoken_fn(binding.conversation_id), args):
            return ("READ_BACK_FIRST", "Before starting a seat, say out loud which seat you will start and what it "
                    "will do, then call spawn_agent again.")
        return None

    def _staged_menu_choice(self, binding, args):
        """answer_menu(confirm=true) runs only for the exact choice (session, option, text) staged with confirm=false in
        an EARLIER turn of this call, so Shaw heard it read back and answered (re-review N5: a model-supplied confirm
        selected a seat's menu option, e.g. "Allow tmux kill-server?", in one call)."""
        start = self._turn_start.get(binding.conversation_id)
        if start is None:
            return False
        want = (str(args.get("session", "")), str(args.get("option", "")), args.get("text"))
        with self.ledger._connect() as c:
            rows = c.execute("SELECT args_json, result_json FROM tool_calls WHERE conversation_id = ? AND name = ?"
                             " AND created < ?", (binding.conversation_id, "answer_menu", start)).fetchall()
        for r in rows:
            a = json.loads(r["args_json"])
            if (not a.get("confirm")
                    and str(json.loads(r["result_json"]).get("result", "")).startswith("CONFIRM_NEEDED")
                    and (str(a.get("session", "")), str(a.get("option", "")), a.get("text")) == want):
                return True
        return False

    def _spawn_cap_refusal(self, conn, binding):
        """Inside the ledger's claim transaction (review M4). Every ATTEMPT counts (still running, failed, errored):
        only a precheck refusal is never stored. Counting only successes let a spawn whose result looked like a
        failure, or that half-started, escape the caps (re-review N3b/N7)."""
        def count(where, a):
            return conn.execute("SELECT COUNT(*) FROM tool_calls WHERE name IN ('spawn_agent', 'research') AND "
                                + where, a).fetchone()[0]
        if count("conversation_id = ?", (binding.conversation_id,)) >= self.spawn_per_session:
            return {"ok": False, "error": "SPAWN_CAP", "detail": f"at most {self.spawn_per_session} new seats per call"}
        if count("principal = ? AND created >= ?", (binding.principal, self.clock() - 86400)) >= self.spawn_per_day:
            return {"ok": False, "error": "SPAWN_CAP", "detail": f"at most {self.spawn_per_day} new seats per day from voice"}
        return None

    # ---- receipts the claim gate trusts ----------------------------------------------------------------------
    def backed(self, conversation_id, since, kind):
        """Does a receipt back a spoken claim? kind ("action", cls): a tool of THAT class succeeded in this conversation
        since `since` (epoch seconds, the turn start) OR within the last RECENT_RECEIPT_S; ("state", None): any tool
        succeeded. Call native-cea7bb2a33fc: remember_note ran, Shaw asked "did you save it?", and the true "I've saved"
        9 s later was blocked because the receipt predated his new turn."""
        since = min(since, time.time() - RECENT_RECEIPT_S)
        with self.ledger._connect() as c:
            rows = c.execute("SELECT name, result_json FROM tool_calls WHERE conversation_id = ? AND created >= ?",
                             (conversation_id, since)).fetchall()
        ok = {r["name"] for r in rows if json.loads(r["result_json"]).get("ok")}
        if kind and kind[0] == "action":
            if kind[1] in SPENDING_BACKS and self.spending is not None and any(self.spending.is_write(n) for n in ok):
                return True
            return bool(ok & CLAIM_BACKERS.get(kind[1], frozenset()))
        return bool(ok)


RECENT_RECEIPT_S = 60.0     # a receipt this fresh backs a claim across a turn boundary ("did you save it?" "I did")

# Which tools can back which spoken action (claim_gate classes). "stop" has none: no voice tool stops anything.
CLAIM_BACKERS = {
    "start": frozenset({"spawn_agent", "research", "gm_command"}),
    "message": frozenset({"inject_message", "agent_message", "send_telegram", "gm_command"}),
    "save": frozenset({"remember_note"}),
    "stop": frozenset(),
    # focus_entity only moves the screen's focus: it fixes nothing (review round 4)
    "fix": frozenset({"gm_command", "inject_message", "spawn_agent", "answer_menu", "research"}),
}
# A successful spending WRITE ("Spent $12 at Chipotle", "Marked Rent paid") backs "I've saved / added / updated it". A
# guard's question is not a success (ok false), so "I saved it" after a duplicate question stays unbacked.
SPENDING_BACKS = frozenset({"save", "fix"})


class Delivery:
    """Async results into a live native session, deduplicated on task_id (ported from the POC's Delivery). A tool the
    call handed over (server.ToolRunner) queues its late result here; the Driver sends each pending row once, and a
    row still pending when the call ends is taken for the call's thread instead (take_pending), never sent twice."""

    def __init__(self, ledger: ToolLedger):
        self.ledger = ledger

    def queue(self, task_id, conversation_id, text):
        """At most one row per task_id: a second queue of the same id is a no-op. Returns True when it was new."""
        with self.ledger._connect() as c:
            cur = c.execute("INSERT OR IGNORE INTO deliveries (task_id, conversation_id, text, state, updated)"
                            " VALUES (?,?,?,?,?)", (task_id, conversation_id, text, "pending", self.ledger.clock()))
            return cur.rowcount == 1

    def take_pending(self, conversation_id, to_state):
        """Every row of this call still pending, moved to `to_state` in one transaction (the call ended: they go to
        its thread, not into a session nobody hears). Returns the rows taken."""
        c = self.ledger._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            rows = [dict(r) for r in c.execute("SELECT * FROM deliveries WHERE conversation_id = ? AND state = ?"
                                               " ORDER BY updated, rowid", (conversation_id, "pending"))]
            c.execute("UPDATE deliveries SET state = ?, updated = ? WHERE conversation_id = ? AND state = 'pending'",
                      (to_state, self.ledger.clock(), conversation_id))
            c.execute("COMMIT")
            return rows
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    def pending(self, conversation_id):
        with self.ledger._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM deliveries WHERE conversation_id = ? AND state = ?"
                                               " ORDER BY updated, rowid", (conversation_id, "pending"))]

    def frames_for(self, conversation_id, user_speaking, assistant_speaking, kind=None):
        """kind: one append kind for every row (the server passes call_context.feed_kind(), the kind measured to be
        voiced); None = the POC's rule (thinking while Arturo speaks, else commentary)."""
        if user_speaking:
            return []
        if kind is not None:
            return [(d["task_id"], protocol._append(kind, d["text"])) for d in self.pending(conversation_id)]
        return [(d["task_id"], (protocol.thinking_append if assistant_speaking else protocol.commentary_append)(d["text"]))
                for d in self.pending(conversation_id)]

    def mark_sent(self, task_id, frame):
        with self.ledger._connect() as c:
            c.execute("UPDATE deliveries SET state = 'attempted', channel = ?, client_event_id = ?, updated = ?"
                      " WHERE task_id = ? AND state = 'pending'",
                      (frame["type"], frame["event_id"], self.ledger.clock(), task_id))

    def on_ack(self, client_event_id):
        with self.ledger._connect() as c:
            c.execute("UPDATE deliveries SET state = 'accepted', updated = ? WHERE client_event_id = ? AND"
                      " state = 'attempted'", (self.ledger.clock(), client_event_id))

    def states(self, conversation_id=None):
        q, a = "SELECT task_id, state FROM deliveries", ()
        if conversation_id is not None:
            q, a = q + " WHERE conversation_id = ?", (conversation_id,)
        with self.ledger._connect() as c:
            return {r["task_id"]: r["state"] for r in c.execute(q, a)}


# An ACTION is a success only in one of the proxy's own success shapes (review round 4: failure prefixes kept drifting;
# a DENIED Telegram send, "Not sent - Shaw has not asked...", backed "I texted you"). A tool with no entry (a lookup) is
# a success unless it starts with a failure shape. answer_menu's staging line (CONFIRM_NEEDED) sends nothing: not a
# success; the confirm check recognises a staged choice by that line itself.
_ACTION_OK = {
    "spawn_agent": re.compile(r"^(CONFIRMED: Agent|PARTIAL: Agent|Agent '[^']*' is running)"),
    "research": re.compile(r"^Task queued:"),
    "gm_command": re.compile(r"^GM response:"),
    "inject_message": re.compile(r"^Message injected into '[^']*' on \S+ \(verified"),
    "agent_message": re.compile(r"^Message sent to \S+: \["),
    "send_telegram": re.compile(r"^Message sent to Shaw on Telegram\."),
    "remember_note": re.compile(r"^Noted and saved: "),
    "answer_menu": re.compile(r"^Sent option "),
}
_SPAWN_TASK_LOST = "Warning: task injection failed."


def action_ok(name, text):
    shape = _ACTION_OK.get(name)
    if shape is None:
        return not _FAIL_PREFIX.match(text)
    return bool(shape.match(text)) and _SPAWN_TASK_LOST not in text
