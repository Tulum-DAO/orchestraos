"""The resolver: agents exact only; people get exact, a said-out-loud guess, a choice, revive or spawn."""
import json

import pytest

from scripts.identity_store import orchestra_db as odb
from scripts.identity_store import seat_names as sn
from scripts.identity_store.seat_resolve import AGENT, PERSON, load_directory, resolve

DIR = [
    {"id": "gm", "name": "gm", "aliases": [], "alive": True, "resumable": True},
    {"id": "pm-demo", "name": "Demo", "aliases": ["Example"], "alive": True, "resumable": True},
    {"id": "dev-demo", "name": "dev-demo", "aliases": [], "alive": True},
    {"id": "qa-demo", "name": "qa-demo", "aliases": [], "alive": False, "resumable": True},
    {"id": "inspiration", "name": "inspiration", "aliases": [], "alive": False, "resumable": False},
    {"id": "pm-sample-advisor", "name": "pm-sample-advisor", "aliases": [], "alive": True},
]


def k(text, caller=PERSON):
    r = resolve(text, DIR, caller)
    return r["kind"], (r.get("seat") or {}).get("id"), [o["id"] for o in r.get("options", [])]


def test_exact_id_name_and_old_name_for_everyone():
    for caller in (AGENT, PERSON):
        assert k("pm-demo", caller) == ("deliver", "pm-demo", [])
        assert k("Demo", caller) == ("deliver", "pm-demo", [])
        assert k("demo", caller) == ("deliver", "pm-demo", [])        # names: case aside
        assert k("example", caller) == ("deliver", "pm-demo", [])        # an old name, forever
        assert k("pm-demo-gen4", caller) == ("deliver", "pm-demo", [])  # a generation's id
        assert k("@gm", caller) == ("deliver", "gm", [])


def test_a_dead_agent_is_found_and_said_dead_with_whether_it_can_resume():
    r = resolve("qa-demo", DIR, PERSON)
    assert r["kind"] == "dead" and r["seat"]["resumable"] is True
    r = resolve("inspiration", DIR, AGENT)
    assert r["kind"] == "dead" and r["seat"]["resumable"] is False


def test_agents_are_exact_only_and_an_unknown_address_comes_back_with_the_closest():
    assert k("PM Demo", AGENT)[0] == "unknown"                 # an id must be written as it is
    assert k("pm-demmo", AGENT)[0] == "unknown"                  # a typo is never guessed for an agent
    r = resolve("pm-demmo", DIR, AGENT)
    assert [s["id"] for s in r["did_you_mean"]][0] == "pm-demo"
    assert resolve("nobody", DIR, AGENT) == {"kind": "unknown", "query": "nobody", "did_you_mean": []}


def test_people_get_spacing_and_case_forgiven_as_exact():
    assert k("PM Demo") == ("deliver", "pm-demo", [])
    assert k("pm sample advisor") == ("deliver", "pm-sample-advisor", [])


def test_people_one_close_agent_is_assumed():
    assert k("pm-demmo") == ("guess", "pm-demo", [])        # one typo
    assert k("inspiraton") == ("guess", "inspiration", [])          # a dead one is guessed too (then revive)
    assert k("demo pm") == ("guess", "pm-demo", [])       # words in any order
    assert k("advisor") == ("guess", "pm-sample-advisor", [])    # a word of its id
    assert k("sample advisor") == ("guess", "pm-sample-advisor", [])


def test_people_several_close_agents_get_a_choice():
    kind, _, opts = k("de")
    assert kind == "unknown"                                        # not a whole word: no match
    kind, _, opts = k("dev de")
    assert kind == "unknown"
    kind, _, opts = k("qa demo dev")
    assert kind == "unknown"
    DIR2 = DIR + [{"id": "ops-lead", "name": "Lead", "aliases": [], "alive": True}, {"id": "dev-lead", "name": "dev-lead", "aliases": [], "alive": True}]
    r = resolve("lead", DIR2, PERSON)
    assert r["kind"] == "deliver" and r["seat"]["id"] == "ops-lead"   # an exact name wins over loose matches
    r = resolve("leed", DIR2, PERSON)
    assert r["kind"] == "guess" and r["seat"]["id"] == "ops-lead"     # "leed" is one typo from "Lead" only
    r = resolve("demo dev qa", DIR2, PERSON)
    assert r["kind"] == "unknown"


def test_people_a_word_shared_by_several_agents_asks_which():
    D = [{"id": "dev-demo", "name": "dev-demo", "aliases": [], "alive": True},
         {"id": "qa-demo", "name": "qa-demo", "aliases": [], "alive": False}]
    r = resolve("demo", D, PERSON)
    assert r["kind"] == "choose" and [o["id"] for o in r["options"]] == ["dev-demo", "qa-demo"]   # alive first
    assert r["options"][1]["alive"] is False


def test_one_clearly_closer_match_beats_looser_ones():
    D = [{"id": "pm-demo", "name": "pm-demo", "aliases": [], "alive": True},
         {"id": "dev-demo", "name": "dev-demo", "aliases": [], "alive": True}]
    assert resolve("pm-dem", D, PERSON)["seat"]["id"] == "pm-demo"   # one typo vs a shared word


def test_never_existed_offers_spawn_for_people():
    assert resolve("muse", DIR, PERSON) == {"kind": "unknown", "query": "muse", "did_you_mean": []}
    assert k("") == ("unknown", None, [])


def test_load_directory_reads_ids_names_liveness_and_resumability(tmp_path):
    (tmp_path / "state").mkdir()
    db = str(tmp_path / "state" / "orchestra-registry.db")
    odb.init_db(db)
    c = odb.get_connection(db)
    for root in ("gm", "pm-demo", "pm-first-project"):
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?, 'T1', 'claude')", (root,))
    sn.set_name(c, "pm-demo", "Demo")
    c.close()
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps({
        "pm-demo": {"tmux_session": "pm-demo", "resume_command": "claude --resume abc"},
        "pm-first-project": {"tmux_session": "pm-first-project", "status": "retired"},
        "scout-g2": {"lineage_root": "scout", "tmux_session": "scout"}}))
    d = {s["id"]: s for s in load_directory(tmp_path, live_sessions={"gm", "pm-demo"}, has_claude=lambda t: True)}
    assert set(d) == {"arturo", "operator", "gm", "pm-demo", "pm-first-project", "scout"}
    assert d["pm-demo"] == {"id": "pm-demo", "name": "Demo", "aliases": [], "alive": True, "resumable": True,
                            "project": None, "full_name": "Demo", "alias_full": []}
    assert d["pm-first-project"]["alive"] is False and d["scout"]["alive"] is False
    assert resolve("demo", list(d.values()), AGENT)["kind"] == "deliver"
    assert resolve("pm first project", list(d.values()), PERSON)["kind"] == "dead"


def test_a_session_left_behind_by_a_crashed_claude_is_dead(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps({
        "gm": {"tmux_session": "gm"}, "pm-demo": {"tmux_session": "pm-demo", "session_id": "abc"}}))
    d = {s["id"]: s for s in load_directory(tmp_path, live_sessions={"gm", "pm-demo"},
                                            has_claude=lambda t: t == "gm")}       # pm-demo's Claude crashed
    assert d["gm"]["alive"] is True and d["pm-demo"]["alive"] is False
    r = resolve("pm-demo", list(d.values()), PERSON)
    assert r["kind"] == "dead" and r["seat"]["resumable"] is True              # the revive offer, not a delivery
    def broken(t):
        raise OSError("ps failed")
    d = {s["id"]: s for s in load_directory(tmp_path, live_sessions={"gm"}, has_claude=broken)}
    assert d["gm"]["alive"] is True                                            # a failed check never kills a live agent


def test_the_assistant_and_the_operator_are_always_reachable(tmp_path, monkeypatch):
    from scripts.identity_store import seat_resolve as sr
    monkeypatch.setattr(sr, "assistant_display_name", lambda: "Example")
    (tmp_path / "state").mkdir()
    d = sr.load_directory(tmp_path, live_sessions=set(), has_claude=lambda t: False)
    for text, want in (("arturo", "arturo"), ("Example", "arturo"), ("operator", "operator")):
        r = sr.resolve(text, d, AGENT)
        assert r["kind"] == "deliver" and r["seat"]["id"] == want, text



# ---- names unique per project ----

P1 = {"slug": "demo", "label": "Demo"}
P2 = {"slug": "other", "label": "Other"}
PDIR = [
    {"id": "gm", "name": "gm", "aliases": [], "alive": True},
    {"id": "dev-demo", "name": "dev", "aliases": [], "alive": True, "project": P1, "full_name": "Demo · dev"},
    {"id": "dev-other", "name": "dev", "aliases": [], "alive": True, "project": P2, "full_name": "Other · dev",
     "alias_full": ["Demo · builder"]},
    {"id": "qa-other", "name": "tester", "aliases": [], "alive": False, "project": P2, "full_name": "Other · tester"},
]


def test_a_full_name_reaches_its_agent_for_everyone():
    for caller in (AGENT, PERSON):
        r = resolve("Other · dev", PDIR, caller)
        assert r["kind"] == "deliver" and r["seat"]["id"] == "dev-other", caller
    assert resolve("the demo dev", PDIR, PERSON)["seat"]["id"] == "dev-demo"           # words, any order
    assert resolve("other dev", PDIR, PERSON)["seat"]["id"] == "dev-other"


def test_a_name_two_projects_share_is_ambiguous_for_agents_and_a_choice_for_people():
    r = resolve("dev", PDIR, AGENT)
    assert r["kind"] == "ambiguous" and [o["full_name"] for o in r["options"]] == ["Demo · dev", "Other · dev"]
    r = resolve("dev", PDIR, PERSON)
    assert r["kind"] == "choose" and {o["id"] for o in r["options"]} == {"dev-demo", "dev-other"}


def test_the_project_being_discussed_picks_which_dev_and_is_said_as_a_guess():
    r = resolve("dev", PDIR, PERSON, context_project="other")
    assert r["kind"] == "guess" and r["seat"]["id"] == "dev-other" and r["seat"]["full_name"] == "Other · dev"


def test_an_old_full_name_from_before_a_move_still_reaches_the_agent():
    assert resolve("Demo · builder", PDIR, AGENT)["seat"]["id"] == "dev-other"


def test_a_name_only_one_project_has_needs_no_project():
    for caller in (AGENT, PERSON):
        r = resolve("tester", PDIR, caller)
        assert r["kind"] == "dead" and r["seat"]["id"] == "qa-other"


def test_load_directory_gives_each_agent_its_project_and_full_name(tmp_path):
    (tmp_path / "state" / "knowledge").mkdir(parents=True)
    (tmp_path / "state" / "knowledge" / "projects.json").write_text(json.dumps(
        {"demo": {"name": "Demo Group", "short_name": "Demo", "pm": "pm-demo"}}))
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {
        "gm": {"tier": "T0"}, "pm-demo": {"tier": "T1", "reports_to": "gm"},
        "dev-demo": {"tier": "T2", "reports_to": "pm-demo"}, "loner": {"tier": "T2"}}}))
    db = str(tmp_path / "state" / "orchestra-registry.db")
    odb.init_db(db)
    c = odb.get_connection(db)
    for root in ("gm", "pm-demo", "dev-demo", "loner"):
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?, 'T1', 'claude')", (root,))
    c.close()
    c = odb.get_connection(db)
    sn.set_name(c, "dev-demo", "dev", orchestra_dir=tmp_path)
    c.close()
    d = {s["id"]: s for s in load_directory(tmp_path, live_sessions=set(), has_claude=lambda t: False)}
    assert d["dev-demo"]["project"] == {"slug": "demo", "label": "Demo"} and d["dev-demo"]["full_name"] == "Demo · dev"
    assert d["pm-demo"]["project"]["slug"] == "demo" and d["pm-demo"]["full_name"] == "pm-demo"   # never renamed: its id
    assert d["gm"]["project"] is None and d["loner"]["project"] is None
