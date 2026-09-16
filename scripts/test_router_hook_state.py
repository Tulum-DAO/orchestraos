"""Identity Layer v1 item (d) — router consults hook-state BEFORE capture-pane
(gm mechanical spec /tmp/gm-amp-router-d.md, RED-first).

Incident by effect: agent_is_idle() decides deliverability by capture-pane and
treats any unrecognized render as busy, so gm's inbox dead-lettered ~2h
(03:10-05:00Z) while gm was demonstrably idle. The authoritative push signal
already exists: state-event-hook.py writes state/agent-events/panes/<N>.json on
every Claude hook ({state: working|idle|waiting_permission, ts}).

Contract under test:
  * hook_state(session) -> (state, ts) | None (pane id via tmux display, file
    from ORCH_EVENTS_DIR; None when absent/unreadable).
  * agent_is_idle(): hook idle -> ONLY the existing typed-composer check;
    hook working/waiting_permission fresh (<= TTL, default 900, env override
    ROUTER_HOOK_WORKING_TTL_S at use time, constant unchanged) -> busy;
    stale or no hook file -> existing capture-pane path unchanged.
  * note_hold(): a row whose target's hook says fresh-working is HELD, never
    dead-lettered (fleet rule 2026-08-15: live busy seat's mail is not destroyed).
"""
import importlib.util
import json
import os
import time

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "message_router", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

SESSION = "hooktest-seat"
PANE = "%7"

IDLE_PLAIN = "✻ Worked for 5s\n\nall done here\n\n❯ \n"
BUSY_PLAIN = "✻ Cooking… (12s · esc to interrupt)\n"
IDLE_ANSI_EMPTY = "❯ \x1b[7m \x1b[27m\n"
IDLE_ANSI_TYPED = "❯ fix the flaky test\x1b[7m \x1b[27m\n"
GEMINI_IDLE = "some output\n> \n? for shortcuts   Gemini 2.5 Flash · high\n"


class _R:
    def __init__(self, out, rc=0):
        self.stdout = out
        self.returncode = rc


def _fake_tmux(plain, ansi, pane=PANE):
    def f(*args, timeout=10):
        a = list(args)
        if "display" in a:
            return _R(pane + "\n")
        if "capture-pane" in a:
            return _R(ansi if "-e" in a else plain)
        return _R("", 1)
    return f


def _write_hook(tmp_path, state, age_s=0.0, pane=PANE):
    d = tmp_path / "panes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (pane.lstrip("%") + ".json")
    p.write_text(json.dumps({"pane": pane, "session_id": "s", "cwd": "/x",
                             "event": "x", "tool": "",
                             "ts": time.time() - age_s, "state": state}))
    return d


def _env_dir(monkeypatch, d):
    monkeypatch.setenv("ORCH_EVENTS_DIR", str(d))


def _quiet(monkeypatch):
    monkeypatch.setattr(mr.time, "sleep", lambda s: None)
    monkeypatch.setattr(mr, "_detector_is_generating", lambda s: False)


def test_router_hook_idle_delivers(tmp_path, monkeypatch):
    """Hook says idle -> deliverable on the typed-composer check ALONE, even
    when the capture-pane heuristics would read busy (the gm dead-letter shape:
    an unrecognized render must not out-vote push truth)."""
    _quiet(monkeypatch)
    _env_dir(monkeypatch, _write_hook(tmp_path, "idle"))
    monkeypatch.setattr(mr, "tmux", _fake_tmux(BUSY_PLAIN, IDLE_ANSI_EMPTY))
    assert mr.agent_is_idle(SESSION) is True


def test_router_hook_idle_typed_composer_holds(tmp_path, monkeypatch):
    """Human mid-composition is invisible to hooks — hook idle + typed composer
    still holds (never clobber the operator's typing)."""
    _quiet(monkeypatch)
    _env_dir(monkeypatch, _write_hook(tmp_path, "idle"))
    monkeypatch.setattr(mr, "tmux", _fake_tmux(IDLE_PLAIN, IDLE_ANSI_TYPED))
    assert mr.agent_is_idle(SESSION) is False


def test_router_hook_working_fresh_busy(tmp_path, monkeypatch):
    """Hook says working (fresh) -> busy, even when the pane LOOKS idle
    (between tool calls a mid-turn pane renders a settled prompt)."""
    _quiet(monkeypatch)
    _env_dir(monkeypatch, _write_hook(tmp_path, "working", age_s=5))
    monkeypatch.setattr(mr, "tmux", _fake_tmux(IDLE_PLAIN, IDLE_ANSI_EMPTY))
    assert mr.agent_is_idle(SESSION) is False


def test_router_hook_working_stale_falls_back_to_capture(tmp_path, monkeypatch):
    """A working hook older than the TTL is a hung tool / crashed seat — fall
    through to the existing capture-pane path (which here reads idle)."""
    _quiet(monkeypatch)
    _env_dir(monkeypatch, _write_hook(tmp_path, "working", age_s=3600))
    monkeypatch.setattr(mr, "tmux", _fake_tmux(IDLE_PLAIN, IDLE_ANSI_EMPTY))
    assert mr.agent_is_idle(SESSION) is True


def test_router_no_hook_file_uses_capture(tmp_path, monkeypatch):
    """No hook file (gemini/codex/no-hook seats) -> existing capture path,
    byte-identical: a gemini idle pane (footer + empty '>') delivers."""
    _quiet(monkeypatch)
    _env_dir(monkeypatch, tmp_path / "panes")   # dir absent/empty: no file
    monkeypatch.setattr(mr, "tmux", _fake_tmux(GEMINI_IDLE, GEMINI_IDLE))
    assert mr.agent_is_idle(SESSION, runtime="gemini") is True


def test_router_never_deadletters_while_hook_working(tmp_path, monkeypatch):
    """Fleet rule 2026-08-15: a live busy seat's mail is HELD, never destroyed.
    A row at the dead-letter threshold whose target's hook says fresh-working
    must NOT be dead-lettered this cycle."""
    _env_dir(monkeypatch, _write_hook(tmp_path, "working", age_s=5))
    monkeypatch.setattr(mr, "tmux", _fake_tmux(IDLE_PLAIN, IDLE_ANSI_EMPTY))
    monkeypatch.setattr(mr, "telegram", lambda *a, **k: None)
    now = time.time()
    msg = {"id": "msg_dl_test", "from_agent": "sender-x", "subject": "s",
           "created_at": "2026-01-01T00:00:00+00:00"}   # ancient: age floor met
    hold_state = {f"{SESSION}|not-idle": {
        "logged_at": now,
        "msgs": {"msg_dl_test": {"escalations": mr.HOLD_ESCALATE_MAX,
                                 "escalated_at": 0}}}}
    killed = []
    mr.note_hold(hold_state, SESSION, msg, "not-idle", now,
                 dead_letter_fn=lambda mid, why: killed.append(mid) or True)
    assert killed == [], "dead-lettered a row while the target's hook said working"
    mrec = hold_state[f"{SESSION}|not-idle"]["msgs"]["msg_dl_test"]
    assert not mrec.get("dead_lettered")


def test_router_ttl_constant_unchanged_under_env(tmp_path, monkeypatch):
    """Env ROUTER_HOOK_WORKING_TTL_S overrides at USE time; the module constant
    stays 900 (core asserted — no env can rewrite the shipped default)."""
    _quiet(monkeypatch)
    assert mr.HOOK_WORKING_TTL_S == 900
    monkeypatch.setenv("ROUTER_HOOK_WORKING_TTL_S", "5")
    assert mr.HOOK_WORKING_TTL_S == 900               # constant untouched
    # a working hook aged 10s is STALE under the 5s env ttl -> capture path (idle)
    _env_dir(monkeypatch, _write_hook(tmp_path, "working", age_s=10))
    monkeypatch.setattr(mr, "tmux", _fake_tmux(IDLE_PLAIN, IDLE_ANSI_EMPTY))
    assert mr.agent_is_idle(SESSION) is True
    # and fresh (2s) under the same env ttl is still busy
    _env_dir(monkeypatch, _write_hook(tmp_path, "working", age_s=2))
    assert mr.agent_is_idle(SESSION) is False
