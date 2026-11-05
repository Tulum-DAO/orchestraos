import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import supervision_store as SS  # noqa: E402


@pytest.fixture
def ss(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    s = SS.SupervisionStore(db_path=tmp_path / "tasks.db")
    s.migrate()
    return s


def test_unarmed_reads_empty_and_writes_refuse(tmp_path, monkeypatch):
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # no sentinel under a fake home
    s = SS.SupervisionStore(db_path=tmp_path / "t.db")
    s.migrate()
    assert s.person_rows("p") == {} and s.roles("p") == [] and s.grants("p") == []
    assert s.assignments("p") == {} and s.gen() == 0 and s.open_holds() == []
    with pytest.raises(SS.SupervisionNotArmed):
        s.set_default("p", level="watch_closely", by="p")
    with pytest.raises(SS.SupervisionNotArmed):
        s.create_hold(agent="dev", tool="Bash", fingerprint="fp", hits=[], level="watch_closely")


# ---- seeding: the "nothing changes" defaults ----
def test_seed_defaults_are_max_autonomy_and_assign_no_agent(ss):
    ss.seed("p", projects=["/h/.orchestra/projects/shore", "/h/orchestraos"])
    d = ss.person_rows("p")[""]
    assert (d["level"], json.loads(d["high_risk"]), d["hold_wait_s"], d["source"]) == ("let_them_run", [], 0, "seed")
    names = {r["name"]: r for r in ss.roles("p")}
    assert set(names) == {"Hands-off", "Trusted builder", "Supervised"}
    assert names["Hands-off"]["level"] == "let_them_run" and names["Supervised"]["level"] == "watch_closely"
    assert names["Trusted builder"]["level"] == "check_in"
    assert sorted(json.loads(names["Trusted builder"]["high_risk"])) == ["destructive", "outside_world"]
    assert all(r["hold_wait_s"] == 0 for r in names.values())
    asg = ss.assignments("p")
    assert {k for k in asg if k[0] == "agent"} == set()                               # no agent assigned
    assert {k[1] for k in asg if k[0] == "project"} == {"/h/.orchestra/projects/shore", "/h/orchestraos"}
    assert all(v == (names["Hands-off"]["id"], "seed") for k, v in asg.items() if k[0] == "project")
    rg = ss.grants("p", scope="role")
    assert {g["category"] + "/" + g["narrow_action"] for g in rg} == {"install/npm", "install/pip"}
    assert all(g["scope_ref"] == names["Trusted builder"]["id"] and g["state"] == "active" for g in rg)


def test_seed_is_once_and_never_resurrects_a_deleted_or_edited_starter(ss):
    ss.seed("p", projects=[])
    sup = next(r for r in ss.roles("p") if r["name"] == "Supervised")
    ss.update_role("p", sup["id"], level="check_in", by="p")
    ss.delete_role("p", next(r["id"] for r in ss.roles("p") if r["name"] == "Hands-off"), by="p")
    ss.seed("p", projects=[])
    assert [r["name"] for r in ss.roles("p")].count("Hands-off") == 0
    sup2 = next(r for r in ss.roles("p") if r["name"] == "Supervised")
    assert sup2["level"] == "check_in" and sup2["touched"] == 1 and sup2["starter"] == "supervised"


def test_seed_never_overwrites_a_person_default(ss):
    ss.set_default("p", level="check_in", high_risk=["outside_world"], by="p")
    ss.seed("p", projects=[])
    assert ss.person_rows("p")[""]["level"] == "check_in" and ss.person_rows("p")[""]["source"] == "person"


def test_new_project_gets_hands_off_by_seed(ss):
    ss.seed("p", projects=[])
    assert ss.seed_projects("p", ["/h/.orchestra/projects/new"]) == ["/h/.orchestra/projects/new"]
    assert ss.assignments("p")[("project", "/h/.orchestra/projects/new")][1] == "seed"
    assert ss.seed_projects("p", ["/h/.orchestra/projects/new"]) == []                # unseen keys only


def test_renamed_starter_still_seeds_projects(ss):
    ss.seed("p", projects=[])
    ho = next(r for r in ss.roles("p") if r["starter"] == "hands_off")
    ss.update_role("p", ho["id"], name="Free range", by="p")
    ss.seed_projects("p", ["/h/y"])
    assert ss.assignments("p")[("project", "/h/y")] == (ho["id"], "seed")


# ---- settings and overrides ----
def test_person_default_and_agent_override_field_by_field(ss):
    ss.set_default("p", level="check_in", high_risk=["outside_world"], by="p")
    ss.set_agent("p", "gm-gen4", hold_wait_s=120, by="p")
    rows = ss.person_rows("p")
    assert rows[""]["source"] == "person" and rows["gm"]["hold_wait_s"] == 120 and rows["gm"]["level"] is None
    ss.set_agent("p", "gm", level="watch_closely", by="p")
    assert ss.person_rows("p")["gm"]["hold_wait_s"] == 120                           # untouched field kept
    ss.set_agent("p", "gm", hold_wait_s=None, level=None, by="p")
    assert "gm" not in ss.person_rows("p") or ss.person_rows("p")["gm"]["hold_wait_s"] is None


@pytest.mark.parametrize("bad", [dict(level="loud"), dict(high_risk=["nope"]), dict(hold_wait_s=541),
                                 dict(hold_wait_s=-1), dict(hold_wait_s=True), dict(high_risk="destructive")])
def test_validation(ss, bad):
    with pytest.raises(SS.Invalid):
        ss.set_default("p", by="p", **{"level": "check_in", **bad})


# ---- roles ----
def test_role_crud_duplicate_and_in_use(ss):
    ss.seed("p", projects=["/h/x"])
    t = next(r for r in ss.roles("p") if r["name"] == "Trusted builder")
    c = ss.duplicate_role("p", t["id"], name="Builder (copy)", by="p")
    copied = ss.grants("p", scope="role", scope_ref=c["id"])
    assert len(copied) == 2 and all(g["source"] == "role_copy" for g in copied)        # grants copied
    assert c["starter"] is None and c["level"] == "check_in"
    with pytest.raises(SS.Invalid, match="name_taken"):
        ss.create_role("p", name="Builder (copy)", level="check_in", by="p")
    ss.assign("p", "agent", "dev", c["id"], by="p")
    with pytest.raises(SS.Conflict, match="role_in_use"):
        ss.delete_role("p", c["id"], by="p")
    ss.assign("p", "agent", "dev", None, by="p")
    ss.delete_role("p", c["id"], by="p")
    assert c["id"] not in {r["id"] for r in ss.roles("p")}
    assert ss.grants("p", scope="role", scope_ref=c["id"]) == []                       # its grants go with it
    # a deleted name is free again
    assert ss.create_role("p", name="Builder (copy)", level="let_them_run", by="p")["name"] == "Builder (copy)"


def test_last_role_cannot_be_deleted(ss):
    r = ss.create_role("p", name="Only", level="check_in", by="p")
    with pytest.raises(SS.Conflict, match="last_role"):
        ss.delete_role("p", r["id"], by="p")


def test_assign_unknown_role_and_gen_strip(ss):
    ss.seed("p", projects=[])
    with pytest.raises(SS.Invalid, match="unknown_role"):
        ss.assign("p", "agent", "dev", "rol_nope", by="p")
    r = ss.roles("p")[0]
    ss.assign("p", "agent", "dev-gen7", r["id"], by="p")
    assert ss.assignments("p")[("agent", "dev")] == (r["id"], "person")


def test_project_default_cannot_be_cleared(ss):
    ss.seed("p", projects=["/h/x"])
    with pytest.raises(SS.Invalid):
        ss.assign("p", "project", "/h/x", None, by="p")


def test_every_write_bumps_gen(ss):
    g0 = ss.gen()
    ss.seed("p", projects=[])
    g1 = ss.gen()
    ss.set_default("p", level="check_in", by="p")
    g2 = ss.gen()
    r = ss.create_role("p", name="X", level="check_in", by="p")
    g3 = ss.gen()
    ss.assign("p", "agent", "dev", r["id"], by="p")
    assert g0 < g1 < g2 < g3 < ss.gen()


# ---- grants ----
def test_grant_lifecycle_and_card_idempotency(ss):
    g = ss.create_grant("p", scope="agent", scope_ref="dev", category="outside_world",
                        narrow={"action": "deploy"}, source="card", source_card="apr_1", by="p")
    again = ss.create_grant("p", scope="agent", scope_ref="dev", category="outside_world",
                            narrow={"action": "deploy"}, source="card", source_card="apr_1", by="p")
    assert again["id"] == g["id"]                       # a replayed answer never doubles a grant
    other = ss.create_grant("p", scope="agent", scope_ref="dev", category="outside_world",
                            narrow={"action": "push"}, source="card", source_card="apr_1", by="p")
    assert other["id"] != g["id"]                       # two hits on one card are two grants
    f = ss.create_grant("p", scope="fleet", scope_ref=None, category="outside_folder",
                        narrow={"folder": "/srv/a"}, source="settings", by="p")
    assert ss.narrow_grant(f["id"], {"folder": "/srv/a/b"}, by="p")["narrow_folder"] == "/srv/a/b"
    with pytest.raises(SS.Invalid, match="widen_not_allowed"):
        ss.narrow_grant(f["id"], {"folder": "/srv"}, by="p")
    with pytest.raises(SS.Invalid, match="widen_not_allowed"):
        ss.narrow_grant(f["id"], {"folder": "/srv/a/bc"}, by="p")       # a sibling by prefix is not inside
    with pytest.raises(SS.Invalid, match="widen_not_allowed"):
        ss.narrow_grant(g["id"], {"action": None}, by="p")
    assert ss.narrow_grant(g["id"], {"recipient": "Example.COM"}, by="p")["narrow_recipient"] == "example.com"
    assert ss.revoke_grant(g["id"], by="p")["state"] == "revoked"
    assert ss.grant(g["id"])["revoked_by"] == "p"
    # revoked grants drop out of the list (the plan's sketch also asserted [] here, but f stays active)
    assert {x["id"] for x in ss.grants("p")} == {other["id"], f["id"]}


def test_deny_grants_are_immutable(ss):
    d = ss.create_grant("p", scope="agent", scope_ref="dev", category="install", narrow={"action": "npm"},
                        effect="deny", source="settings", by="p")
    with pytest.raises(SS.Invalid, match="deny_immutable"):
        ss.narrow_grant(d["id"], {"folder": "/x"}, by="p")


@pytest.mark.parametrize("bad", [dict(scope="galaxy"), dict(category="nope"), dict(effect="maybe"),
                                 dict(scope="fleet", scope_ref="x"), dict(scope="agent", scope_ref=None),
                                 dict(narrow={"folder": "rel/path"}), dict(narrow={"colour": "red"})])
def test_grant_validation(ss, bad):
    kw = dict(scope="agent", scope_ref="dev", category="install", source="settings", by="p")
    kw.update(bad)
    with pytest.raises(SS.Invalid):
        ss.create_grant("p", **kw)


def test_pending_chat_grant_is_inert_until_activated(ss):
    g = ss.create_grant("p", scope="agent", scope_ref="dev", category="install", narrow={"action": "npm"},
                        source="chat", state="pending", said="you can npm install", proposed_by="dev", by="dev")
    assert [x["id"] for x in ss.grants("p", states=("active",))] == []
    ss.activate_grant(g["id"], by="p")
    assert [x["id"] for x in ss.grants("p", states=("active",))] == [g["id"]]
    h = ss.create_grant("p", scope="agent", scope_ref="dev", category="install", narrow={"action": "pip"},
                        source="chat", state="pending", by="dev")
    assert ss.decline_grant(h["id"], by="p")["state"] == "declined"
    with pytest.raises(SS.Conflict):
        ss.activate_grant(h["id"], by="p")
    with pytest.raises(SS.NotFound):
        ss.revoke_grant("grt_missing", by="p")


# ---- holds ----
def test_holds_retry_token_is_single_use(ss):
    h = ss.create_hold(agent="dev", session_id="s1", tool="Bash", fingerprint="fp", hits=[], level="watch_closely",
                       card_id="apr_9")
    ss.set_hold(h, state="allowed", decision="allow_once", retry_until="2999-01-01T00:00:00Z")
    assert ss.take_retry("dev", "fp", now="2026-11-05T00:00:00Z") == h
    assert ss.take_retry("dev", "fp", now="2026-11-05T00:00:01Z") is None


def test_retry_expired_is_not_taken(ss):
    h = ss.create_hold(agent="dev", tool="Bash", fingerprint="fp", hits=[], level="watch_closely")
    ss.set_hold(h, state="allowed", retry_until="2026-11-05T00:00:00Z")
    assert ss.take_retry("dev", "fp", now="2026-11-05T00:00:01Z") is None


def test_create_hold_unique_per_tool_call_and_filing_first(ss):
    a = ss.create_hold(agent="dev", session_id="s1", tool_use_id="tu1", tool="Bash", fingerprint="fp",
                       hits=[{"category": "destructive"}], level="watch_closely")
    b = ss.create_hold(agent="dev", session_id="s1", tool_use_id="tu1", tool="Bash", fingerprint="fp",
                       hits=[], level="watch_closely")
    assert a == b and ss.hold(a)["state"] == "filing"
    assert json.loads(ss.hold(a)["hits"]) == [{"category": "destructive"}]


def test_transition_is_compare_and_set(ss):
    h = ss.create_hold(agent="dev", tool="Bash", fingerprint="fp", hits=[], level="watch_closely")
    assert ss.transition_hold(h, "waiting", ("filing",), card_id="apr_7") is True
    assert ss.hold_by_card("apr_7")["id"] == h
    assert ss.transition_hold(h, "open", ("filing",)) is False                    # someone else won
    assert ss.heartbeat(h) is True
    assert ss.transition_hold(h, "allowed", ("waiting", "open"), decision="allow_once") is True
    assert ss.hold(h)["decided_at"] is not None
    assert ss.heartbeat(h) is False                                                # swept / decided: re-read
    assert ss.transition_hold(h, "denied", ("waiting", "open")) is False           # nothing leaves allowed
    with pytest.raises(SS.Invalid):
        ss.transition_hold(h, "bogus", ("allowed",))


def test_sweep_withdraws_dead_waiting_holds_but_never_open(ss):
    a = ss.create_hold(agent="dev", tool="Bash", fingerprint="a", hits=[], level="watch_closely", card_id="apr_a")
    b = ss.create_hold(agent="dev", tool="Bash", fingerprint="b", hits=[], level="watch_closely", card_id="apr_b")
    ss.set_hold(b, state="open")
    gone = ss.sweep_stale(now_offset_s=60, stale_s=30)
    assert [x["id"] for x in gone] == [a] and ss.hold(b)["state"] == "open"
    assert ss.hold(a)["state"] == "withdrawn"


def test_sweep_spares_a_live_heartbeat(ss):
    a = ss.create_hold(agent="dev", tool="Bash", fingerprint="a", hits=[], level="watch_closely")
    ss.transition_hold(a, "waiting", ("filing",), card_id="apr_a")
    ss.set_hold(a, hook_seen_at="2999-01-01T00:00:00Z")
    assert ss.sweep_stale(now_offset_s=60, stale_s=30) == []
    ss.set_hold(a, hook_seen_at="2000-01-01T00:00:00Z")
    assert [x["id"] for x in ss.sweep_stale(stale_s=30)] == [a]


def test_open_hold_found_by_fingerprint_and_notify_is_once(ss):
    h = ss.create_hold(agent="dev", session_id="s1", tool="Bash", fingerprint="fp", hits=[], level="watch_closely",
                       card_id="apr_1")
    assert ss.open_hold("dev", "fp") is None
    ss.set_hold(h, state="open")
    assert ss.open_hold("dev", "fp")["id"] == h
    assert [x["id"] for x in ss.open_holds()] == [h]
    assert ss.claim_notify(h) is True and ss.claim_notify(h) is False


# ---- migration ----
def test_migrate_twice_and_racing(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    p = tmp_path / "t.db"
    ts = [threading.Thread(target=lambda: SS.SupervisionStore(db_path=p).migrate()) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    SS.SupervisionStore(db_path=p).migrate()
    names = {r[0] for r in sqlite3.connect(p).execute("select name from sqlite_master where type='table'")}
    assert {"supervision_settings", "supervision_roles", "role_assignments", "standing_grants",
            "supervision_meta", "gate_holds"} <= names


def test_project_key_rule(tmp_path):
    data = tmp_path / "data"
    (data / "projects" / "shore" / "sub").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert SS.project_key(str(data / "projects" / "shore" / "sub"), str(data)) == os.path.realpath(
        data / "projects" / "shore")
    assert SS.project_key(str(other), str(data)) == os.path.realpath(other)
    assert SS.project_key("", str(data)) is None
    assert SS.inside("/a/b", "/a") and SS.inside("/a", "/a") and not SS.inside("/ab", "/a")


def test_cli_migrate_seed(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    (data / "projects" / "shore").mkdir(parents=True)
    (data / "registry.json").write_text(json.dumps({"agents": {
        "dev-x": {"cwd": str(data / "projects" / "shore")}, "pm-x": {"cwd": str(data / "projects" / "shore")},
        "odd": {"cwd": str(tmp_path)}, "nocwd": {}}}))
    (tmp_path / "orchestra.toml").write_text("")
    env = {**os.environ, "ORCHESTRA_DIR": str(data), "APPROVAL_DDL_ARMED": SS.MIGRATION_ID,
           "ORCHESTRA_CONFIG": str(tmp_path / "orchestra.toml")}
    out = subprocess.run([sys.executable, str(HERE / "supervision_store.py"), "migrate", "--seed"],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    monkeypatch.setenv("APPROVAL_DDL_ARMED", SS.MIGRATION_ID)
    s = SS.SupervisionStore(db_path=data / "state" / "tasks.db")
    asg = s.assignments("operator")
    assert {k[1] for k in asg if k[0] == "project"} == {os.path.realpath(data / "projects" / "shore"),
                                                         os.path.realpath(tmp_path)}
    assert {k for k in asg if k[0] == "agent"} == set()
    assert s.person_rows("operator")[""]["level"] == "let_them_run"
    env.pop("APPROVAL_DDL_ARMED")
    env["HOME"] = str(tmp_path)
    out = subprocess.run([sys.executable, str(HERE / "supervision_store.py"), "migrate"],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 3
