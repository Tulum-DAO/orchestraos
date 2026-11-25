"""Effect adapters and mode (design §7). Shadow is the default and never reaches approval.py or the
message store; live is proven once, against the fenced scratch DB, with notification refused."""
import json
import os
import sqlite3
import sys

import pytest

from postcall import effects
from postcall.effects import Effect

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPROVAL_CLI = os.path.join(REPO, "scripts", "approval.py")

CARD = Effect("card:pocket:r1:r0:p1", "card", {
    "title": "Synthetic Vendor Sync: 1 items", "summary": "Decide by: Fri 27 Nov 15:00 Tulum · If no answer: x",
    "questions": [{"prompt": "How should I handle the items below?", "kind": "menu", "required": True,
                   "menu": {"options": [{"label": "Accept"}, {"label": "Per item"}, {"label": "Drop all"}]}},
                  {"prompt": "Do a1 · Dana Reyes · Due: unknown", "kind": "menu", "required": False,
                   "menu": {"options": [{"label": "Take it myself (suggested)"}, {"label": "Drop"}]}}],
    "frozen": []})


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTCALL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("POSTCALL_MODE", raising=False)
    return tmp_path


def test_missing_mode_file_means_shadow(state_dir):
    assert effects.mode() == "shadow"


@pytest.mark.parametrize("content,expected", [("live\n", "live"), ("shadow", "shadow"), ("LIVE!!", "shadow"),
                                              ("", "shadow")])
def test_mode_file_is_strict(state_dir, content, expected):
    (state_dir / "MODE").write_text(content)
    assert effects.mode() == expected


def test_unreadable_mode_file_means_shadow(state_dir):
    (state_dir / "MODE").mkdir()
    assert effects.mode() == "shadow"


def test_shadow_adapter_writes_jsonl_and_finds_it_without_any_subprocess(state_dir):
    shadow = effects.adapter_for("shadow")
    assert shadow.find(CARD) is None
    ext = shadow.send(CARD)
    assert shadow.find(CARD) == ext
    rows = [json.loads(line) for line in (state_dir / "shadow" / "shadow.jsonl").read_text().splitlines()]
    assert [r["effect_id"] for r in rows] == [CARD.effect_id]
    assert rows[0]["payload"]["title"] == CARD.payload["title"]


def test_shadow_and_live_ledgers_are_separate_files(state_dir):
    assert effects.ledger_path("shadow") != effects.ledger_path("live")
    assert effects.ledger_path("shadow").startswith(str(state_dir))


def test_live_card_is_filed_through_the_real_cli_into_the_scratch_db_without_a_push(request, monkeypatch):
    request.node.postcall_allow_argv = [[sys.executable, APPROVAL_CLI]]
    monkeypatch.setenv("POSTCALL_APPROVAL_CLI", APPROVAL_CLI)
    live = effects.adapter_for("live")
    assert live.find(CARD) is None
    qid = live.send(CARD)
    assert qid.startswith("qnr_")
    assert live.find(CARD) == qid
    with sqlite3.connect(os.environ["APPROVAL_DB_PATH"]) as c:
        row = c.execute("SELECT from_agent, op_key, notified_at FROM questionnaires WHERE id=?", (qid,)).fetchone()
    assert row == ("postcall-dev", CARD.effect_id, None)
