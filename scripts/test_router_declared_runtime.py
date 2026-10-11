"""RED-first (PR 1, DEC-1791656515249463): the router judges a seat by its DECLARED runtime.

Before: message-router.py used Claude's '❯' signature for every seat unless ROUTER_GEMINI_IDLE_ARMED
was set, and nothing in this repo sets it. A Codex ('›') or agy ('>') pane never shows '❯', so the
router read it busy and held its mail forever. Gate container, real logged-in codex 0.153.4: an idle
seat was HELD "not-idle" for 2 minutes; armed, it was delivered in 12 s and replied.

Now: a row that declares a runtime with a known signature is judged by it; rows with no runtime
field keep the old rule; ROUTER_DECLARED_RUNTIME_DISABLED restores today's behaviour. All screens
below are REAL captures (fixtures/codex, fixtures/agy, fixtures/claude-2.1.295).
"""
import importlib.util
import json
import os
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"


@pytest.fixture
def mr(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("ROUTER_GEMINI_IDLE_ARMED", raising=False)
    monkeypatch.delenv("ROUTER_DECLARED_RUNTIME_DISABLED", raising=False)
    monkeypatch.delenv("ROUTER_AGY_CLEANUP_ARMED", raising=False)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "state").mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("message_router_pr1", HERE / "message-router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import runtime_signatures as rs
    reg = tmp_path / "data" / "registry.json"
    reg.write_text(json.dumps({"agents": {
        "cx": {"name": "cx", "runtime": "codex", "model": "gpt-5.6-terra"},
        "gx": {"name": "gx", "runtime": "gemini", "model": "gemini-3.7-flash"},
        "ax": {"name": "ax", "runtime": "agy"},
        "cl": {"name": "cl", "runtime": "claude", "model": "claude-opus-5"},
        "old": {"name": "old", "model": "gemini-3.7-flash"},            # no runtime field (legacy row)
        "oldc": {"name": "oldc", "model": "claude-opus-5"},
        "typo": {"name": "typo", "runtime": "grok"},                     # no signature in this install
    }}))
    monkeypatch.setattr(rs, "REGISTRY", reg)
    monkeypatch.setattr(m, "UNKNOWN_RUNTIME_WARNED", tmp_path / "data" / "state" / "warned.json")
    m.time.sleep = lambda s: None
    m.hook_state = lambda s: None
    m._logs = []
    m.log = lambda msg: m._logs.append(msg)
    m._home = tmp_path / "home"
    return m


def screen(m, name, sent=None):
    """Point the module's tmux at a captured screen; record send-keys."""
    base = FIX / name
    plain = Path(str(base) + ".txt").read_text()
    ansi_p = Path(str(base) + ".ansi")
    ansi = ansi_p.read_text() if ansi_p.exists() else plain

    def tmux(*args):
        if args and args[0] == "send-keys" and sent is not None:
            sent.append(args)
        out = ansi if "-e" in args else plain
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    m.tmux = tmux


class _Store:
    """Records the requeue UPDATE's args."""
    def __init__(self):
        self.sql = []

    def _conn(self):
        store = self

        class C:
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def execute(self, q, args):
                store.sql.append(args)
                return types.SimpleNamespace(rowcount=1)
        return C()


def arm(m):
    (m._home / "runtime").mkdir(exist_ok=True)
    (m._home / "runtime" / "ROUTER_GEMINI_IDLE_ARMED").touch()


def kill(m):
    (m._home / "runtime").mkdir(exist_ok=True)
    (m._home / "runtime" / "ROUTER_DECLARED_RUNTIME_DISABLED").touch()


# --- which signature -----------------------------------------------------------------------

def test_declared_rows_resolve_to_their_runtime_unarmed(mr):
    assert mr.routing_runtime("cx") == "codex"
    assert mr.routing_runtime("gx") == "gemini"
    assert mr.routing_runtime("ax") == "gemini"      # agy is the gemini runtime
    assert mr.routing_runtime("cl") == "claude"


def test_undeclared_rows_keep_the_old_rule(mr):
    assert mr.routing_runtime("old") == "claude"      # unarmed: claude, byte-identical to before
    assert mr.routing_runtime("nobody") == "claude"
    arm(mr)
    assert mr.routing_runtime("old") == "gemini"      # armed: inferred, as before
    assert mr.routing_runtime("oldc") == "claude"


def test_unknown_runtime_value_warns_once_per_seat_and_takes_the_old_path(mr):
    assert mr.routing_runtime("typo") == "claude"
    assert mr.routing_runtime("typo") == "claude"
    warns = [l for l in mr._logs if "typo" in l and "WARNING" in l]
    assert len(warns) == 1 and "'grok'" in warns[0] and "no prompt signature" in warns[0]
    # a fresh router process (every cron tick) must not warn again
    mr._logs.clear()
    assert mr.routing_runtime("typo") == "claude"
    assert not [l for l in mr._logs if "WARNING" in l]


def test_kill_switch_restores_todays_behaviour(mr):
    kill(mr)
    assert mr.routing_runtime("cx") == "claude"
    assert mr.routing_runtime("gx") == "claude"


def test_kill_switch_and_arm_together_mean_the_old_armed_behaviour(mr):
    kill(mr); arm(mr)
    assert mr.routing_runtime("cx") == "codex"        # old armed path: agent_runtime reads the field
    assert mr.routing_runtime("old") == "gemini"
    assert mr.routing_runtime("typo") == "claude"
    assert not [l for l in mr._logs if "WARNING" in l], "declarations are off: no warning either"


# --- idle / busy on REAL screens, through the router's own resolution -----------------------

@pytest.mark.parametrize("agent,fixture", [
    ("cx", "codex/idle_0.153.4.pane"),
    ("gx", "agy/idle_1.3.3.pane"),
    ("ax", "agy/idle_1.3.3.pane"),
    ("ax", "agy/idle_after_turn_1.3.3.pane"),
    ("cl", "claude-2.1.295/idle_manual_mode.pane"),
])
def test_an_idle_declared_seat_reads_idle_unarmed(mr, agent, fixture):
    screen(mr, fixture)
    assert mr.agent_is_idle("s", mr.routing_runtime(agent)) is True


@pytest.mark.parametrize("fixture", ["codex/busy_0.153.4.pane", "codex/typed_0.153.4.pane",
                                     "codex/stuck_marker_0.153.4.pane", "codex/trust_prompt_0.153.4.pane"])
def test_a_codex_seat_that_is_not_free_never_reads_idle(mr, fixture):
    """busy keeps the placeholder AND has '›' in scrollback; typed/stuck hold text; the trust prompt
    is a menu. The signature must anchor on the composer, not on any '›' line."""
    screen(mr, fixture)
    assert mr.agent_is_idle("s", mr.routing_runtime("cx")) is False


@pytest.mark.parametrize("fixture", ["agy/busy_generating_1.3.3.pane", "agy/busy_running_task_1.3.3.pane",
                                     "agy/permission_prompt_1.3.3.pane", "agy/typed_1.3.3.pane",
                                     "agy/stuck_marker_1.3.3.pane"])
@pytest.mark.parametrize("agent", ["ax", "gx"])
def test_an_agy_seat_that_is_not_free_never_reads_idle(mr, agent, fixture):
    """busy keeps the empty '>' composer AND has a '> Run ...' user line in scrollback, and its footer
    still names the model ('Gemini 3.8 Flash'); the permission prompt is a '> 1.' menu; typed/stuck hold
    text. The signature must anchor on the composer + idle footer, not on any '>' line."""
    screen(mr, fixture)
    assert mr.agent_is_idle("s", mr.routing_runtime(agent)) is False


def test_an_undeclared_legacy_row_on_a_codex_pane_is_unchanged(mr):
    screen(mr, "codex/idle_0.153.4.pane")
    assert mr.agent_is_idle("s", mr.routing_runtime("oldc")) is False   # judged by claude, as before


# --- the stuck-composer cleanup ACTS on the pane: it must never touch human text -----------

def test_cleanup_never_clears_human_typed_codex_text(mr):
    sent = []
    screen(mr, "codex/typed_0.153.4.pane", sent)
    assert mr.stuck_own_message("s", mr.routing_runtime("cx")) is None
    assert mr.cleanup_stuck_injection("s", None, mr.routing_runtime("cx")) is False
    assert sent == [], f"keys were sent to a composer holding a person's text: {sent}"


def test_cleanup_never_touches_an_idle_or_busy_codex_pane(mr):
    for fx in ("codex/idle_0.153.4.pane", "codex/busy_0.153.4.pane"):
        sent = []
        screen(mr, fx, sent)
        assert mr.cleanup_stuck_injection("s", None, mr.routing_runtime("cx")) is False
        assert sent == []


def test_cleanup_never_clears_human_typed_agy_text(mr):
    """A declared agy seat now reaches the cleanup UNARMED. On the REAL typed agy screen nothing is ours."""
    sent = []
    screen(mr, "agy/typed_1.3.3.pane", sent)
    rt = mr.routing_runtime("ax")
    assert rt == "gemini"
    assert mr.stuck_own_message("s", rt) is None
    assert mr.cleanup_stuck_injection("s", None, rt) is False
    assert sent == [], f"keys were sent to a composer holding a person's text: {sent}"


@pytest.mark.parametrize("fixture", ["agy/idle_1.3.3.pane", "agy/idle_after_turn_1.3.3.pane",
                                     "agy/busy_generating_1.3.3.pane", "agy/permission_prompt_1.3.3.pane"])
def test_cleanup_never_touches_an_idle_busy_or_menu_agy_pane(mr, fixture):
    sent = []
    screen(mr, fixture, sent)
    assert mr.cleanup_stuck_injection("s", None, mr.routing_runtime("ax")) is False
    assert sent == []


def _agy_stuck_then_idle(mr, sent):
    """The real stuck-marker agy screen; after any key, the real idle-after-turn screen."""
    screen(mr, "agy/stuck_marker_1.3.3.pane", sent)
    idle = (FIX / "agy/idle_after_turn_1.3.3.pane.txt").read_text()
    idle_a = (FIX / "agy/idle_after_turn_1.3.3.pane.ansi").read_text()
    stuck_tmux = mr.tmux

    def tmux(*args):
        if sent:
            return types.SimpleNamespace(returncode=0, stdout=idle_a if "-e" in args else idle, stderr="")
        return stuck_tmux(*args)
    mr.tmux = tmux


def test_agy_cleanup_is_off_by_default_even_on_our_own_stuck_line(mr):
    """gm ruling (msg_65f2bd8b): for declared agy/gemini rows the cleanup, the one router path that
    SENDS keys, stays off until its proof is reviewed. The marker is found (so the idle gate still
    reads the pane as not idle), but zero keys go out and the log names the reason."""
    sent = []
    _agy_stuck_then_idle(mr, sent)
    rt = mr.routing_runtime("ax")
    stuck = mr.stuck_own_message("s", rt)
    assert stuck and stuck.startswith("[MSG from gm") and "/tmp/agent-msg-msg_fixture_0001.md" in stuck
    st = _Store()
    assert mr.cleanup_stuck_injection("s", st, rt) is False
    assert sent == [] and st.sql == []
    assert any("CLEANUP skipped" in l and "ROUTER_AGY_CLEANUP_ARMED" in l for l in mr._logs), mr._logs


@pytest.mark.parametrize("how", ["env", "file"])
def test_armed_agy_cleanup_clears_our_wrapped_line_and_requeues_the_full_msg_id(mr, monkeypatch, how):
    """agy wraps the router line at a space (80 columns), so the msg path is whole on line 2. One C-u
    clears it (observed on the real CLI) and the id is requeued. Only when armed."""
    if how == "env":
        monkeypatch.setenv("ROUTER_AGY_CLEANUP_ARMED", "1")
    else:
        (mr._home / "runtime").mkdir(exist_ok=True)
        (mr._home / "runtime" / "ROUTER_AGY_CLEANUP_ARMED").touch()
    sent = []
    _agy_stuck_then_idle(mr, sent)
    st = _Store()
    assert mr.cleanup_stuck_injection("s", st, mr.routing_runtime("ax")) is True
    assert sent == [("send-keys", "-t", "s", "C-u")]
    assert st.sql == [("msg_fixture_0001",)]


def test_armed_agy_cleanup_still_never_clears_human_typed_text(mr, monkeypatch):
    monkeypatch.setenv("ROUTER_AGY_CLEANUP_ARMED", "1")
    sent = []
    screen(mr, "agy/typed_1.3.3.pane", sent)
    assert mr.cleanup_stuck_injection("s", None, mr.routing_runtime("ax")) is False
    assert sent == []


def test_the_agy_cleanup_arm_does_not_touch_codex(mr):
    """Codex cleanup is proven on real screens and ships on; the agy arm is not consulted for it."""
    sent = []
    screen(mr, "codex/stuck_marker_0.153.4.pane", sent)
    assert mr.stuck_own_message("s", mr.routing_runtime("cx"))
    mr.cleanup_stuck_injection("s", _Store(), mr.routing_runtime("cx"))
    assert sent == [("send-keys", "-t", "s", "C-u")]


def test_cleanup_finds_our_own_wrapped_codex_line_and_the_full_msg_id(mr):
    screen(mr, "codex/stuck_marker_0.153.4.pane")
    stuck = mr.stuck_own_message("s", mr.routing_runtime("cx"))
    assert stuck and stuck.startswith("[MSG from gm")
    assert "/tmp/agent-msg-msg_fixture_0001.md" in stuck, f"wrapped id not rejoined: {stuck!r}"


def test_cleanup_clears_our_codex_line_and_requeues_it(mr):
    """C-u is sent once; after it the composer reads empty (Ctrl-U cleared it on the real CLI)."""
    sent = []
    screen(mr, "codex/stuck_marker_0.153.4.pane", sent)
    idle = (FIX / "codex/idle_0.153.4.pane.txt").read_text()
    idle_a = (FIX / "codex/idle_0.153.4.pane.ansi").read_text()
    stuck_tmux = mr.tmux

    def tmux(*args):
        if sent:     # after C-u the real composer showed the placeholder again
            return types.SimpleNamespace(returncode=0, stdout=idle_a if "-e" in args else idle, stderr="")
        return stuck_tmux(*args)
    mr.tmux = tmux

    st = _Store()
    assert mr.cleanup_stuck_injection("s", st, mr.routing_runtime("cx")) is True
    assert sent == [("send-keys", "-t", "s", "C-u")]
    assert st.sql == [("msg_fixture_0001",)]
