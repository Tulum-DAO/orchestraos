import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate_policy as GP  # noqa: E402
from gate_classify import Hit  # noqa: E402

DEPLOY = Hit("outside_world", "deploy", "netlify deploy")
UNK = Hit("destructive", "unrecognised", "python3 -c …", certain=False)
RM = Hit("destructive", "delete_files", "rm -rf x", folder="/p/x")


def g(scope, ref=None, cat="outside_world", effect="allow", **narrow):
    return {"id": f"{scope}{ref}{effect}{narrow}", "scope": scope, "scope_ref": ref, "category": cat, "effect": effect,
            "state": "active", "narrow_action": narrow.get("action"), "narrow_folder": narrow.get("folder"),
            "narrow_recipient": narrow.get("recipient"), "created_at": "2026-11-05T00:00:00Z"}


PROJ = "/h/.orchestra/projects/shore"


def R(level, high_risk=(), role=None, role_source=None, wait=0):
    return GP.Resolved(level, tuple(high_risk), wait, role, role_source, {}, False)


W = dict(agent="dev", session_id="s1", project=PROJ)


def held(hits, level, high_risk=(), grants=(), resolved=None, **who):
    return GP.held_hits(hits, resolved=resolved or R(level, high_risk), grants=list(grants), **{**W, **who})


# ---- the resolution chain (D1) ----
HANDS_OFF = {"id": "rH", "level": "let_them_run", "high_risk": [], "hold_wait_s": 0}
SUPERVISED = {"id": "rS", "level": "watch_closely", "high_risk": [], "hold_wait_s": 0}
TRUSTED = {"id": "rT", "level": "check_in", "high_risk": ["destructive"], "hold_wait_s": 0}
ROLES = {r["id"]: r for r in (HANDS_OFF, SUPERVISED, TRUSTED)}
SEED_DEFAULT = {"": {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0, "source": "seed"}}


def test_release_defaults_resolve_to_let_them_run_and_are_not_explicit():
    r = GP.resolve(SEED_DEFAULT, ROLES, {("project", PROJ): ("rH", "seed")}, "dev", PROJ)
    assert (r.level, r.hold_wait_s, r.role_id, r.role_source, r.explicit) == ("let_them_run", 0, "rH", "project", False)
    assert GP.resolve({}, {}, {}, "dev", None).level == "let_them_run"          # unarmed / nothing seeded: built-in
    assert GP.resolve({}, {}, {}, "dev", None).sources == {"level": "builtin", "high_risk": "builtin",
                                                           "hold_wait_s": "builtin"}


def test_chain_order_most_specific_wins_field_by_field():
    asg = {("project", PROJ): ("rH", "seed"), ("agent", "dev"): ("rS", "person")}
    assert GP.resolve(SEED_DEFAULT, ROLES, asg, "dev-gen2", PROJ).level == "watch_closely"   # agent role > project role
    rows = {**SEED_DEFAULT, "dev": {"level": None, "high_risk": None, "hold_wait_s": 120, "source": "person"}}
    r = GP.resolve(rows, ROLES, asg, "dev", PROJ)
    assert (r.level, r.hold_wait_s, r.sources["hold_wait_s"], r.explicit) == ("watch_closely", 120, "agent", True)
    assert r.sources["level"] == "role" and r.role_source == "agent"
    assert GP.resolve(SEED_DEFAULT, ROLES, {("project", PROJ): ("rT", "person")}, "pm", PROJ).high_risk == ("destructive",)
    assert GP.resolve(SEED_DEFAULT, ROLES, {}, "pm", None).level == "let_them_run"         # person default


def test_raw_store_rows_and_role_lists_resolve():
    rows = {"": {"level": "check_in", "high_risk": '["install"]', "hold_wait_s": None, "source": "person"}}
    roles = [dict(TRUSTED, high_risk='["destructive"]', starter="trusted_builder", touched=0)]
    r = GP.resolve(rows, roles, {}, "dev", None)
    assert (r.level, r.high_risk, r.hold_wait_s, r.explicit) == ("check_in", ("install",), 0, True)
    assert r.sources["hold_wait_s"] == "builtin"
    r2 = GP.resolve(SEED_DEFAULT, roles, {("project", PROJ): ("rT", "seed")}, "dev", PROJ)
    assert r2.high_risk == ("destructive",) and r2.sources["level"] == "project_role" and r2.explicit is False


def test_explicit_follows_person_made_values():                                      # §9
    seeded = dict(SUPERVISED, starter="supervised", touched=0)
    asg_seed = {("project", PROJ): ("rS", "seed")}
    assert GP.resolve(SEED_DEFAULT, [seeded], asg_seed, "dev", PROJ).explicit is False
    assert GP.resolve(SEED_DEFAULT, [dict(seeded, touched=1)], asg_seed, "dev", PROJ).explicit is True   # edited
    assert GP.resolve(SEED_DEFAULT, [dict(seeded, starter=None)], asg_seed, "dev", PROJ).explicit is True  # made
    assert GP.resolve(SEED_DEFAULT, [seeded], {("project", PROJ): ("rS", "person")}, "dev", PROJ).explicit is True
    assert GP.resolve(SEED_DEFAULT, [seeded], {("agent", "dev"): ("rS", "person")}, "dev", PROJ).explicit is True
    person_default = {"": dict(SEED_DEFAULT[""], source="person")}
    assert GP.resolve(person_default, {}, {}, "dev", None).explicit is True
    seed_agent = {**SEED_DEFAULT, "dev": {"level": "check_in", "high_risk": None, "hold_wait_s": None,
                                          "source": "seed"}}
    assert GP.resolve(seed_agent, {}, {}, "dev", None).explicit is False


def test_deleted_role_falls_through():
    gone = dict(SUPERVISED, deleted_at="2026-11-05T00:00:00Z")
    r = GP.resolve(SEED_DEFAULT, [gone, HANDS_OFF], {("agent", "dev"): ("rS", "person"),
                                                     ("project", PROJ): ("rH", "seed")}, "dev", PROJ)
    assert (r.level, r.role_id, r.role_source) == ("let_them_run", "rH", "project")


# ---- what each level holds ----
def test_what_each_level_holds():
    hits = [DEPLOY, UNK, RM]
    assert held(hits, "let_them_run") == []
    assert held(hits, "check_in") == []
    assert held(hits, "check_in", ["destructive"]) == [RM]                    # uncertain passes under Check in
    assert held(hits, "watch_closely") == hits


def test_grant_lets_it_through_in_every_level():
    for lvl in ("watch_closely", "check_in"):
        assert held([DEPLOY], lvl, ["outside_world"], [g("fleet")]) == []


def test_role_grants_apply_only_through_the_agents_role():
    own = R("watch_closely", role="rS", role_source="agent")
    proj = R("watch_closely", role="rH", role_source="project")
    assert held([DEPLOY], "x", grants=[g("role", "rS")], resolved=own) == []
    assert held([DEPLOY], "x", grants=[g("role", "rT")], resolved=own) == [DEPLOY]   # not its role
    assert held([DEPLOY], "x", grants=[g("role", "rH")], resolved=proj) == []        # project default role (tier 2)
    assert GP.tier_of(g("role", "rH"), resolved=proj, **W) == 2
    assert GP.tier_of(g("role", "rS"), resolved=own, **W) == 4


def test_tiers_session_agent_role_project_projectrole_fleet():
    own = R("watch_closely", role="rS", role_source="agent")
    # agent deny (5) beats role allow (4); role deny (4) beats project allow (3); project deny (3) beats fleet allow (1)
    assert held([DEPLOY], "x", grants=[g("role", "rS"), g("agent", "dev", effect="deny")], resolved=own) == [DEPLOY]
    assert held([DEPLOY], "x", grants=[g("project", PROJ), g("role", "rS", effect="deny")], resolved=own) == [DEPLOY]
    assert held([DEPLOY], "x", grants=[g("fleet"), g("project", PROJ, effect="deny")], resolved=own) == [DEPLOY]
    assert held([DEPLOY], "x", grants=[g("agent", "dev", effect="deny"), g("session", "s1")], resolved=own) == []


def test_project_role_deny_loses_to_project_allow():
    proj = R("watch_closely", role="rH", role_source="project")
    assert held([DEPLOY], "x", grants=[g("project", PROJ), g("role", "rH", effect="deny")], resolved=proj) == []


def test_agent_grant_reaches_rotated_generations():
    assert held([DEPLOY], "watch_closely", grants=[g("agent", "dev")], agent="dev-gen7") == []


def test_session_grant_ends_with_the_session():
    assert held([DEPLOY], "watch_closely", grants=[g("session", "s1")], session_id="s2") == [DEPLOY]


def test_narrowing():
    assert GP.grant_matches(g("fleet", action="push"), DEPLOY) is False
    assert GP.grant_matches(g("fleet", cat="destructive", folder="/p"), RM) is True
    assert GP.grant_matches(g("fleet", cat="destructive", folder="/q"), RM) is False
    assert GP.grant_matches(g("fleet", cat="destructive", folder="/p"), UNK) is False   # no folder on the hit
    mail = Hit("outside_world", "message", "mail x", recipient="Ann@Example.com")
    assert GP.grant_matches(g("fleet", recipient="ann@example.com"), mail) is True
    assert GP.grant_matches(g("fleet", recipient="bob@example.com"), mail) is False
    assert GP.grant_matches(g("fleet", recipient="ann@example.com"), DEPLOY) is False


def test_same_tier_deny_beats_narrow_allow():                                # review 7
    assert held([DEPLOY], "watch_closely", grants=[g("agent", "dev", action="deploy"),
                                                   g("agent", "dev", effect="deny")]) == [DEPLOY]


def test_newest_allow_is_the_winner():
    a, b = dict(g("fleet"), id="a", created_at="2026-11-05T00:00:00Z"), dict(g("fleet"), id="b",
                                                                               created_at="2026-11-06T00:00:00Z")
    assert GP.winner([a, b], DEPLOY, resolved=R("watch_closely"), **W)["id"] == "b"
    assert GP.winner([], DEPLOY, resolved=R("watch_closely"), **W) is None


def test_folder_containment_is_by_components():
    assert GP.grant_matches(g("fleet", cat="destructive", folder="/p"),
                            Hit("destructive", "delete_files", "x", folder="/p2/x")) is False


def test_pending_and_revoked_grants_do_nothing():
    for st in ("pending", "revoked", "declined"):
        assert held([DEPLOY], "watch_closely", grants=[dict(g("fleet"), state=st)]) == [DEPLOY]


def test_project_of():
    reg = {"dev": {"cwd": "/h/.orchestra/projects/shore/sub"}, "gm": {"cwd": "/h/orchestraos"}}
    assert GP.project_of("dev-gen2", reg, "/h/.orchestra") == PROJ
    assert GP.project_of("gm", reg, "/h/.orchestra") == "/h/orchestraos"
    assert GP.project_of("dev", {"agents": reg}, "/h/.orchestra") == PROJ           # registry.json shape
    assert GP.project_of("nobody", reg, "/h/.orchestra") is None


def test_project_of_matches_the_store_seed_keys(tmp_path):
    import supervision_store as SS
    (tmp_path / "projects" / "shore" / "sub").mkdir(parents=True)
    reg = {"agents": {"dev": {"cwd": str(tmp_path / "projects" / "shore" / "sub")}}}
    assert GP.project_of("dev", reg, str(tmp_path)) == SS.registry_project_keys(str(tmp_path), reg)[0]


def test_would_hold_reports_every_level():
    w = GP.would_hold([DEPLOY, UNK], [], **W)
    assert w == {"let_them_run": [], "check_in": [],
                 "watch_closely": [["outside_world", "deploy"], ["destructive", "unrecognised"]]}
    w2 = GP.would_hold([DEPLOY, UNK], [g("fleet")], resolved=R("let_them_run", ["outside_world"]), **W)
    assert w2 == {"let_them_run": [], "check_in": [], "watch_closely": [["destructive", "unrecognised"]]}


def test_card_text():
    res = GP.Resolved("watch_closely", (), 0, "rS", "agent", {"level": "role"}, True)
    t = GP.card_text("dev", [DEPLOY], res, role_names={"rS": "Supervised"})
    assert t["question"] == "dev wants to deploy: netlify deploy"
    assert "Held because dev's role is Supervised" in t["summary"]
    assert t["summary"] == ("Outside-world action · deploy. Held because dev's role is Supervised. "
                            "dev is carrying on with other work.")


def test_card_text_why_lines():
    def why(src, level="watch_closely", hr=(), **kw):
        return GP.card_text("dev", [RM], GP.Resolved(level, tuple(hr), 0, "rT", None, {"level": src}, True),
                            role_names={"rT": "Trusted builder"}, **kw)["why"]
    assert why("agent") == "Held because you set dev to Watch closely"
    assert why("project_role", project=PROJ) == "Held because shore's default role is Trusted builder"
    assert why("default") == "Held because your default is Watch closely"
    assert why("role", "check_in", ["destructive"]) == ("Held because dev's role is Trusted builder "
                                                        "(you marked destructive action high-risk)")
    q = GP.card_text("dev", [UNK], R("watch_closely"))["question"]
    assert q == "dev wants to run a command the gate doesn't recognise: python3 -c …"


# ---- covers_item / resolve_all (store-backed) ----
@pytest.fixture
def ss(tmp_path, monkeypatch):
    import supervision_store as SS
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    s = SS.SupervisionStore(db_path=tmp_path / "tasks.db")
    s.migrate()
    return s


def _card(hold_id, origin="gate", kind="gate"):
    return {"id": "apr_1", "kind": "menu", "origin": origin,
            "menu": json.dumps({"bridge": {"kind": kind, "permission": True, "hold_id": hold_id}})}


def test_covers_item_only_when_every_held_hit_is_allowed_now(ss, tmp_path):
    ss.set_default("p", level="watch_closely", by="p")
    hid = ss.create_hold(agent="dev", tool="Bash", fingerprint="fp", hits=[DEPLOY.to_dict(), RM.to_dict()],
                         level="watch_closely", session_id="s1", state="open")
    raw = _card(hid)
    kw = dict(person="p", registry={}, data_dir=str(tmp_path))
    assert GP.covers_item({}, raw, ss, **kw) is False
    ss.create_grant("p", scope="agent", scope_ref="dev", category="outside_world", source="drawer", by="p")
    assert GP.covers_item({}, raw, ss, **kw) is False                                 # RM still held
    ss.create_grant("p", scope="fleet", scope_ref=None, category="destructive", source="drawer", by="p",
                    narrow={"folder": "/p"})
    assert GP.covers_item({}, raw, ss, **kw) is True
    assert GP.covers_item({"card": raw}, None, ss, **kw) is True
    assert GP.covers_item({}, _card(hid, kind="headless"), ss, **kw) is False         # not a gate card
    assert GP.covers_item({}, _card(hid, origin="menu_bridge"), ss, **kw) is False
    assert GP.covers_item({}, _card("gh_missing"), ss, **kw) is False
    assert GP.covers_item({}, {"id": "x", "menu": "{bad"}, ss, **kw) is False


def test_resolve_all(ss, tmp_path):
    ss.seed("p", projects=[str(tmp_path / "projects" / "shore")])
    sup = next(r for r in ss.roles("p") if r["name"] == "Supervised")
    ss.assign("p", "agent", "qa", sup["id"], by="p")
    reg = {"agents": {"dev": {"cwd": str(tmp_path / "projects" / "shore")}, "qa": {"cwd": "/elsewhere"}}}
    out = GP.resolve_all(ss, "p", reg, str(tmp_path))
    assert (out["dev"].level, out["dev"].role_source, out["dev"].explicit) == ("let_them_run", "project", False)
    assert (out["qa"].level, out["qa"].role_source, out["qa"].explicit) == ("watch_closely", "agent", True)
