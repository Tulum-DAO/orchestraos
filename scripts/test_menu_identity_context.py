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
