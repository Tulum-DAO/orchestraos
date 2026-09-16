#!/usr/bin/env python3
"""Continuity v4 — Phase B read-only drift SOAK runner (gm-approved, guardrailed).

One */30 cron invocation: import the live JSON planes into a THROWAWAY scratch
db, run the drift report, and append one dated JSONL line to the soak log. It
NEVER writes production: guardrails, in order —
  (1) kill-switch: exit immediately if ~/runtime/CV4_DRIFT_DISABLED exists.
  (2) read-only hard bind + PROOF: sha256 every source plane before AND after;
      if any byte changed, log ERROR and abort (a drift job that mutated its own
      source is disqualified from ever running).
  (3) scratch db in a tempdir, deleted after — the authority tasks.db is only
      opened via a COPY-less separate file; production tables are untouched.
  (4) append-only dated log: state/cv4-soak/<YYYY-MM-DD>.jsonl.

Shadow-only. Nothing arms. Exit 0 on success or disabled; non-zero only on the
read-only-violation abort (which must page a human).
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(os.environ.get("ORCHESTRA_DIR",
                           os.path.expanduser("~/scripts/agent-orchestra")))
KILL_SWITCH = Path(os.path.expanduser("~/runtime/CV4_DRIFT_DISABLED"))
SOAK_DIR = ORCH / "state" / "cv4-soak"

sys.path.insert(0, str(ORCH / "scripts"))


def _source_planes() -> list[Path]:
    planes = [ORCH / "registry.json", ORCH / "state" / "agent-sessions.json"]
    adir = ORCH / "state" / "agents"
    if adir.is_dir():
        planes += sorted(adir.glob("*.json"))
    return [p for p in planes if p.exists()]


def _hash_all(paths) -> dict:
    out = {}
    for p in paths:
        try:
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception:
            out[str(p)] = None
    return out


def _log(record: dict) -> Path:
    SOAK_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logf = SOAK_DIR / f"{day}.jsonl"
    with open(logf, "a") as f:                    # append-only
        f.write(json.dumps(record, default=str) + "\n")
    return logf


def _current_epoch_id():
    """Read-only lookup of the active soak epoch so each record is tagged; only
    records under the open (fenced) epoch count toward the 7-day gate. Never
    raises — a missing table/db yields None (soak stays read-only + robust)."""
    try:
        from continuity import soak_epoch as SE
        ep = SE.current_open_epoch_safe(str(ORCH / "state" / "tasks.db"))
        return ep["epoch_id"] if ep else None
    except Exception:
        return None


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    epoch_id = _current_epoch_id()

    # (1) kill-switch
    if KILL_SWITCH.exists():
        _log({"ts": now, "status": "disabled", "reason": str(KILL_SWITCH),
              "soak_epoch_id": epoch_id})
        return 0

    from continuity import store as S, projections as P

    planes = _source_planes()
    before = _hash_all(planes)                    # (2) pre-hash

    scratch_dir = tempfile.mkdtemp(prefix="cv4-soak-")
    scratch_db = os.path.join(scratch_dir, "shadow.db")
    try:
        S.ensure_schema(scratch_db)
        summary = S.import_json(db_path=scratch_db, orchestra_dir=str(ORCH))
        drift = P.drift_report(db_path=scratch_db, orchestra_dir=str(ORCH))

        after = _hash_all(planes)                 # (2) post-hash PROOF
        mutated = [p for p in before if before[p] != after.get(p)]
        if mutated:
            rec = {"ts": now, "status": "READ_ONLY_VIOLATION",
                   "mutated": mutated, "soak_epoch_id": epoch_id}
            _log(rec)
            print("FATAL: soak run mutated source planes:", mutated,
                  file=sys.stderr)
            return 2

        record = {
            "ts": now, "status": "ok",
            "soak_epoch_id": epoch_id,
            "seats": summary["seats"],
            "conflicts_surfaced": summary["conflicts"],
            "drift_explained_by_conflict": drift["explained_by_conflict"],
            "drift_unexplained": len(drift["unexplained"]),
            "unexplained_sample": drift["unexplained"][:10],
            "source_byte_identical": True,
        }
        logf = _log(record)
        print(json.dumps({k: record[k] for k in
                          ("seats", "conflicts_surfaced",
                           "drift_unexplained")},
                         default=str), "->", logf)
        return 0
    finally:
        try:
            os.remove(scratch_db)
        except OSError:
            pass
        try:
            os.rmdir(scratch_dir)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
