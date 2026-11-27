"""codex_watch: is Codex serving? The reviewer rule flips itself both ways (run by limit_watch --codex-watch).

The operator, 2026-11-26: "codex usage available again (I shouldn't have to tell you)". Every watcher
run takes the readings it can get for free, plus a probe when one is due, and decides:
  * readings, newest evidence first:
      - a Codex limit event from this run's rollout scan (a seat hit the limit): OUT, window named;
      - the Codex usage reading (state/usage/api-codex-default.json, usage_poll): a window at 100% is
        OUT, otherwise OK; older than 15 min is no reading;
      - a probe: `codex exec --ephemeral` asking for a one-word reply, in a clean environment (no
        seat identity, hooks off, read-only sandbox, outside any git tree). A limit text in its
        output is OUT with the window named; a normal reply is OK; anything else (timeout, not
        logged in, not installed) is UNKNOWN and changes nothing.
    The probe runs every run while Codex is OUT or the status is not known yet (an OUT probe costs
    nothing, and the way back is seen within two runs), and every OK_PROBE_S while OK: the usage
    reading cannot show a workspace out of credits, so the slow probe is the cross-check.
  * one reading per run: the probe when it ran (a live call outranks a file), else OUT before OK.
  * debounce: two consecutive readings that disagree with the current status flip it; an UNKNOWN
    reading neither counts nor resets the streak.
  * on a flip: state/codex-availability.json is rewritten (status, window, reset, since, evidence,
    gating_reviewer), every review-consuming seat gets ONE line, and gm one mail per episode edge.
    LANDING_GATE.md's reviewer order is read from that file.
  * a second login (gm order, 2026-11-26): when the default login (~/.codex) is OUT, the probe tries each
    parked codex login under state/account-vault/codex-*/ (or CODEX_WATCH_ALT_HOMES, colon-separated)
    with CODEX_HOME set, and the first one that serves makes the reading OK "via <its email>". The
    availability file then names it in codex_home, so a review gate runs `codex exec` with that
    CODEX_HOME and the running codex panes are untouched. While a parked login serves, the default
    login's own limit readings (rollout, usage) change nothing; only a live probe can flip it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import base64
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import limit_event as LE

DEFAULT_RECIPIENTS = ["orchestraos-builder", "orchestra-builder", "arturo-context-dev", "avatar-dev",
                      "postcall-dev", "memory-shadow-dev", "pm-nod"]
FLIP_AFTER = 2                 # consecutive readings before a flip
OK_PROBE_S = 30 * 60           # while OK: one probe per 30 min (a probe spends tokens)
USAGE_FRESH_S = 15 * 60
PROBE_TIMEOUT_S = 90
PROBE_PROMPT = "Reply with the single word: ok"
# a seat's identity in the environment makes the fleet hooks treat the probe as that seat
_IDENTITY_ENV = re.compile(r"^(TMUX|TMUX_PANE|STY|AGENT_.*|ORCHESTRA_AGENT.*|ORCHESTRA_SEAT.*|CLAUDE.*|"
                           r"CODEX_THREAD.*|CODEX_SESSION.*|CSH_.*|PARENT_AGENT.*)$")
_ERR_PREFIX = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?:ERROR|Error|error)\s*:\s*")
_OUT_ANYWHERE = re.compile(r"out of credits|usage limit|rate limit reached|insufficient_quota", re.I)


@dataclass
class Reading:
    status: str                  # ok | out | unknown
    window: str | None           # credits | 5-hour | weekly | unknown, when out
    reset_at: int | None
    source: str                  # rollout | usage | probe
    evidence: str
    codex_home: str | None = None  # the parked login that served this probe; None = the default login


# --- 1. readings ---------------------------------------------------------------------------------

def classify_probe(rc, stdout, stderr, *, now=None) -> Reading:
    """One probe's output. A limit text anywhere it is printed is OUT, whatever the exit code."""
    when = datetime.fromtimestamp(now or time.time(), timezone.utc)
    lines = [_ERR_PREFIX.sub("", l) for l in f"{stdout or ''}\n{stderr or ''}".splitlines()]
    ev = LE.from_text("\n".join(lines), now=when)
    if ev is not None:
        return Reading("out", ev.window, ev.reset_at, "probe", ev.text[:200])
    if rc not in (0, None):
        hit = next((l.strip() for l in lines if _OUT_ANYWHERE.search(l)), None)
        if hit:
            return Reading("out", "unknown", None, "probe", hit[:200])
        tail = next((l.strip() for l in reversed(lines) if l.strip()), "")
        return Reading("unknown", None, None, "probe", f"rc={rc}: {tail[:160]}")
    if (stdout or "").strip():
        return Reading("ok", None, None, "probe", f"reply: {(stdout or '').strip()[:40]}")
    return Reading("unknown", None, None, "probe", "rc=0 with no reply")


def classify_usage(u, now) -> Reading | None:
    """usage_poll's Codex reading: a window at 100% is OUT; a stale or missing reading is no reading."""
    if not u or now - float(u.get("as_of") or 0) > USAGE_FRESH_S:
        return None
    w = u.get("windows") or {}
    for key, name in (("weekly", "weekly"), ("5h", "5-hour")):
        win = w.get(key) or {}
        if (win.get("used_pct") or 0) >= 100:
            return Reading("out", name, win.get("resets_at"), "usage", f"{name} used_pct {win.get('used_pct')}")
    if not w:
        return None
    five, week = (w.get("5h") or {}).get("used_pct"), (w.get("weekly") or {}).get("used_pct")
    return Reading("ok", None, None, "usage", f"5-hour {five}%, weekly {week}%")


def from_events(events, now) -> Reading | None:
    """A Codex limit event from this run's rollout scan."""
    for ev in events or ():
        if getattr(ev, "runtime", None) == "codex":
            return Reading("out", ev.window, ev.reset_at, "rollout", (ev.text or LE.describe(ev))[:200])
    return None


# --- the probe -----------------------------------------------------------------------------------

def probe_env(base=None) -> dict:
    env = dict(os.environ if base is None else base)
    return {k: v for k, v in env.items() if not _IDENTITY_ENV.match(k)}


def probe_argv(codex) -> list:
    return [codex, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
            "-c", "features.hooks=false", "-c", 'model_reasoning_effort="low"', PROBE_PROMPT]


def should_probe(state, now, fresh_usage) -> bool:
    since = now - float(state.get("last_probe") or 0)
    if state.get("status") != "ok":
        return True
    s = state.get("streak") or {}
    if s.get("count") and s.get("status") != state.get("status"):
        return True        # a flip is pending: confirm it now, or a low usage reading cancels it every run
    return since >= OK_PROBE_S


def alt_homes(vault=None) -> list:
    """The parked codex logins to try when the default one is out: CODEX_WATCH_ALT_HOMES, else every
    state/account-vault/codex-*/ that holds an auth.json."""
    env = os.environ.get("CODEX_WATCH_ALT_HOMES")
    if env:
        return [h for h in env.split(":") if h]
    vault = vault or os.path.join(os.path.expanduser(os.environ.get("ORCHESTRA_DIR") or "~/.orchestra"),
                                  "state", "account-vault")
    try:
        names = sorted(os.listdir(vault))
    except OSError:
        return []
    return [os.path.join(vault, n) for n in names
            if n.startswith("codex-") and os.path.isfile(os.path.join(vault, n, "auth.json"))]


def login_label(home) -> str:
    """The login's email from its id_token claims (read, never printed or logged); else the folder name."""
    try:
        with open(os.path.join(home, "auth.json")) as f:
            tok = json.load(f)["tokens"]["id_token"]
        part = tok.split(".")[1]
        email = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("email")
        if isinstance(email, str) and "@" in email and len(email) < 200:
            return email
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    return os.path.basename(os.path.normpath(home))


def _probe_once(codex, home, now, runner) -> Reading:
    env = probe_env()
    env.pop("CODEX_HOME", None)          # the default login is ~/.codex, never a home this process inherited
    if home:
        env["CODEX_HOME"] = home
    with tempfile.TemporaryDirectory(prefix="codex-watch-") as cwd:
        try:
            p = runner(probe_argv(codex), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
            return classify_probe(p.returncode, p.stdout, p.stderr, now=now)
        except subprocess.TimeoutExpired:
            return Reading("unknown", None, None, "probe", f"timed out after {PROBE_TIMEOUT_S}s")
        except OSError as e:
            return Reading("unknown", None, None, "probe", f"{type(e).__name__}: {e}"[:160])


def run_probe(now=None, homes=None, runner=None) -> Reading:
    """Probe the default login; when it is out, the parked logins in turn. The first that serves wins."""
    codex = os.environ.get("CODEX_WATCH_BIN") or shutil.which("codex") or os.path.expanduser("~/.local/bin/codex")
    runner = runner or subprocess.run
    first = _probe_once(codex, None, now, runner)
    if first.status != "out":
        return first
    unread = None
    for home in (alt_homes() if homes is None else homes):
        r = _probe_once(codex, home, now, runner)
        if r.status == "ok":
            return Reading("ok", None, None, "probe",
                           f"ok via {login_label(home)}; default login {_window_words(first)}: {first.evidence}"[:240],
                           codex_home=home)
        if r.status == "unknown" and unread is None:
            unread = Reading("unknown", None, None, "probe", f"{login_label(home)}: {r.evidence}"[:200])
    return unread or first               # OUT only when every login answered OUT


# --- 2. decide -----------------------------------------------------------------------------------

def _write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def _window_words(r: Reading) -> str:
    words = {"credits": "out of credits", "5-hour": "5-hour limit", "weekly": "weekly limit",
             "unknown": "usage limit (window not shown)"}.get(r.window or "unknown", r.window)
    return f"{words}, {LE.reset_label(r.reset_at)}" if r.reset_at else words


def _write_availability(path, state, r, stamp):
    _write_json(path, {
        "status": state["status"], "window": state.get("window"), "reset_at": state.get("reset_at"),
        "reset_label": LE.reset_label(state["reset_at"]) if state.get("reset_at") else None,
        "since": state.get("since") or stamp, "evidence": state.get("evidence"), "source": r.source,
        "gating_reviewer": "claude" if state["status"] == "out" else "codex",
        "codex_home": state.get("codex_home"),
        "_doc": "codex_watch (limit_watch --codex-watch): who gates reviews. out -> Claude reviewer, "
                "naming the window; ok -> codex-gm / codex exec first, with CODEX_HOME=codex_home when "
                "it is set (a parked login serves while the default one is out)."})


def flush_mail(state, mail, save=None):
    """Send the queued flip mails; one that fails stays queued for the next run, sent ones never repeat."""
    queue = state.get("pending_mail") or []
    while queue:
        to, prio, subj, body = queue[0]
        try:
            mail(to, prio, subj, body)
        except Exception:  # noqa: BLE001 — a timed-out send is retried next run, after the flip is saved
            break
        queue.pop(0)
        if save:
            save(state)
    if queue:
        state["pending_mail"] = queue
    else:
        state.pop("pending_mail", None)


def decide(state, readings, now, mail, *, availability_path, log, recipients=None, save=None):
    """Fold this run's readings into `state` (persisted by the caller); flip + mail on a debounced change.
    Returns "flipped" | "baseline" | None."""
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    known = [r for r in readings if r is not None and r.status in ("ok", "out")]
    if state.get("codex_home"):
        known = [r for r in known if r.source == "probe"]   # rollout/usage readings are the default login's
    if not known:
        return None
    # the probe is a live call made this run: it outranks the rest; otherwise an OUT reading outranks OK
    r = next((x for x in known if x.source == "probe"), None) or next(
        (x for x in known if x.status == "out"), known[0])
    cur = state.get("status")
    if r.status == cur:
        state["streak"] = {"status": cur, "count": 0}
        if cur == "ok" and r.source == "probe" and r.codex_home != state.get("codex_home"):
            state["codex_home"], state["evidence"] = r.codex_home, f"{r.source}: {r.evidence}"
            _write_availability(availability_path, state, r, stamp)   # same status, other login: no mail
            log({"ts": stamp, "kind": "codex_home", "codex_home": r.codex_home, "evidence": r.evidence})
        if cur == "out" and r.window and r.window != state.get("window"):
            state["window"], state["reset_at"] = r.window, r.reset_at  # the window got named better
        return None
    s = state.get("streak") or {}
    count = s.get("count", 0) + 1 if s.get("status") == r.status else 1
    state["streak"] = {"status": r.status, "count": count, "last": r.evidence}
    if count < FLIP_AFTER:
        return None
    state.update(status=r.status, window=r.window if r.status == "out" else None,
                 reset_at=r.reset_at if r.status == "out" else None, since=stamp,
                 evidence=f"{r.source}: {r.evidence}", streak={"status": r.status, "count": 0},
                 codex_home=r.codex_home if r.status == "ok" else None)
    _write_availability(availability_path, state, r, stamp)
    log({"ts": stamp, "kind": "codex_flip", "from": cur, "to": r.status, "window": state["window"],
         "reset_at": state["reset_at"], "evidence": state["evidence"]})
    if cur is None and r.status == "ok":
        return "baseline"                          # first status, and the fleet already assumes codex
    if r.status == "out":
        line = (f"Codex is OUT ({_window_words(r)}): gating reviews go to a fenced Claude reviewer; "
                f"name that window on the card. Back to codex when this file says ok: {availability_path}")
        gm_subj = f"codex OUT: {_window_words(r)}"
    else:
        line = (f"Codex is BACK: gating reviews go to codex-gm / codex exec first again; "
                f"Claude reviewer only when codex is out ({availability_path}).")
        gm_subj = "codex back: reviews to codex first"
    prio = "high" if r.status == "out" else "medium"
    seats = recipients if recipients is not None else DEFAULT_RECIPIENTS
    # the flip is saved BEFORE any mail: a send that fails cannot make the next run flip (and mail) again
    state["pending_mail"] = [[seat, prio, gm_subj, line + "\n"] for seat in seats] + [
        ["gm", prio, gm_subj, f"{line}\nEvidence: {state['evidence']}. Seats told: {', '.join(seats)}.\n"]]
    if save:
        save(state)
    flush_mail(state, mail, save)
    return "flipped"


# --- 3. one run ----------------------------------------------------------------------------------

def tick(paths, events, now, mail, log, probe=run_probe):
    """limit_watch calls this once per run with its rollout events."""
    st_path = os.path.join(paths.state_dir, "codex_watch.json")
    try:
        with open(st_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    try:
        with open(os.path.join(paths.usage_dir, "api-codex-default.json")) as f:
            usage = json.load(f)
    except (OSError, ValueError):
        usage = None
    save = lambda st: _write_json(st_path, st)
    flush_mail(state, mail, save)                        # anything a failed send left queued last run
    readings = [from_events(events, now)]
    u = classify_usage(usage, now)
    if u is not None and float((usage or {}).get("as_of") or 0) > float(state.get("usage_as_of") or 0):
        readings.append(u)                               # one usage reading counts once, not every run
        state["usage_as_of"] = float(usage["as_of"])
    if should_probe(state, now, u is not None):
        state["last_probe"] = now
        p = probe(now)
        readings.append(p)
        log({"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
             "kind": "codex_probe", "status": p.status, "window": p.window, "evidence": p.evidence})
    cfg = {}
    try:
        with open(paths.failover_cfg) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    res = decide(state, readings, now, mail, log=log, recipients=cfg.get("codex_watch_recipients"),
                 availability_path=os.path.join(paths.orch, "state", "codex-availability.json"), save=save)
    _write_json(st_path, state)
    return res
