"""The supervisor's process table, built from Settings. Nothing here spawns."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .settings import Settings


@dataclass
class ProcEntry:
    name: str
    kind: str                     # "service" (long-running, restarted) | "beat" (interval one-shot)
    argv: list
    cwd: str
    env: dict = field(default_factory=dict)
    port: Optional[int] = None
    interval: Optional[int] = None
    enabled: bool = True
    note: str = ""


def build_process_table(st: Settings) -> list:
    root = st.repo_root
    py = st.python_bin()
    cwd = str(root)
    beat_on = st.beat_enabled
    return [
        ProcEntry("gateway", "service", [py, str(root / "scripts" / "watch_gateway.py")], cwd,
                  port=st.gateway_port, note="approvals/inject gateway (scripts/watch_gateway.py)"),
        ProcEntry("api", "service", ["node", str(root / "api" / "dist" / "server.js")], cwd,
                  port=st.api_port, note="express API (api/dist/server.js)"),
        ProcEntry("dashboard", "service", ["node", str(root / "dashboard-proxy.js")], cwd,
                  port=st.dashboard_port, note="static dashboard + /api proxy + terminal ws"),
        ProcEntry("arturo", "service", ["bash", str(root / "services" / "arturo" / "run.sh")], cwd,
                  port=st.arturo_port, enabled=st.arturo_enabled,
                  note="voice brain (optional; [arturo] enabled)"),
        ProcEntry("bus_beat", "beat", [py, str(root / "scripts" / "lineage_daemon" / "bus_beat.py")], cwd,
                  interval=st.bus_beat_interval, enabled=beat_on, note="event-bus drain"),
        ProcEntry("boundary_delivery", "beat",
                  [py, str(root / "scripts" / "lineage_daemon" / "boundary_delivery.py")], cwd,
                  env={"BOUNDARY_DELIVER_ARMED": "1"},
                  interval=st.bus_beat_interval, enabled=beat_on and st.boundary_delivery_armed,
                  note="turn-boundary delivery (armed)"),
        ProcEntry("cron_beat", "beat", [py, str(root / "scripts" / "lineage_daemon" / "cron_beat.py")], cwd,
                  interval=st.cron_beat_interval, enabled=beat_on, note="blue-green rotation beat"),
        ProcEntry("router", "beat", [py, str(root / "scripts" / "message-router.py"), "--cron"], cwd,
                  interval=st.router_interval, enabled=st.router_enabled, note="msg_store delivery backstop"),
        # An ANSWERED card reaches its seat through this beat (verified inject into the seat's
        # live pane + durable msg_store row + watchdog); the reference install ran it from a
        # per-minute crontab and the supervisor had no equivalent (B1 finding 6). EXPIRE_PENDING=0:
        # cards never expire on their own (operator ruling).
        ProcEntry("approval_resume", "beat", [py, str(root / "scripts" / "approval_resume.py")], cwd,
                  env={"EXPIRE_PENDING": "0"}, interval=60, note="deliver answered approvals to their seat"),
    ]


def render_table(table: list) -> str:
    rows = [("NAME", "KIND", "ENABLED", "PORT/INTERVAL", "COMMAND")]
    for e in table:
        when = f":{e.port}" if e.kind == "service" else f"every {e.interval}s"
        rows.append((e.name, e.kind, "yes" if e.enabled else "no", when, " ".join(e.argv)))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    out = []
    for r in rows:
        out.append("  ".join(r[i].ljust(widths[i]) for i in range(4)) + "  " + r[4])
    return "\n".join(out)
