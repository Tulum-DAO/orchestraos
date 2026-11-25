"""OFFLINE fixture mode (ios-watch-dev item 5, msg_0257a273): the production endpoint, $0, for grading the client end to
end without GPT-Live minutes. The test page's simulator (simulator.OfflineProvider) stands in for the provider; its
tool calls go to OfflineTools, which has its OWN ledger and canned results and can never reach a real tool."""
import asyncio
import json
import uuid

from . import protocol
from .tool_ledger import ToolLedger
from .tools import Delivery, SessionBinding

OFFLINE_SPECS = {   # the simulator's tool names (the POC mock fleet); nothing here maps to Arturo's real tools
    "fleet_status": ("Offline: the scripted fleet.", {"type": "object", "properties": {}}),
    "create_task": ("Offline: a scripted task receipt.", {"type": "object", "properties": {"title": {"type": "string"}}}),
    "get_task_status": ("Offline: a scripted task state.", {"type": "object", "properties": {"task_id": {"type": "string"}}}),
    "task_control": ("Offline: a scripted task change.", {"type": "object", "properties": {"task_id": {"type": "string"},
                                                                                           "action": {"type": "string"}}}),
}


class OfflineTools:
    def __init__(self, ledger: ToolLedger):
        self.ledger = ledger
        self.trace = []

    def bind(self, principal, device, conversation_id=None):
        return SessionBinding(principal=principal, device=device,
                              conversation_id=conversation_id or f"offline-{uuid.uuid4().hex[:12]}",
                              allowed=frozenset(OFFLINE_SPECS))

    def declared_tools(self, binding):
        return [protocol.function_tool(n, d, p, strict=False) for n, (d, p) in sorted(OFFLINE_SPECS.items())]

    def new_turn(self, conversation_id):
        pass

    def backed(self, conversation_id, since, kind):
        return True                                   # scripted lines; the gate still runs (hold, late, counts)

    def execute(self, binding, tool_call_id, name, arguments_json):
        if name not in OFFLINE_SPECS:
            self.trace.append({"call_id": tool_call_id, "name": name, "decision": "refused", "error": "TOOL_NOT_ALLOWED",
                               "conversation_id": binding.conversation_id})
            return {"ok": False, "error": "TOOL_NOT_ALLOWED", "detail": "offline mode has only the scripted tools"}
        try:
            args = json.loads(arguments_json or "{}")
        except ValueError:
            args = {}

        def effect(conn):
            if name == "fleet_status":
                return {"ok": True, "agents": [{"seat": "offline-seat", "status": "running", "doing": "a scripted demo"}],
                        "as_of": "offline"}, None
            if name == "create_task":
                tid = f"task_offline_{uuid.uuid4().hex[:8]}"
                return {"ok": True, "task_id": tid, "receipt": "accepted"}, None
            if name == "get_task_status":
                return {"ok": True, "task_id": args.get("task_id"), "receipt": "accepted"}, None
            return {"ok": True, "task_id": args.get("task_id"), "receipt": "cancelled"}, None

        result, dup = self.ledger.run_once(tool_call_id, binding.conversation_id, binding.principal, name, args, effect)
        self.trace.append({"call_id": tool_call_id, "name": name, "decision": "duplicate" if dup else "executed",
                           "ok": result.get("ok"), "conversation_id": binding.conversation_id})
        return result


class LocalTransport:
    """The session's transport in offline mode: the simulator's frames in, our frames to the simulator out."""

    def __init__(self, call):
        self.call = call
        self.inbox = []

    def send(self, payload):
        frame = json.loads(payload)
        if frame.get("type") != "session.input_audio.append":     # the simulator has no ASR
            asyncio.ensure_future(self.call.provider.on_client(frame))

    def recv(self):
        return self.inbox.pop(0) if self.inbox else None

    def close(self):
        pass


__all__ = ["OfflineTools", "LocalTransport", "Delivery"]
