#!/usr/bin/env python3
"""ingest_beat.py: save every terminal (tmux) seat's chat into the conversation index, once a minute.

A headless seat is saved by its supervisor after each message; a seat that runs in a terminal has no supervisor, so
this beat does it. For each registered Claude seat that is not headless, live, and has a session:
  - the active-session rule: the index's active session for the seat must be the one the registry names (the
    identity store's projection, the truth for a terminal seat); when it is not (never registered, or the seat moved
    to a new session), that session is registered as the active one first;
  - one incremental pass (episodic_memory.ingest_tick) under the seat's ingest lock, which the rotation export and a
    supervisor's save also take; a seat whose lock is held is skipped this minute, never waited on;
  - a turn in progress is saved up to its last complete line (the rest at the next minute);
  - the transcript is found through the path the store's cursor already holds (locate_cached), and searched for
    only when the session changes or its file is gone.
One JSON line per run on stdout (the launcher logs it). Never raises for one seat: its error is in the line.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import episodic_memory as EM  # noqa: E402
import headless_switch as HS  # noqa: E402


def terminal_seats(orch_dir=None) -> list[dict]:
    """Live, registered Claude seats that run in a terminal, each with the session the registry names."""
    try:
        agents = json.loads((Path(HS.data_dir(orch_dir)) / "registry.json").read_text()).get("agents") or {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for seat, row in agents.items():
        if not isinstance(row, dict) or not row.get("session_id"):
            continue
        if str(row.get("status") or "") in ("retired", "parked"):
            continue
        if str(row.get("runtime") or "claude").lower() != "claude":
            continue
        try:
            if HS.is_headless(seat, orch_dir=orch_dir):
                continue                                   # its supervisor saves it
        except Exception:  # noqa: BLE001 — unreadable switch: treat as a terminal seat
            pass
        out.append({"seat": seat, "session_id": str(row["session_id"]), "model": row.get("model")})
    return out


def save_seat(s: dict, em=EM, db_path=None) -> dict:
    seat, sid = s["seat"], s["session_id"]
    res = {"seat": seat, "session_id": sid}
    t0 = time.monotonic()
    try:
        act = (em.active_session(seat, db_path=db_path) or {}).get("provider_session_id")
        if act != sid:
            em.register_session(seat, "claude", s.get("model"), sid, em.generation_of(sid), None, db_path=db_path)
            res["registered"] = True
            res["was_active"] = act
        got = em.ingest_tick(seat, db_path=db_path, wait_s=0)
        res.update(added=got.get("added"), lag_bytes=got.get("lag_bytes"),
                   **({"skipped": got["skipped"]} if got.get("skipped") else {}),
                   **({"errors": [x.get("error") for x in got.get("sessions") or [] if x.get("error")]}
                      if any(x.get("error") for x in got.get("sessions") or []) else {}))
    except Exception as e:  # noqa: BLE001 — one seat never stops the others
        res["error"] = f"{type(e).__name__}: {e}"
    res["ms"] = round((time.monotonic() - t0) * 1000, 1)
    return res


def run(orch_dir=None, em=EM, db_path=None) -> dict:
    t0 = time.monotonic()
    seats = [save_seat(s, em=em, db_path=db_path) for s in terminal_seats(orch_dir)]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seats": seats,
            "ms": round((time.monotonic() - t0) * 1000, 1)}


def main() -> int:
    print(json.dumps(run(os.environ.get("ORCHESTRA_DIR") or None)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
