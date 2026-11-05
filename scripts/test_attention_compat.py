"""Compatibility goldens for /pending-approvals and /history (attention part 1).

The goldens in fixtures/attention_compat/ were captured from the code BEFORE part 1 (0bd608b). The
installed phone and watch read these two routes until they move to /attention, so:
  * /pending-approvals must stay byte-identical (after _stable's normalisation of ids and clocks);
  * /history may only GAIN fields, never lose or change one.

Regenerate (only ever from the pre-change code): ATTN_WRITE_GOLDEN=1 pytest scripts/test_attention_compat.py
"""
import json
import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from questionnaire_schema import QuestionnaireStore  # noqa: E402

FIX = HERE / "fixtures" / "attention_compat"
FLEET = "fleet-bearer-0123456789abcdef-for-tests"
AUTH = {"Authorization": f"Bearer {FLEET}"}
WRITE = os.environ.get("ATTN_WRITE_GOLDEN") == "1"

#: Keys whose values are clocks: normalised to "<ts>" (or kept None) so a golden is stable.
_TS_KEYS = {"created_at", "answered_at", "expires_at", "snoozed_until", "captured_at", "at",
            "retired_at", "discarded_at", "submitted_at"}


def _seed(tmp_path, monkeypatch):
    """A scratch ledger with one row of each shape the two feeds serve. Returns {label: id}."""
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    db = str(data / "state" / "tasks.db")
    st = ApprovalStore(db_path=db); st.migrate()
    qs = QuestionnaireStore(db_path=db); qs.migrate()
    ids = {}
    ids["approval"] = st.create(from_agent="gm", question="Approve the deploy?", worker_kind="pane",
                                risk_level="medium", reversibility="easy", summary="why")
    ids["menu_single"] = st.create(
        from_agent="dev", question="Pick one", worker_kind="pane", kind="menu", options=["A", "B"],
        menu={"question": "Pick one", "options": [{"n": "1", "label": "A", "input_kind": "direct"},
                                                  {"n": "2", "label": "B", "input_kind": "direct"}]})
    ids["menu_multi"] = st.create(
        from_agent="dev", question="Two parts", worker_kind="pane", kind="menu", options=["X", "Y"],
        menu={"question": "Part one", "part_count": 2, "walk_complete": True,
              "options": [{"n": "1", "label": "X", "input_kind": "direct"}],
              "parts": [{"question": "Part one", "options": [{"n": "1", "label": "X"}]},
                        {"question": "Part two", "options": [{"n": "1", "label": "Y"}]}]})
    ids["human_task"] = st.create(from_agent="gm", question="Plug in the phone", worker_kind="pane",
                                  kind="human_task", options=["done", "cant", "snooze"],
                                  block_task="Plug in the phone", blocks_what="deploy")
    ids["commitment"] = st.create(from_agent="gm", question="Send the invoice", worker_kind="pane",
                                  kind="human_task", options=["done", "cant", "snooze"], feature="commitment",
                                  block_task="Send the invoice", evidence={"due_ts": "2026-12-01T00:00:00+00:00"})
    ids["completion_pending"] = st.create(from_agent="gm", question="Voice panel live", worker_kind="pane",
                                          kind="completion", options=["accept", "respond"],
                                          evidence={"tests": "12 passed", "urls": [], "verified": True})
    ids["completion_accepted"] = st.create(from_agent="gm", question="Old done", worker_kind="pane",
                                           kind="completion", options=["accept", "respond"], evidence={})
    st.record_answer(ids["completion_accepted"], "accept", None, surface="watch", answered_by="operator")
    st.ack(ids["completion_accepted"])
    ids["answered_web"] = st.create(from_agent="gm", question="Ship the picker?", worker_kind="pane")
    st.record_answer(ids["answered_web"], "approve", None, surface="web", answered_by="operator")
    ids["discarded"] = st.create(from_agent="pm", question="Old ask", worker_kind="pane")
    st.discard(ids["discarded"])
    ids["retired"] = st.create(from_agent="pm", question="Superseded ask", worker_kind="pane")
    ok, why = st.retire(ids["retired"], "pm", "done another way", now_iso="2026-01-01T00:00:00Z")
    assert ok, why
    ids["qnr_pending"] = qs.create(from_agent="gm", title="Onboarding",
                                   questions=[{"prompt": "Name?", "kind": "free_text"}])
    ids["qnr_submitted"] = qs.create(from_agent="gm", title="Prefs",
                                     questions=[{"prompt": "Colour?", "kind": "free_text"}])
    qs.submit(ids["qnr_submitted"], {1: {"answer_text": "blue"}})
    return db, ids


def _stable(rows, ids):
    """Drop priority_score, replace ids by their seed label and clocks by "<ts>", sort keys and rows."""
    names = {v: k for k, v in ids.items()}

    def norm(x, key=None):
        if isinstance(x, dict):
            return {k: norm(v, k) for k, v in sorted(x.items()) if k != "priority_score"}
        if isinstance(x, list):
            return [norm(v) for v in x]
        if isinstance(x, str):
            if key in _TS_KEYS:
                return "<ts>"
            for rid, label in names.items():
                x = x.replace(rid, f"<{label}>")
            return x
        return x
    out = [norm(r) for r in rows]
    return sorted(out, key=lambda r: json.dumps(r, sort_keys=True))


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    db, ids = _seed(tmp_path, monkeypatch)
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path / "data")
    monkeypatch.setattr(G, "ApprovalStore", lambda *a, **k: ApprovalStore(db_path=db))
    monkeypatch.setattr(G, "QuestionnaireStore", lambda *a, **k: QuestionnaireStore(db_path=db))
    monkeypatch.setattr(G, "_perm_pseudo_rows", lambda: [])
    return db, ids


async def _get(path):
    client = TestClient(TestServer(G.build_app()))
    await client.start_server()
    try:
        r = await client.get(path, headers=AUTH)
        assert r.status == 200
        return await r.json()
    finally:
        await client.close()


def _golden(name, got):
    path = FIX / f"{name}.golden.json"
    if WRITE:
        FIX.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    return json.loads(path.read_text())


async def test_pending_approvals_is_byte_identical_to_the_golden(seeded):
    db, ids = seeded
    got = _stable((await _get("/pending-approvals"))["pending"], ids)
    assert got == _golden("pending", got)


async def test_history_is_the_golden_plus_only_additive_fields(seeded):
    db, ids = seeded
    got = _stable((await _get("/history"))["history"], ids)
    gold = _golden("history", got) if WRITE else json.loads((FIX / "history.golden.json").read_text())
    assert len(got) == len(gold)
    for g, o in zip(sorted(got, key=lambda r: r["id"]), sorted(gold, key=lambda r: r["id"])):
        assert {k: g[k] for k in o} == o                     # nothing removed or changed
        assert set(g) - set(o) <= {"answered_by", "answer_surface", "answer_device", "option_n"}


async def test_history_says_who_answered_and_on_which_surface(seeded):
    db, ids = seeded
    got = (await _get("/history"))["history"]
    web = next(r for r in got if r["id"] == ids["answered_web"])
    assert (web["answered_by"], web["answer_surface"], web["answer_device"]) == ("operator", "web", None)
    acc = next(r for r in got if r["id"] == ids["completion_accepted"])
    assert acc["answer_surface"] == "watch" and "option_n" in acc
    qnr = next(r for r in got if r["id"] == ids["qnr_submitted"])
    assert "answered_by" not in qnr                      # questionnaire rows are unchanged


async def test_completion_cards_still_wait_on_the_old_feed_after_mirroring(seeded):
    import attention_store as S
    db, ids = seeded
    st = S.AttentionStore(db_path=db); st.migrate()
    assert S.mirror_completions(st, ApprovalStore(db_path=db))
    got = {r["id"] for r in (await _get("/pending-approvals"))["pending"]}
    assert ids["completion_pending"] in got
