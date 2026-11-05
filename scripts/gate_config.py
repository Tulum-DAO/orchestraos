"""gate_config.py — the safety gate's files under <data>/state/attention/ (attention part 2a, plan §2.2 / §5.4).

Stdlib only and cheap to import: the PreToolUse hook (hooks/gate-hook.py) imports it on every call that is not
killed by GATE_OFF.

  gate.json        the switches the hook reads on every call: mode (off | observe | enforce), person, gateway_url,
                   loopback_hosts, the person's `default` and an `agents` snapshot of each agent's resolved settings
                   (the fallback when the DB cannot be read), gen, updated_at, v. Written atomically (tmp +
                   os.replace, 0600) by the gateway and by `orchestra gate mode`. Missing or unreadable -> mode off.
  [gate] default_mode in orchestra.toml
                   the mode the gateway writes the first time it starts with no mode set (off | observe |
                   enforce). Missing or bad -> "off": the gate ships off and an operator opts in, here or with
                   `orchestra gate mode enforce`. A mode already in gate.json is never overwritten by it.
  GATE_OFF         the instant kill switch: present -> the hook allows everything and writes nothing.
  gate-log.jsonl   one redacted line per classified call (calls with hits only): the decision and the per-level
                   would_hold tuning data. Rotated to gate-log.jsonl.1 at 5 MB.
  gate-seen/<agent>.json
                   the coverage heartbeat {session_id, ts, mode, v}, rewritten at most once a minute per agent (or
                   at once when the session or the mode changed).
"""
from __future__ import annotations

import json
import os
import re
import time

V = 1
MODES = ("off", "observe", "enforce")
LEVELS = ("watch_closely", "check_in", "let_them_run")
BUILTIN = {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0}
LOG_MAX_BYTES = 5 * 1024 * 1024
HEARTBEAT_EVERY_S = 60
DETAIL_MAX = 300
_GEN_SUFFIX = re.compile(r"-gen\d+$")


def paths(data_dir) -> dict:
    """Every gate file for a data dir: dir, gate, kill, log, log_old, seen_dir."""
    d = os.path.join(str(data_dir), "state", "attention")
    return {"dir": d, "gate": os.path.join(d, "gate.json"), "kill": os.path.join(d, "GATE_OFF"),
            "log": os.path.join(d, "gate-log.jsonl"), "log_old": os.path.join(d, "gate-log.jsonl.1"),
            "seen_dir": os.path.join(d, "gate-seen")}


def kill_switch(data_dir) -> bool:
    """True when <data>/state/attention/GATE_OFF exists (the hook then allows everything)."""
    return os.path.exists(os.path.join(str(data_dir), "state", "attention", "GATE_OFF"))


def default_mode(raw) -> str:
    """`[gate] default_mode` from `raw` (a parsed orchestra.toml; the gateway passes its config).
    Missing, bad or not a valid mode -> "off". Never raises. Not used on the hook's per-call path."""
    sec = raw.get("gate") if isinstance(raw, dict) else None
    mode = sec.get("default_mode") if isinstance(sec, dict) else None
    return mode if mode in MODES else "off"


def read_gate_raw(data_dir) -> dict | None:
    """The parsed gate.json, or None when it is missing, unreadable or not an object (the gateway's
    "is a mode set yet?" check at startup)."""
    try:
        with open(paths(data_dir)["gate"]) as fh:
            d = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def read_gate(data_dir) -> dict:
    """gate.json with a valid mode. Missing, unreadable or a bad mode -> {"mode": "off"} (the gate does nothing)."""
    d = read_gate_raw(data_dir)
    if d is None or d.get("mode") not in MODES:
        return {"mode": "off"}
    return d


def write_gate(data_dir, **fields) -> dict:
    """Merge `fields` over the current gate.json (a missing or broken file starts empty) and write it atomically
    with mode 0600. Stamps v and updated_at. Raises ValueError for a bad mode and OSError when it cannot write.
    Returns what was written."""
    if "mode" in fields and fields["mode"] not in MODES:
        raise ValueError(f"bad mode {fields['mode']!r}")
    p = paths(data_dir)
    cur = read_gate_raw(data_dir) or {}
    cur.update(fields)
    cur["v"] = V
    cur["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(p["dir"], exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=p["dir"], prefix=".gate.", suffix=".tmp")
    try:
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, json.dumps(cur, indent=1, sort_keys=True).encode())
        finally:
            os.close(fd)
        os.replace(tmp, p["gate"])
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return cur


def base_agent(agent: str) -> str:
    return _GEN_SUFFIX.sub("", agent or "")


def settings_for(gate: dict, agent: str) -> dict:
    """The snapshot settings for one agent: agents[agent] (exact, then -genN stripped), else `default`, else the
    built-in Let them run. Always {level, high_risk (list), hold_wait_s (int), role}."""
    agents = gate.get("agents") if isinstance(gate.get("agents"), dict) else {}
    row = agents.get(agent)
    if not isinstance(row, dict):
        row = agents.get(base_agent(agent))
    if not isinstance(row, dict):
        row = gate.get("default") if isinstance(gate.get("default"), dict) else {}
    level = row.get("level") if row.get("level") in LEVELS else BUILTIN["level"]
    hr = row.get("high_risk")
    hr = [c for c in hr if isinstance(c, str)] if isinstance(hr, list) else []
    try:
        wait = max(0, int(row.get("hold_wait_s") or 0))
    except (TypeError, ValueError):
        wait = 0
    return {"level": level, "high_risk": hr, "hold_wait_s": wait, "role": row.get("role")}


def project_key(cwd: str | None, data_dir) -> str | None:
    """supervision_store.project_key without its imports (the hook's hot path): a cwd under
    <data>/projects/<name>/… -> the realpath of <data>/projects/<name>; any other cwd -> its own realpath."""
    if not cwd:
        return None
    real = os.path.realpath(cwd)
    root = os.path.realpath(os.path.join(str(data_dir), "projects"))
    if os.path.isabs(real) and real != root:
        try:
            under = os.path.commonpath([os.path.normpath(real), os.path.normpath(root)]) == os.path.normpath(root)
        except ValueError:
            under = False
        if under:
            return os.path.join(root, os.path.relpath(real, root).split(os.sep)[0])
    return real


# ---- the gate log -------------------------------------------------------------------------------------------------

def _redact(text) -> str:
    try:
        from gate_classify import redact
        return redact(str(text))
    except Exception:  # noqa: BLE001 — never lose the line over redaction; truncate at least
        s = " ".join(str(text).split())
        return s[:DETAIL_MAX]


def log_line(data_dir, rec: dict) -> None:
    """Append one JSON line to gate-log.jsonl (redacted `detail`, a `ts` when missing); rotate to .1 at 5 MB.
    Never raises."""
    try:
        p = paths(data_dir)
        rec = dict(rec)
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if "detail" in rec and rec["detail"] is not None:
            rec["detail"] = _redact(rec["detail"])
        line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
        try:
            if os.stat(p["log"]).st_size >= LOG_MAX_BYTES:
                os.replace(p["log"], p["log_old"])
        except FileNotFoundError:
            os.makedirs(p["dir"], exist_ok=True)
        fd = os.open(p["log"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001
        pass


def read_log(data_dir, limit: int = 200, agent: str | None = None) -> list:
    """The newest `limit` log lines (oldest first), optionally for one agent (-genN stripped on both sides);
    reads the rotated file too when the current one is short."""
    p = paths(data_dir)
    out = []
    for f in (p["log_old"], p["log"]):
        try:
            with open(f) as fh:
                for ln in fh:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if agent and base_agent(str(r.get("agent") or "")) != base_agent(agent):
                        continue
                    out.append(r)
        except OSError:
            continue
    return out[-max(0, int(limit)):] if limit else out


# ---- coverage heartbeat -----------------------------------------------------------------------------------------

def _safe_name(agent: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", agent or "")[:120] or "_"


def heartbeat(data_dir, agent: str, session_id, mode: str, now: float | None = None) -> bool:
    """Write gate-seen/<agent>.json = {session_id, ts, mode, v} unless the file is under a minute old and already
    says the same session and mode. True when written. Never raises."""
    try:
        now = time.time() if now is None else now
        d = paths(data_dir)["seen_dir"]
        f = os.path.join(d, _safe_name(agent) + ".json")
        try:
            if now - os.stat(f).st_mtime < HEARTBEAT_EVERY_S:
                with open(f) as fh:
                    cur = json.load(fh)
                if cur.get("session_id") == session_id and cur.get("mode") == mode:
                    return False
        except (OSError, ValueError, AttributeError):
            pass
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".seen.{os.getpid()}.{time.monotonic_ns()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            try:
                os.write(fd, json.dumps({"session_id": session_id, "ts": now, "mode": mode, "v": V}).encode())
            finally:
                os.close(fd)
            os.replace(tmp, f)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:  # noqa: BLE001
        return False


def seen(data_dir) -> dict:
    """{agent: {session_id, ts, mode, v}} from gate-seen/ (unreadable files skipped)."""
    d = paths(data_dir)["seen_dir"]
    out = {}
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json") or n.startswith("."):
            continue
        try:
            with open(os.path.join(d, n)) as fh:
                r = json.load(fh)
            if isinstance(r, dict):
                out[n[:-5]] = r
        except (OSError, ValueError):
            continue
    return out
