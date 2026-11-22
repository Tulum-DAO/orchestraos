"""Where the operator is in onboarding, as server state (DEC-1791511578959986).

The operator, 2026-10-09: "Arturo should always know how far along in the onboarding a user is in
case they leave part way through and come back." The operator facts (operator_store) already hold
their name and devices, and the team is seen live (starter_team_state). This file holds the rest,
written only by server code, never by the brain:

  onboarding_conversation   the onboarding thread. Every browser and device opens THIS one while
                            onboarding is not finished, so leaving and coming back resumes it.
  team_declined_at          the operator said "not now" to the starter team on a dashboard turn.
  fresh_at                  when Arturo first looked at this install without onboarding.json and
                            found it fresh (no evidence of an earlier install). From then on this is
                            a first run in progress: a team or a name that appears later is the
                            onboarding's own doing, never evidence of an upgrade.
  rerun                     set by hand to run onboarding again on an install that is not fresh.

Which devices are paired is read from the device store (Arturo's own codes), never stored here.
Writes hold <file>.lock (flock) so `orchestra init` and a running proxy never lose each other's key.
Reset: delete this file together with onboarding.json (docs/ARTURO.md).

Onboarding is finished when <ARTURO_STATE>/onboarding.json exists. That flag arrived with the
first-run feature, so an install that predates it has no flag although it is plainly not a first
run (orchestra-builder D-7: an upgraded install re-ran onboarding, offered a starter team for a
week, and its playbook sent a /clear to an agent). `ensure_onboarded` decides once, the first time
it sees no flag: positive evidence of an earlier install writes the flag with reason
"upgrade-inferred"; none marks the install fresh (fresh_at) and onboarding runs as it always did.
"""
from __future__ import annotations

import json
import os
import contextlib
import fcntl
import tempfile
import threading
import time
from pathlib import Path

# Every write is read-modify-write: one at a time, or two openers (or an opener and a decline) lose an
# update or both "win" the pin. The thread lock covers one process; the flock (<file>.lock) covers
# `orchestra init` deciding while a proxy pins (#603 review F3: losing fresh_at is a one-way door).
_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked(state_dir):
    with _LOCK:
        p = _path(state_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p) + ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

FILENAME = "onboarding-progress.json"
PAIR_MINTER = "arturo-onboarding"          # arturo-proxy.py _PAIR_MINTER


def _path(state_dir) -> Path:
    return Path(state_dir) / FILENAME


def read(state_dir) -> dict:
    try:
        rec = json.loads(_path(state_dir).read_text())
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(state_dir, rec):
    p = _path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=FILENAME + ".", suffix=".tmp", dir=p.parent)   # never a shared tmp
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(rec) + "\n")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def pin_conversation(state_dir, conversation_id) -> str:
    """The onboarding thread is the first one an opener ran in; later calls keep it. Returns the
    pinned id (which may be another browser's, when two opened at once)."""
    with _locked(state_dir):
        rec = read(state_dir)
        if not rec.get("onboarding_conversation") and conversation_id:
            rec["onboarding_conversation"] = str(conversation_id)
            _write(state_dir, rec)
        return rec.get("onboarding_conversation") or ""


def conversation(state_dir) -> str:
    return str(read(state_dir).get("onboarding_conversation") or "")


def set_team_declined(state_dir, when=None):
    with _locked(state_dir):
        rec = read(state_dir)
        rec["team_declined_at"] = float(when if when is not None else time.time())
        _write(state_dir, rec)


def clear_team_declined(state_dir):
    with _locked(state_dir):
        rec = read(state_dir)
        if rec.pop("team_declined_at", None) is not None:
            _write(state_dir, rec)


def team_declined(state_dir) -> bool:
    return bool(read(state_dir).get("team_declined_at"))


def paired(device_records, devices=("iPhone", "iPad", "Mac")) -> dict:
    """{device: 'connected' | 'code not used yet'} for the codes Arturo made (label
    "<device> (arturo)", lowercase), ignoring revoked ones. Connected wins over an unused code.
    Names and states only: never a code, token or device id."""
    by_label = {f"{d.lower()} (arturo)": d for d in devices}
    out = {}
    for rec in device_records or []:
        if rec.get("minted_by") != PAIR_MINTER or rec.get("revoked_at"):
            continue
        device = by_label.get(str(rec.get("label") or "").strip().lower())
        if device is None:
            continue
        if rec.get("last_seen_at"):
            out[device] = "connected"
        else:
            out.setdefault(device, "code not used yet")
    return {d: out[d] for d in devices if d in out}       # a fixed order, not the store's


# ---- onboarded, and the upgrade inference (orchestra-builder D-7) --------------------------------
FLAG = "onboarding.json"
UPGRADE_REASON = "upgrade-inferred"


def flag_path(state_dir) -> Path:
    return Path(state_dir) / FLAG


def write_flag(state_dir, rec) -> bool:
    """Write onboarding.json whole, or not at all, and never over an existing one: the record goes to a
    private temp file first and is hard-linked into place, which fails when the flag already exists
    (os.replace would overwrite it). True when this call wrote it."""
    p = flag_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=FLAG + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, p)
            return True
        except FileExistsError:
            return False
        except OSError:
            # No hard links on this filesystem (exFAT, SMB, some FUSE): create-exclusive still refuses
            # an existing flag, and the record is small enough to land in one write (#603 review F1).
            data = open(tmp).read()
        try:
            fd2 = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd2, "w") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    finally:
        Path(tmp).unlink(missing_ok=True)


def _read_json(path):
    """("absent" | "ok" | "unreadable", value)."""
    try:
        return "ok", json.loads(Path(path).read_text())
    except FileNotFoundError:
        return "absent", None
    except (OSError, ValueError):
        return "unreadable", None


def upgrade_evidence(orchestra_dir, state_dir, pinned_conversation="") -> tuple:
    """(evidence, unreadable): what says this install existed before onboarding did, and which sources
    could not be read. POSITIVE evidence only: a missing file is a fresh install's normal state, and an
    unreadable or malformed one proves nothing either way.

    - registry.json (the data dir's, as starter_team_state reads it) has gm as a T0 seat. That is also
      every starter_team_state reading of "present" or "incomplete": both need a T0 gm.
    - starter_team_state would read "other_manager": a T0 seat not named gm.
    - operator.json holds a name, unless it was learned on the pinned onboarding thread (an install
      whose onboarding began before fresh_at existed: that name is the onboarding's own)."""
    evidence, unreadable = [], []
    status, reg = _read_json(Path(orchestra_dir) / "registry.json")
    agents = reg.get("agents", {}) if isinstance(reg, dict) else None
    if status == "unreadable" or (status == "ok" and not isinstance(agents, dict)):
        unreadable.append("registry.json")
    elif status == "ok":
        managers = sorted(n for n, r in agents.items()
                          if isinstance(r, dict) and str(r.get("tier") or "").upper() == "T0")
        if "gm" in managers:
            evidence.append("registry: gm is a T0 seat (starter team present or incomplete)")
        elif managers:
            evidence.append(f"starter team state: other_manager ({managers[0]} is T0)")
    status, op = _read_json(Path(state_dir) / "operator.json")
    facts = op.get("facts") if isinstance(op, dict) else None
    if status == "unreadable" or (status == "ok" and not isinstance(facts, dict)):
        unreadable.append("operator.json")
    elif status == "ok":
        name = facts.get("name")
        if isinstance(name, dict) and str(name.get("value") or "").strip():
            if not (pinned_conversation and name.get("source_thread") == pinned_conversation):
                evidence.append("operator.json: a name is saved")
    return evidence, unreadable


def _progress_status(state_dir):
    status, rec = _read_json(_path(state_dir))
    if status == "ok" and not isinstance(rec, dict):
        status = "unreadable"
    return status, (rec if status == "ok" else {})


def ensure_onboarded(state_dir, orchestra_dir, now=None) -> dict:
    """Is onboarding finished on this install? {"onboarded", "how", "evidence", "unreadable"}, how one of:
    flag (onboarding.json exists: finished, or inferred earlier), inferred (written now), fresh (a first
    run, marked or in progress), undecided (a source is unreadable and nothing else proves an upgrade:
    onboarding runs, and the next call looks again)."""
    out = {"onboarded": False, "how": "fresh", "evidence": [], "unreadable": []}
    if flag_path(state_dir).exists():
        return {**out, "onboarded": True, "how": "flag"}
    pstatus, rec = _progress_status(state_dir)
    if pstatus != "unreadable" and (rec.get("fresh_at") is not None or rec.get("rerun")):
        return out
    evidence, unreadable = upgrade_evidence(orchestra_dir, state_dir,
                                            str(rec.get("onboarding_conversation") or ""))
    if pstatus == "unreadable":
        unreadable = [FILENAME] + unreadable      # evidence from another source still wins (F6)
    if evidence:
        when = float(now if now is not None else time.time())
        try:
            write_flag(state_dir, {"reason": UPGRADE_REASON, "evidence": evidence, "inferred_at": when})
        except OSError:
            pass                     # still not a first run; the next call writes it
        return {**out, "onboarded": True, "how": "inferred", "evidence": evidence, "unreadable": unreadable}
    if unreadable:
        return {**out, "how": "undecided", "unreadable": unreadable}
    try:
        with _locked(state_dir):
            pstatus, rec = _progress_status(state_dir)
            if pstatus in ("ok", "absent") and rec.get("fresh_at") is None:
                rec["fresh_at"] = float(now if now is not None else time.time())
                _write(state_dir, rec)
    except OSError:
        pass                         # unmarked: the next call decides again, the same way
    return out


def describe(state_dir, orchestra_dir) -> tuple:
    """READ-ONLY (orchestra doctor): (state, detail). state is "present", "inferred-on-upgrade",
    "fresh-not-onboarded", "will-infer" (no flag yet, evidence found: Arturo writes it on its next
    read) or "undecided"."""
    status, flag = _read_json(flag_path(state_dir))
    if status != "absent":
        if isinstance(flag, dict) and flag.get("reason") == UPGRADE_REASON:
            ev = "; ".join(str(e) for e in flag.get("evidence") or []) or "no evidence recorded"
            return "inferred-on-upgrade", f"{flag_path(state_dir)}: {UPGRADE_REASON} ({ev})"
        return "present", f"{flag_path(state_dir)}: onboarding finished"
    pstatus, rec = _progress_status(state_dir)
    if pstatus == "unreadable":
        evidence, _ = upgrade_evidence(orchestra_dir, state_dir)
        return "undecided", (f"no {FLAG}; {_path(state_dir)} is unreadable" +
                             (f"; Arturo still writes the flag ({UPGRADE_REASON}) on its next read: " + "; ".join(evidence)
                              if evidence else ", so onboarding runs"))
    if rec.get("rerun"):
        return "fresh-not-onboarded", f"no {FLAG}; onboarding re-run requested ({FILENAME} rerun)"
    if rec.get("fresh_at") is not None:
        return "fresh-not-onboarded", f"no {FLAG}; a first run (found fresh, no earlier install)"
    evidence, unreadable = upgrade_evidence(orchestra_dir, state_dir, str(rec.get("onboarding_conversation") or ""))
    if evidence:
        return "will-infer", f"no {FLAG} yet; Arturo writes it ({UPGRADE_REASON}) on its next read: " + "; ".join(evidence)
    if unreadable:
        return "undecided", f"no {FLAG}; cannot read {', '.join(unreadable)}, so onboarding runs until it can"
    return "fresh-not-onboarded", f"no {FLAG}; no earlier install found, so onboarding runs"
