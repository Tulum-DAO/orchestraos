"""A menu's identity includes what it is ABOUT, not just its question.

Two serial "Do you want to proceed?" prompts for DIFFERENT commands used to share one identity:
the same permission card id (perm:<session>:<digest>:<n>, digest = sha(session|question)) and,
for bridged menus, the same op_key, so ApprovalStore.create() handed the second prompt the
FIRST prompt's row id (pending dedup, and for 900 s after it was answered, the re-card guard).
A stale tap meant for command A then carried a card id that matched command B.

The detector now captures the block between the widget frame and the question (the command,
its description) as `context`, and every identity key includes it.
"""
import importlib.util
import pathlib

import pytest

import menu_bridge_core as core
import scripts.watch_gateway as G
from scripts.approval_schema import ApprovalStore

ROOT = pathlib.Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "menus" / "perm_bash.pane.txt"
CMD_A = "curl -s https://example.com -o /tmp/ex.html"
CMD_B = "rm -rf /tmp/important"


def _status_mod():
    spec = importlib.util.spec_from_file_location("agent_status_ctx", ROOT / "agent-status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def S():
    return _status_mod()


def _pane(cmd=CMD_A, desc="Fetch example.com and save to /tmp/ex.html"):
    text = FIXTURE.read_text()
    return (text.replace(f"   {CMD_A}", f"   {cmd}")
                .replace("   Fetch example.com and save to /tmp/ex.html", f"   {desc}")).splitlines()


# ---------------------------------------------------------------- the detector

def test_the_detector_captures_the_command_a_permission_prompt_is_about(S):
    m = S.parse_pending_menu(_pane())
    assert m["kind"] == "permission" and m["question"].startswith("Do you want to proceed?")
    assert CMD_A in m["context"]
    assert "Do you want to proceed?" not in m["context"], "the question is not part of the context"
    assert "Running" not in m["context"], "nothing above the widget frame (scrollback) leaks in"


def test_two_prompts_with_the_same_question_and_different_commands_differ(S):
    a, b = S.parse_pending_menu(_pane(CMD_A)), S.parse_pending_menu(_pane(CMD_B, "Delete it"))
    assert a["question"] == b["question"]
    assert a["context"] != b["context"]


def test_context_is_whitespace_normalised(S):
    a = S.parse_pending_menu(_pane())
    b = S.parse_pending_menu([ln.replace("curl -s", "curl    -s") + "   " for ln in _pane()])
    assert a["context"] == b["context"]


def test_no_frame_means_no_context_and_the_old_identity(S):
    lines = ["Some prose the agent wrote.", "", "Do you want to proceed?",
             "❯ 1. Yes", "  2. No", "", "Esc to cancel"]
    m = S.parse_pending_menu(lines)
    if m is not None:
        assert "context" not in m


# ---------------------------------------------------------------- the keys

def test_op_key_includes_the_context_and_is_unchanged_without_one():
    q = "Do you want to proceed?"
    old = core.menu_op_key("seat", q)
    assert core.menu_op_key("seat", q, "") == old, "no context = byte-identical old key"
    assert core.menu_op_key("seat", q, "Bash command\n" + CMD_A) != core.menu_op_key("seat", q, "Bash command\n" + CMD_B)
    assert core.menu_op_key("seat", q, "x") == core.menu_op_key("seat", q, "x")


def test_the_permission_card_id_differs_for_a_different_command(S, monkeypatch, tmp_path):
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    a = S.parse_pending_menu(_pane(CMD_A))
    b = S.parse_pending_menu(_pane(CMD_B, "Delete it"))
    led = {}
    _, da = G._stamp_instance("seat", a, ledger=led, persist=False)
    _, db = G._stamp_instance("seat", b, ledger=led, persist=False)
    assert da != db
    assert G._perm_digest("seat", a["question"]) == G._perm_digest("seat", a["question"], ""), "compat"


# ---------------------------------------------------------------- the ledger (real store)

@pytest.fixture
def store(tmp_path):
    st = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    st.migrate()
    assert str(tmp_path) in st.db_path
    return st


def _create(store, ctx):
    q = "Do you want to proceed?"
    return store.create("seat", q, "pane", kind="menu", op_key=core.menu_op_key("seat", q, ctx))


def test_a_serial_prompt_for_a_DIFFERENT_command_gets_its_own_row_even_after_the_first_was_answered(store):
    a = _create(store, "Bash command\n" + CMD_A)
    assert _create(store, "Bash command\n" + CMD_B) != a, "while A is pending"
    store.record_answer(a, "1", "Yes")
    b = _create(store, "Bash command\n" + CMD_B)
    assert b != a, "the re-card guard must not hand B the answered row of a different command"


def test_the_SAME_command_still_reuses_its_row_as_before(store):
    a = _create(store, "Bash command\n" + CMD_A)
    assert _create(store, "Bash command\n" + CMD_A) == a
    store.record_answer(a, "1", "Yes")
    assert _create(store, "Bash command\n" + CMD_A) == a, "re-card guard unchanged for the same prompt"


# ---------------------------------------------------------------- review round 1 (each reproduced)

def test_a_permission_card_id_is_STABLE_across_sweeps_while_the_same_prompt_is_on_screen(S, monkeypatch, tmp_path):
    """The reap step compared the ledger against keys built with the OLD digest, so the live
    prompt looked absent, went 'gone' after the grace, and instance_n bumped every few sweeps."""
    import time as _t
    menu = S.parse_pending_menu(_pane())

    class _AS:
        def get_agent_status(self, sess):
            return {"pending_menu": menu}

    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["sessA"])
    monkeypatch.setitem(G._agents_cache, "data", None)
    clock = {"t": _t.time()}
    monkeypatch.setattr(_t, "time", lambda: clock["t"])
    ids = []
    for _ in range(8):
        ids.append(G._perm_pseudo_rows()[0]["id"])
        clock["t"] += 2.0
    assert len(set(ids)) == 1, ids


@pytest.mark.parametrize("width", [60, 80, 100, 120, 200])
def test_the_same_prompt_keeps_its_key_when_the_pane_rewraps(S, width):
    long_cmd = "curl -s https://example.com/a/very/long/path/that/wraps/" + "x" * 150 + " -o /tmp/out.html"
    def wrapped(w):
        lines = []
        for ln in _pane(long_cmd):
            if long_cmd in ln:
                body = ln.strip()
                lines += ["   " + body[i:i + w] for i in range(0, len(body), w)]
            else:
                lines.append(ln)
        return lines
    base = S.parse_pending_menu(wrapped(300))
    m = S.parse_pending_menu(wrapped(width))
    # The CONTEXT part of the identity is width-proof. (The question's "[Tool]" suffix comes from
    # _menu_tool, which reads the last 20 screen lines; that older fragility is tracked separately.)
    assert core.menu_identity("q", m["context"]) == core.menu_identity("q", base["context"])


def test_the_backstop_push_scan_uses_the_gateways_digest(S):
    import scripts.approval_notify as N
    import hashlib
    menu = S.parse_pending_menu(_pane())
    want = G._perm_digest("sessA", menu["question"], menu["context"])
    got = hashlib.sha256(("sessA|" + core.menu_identity(menu["question"], menu["context"])).encode()).hexdigest()[:16]
    assert got == want
    src = pathlib.Path(N.__file__).read_text()
    assert "menu_identity(q, menu.get(\"context\")" in src, "the notify scan keys on the same rule"


def test_tab_bars_and_tick_glyphs_never_enter_the_context(S):
    lines = ["─" * 60, "←  ☐ Approach  ☐ Scope  ✔ Submit  →", "", "Which approach?",
             "❯ 1. Fast", "  2. Safe", "", "Enter to select · ↑/↓ to navigate · Esc to cancel"]
    ticked = [ln.replace("☐ Approach", "☒ Approach") for ln in lines]
    a, b = S.parse_pending_menu(lines), S.parse_pending_menu(ticked)
    if a is not None and b is not None:
        assert a.get("context") == b.get("context")
        assert "Submit" not in (a.get("context") or "")


def test_is_session_carded_cannot_be_called_without_the_context():
    import menu_bridge as mb
    with pytest.raises(TypeError):
        mb.is_session_carded(None, "s", "q?")
