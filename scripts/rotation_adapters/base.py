"""The adapter interface a seat's rotation runs through, one adapter per runtime.

An adapter starts a fresh session of its runtime for a seat (seeded as its seed mode says), describes what that
session can do, reports how full its window is, and retires a session it started. The supervisor owns the timing, the
identity store and the chat; an adapter only knows its runtime.

Seed modes: "briefing" (a packet the supervisor attaches ahead of the next real input: works for any runtime) and
"seeded_file" (a session file written in the runtime's own format; not built yet).
"""
from __future__ import annotations

from dataclasses import dataclass, field


class RotationUnsupported(RuntimeError):
    """This runtime cannot be rotated (its adapter is not built and tested). The reason is shown as is."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class SessionHandle:
    runtime: str
    session_id: str
    proc: object = None                 # the running process (asyncio subprocess for the CLI adapters)
    argv: list = field(default_factory=list)
    started_at: float = 0.0
    meta: dict = field(default_factory=dict)


#: What a capabilities() description compares on (normalised, so a list order or a status word does not count).
CAPABILITY_FIELDS = ("model", "effort", "tools", "permission_mode", "skills", "agents", "commands", "cwd",
                     "mcp_servers", "output_style")


class Adapter:
    runtime = ""
    seed_mode = "briefing"
    #: Where the context goes before the switch: a startup-instructions slot of the runtime (Claude:
    #: "append-system-prompt-file"), or None, and then it rides on the first input instead.
    startup_slot = None

    async def start_session(self, seat: str, seed: dict) -> SessionHandle:
        raise RotationUnsupported(f"{self.runtime or 'this runtime'} rotation is not supported yet: "
                                  f"its adapter isn't built and tested")

    def capabilities(self, handle: SessionHandle | None = None) -> dict:
        """The session's init description, normalised (see CAPABILITY_FIELDS)."""
        return {}

    def usage(self) -> dict | None:
        """{"used": tokens, "window": tokens}, or None when the runtime does not say."""
        return None

    async def retire(self, handle: SessionHandle) -> int | None:
        """Stop a session this adapter started; its exit code once it has exited."""
        return None


def capability_diff(old: dict, new: dict, fields=CAPABILITY_FIELDS) -> dict:
    """{field: (old, new)} for every field both describe and that differs; a field one side does not know is not a
    difference (a configured description has no tool list, an init event has no settings path)."""
    out = {}
    for f in fields:
        a, b = old.get(f), new.get(f)
        if a is None or b is None:
            continue
        if f == "mcp_servers" and isinstance(a, dict) and isinstance(b, dict):
            # Like for like: a server connected before must be connected now; one that was not (failed, needs
            # auth) is not required, and a new one is no loss.
            lost = sorted(k for k, v in a.items() if v == "connected" and b.get(k) != "connected")
            if lost:
                out[f] = (a, b)
            continue
        if a != b:
            out[f] = (a, b)
    return out
