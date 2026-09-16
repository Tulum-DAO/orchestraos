import json, re
from services.arturo import call_journal as cj

VC_RE = re.compile(r"^vc_[A-Za-z0-9_-]{4,64}\Z")


def test_find_matching_call_by_first_turn():
    # incoming continues an existing call (shares first user turn) → matches it
    live = [("vc_aaa", ["hey arturo", "what's up with acme"]),
            ("vc_bbb", ["totally different call"])]
    assert cj.find_matching_call(["hey arturo", "what's up with acme", "and dental?"], live) == "vc_aaa"


def test_find_matching_call_survives_window_shift():
    # ElevenLabs truncated the head; incoming no longer has the first turn, but overlaps later turns
    live = [("vc_aaa", ["turn one", "turn two", "turn three"])]
    assert cj.find_matching_call(["turn two", "turn three", "turn four"], live) == "vc_aaa"


def test_find_matching_call_no_overlap_returns_none():
    live = [("vc_aaa", ["god i hate your voice"])]
    assert cj.find_matching_call(["say hi"], live) is None
    assert cj.find_matching_call([], live) is None


def test_is_subset_of_another_flags_fragment():
    # a 1-turn shard whose user-turn is contained in an 8-turn call → fragment of it
    others = [("vc_big", ["god i hate your voice", "well not only that", "no transcript"]),
              ("vc_other", ["totally different"])]
    assert cj.is_subset_of_another("vc_shard", ["god i hate your voice"], others) == "vc_big"


def test_is_subset_of_another_none_when_unique():
    others = [("vc_big", ["a", "b", "c"])]
    assert cj.is_subset_of_another("vc_x", ["z"], others) is None
    # equal sets are NOT a subset-fragment (need strictly more)
    assert cj.is_subset_of_another("vc_x", ["a", "b", "c"], others) is None


def test_add_turn_dedups_identical_consecutive(tmp_path):
    j = cj.CallJournal(dir=tmp_path, page="voice")
    j.add_turn("user", "say hi")
    j.add_turn("user", "say hi")     # retry / re-send — must NOT double
    assert sum(1 for t in json.loads(j.path.read_text())["turns"] if t["role"] == "user") == 1


def test_new_call_mints_valid_id_and_writes_atomically(tmp_path):
    j = cj.CallJournal(dir=tmp_path, page="Approvals")
    assert VC_RE.match(j.call_id)
    p = tmp_path / f"{j.call_id}.json"
    assert p.exists()
    d = json.loads(p.read_text())
    assert d["call_id"] == j.call_id
    assert d["status"] == "live"
    assert d["page"] == "Approvals"
    assert d["turns"] == []
    assert d["summary"] is None
    assert isinstance(d["started_at"], (int, float))
    assert list(tmp_path.glob("*.tmp")) == []


def test_append_turns_and_finalize(tmp_path):
    j = cj.CallJournal(dir=tmp_path, page="Agents")
    j.add_turn("user", "what's up with acme")
    j.add_tool("gm_command", {"prompt": "status"}, "3 agents", status="done")
    j.add_turn("arturo", "Three agents are working.")
    d = json.loads(j.path.read_text())
    assert [t["role"] for t in d["turns"]] == ["user", "tool", "arturo"]
    assert d["turns"][1]["tool"] == "gm_command"
    assert d["turns"][1]["status"] == "done"
    assert all("ts" in t for t in d["turns"])
    j.finalize("Checked Acme.\nThree agents working.")
    d2 = json.loads(j.path.read_text())
    assert d2["status"] == "ended"
    assert d2["summary"] == "Checked Acme.\nThree agents working."


def test_fuzzy_match_survives_asr_retranscription():
    # The real gm REOPEN bug: opener re-transcribed LONGER ~3s in must still resolve to the call.
    A = ["Just checking in on what this sounded like when I changed your name."]
    turn2 = ["Just checking in on what this sounded like when I changed your name. Or I mean, your, your voice.",
             "Can you see what page I'm on?"]
    assert cj.find_matching_call(turn2, [("vc_A", A)]) == "vc_A"


def test_pick_injection_winner_suppresses_shard():
    j = [("vc_A", 1786380084.82, ["changed your name"]),
         ("vc_B", 1786380088.08, ["changed your name or I mean your voice", "what page", "and now", "what about now"])]
    assert cj.pick_injection_winner("vc_A", j) is False   # thin shard suppressed
    assert cj.pick_injection_winner("vc_B", j) is True    # real call injects


def test_pick_injection_winner_single_call_injects():
    j = [("vc_solo", 100.0, ["hello there"])]
    assert cj.pick_injection_winner("vc_solo", j) is True
