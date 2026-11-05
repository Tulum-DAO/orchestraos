"""Supervision on a scratch gateway (attention part 2a, plan §7.1 / §9 / Task 8): GET/PUT /supervision, roles,
project roles, standing grants, the gate's mode / log / holds, push levels, the tick's sweeper and seeding, and
gate.json. Every store is bound to a tmp_path ledger; nothing touches a live pane, file or DB."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attention_push  # noqa: E402
import gate_cards  # noqa: E402
import gate_config as GC  # noqa: E402
import gate_policy as GP  # noqa: E402
import notify_policy  # noqa: E402
import scripts.watch_gateway as G  # noqa: E402
from attention_testbox import Box  # noqa: E402
from gate_classify import Hit  # noqa: E402
from scripts.device_tokens import DeviceStore  # noqa: E402
from supervision_store import SupervisionStore  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"
TAILSCALE = "100.64.0.7"


class SupBox(Box):
    """Box + a registry (two claude agents in one project folder, one codex agent in another), an admin device,
    a stubbed `tailscale ip -4` and a SupervisionStore on the same ledger. default_mode stands in for
    `[gate] default_mode` in orchestra.toml: most tests run an install that opted in to enforce; None leaves the
    real loader (no config -> the shipped default, off)."""

    def __init__(self, tmp_path, monkeypatch, armed=True, default_mode="enforce"):
        super().__init__(tmp_path, monkeypatch, armed=armed)
        self.mp = monkeypatch
        if default_mode is not None:
            monkeypatch.setattr(GC, "default_mode", lambda raw: default_mode)
        projects = self.data / "projects"
        for d in ("acme/site", "other"):
            (projects / d).mkdir(parents=True, exist_ok=True)
        self.registry = {"agents": {
            "dev-acme": {"cwd": str(projects / "acme" / "site"), "session_id": SESSION, "runtime": "claude"},
            "pm-acme": {"cwd": str(projects / "acme"), "runtime": "claude"},
            "codex-dev": {"cwd": str(projects / "other"), "runtime": "codex"},
        }}
        self.write_registry()
        self.acme = str((projects / "acme").resolve())
        self.other = str((projects / "other").resolve())
        monkeypatch.setattr(G, "_tailscale_ipv4", lambda: [TAILSCALE])
        monkeypatch.setitem(G._SUP_STATE, "loopback", None)
        monkeypatch.setitem(G._SUP_STATE, "reconciled_at", 0.0)
        _, self.admin = DeviceStore(self.data / "state" / "devices").mint("Laptop", ["read", "approve", "admin"])
        self.ss = SupervisionStore(db_path=self.db)

    def write_registry(self):
        (self.data / "registry.json").write_text(json.dumps(self.registry))

    def auth(self, who="fleet"):
        if who == "admin":
            return {"Authorization": f"Bearer {self.admin}"}
        return super().auth(who)

    # ---- helpers ----
    def gate(self):
        return GC.read_gate_raw(str(self.data))

    def role_id(self, starter):
        return next(r["id"] for r in self.ss.roles("alex") if r.get("starter") == starter)

    def start(self):
        """One gateway start (startup seeds and writes gate.json)."""
        st, d = self.call("GET", "/supervision")
        assert st == 200, d
        return d

    def tick(self):
        G._SUP_STATE["reconciled_at"] = 0.0
        G._supervision_tick()

    def gate_card(self, agent, hits, *, state="open", created_at=None):
        hid = self.ss.create_hold(agent=agent, tool="Bash", fingerprint=f"fp-{len(self.ss.open_holds(('open', 'waiting')))}",
                                  hits=[h.to_dict() for h in hits], level="watch_closely", session_id=SESSION,
                                  state=state, now=created_at)
        resolved = GP.Resolved("watch_closely", (), 0, None, None, {"level": "agent"}, True)
        rid = self.ledger.create(**gate_cards.card_kwargs(hid, agent=agent, hits=hits, resolved=resolved))
        self.ss.set_hold(hid, card_id=rid)
        return hid, rid

    def attention_push(self):
        st, d = self.call("GET", "/attention", params={"tab": "waiting,update"})
        assert st == 200, d
        return {i["id"]: i["push"] for i in d["items"]}


@pytest.fixture
def box(tmp_path, monkeypatch):
    return SupBox(tmp_path, monkeypatch)


@pytest.fixture
def unarmed(tmp_path, monkeypatch):
    return SupBox(tmp_path, monkeypatch, armed=False)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A fresh install: the shipped orchestra.example.toml as its config, the real default_mode loader."""
    import tomllib
    raw = tomllib.loads((Path(__file__).resolve().parent.parent / "orchestra.example.toml").read_text())
    monkeypatch.setattr(notify_policy, "_config", lambda: raw)
    return SupBox(tmp_path, monkeypatch, default_mode=None)


NPM = Hit(category="install", action="npm", detail="npm install left-pad")


# ---- GET /supervision ---------------------------------------------------------------------------------------------

def test_get_supervision_shape_after_seed(box):
    b = box.start()
    assert b["gate_mode"] == "enforce" and b["armed"] is True and b["person"] == "alex"
    assert b["default"] == {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0, "source": "seed"}
    assert {x["name"] for x in b["roles"]} == {"Hands-off", "Trusted builder", "Supervised"}
    hands_off = box.role_id("hands_off")
    assert b["projects"] and all(p["role_id"] == hands_off for p in b["projects"])
    assert {p["key"]: p["agents"] for p in b["projects"]} == {
        box.acme: ["dev-acme", "pm-acme"], box.other: ["codex-dev"]}
    assert all(a["role"] is None or a["role"]["source"] == "project" for a in b["agents"])     # no agent assigned
    assert all(a["effective"]["level"] == "let_them_run" for a in b["agents"])
    codex = next(a for a in b["agents"] if a["runtime"] == "codex")
    assert codex["gate"] == {"status": "not_supported", "label": "Can't be gated yet"}
    dev = next(a for a in b["agents"] if a["agent"] == "dev-acme")
    assert dev["gate"]["status"] == "not_seen" and dev["override"] == {"level": None, "high_risk": None,
                                                                         "hold_wait_s": None}
    assert [lv["id"] for lv in b["levels"]] == ["watch_closely", "check_in", "let_them_run"]
    assert b["wait_choices"] == [0, 60, 120, 300, 540]
    cats = {c["id"]: c for c in b["categories"]}
    assert set(cats) == {"outside_world", "destructive", "outside_folder", "install"}
    assert {"id": "deploy", "label": "Deploys"} in cats["outside_world"]["actions"]
    used = {r["name"]: r["used_by"] for r in b["roles"]}
    assert used["Hands-off"] == {"projects": 2, "agents": 0}


def test_agents_filter_and_gate_status_from_the_heartbeat(box):
    box.start()
    GC.heartbeat(str(box.data), "dev-acme", SESSION, "enforce")
    st, b = box.call("GET", "/supervision", who="read", params={"agents": "dev-acme"})
    assert st == 200 and [a["agent"] for a in b["agents"]] == ["dev-acme"]
    assert b["agents"][0]["gate"]["status"] == "protected" and b["agents"][0]["gate"]["session_id"] == SESSION
    assert [p["agents"] for p in b["projects"]] == [["dev-acme"]]
    GC.heartbeat(str(box.data), "pm-acme", "another-session", "enforce")
    _, b = box.call("GET", "/supervision")
    pm = next(a for a in b["agents"] if a["agent"] == "pm-acme")
    assert pm["gate"]["status"] == "protected"                 # no registry session to compare: seen is enough


# ---- PUT /supervision/default -------------------------------------------------------------------------------------

def test_put_default_sets_person_source_rewrites_gate_json_and_bumps_rev(box):
    box.start()
    rev, gen = box.astore.rev(), box.ss.gen()
    st, b = box.call("PUT", "/supervision/default", body={"level": "check_in", "high_risk": ["destructive"]})
    assert st == 200 and b["default"] == {"level": "check_in", "high_risk": ["destructive"], "hold_wait_s": 0,
                                          "source": "person"}
    assert box.astore.rev() > rev and box.ss.gen() > gen
    g = box.gate()
    assert g["gen"] == box.ss.gen() and g["default"] == {"level": "check_in", "high_risk": ["destructive"],
                                                         "hold_wait_s": 0}
    # every agent sits in a project whose seeded Hands-off role outranks the default (D1 chain)
    assert g["agents"]["dev-acme"]["level"] == "let_them_run"
    st, b = box.call("PUT", "/supervision/default", who="approve", body={"level": "let_them_run"})
    assert st == 403 and b["needed_scope"] == "admin"                  # the default is every agent's: admin only
    st, b = box.call("PUT", "/supervision/default", body={"level": "loud"})
    assert st == 422 and b == {"ok": False, "error": "invalid", "field": "level"}
    st, b = box.call("PUT", "/supervision/default", body={"hold_wait_s": 9999})
    assert st == 422 and b["field"] == "hold_wait_s"


# ---- PUT /supervision/agents/{agent} ------------------------------------------------------------------------------

def test_put_agent_role_and_override_and_clear(box):
    box.start()
    sup = box.role_id("supervised")
    st, b = box.call("PUT", "/supervision/agents/dev-acme", who="approve", body={"role_id": sup})
    assert st == 200
    dev = b["agent"]
    assert dev["role"] == {"id": sup, "name": "Supervised", "source": "agent"}
    assert dev["effective"]["level"] == "watch_closely"
    assert box.gate()["agents"]["dev-acme"]["role"] == sup
    _, b = box.call("PUT", "/supervision/agents/dev-acme", body={"level": "check_in", "hold_wait_s": 60})
    assert b["agent"]["override"]["level"] == "check_in" and b["agent"]["effective"]["level"] == "check_in"
    assert b["agent"]["effective"]["hold_wait_s"] == 60
    _, b = box.call("PUT", "/supervision/agents/dev-acme", body={"level": None})
    assert b["agent"]["effective"]["level"] == "watch_closely"                      # follows its role again
    assert b["agent"]["effective"]["hold_wait_s"] == 60                             # untouched (omitted)
    _, b = box.call("PUT", "/supervision/agents/dev-acme", body={"role_id": None, "hold_wait_s": None})
    assert b["agent"]["role"]["source"] == "project" and b["agent"]["effective"]["level"] == "let_them_run"
    st, b = box.call("PUT", "/supervision/agents/dev-acme", body={"role_id": "rol_nope"})
    assert st == 422 and b["field"] == "role_id"


def test_put_agent_404_for_unknown(box):
    box.start()
    st, b = box.call("PUT", "/supervision/agents/nobody", body={"level": "check_in"})
    assert st == 404 and b["error"] == "unknown_agent"


def test_put_agent_on_codex_carries_not_supported(box):
    box.start()
    st, b = box.call("PUT", "/supervision/agents/codex-dev", body={"level": "watch_closely"})
    assert st == 200 and b["gate"] == {"status": "not_supported", "label": "Can't be gated yet"}
    assert b["agent"]["effective"]["level"] == "watch_closely"


# ---- PUT /supervision/projects/{key} ------------------------------------------------------------------------------

def test_project_default_role_set_but_never_cleared(box):
    box.start()
    tb = box.role_id("trusted_builder")
    from urllib.parse import quote
    path = "/supervision/projects/" + quote(box.acme, safe="")
    st, b = box.call("PUT", path, body={"role_id": tb})
    assert st == 200 and next(p for p in b["projects"] if p["key"] == box.acme)["role_id"] == tb
    dev = next(a for a in b["agents"] if a["agent"] == "dev-acme")
    assert dev["role"] == {"id": tb, "name": "Trusted builder", "source": "project"}
    assert dev["effective"]["level"] == "check_in"
    st, b = box.call("PUT", path, body={"role_id": None})
    assert st == 422 and b["field"] == "role_id"
    st, b = box.call("PUT", "/supervision/projects/" + quote("/no/such/project", safe=""), body={"role_id": tb})
    assert st == 404


# ---- roles ----------------------------------------------------------------------------------------------------------

def test_roles_crud_duplicate_in_use_last_role(box):
    box.start()
    st, b = box.call("POST", "/supervision/roles", body={"name": "Night shift", "description": "Quiet hours",
                                                        "level": "check_in", "high_risk": ["install"],
                                                        "hold_wait_s": 120})
    assert st == 201 and b["role"]["name"] == "Night shift" and b["role"]["starter"] is None
    night = b["role"]["id"]
    st, b = box.call("POST", "/supervision/roles", body={"name": "Night shift", "level": "check_in"})
    assert st == 422 and b["error"] == "name_taken"
    st, b = box.call("POST", "/supervision/roles", body={"name": "No level"})
    assert st == 422 and b["field"] == "level"
    st, b = box.call("PUT", f"/supervision/roles/{night}", body={"level": "watch_closely"})
    assert st == 200 and b["role"]["level"] == "watch_closely"
    hands = box.role_id("hands_off")
    st, b = box.call("PUT", f"/supervision/roles/{hands}", body={"description": "Edited"})
    assert st == 200 and b["role"]["touched"] is True                       # a starter edited -> person-made
    tb = box.role_id("trusted_builder")
    st, b = box.call("POST", f"/supervision/roles/{tb}/duplicate", body={"name": "Trusted builder (copy)"})
    assert st == 201 and b["role"]["name"] == "Trusted builder (copy)"
    assert {(g["category"], g["narrow"].get("action"), g["source"]) for g in b["role"]["grants"]} == {
        ("install", "npm", "role_copy"), ("install", "pip", "role_copy")}
    st, b = box.call("GET", "/supervision/roles", who="read")
    assert st == 200 and len(b["roles"]) == 5 and all("grants" in r for r in b["roles"])
    st, b = box.call("DELETE", f"/supervision/roles/{hands}")
    assert st == 409 and b["error"] == "role_in_use" and b["used_by"] == {"projects": 2, "agents": 0}
    st, b = box.call("DELETE", f"/supervision/roles/{night}")
    assert st == 200 and b["role"]["deleted_at"]
    assert box.call("DELETE", f"/supervision/roles/{night}")[0] == 404
    # the last role: no project folders left (else the next start re-seeds them), free every assignment,
    # then delete down to one
    box.registry["agents"] = {}
    box.write_registry()
    c = sqlite3.connect(box.db)
    c.execute("DELETE FROM role_assignments")
    c.commit(); c.close()
    ids = [r["id"] for r in box.ss.roles("alex")]
    for rid in ids[:-1]:
        assert box.call("DELETE", f"/supervision/roles/{rid}")[0] == 200
    st, b = box.call("DELETE", f"/supervision/roles/{ids[-1]}")
    assert st == 409 and b["error"] == "last_role"


# ---- grants ---------------------------------------------------------------------------------------------------------

def test_grants_crud_and_validation(box):
    box.start()
    st, b = box.call("POST", "/grants", who="approve", body={"scope": "agent", "scope_ref": "dev-acme",
                                                            "category": "outside_world",
                                                            "narrow": {"action": "deploy"}, "effect": "allow"})
    assert st == 201 and b["grant"]["label"] == "Dev-acme may deploy without asking"
    assert b["grant"]["narrow"] == {"action": "deploy"} and b["grant"]["source"] == "settings"
    gid = b["grant"]["id"]
    bad = [
        ({"scope": "agent", "scope_ref": "dev-acme", "category": "magic"}, "category"),
        ({"scope": "agent", "scope_ref": "dev-acme", "category": "install", "narrow": {"action": "deploy"}},
         "narrow"),
        ({"scope": "agent", "scope_ref": "dev-acme", "category": "install", "narrow": {"folder": "/x"}},
         "narrow"),
        ({"scope": "agent", "scope_ref": "dev-acme", "category": "install", "narrow": {"recipient": "a@b"}},
         "narrow"),
        ({"scope": "fleet", "scope_ref": None, "category": "install", "effect": "deny"}, "effect"),
        ({"scope": "agent", "scope_ref": "nobody", "category": "install"}, "scope_ref"),
        ({"scope": "session", "scope_ref": "not-a-session", "category": "install"}, "scope_ref"),
        ({"scope": "session", "scope_ref": SESSION, "agent": "nobody", "category": "install"}, "agent"),
        ({"scope": "role", "scope_ref": "rol_nope", "category": "install"}, "scope_ref"),
        ({"scope": "project", "scope_ref": "/nowhere", "category": "install"}, "scope_ref"),
    ]
    for body, field in bad:
        st, b = box.call("POST", "/grants", body=body)
        assert st == 422 and b["error"] == "invalid" and b["field"] == field, (body, b)
    # a session grant carries its agent
    st, b = box.call("POST", "/grants", who="approve", body={"scope": "session", "scope_ref": SESSION,
                                                            "agent": "dev-acme", "category": "install"})
    assert st == 201 and b["grant"]["agent"] == "dev-acme" and b["grant"]["scope_ref"] == SESSION
    # ...and the session must be that agent's: another agent's (or an unknown) session cannot be labelled with it
    other = "99999999-8888-7777-6666-555555555555"
    st, b = box.call("POST", "/grants", who="approve", body={"scope": "session", "scope_ref": other,
                                                            "agent": "dev-acme", "category": "install"})
    assert st == 422 and b["field"] == "scope_ref", b
    GC.heartbeat(str(box.data), "pm-acme", other, "enforce")                  # pm-acme's live session
    st, b = box.call("POST", "/grants", who="approve", body={"scope": "session", "scope_ref": other,
                                                            "agent": "dev-acme", "category": "install"})
    assert st == 422 and b["field"] == "scope_ref", b
    assert box.call("POST", "/grants", who="approve", body={"scope": "session", "scope_ref": other,
                                                           "agent": "pm-acme", "category": "install"})[0] == 201
    # a bare session id (no agent) is admin-only
    st, b = box.call("POST", "/grants", who="approve", body={"scope": "session", "scope_ref": SESSION,
                                                            "category": "install"})
    assert st == 403 and b["needed_scope"] == "admin", b
    # role and project scopes (admin)
    tb = box.role_id("trusted_builder")
    assert box.call("POST", "/grants", body={"scope": "role", "scope_ref": tb, "category": "install",
                                            "narrow": {"action": "system"}})[0] == 201
    assert box.call("POST", "/grants", who="admin", body={"scope": "project", "scope_ref": box.acme,
                                                         "category": "install"})[0] == 201
    # PATCH narrows only
    st, b = box.call("POST", "/grants", body={"scope": "agent", "scope_ref": "dev-acme",
                                             "category": "outside_folder"})
    fid = b["grant"]["id"]
    site = box.acme + "/site"
    st, b = box.call("PATCH", f"/grants/{fid}", body={"narrow": {"folder": box.acme}})
    assert st == 200 and b["grant"]["narrow"]["folder"] == box.acme
    st, b = box.call("PATCH", f"/grants/{fid}", body={"narrow": {"folder": site}})
    assert st == 200 and b["grant"]["narrow"]["folder"] == site
    for folder in (box.other, site + "/../../other", box.acme):
        st, b = box.call("PATCH", f"/grants/{fid}", body={"narrow": {"folder": folder}})
        assert st == 422 and b["error"] == "widen_not_allowed", folder
    st, b = box.call("PATCH", f"/grants/{fid}", body={"narrow": {"folder": None}})
    assert st == 422 and b["error"] == "widen_not_allowed"
    st, b = box.call("PATCH", f"/grants/{gid}", body={"narrow": {"action": "push"}})
    assert st == 422 and b["error"] == "widen_not_allowed"
    st, b = box.call("PATCH", f"/grants/{gid}", body={"narrow": {"recipient": "ops@example.com"}})
    assert st == 200 and b["grant"]["narrow"] == {"action": "deploy", "recipient": "ops@example.com"}
    st, b = box.call("PATCH", f"/grants/{gid}", body={"scope": "fleet"})
    assert st == 422 and b["error"] == "field_not_patchable"
    st, b = box.call("POST", "/grants", body={"scope": "agent", "scope_ref": "dev-acme", "category": "install",
                                             "effect": "deny"})
    st, b = box.call("PATCH", f"/grants/{b['grant']['id']}", body={"narrow": {"action": "npm"}})
    assert st == 422 and b["error"] == "deny_is_immutable"
    # revoke, then it is closed
    st, b = box.call("DELETE", f"/grants/{gid}", who="approve")
    assert st == 200 and b["grant"]["state"] == "revoked"
    st, b = box.call("PATCH", f"/grants/{gid}", body={"narrow": {"recipient": "x@example.com"}})
    assert st == 409 and b["error"] == "not_active"
    st, b = box.call("DELETE", f"/grants/{gid}")
    assert st == 200 and b["grant"]["state"] == "revoked"                   # revoking again is idempotent
    assert box.call("DELETE", "/grants/grt_nope")[0] == 404
    # listings
    _, b = box.call("GET", "/grants", who="read")
    assert gid not in {g["id"] for g in b["grants"]}                                 # default active,pending
    _, b = box.call("GET", "/grants", params={"state": "all", "scope": "agent"})
    assert gid in {g["id"] for g in b["grants"]} and {g["scope"] for g in b["grants"]} == {"agent"}
    assert box.call("GET", "/grants", params={"state": "bogus"})[0] == 400


def test_grants_for_an_agent_mark_the_winner(box):
    box.start()
    _, f = box.call("POST", "/grants", body={"scope": "fleet", "scope_ref": None, "category": "install",
                                            "narrow": {"action": "npm"}})
    _, d = box.call("POST", "/grants", body={"scope": "agent", "scope_ref": "dev-acme", "category": "install",
                                            "narrow": {"action": "npm"}, "effect": "deny"})
    _, other = box.call("POST", "/grants", body={"scope": "agent", "scope_ref": "pm-acme",
                                                "category": "install"})
    _, b = box.call("GET", "/grants", params={"agent": "dev-acme"})
    by = {g["id"]: g for g in b["grants"]}
    assert other["grant"]["id"] not in by                                  # pm's grant never reaches dev
    assert by[d["grant"]["id"]]["applies"] is True
    fleet = by[f["grant"]["id"]]
    assert fleet["applies"] is False and fleet["overridden_by"].startswith(d["grant"]["id"])
    assert fleet["overridden_by"].endswith(": off)")
    _, b = box.call("GET", "/grants", params={"agent": "pm-acme"})
    assert {g["id"]: g["applies"] for g in b["grants"]}[f["grant"]["id"]] is True


def test_pending_chat_grant_is_declined_and_its_card_retired(box):
    box.start()
    gid, rid = gate_cards.propose_grant(box.ledger, box.ss, agent="dev-acme", category="outside_world",
                                        narrow={"action": "deploy"}, said="you can deploy", person="alex",
                                        notify=lambda *a, **k: None)
    st, b = box.call("DELETE", f"/grants/{gid}")
    assert st == 200 and b["grant"]["state"] == "declined"
    assert box.ledger.get(rid)["status"] in ("retired", "resolved_elsewhere")


# ---- scopes ---------------------------------------------------------------------------------------------------------

def test_member_device_cannot_write_fleet_role_or_project_grants_or_roles(box):
    box.start()
    tb = box.role_id("trusted_builder")
    from urllib.parse import quote
    for method, path, body in [
        ("POST", "/grants", {"scope": "fleet", "scope_ref": None, "category": "install"}),
        ("POST", "/grants", {"scope": "role", "scope_ref": tb, "category": "install"}),
        ("POST", "/grants", {"scope": "project", "scope_ref": box.acme, "category": "install"}),
        ("POST", "/supervision/roles", {"name": "Mine", "level": "check_in"}),
        ("PUT", f"/supervision/roles/{tb}", {"level": "let_them_run"}),
        ("POST", f"/supervision/roles/{tb}/duplicate", {"name": "Copy"}),
        ("DELETE", f"/supervision/roles/{tb}", None),
        ("PUT", "/supervision/projects/" + quote(box.acme, safe=""), {"role_id": tb}),
    ]:
        st, b = box.call(method, path, who="approve", body=body)
        assert st == 403 and b["needed_scope"] == "admin", (method, path, b)
    gid = box.ss.create_grant("alex", scope="fleet", scope_ref=None, category="install", source="settings",
                              by="alex")["id"]
    assert box.call("DELETE", f"/grants/{gid}", who="approve")[0] == 403
    assert box.call("DELETE", f"/grants/{gid}", who="admin")[0] == 200
    st, _ = box.call("PUT", "/supervision/agents/dev-acme", who="read", body={"level": "check_in"})
    assert st == 403


def test_gate_mode_needs_admin(box):
    box.start()
    for who in ("read", "approve"):
        st, b = box.call("PUT", "/gate/mode", who=who, body={"mode": "off"})
        assert st == 403 and b["needed_scope"] == "admin"
        assert box.call("GET", "/gate/log", who=who)[0] == 403
    st, b = box.call("PUT", "/gate/mode", who="admin", body={"mode": "observe"})
    assert st == 200 and b == {"ok": True, "mode": "observe"} and box.gate()["mode"] == "observe"
    assert box.call("PUT", "/gate/mode", body={"mode": "loud"})[0] == 422


def test_gate_log_and_hold(box):
    box.start()
    GC.log_line(str(box.data), {"agent": "dev-acme", "decision": "allow", "detail": "npm install x"})
    GC.log_line(str(box.data), {"agent": "pm-acme", "decision": "allow"})
    st, b = box.call("GET", "/gate/log", params={"agent": "dev-acme", "limit": "5"})
    assert st == 200 and [x["agent"] for x in b["lines"]] == ["dev-acme"]
    assert box.call("GET", "/gate/log", params={"limit": "0"})[0] == 400
    hid, rid = box.gate_card("dev-acme", [NPM])
    st, b = box.call("GET", f"/gate/holds/{hid}", who="read")
    assert st == 200 and (b["id"], b["state"], b["card_id"], b["agent"]) == (hid, "open", rid, "dev-acme")
    assert b["hits"][0]["action"] == "npm" and b["decision"] is None and b["created_at"]
    assert box.call("GET", "/gate/holds/gh_nope")[0] == 404


def test_unarmed_writes_409(unarmed):
    st, b = unarmed.call("GET", "/supervision")
    assert st == 200 and b["armed"] is False and b["roles"] == [] and b["gate_mode"] == "off"
    assert unarmed.gate() is None                                     # no gate.json while unarmed
    for method, path, body in [
        ("PUT", "/supervision/default", {"level": "check_in"}),
        ("PUT", "/supervision/agents/dev-acme", {"level": "check_in"}),
        ("POST", "/supervision/roles", {"name": "x", "level": "check_in"}),
        ("POST", "/grants", {"scope": "agent", "scope_ref": "dev-acme", "category": "install"}),
        ("PUT", "/gate/mode", {"mode": "enforce"}),
    ]:
        st, b = unarmed.call(method, path, body=body)
        assert st == 409 and b["error"] == "not_armed", (method, path, b)
    assert unarmed.call("PUT", "/gate/mode", body={"mode": "off"})[0] == 200        # off is never refused


# ---- /approval-answers option 4 -------------------------------------------------------------------------------------

def test_option_4_needs_admin_scope(box, monkeypatch):
    box.start()
    monkeypatch.setattr(G.approval_resume, "fire_resume", lambda *a, **k: None)
    _, rid = box.gate_card("dev-acme", [NPM])
    st, b = box.call("POST", "/approval-answers", who="approve", body={"id": rid, "answer": "option", "option_n": "4"})
    assert st == 403 and b["error"] == "all_agents_needs_admin"
    assert box.ledger.get(rid)["status"] == "pending"
    st, b = box.call("POST", "/approval-answers", who="admin", body={"id": rid, "answer": "option", "option_n": "4"})
    assert st == 200 and b["ok"], b
    # option 4 on any other menu card is not the gate's business
    other = box.ledger.create(from_agent="dev-acme", question="Pick", worker_kind="pane", kind="menu",
                              options=["a", "b", "c", "d"],
                              menu={"question": "Pick", "options": [{"n": str(i), "label": x, "input_kind": "direct"}
                                                                    for i, x in enumerate("abcd", 1)]})
    st, b = box.call("POST", "/approval-answers", who="approve", body={"id": other, "answer": "option",
                                                                      "option_n": "4"})
    assert st != 403


def test_confirm_on_a_wide_grant_proposal_needs_admin_scope(box, monkeypatch):
    box.start()
    monkeypatch.setattr(G.approval_resume, "fire_resume", lambda *a, **k: None)
    for scope, ref in (("fleet", None), ("project", box.acme)):
        _, rid = gate_cards.propose_grant(box.ledger, box.ss, agent="dev-acme", category="install",
                                          narrow={"action": "npm"}, scope=scope, scope_ref=ref, person="alex")
        assert json.loads(box.ledger.get(rid)["menu"])["bridge"]["scope"] == scope
        st, b = box.call("POST", "/approval-answers", who="approve", body={"id": rid, "answer": "option", "option_n": "1"})
        assert st == 403 and b["error"] == "all_agents_needs_admin", scope
        assert box.ledger.get(rid)["status"] == "pending"
        st, b = box.call("POST", "/approval-answers", who="approve", body={"id": rid, "answer": "option", "option_n": "2"})
        assert st == 200 and b["ok"], (scope, b)                      # Don't is anyone's
    _, rid = gate_cards.propose_grant(box.ledger, box.ss, agent="dev-acme", category="install",
                                      narrow={"action": "npm"}, scope="fleet", person="alex")
    st, b = box.call("POST", "/approval-answers", who="admin", body={"id": rid, "answer": "option", "option_n": "1"})
    assert st == 200 and b["ok"], b
    # an agent-scope proposal is an ordinary Confirm
    _, rid = gate_cards.propose_grant(box.ledger, box.ss, agent="dev-acme", category="install",
                                      narrow={"action": "npm"}, person="alex")
    st, b = box.call("POST", "/approval-answers", who="approve", body={"id": rid, "answer": "option", "option_n": "1"})
    assert st == 200 and b["ok"], b


# ---- push (§9) ------------------------------------------------------------------------------------------------------

def _push_fixture(box):
    box.mp.setitem(G._agents_cache, "data", [{"id": "dev-acme", "state": "working"}])
    box.ledger.create(from_agent="dev-acme", question="Rename the module?", worker_kind="pane")   # not blocking
    box.ledger.create(from_agent="pm-acme", question="Ship?", worker_kind="pane", risk_level="high")
    box.qstore.create(from_agent="pm-acme", title="Prefs", questions=[{"prompt": "C?", "kind": "free_text"}])
    box.astore.add_done("dev-acme", "Shipped picker", key="k1")
    box.perm_rows = [{"id": "perm:dev-acme:abc:1", "kind": "permission", "from_agent": "dev-acme",
                      "session": "dev-acme", "question": "Allow?", "created_at": "2026-11-04T10:00:00+00:00",
                      "menu": {"options": []}}]


def test_push_unchanged_with_seeded_defaults(box):
    """The release state (Let them run seeded default, Hands-off on every project, starters untouched) changes no
    push: every item's decision equals part 1's (decide_push without levels, the part-1 call)."""
    box.start()
    _push_fixture(box)
    assert G._sup_push_levels("alex") == (None, {})
    st, d = box.call("GET", "/attention", params={"tab": "waiting,update"})
    assert st == 200 and len(d["items"]) == 5
    prefs = notify_policy.load_prefs(G._data_dir(), "alex")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for it in d["items"]:
        part1 = attention_push.decide_push({k: v for k, v in it.items() if k != "push"}, prefs, now, "UTC")
        assert it["push"] == part1, it["id"]
        assert not it["push"]["reason"].startswith(("level=", "agent_level=")), it
    # and the same list with supervision switched off entirely (part 1's code path) is identical
    seeded = box.attention_push()
    box.mp.setattr(G, "_sup_push_levels", lambda person: (None, {}))
    assert box.attention_push() == seeded


def test_push_follows_a_level_a_person_chose(box):
    box.start()
    _push_fixture(box)
    before = box.attention_push()
    st, _ = box.call("PUT", "/supervision/agents/dev-acme", body={"level": "let_them_run"})
    assert st == 200
    assert G._sup_push_levels("alex") == (None, {"dev-acme": "let_them_run"})
    after = box.attention_push()
    dev_card = next(i for i, p in after.items() if i.startswith("apr_") and p["reason"].startswith("agent_level"))
    assert after[dev_card] == {"decision": "list", "reason": "agent_level=let_them_run"}       # non-blocking
    assert after["perm:dev-acme:abc:1"]["decision"] == "push"                             # blocking
    assert before[dev_card]["reason"].startswith("notify_level=")
    pm = [i for i in after if i not in (dev_card, "perm:dev-acme:abc:1") and not i.startswith("evt_")]
    assert all(after[i] == before[i] for i in pm)                       # pm has no level: unchanged


def test_person_made_project_role_and_default_reach_push(box):
    box.start()
    from urllib.parse import quote
    assert G._sup_push_levels("alex") == (None, {})
    box.call("PUT", "/supervision/projects/" + quote(box.acme, safe=""),
             body={"role_id": box.role_id("supervised")})
    _, levels = G._sup_push_levels("alex")
    assert levels == {"dev-acme": "watch_closely", "pm-acme": "watch_closely"}
    box.call("PUT", f"/supervision/roles/{box.role_id('hands_off')}", body={"description": "edited"})
    _, levels = G._sup_push_levels("alex")
    assert levels["codex-dev"] == "let_them_run"                         # an edited starter is person-made
    box.call("PUT", "/supervision/default", body={"level": "check_in"})
    assert G._sup_push_levels("alex")[0] == "check_in"


# ---- the tick -------------------------------------------------------------------------------------------------------

def test_sweeper_withdraws_dead_waiting_holds_and_retires_cards(box):
    box.start()
    hid, rid = box.gate_card("dev-acme", [NPM], state="waiting", created_at="2026-01-01T00:00:00Z")
    live, live_card = box.gate_card("pm-acme", [NPM], state="waiting")
    box.ss.heartbeat(live)
    box.tick()
    assert box.ss.hold(hid)["state"] == "withdrawn"
    assert box.ledger.get(rid)["status"] in ("retired", "resolved_elsewhere")
    assert box.ss.hold(live)["state"] == "waiting" and box.ledger.get(live_card)["status"] == "pending"


def test_tick_releases_open_holds_covered_by_a_new_grant(box):
    box.start()
    hid, rid = box.gate_card("dev-acme", [NPM])
    box.tick()
    assert box.ss.hold(hid)["state"] == "open"
    st, _ = box.call("POST", "/grants", body={"scope": "agent", "scope_ref": "dev-acme", "category": "install",
                                             "narrow": {"action": "npm"}})
    assert st == 201
    box.tick()
    h = box.ss.hold(hid)
    assert (h["state"], h["decision"]) == ("released", "covered")
    assert box.ledger.get(rid)["status"] in ("retired", "resolved_elsewhere")


def test_tick_withdraws_open_holds_whose_card_ended(box):
    box.start()
    hid, rid = box.gate_card("dev-acme", [NPM])
    keep, keep_card = box.gate_card("pm-acme", [NPM])
    gate_cards.retire_card(box.ledger, rid, "superseded")
    box.tick()
    assert box.ss.hold(hid)["state"] == "withdrawn"
    assert box.ss.hold(keep)["state"] == "open"


def test_new_project_folder_gets_hands_off_on_the_next_tick(box):
    box.start()
    new = box.data / "projects" / "fresh"
    new.mkdir(parents=True)
    box.registry["agents"]["fresh-dev"] = {"cwd": str(new), "runtime": "claude"}
    box.write_registry()
    box.tick()
    asg = box.ss.assignments("alex")
    assert asg[("project", str(new.resolve()))] == (box.role_id("hands_off"), "seed")
    assert not any(k == "agent" for (k, _t) in asg)                      # no agent is ever assigned a role
    assert box.gate()["agents"]["fresh-dev"]["level"] == "let_them_run"


def test_snapshot_failure_is_500_and_tick_reconciles(box, monkeypatch):
    box.start()
    real = GC.write_gate
    failed = []

    def flaky(data_dir, **fields):
        if (fields.get("default") or {}).get("level") == "watch_closely" and not failed:
            failed.append(1)
            raise OSError("disk full")
        return real(data_dir, **fields)
    monkeypatch.setattr(GC, "write_gate", flaky)
    st, b = box.call("PUT", "/supervision/default", body={"level": "watch_closely"})
    assert st == 500 and b["error"] == "snapshot_failed"
    assert box.ss.person_rows("alex")[""]["level"] == "watch_closely"           # the DB change stands
    assert box.gate()["gen"] < box.ss.gen()
    box.tick()
    g = box.gate()
    assert g["gen"] == box.ss.gen() and g["default"]["level"] == "watch_closely" and g["mode"] == "enforce"


def test_gate_json_carries_loopback_hosts(box):
    box.start()
    g = box.gate()
    assert g["loopback_hosts"] == ["127.0.0.1", "localhost", "::1", TAILSCALE]
    assert g["mode"] == "enforce" and g["person"] == "alex" and g["v"] == 1
    assert g["gateway_url"].startswith("http://127.0.0.1:")
    assert g["default"] == {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0}
    assert g["agents"]["dev-acme"] == {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0,
                                            "role": box.role_id("hands_off")}
    assert g["gen"] == box.ss.gen()


def test_loopback_without_tailscale_is_the_fixed_three(box, monkeypatch):
    monkeypatch.setattr(G, "_tailscale_ipv4", lambda: [])
    box.start()
    assert box.gate()["loopback_hosts"] == ["127.0.0.1", "localhost", "::1"]


def test_a_fresh_install_ships_the_gate_off(fresh):
    b = fresh.start()
    assert b["armed"] is True and b["gate_mode"] == "off"
    g = fresh.gate()
    assert g is not None and "mode" not in g                       # snapshot written, no mode chosen for anyone
    assert GC.read_gate(str(fresh.data))["mode"] == "off"          # what the hook reads: it checks nothing
    fresh.start()                                                  # a restart keeps it off
    assert GC.read_gate(str(fresh.data))["mode"] == "off"


def test_default_mode_from_config_and_bad_values_are_off():
    assert GC.default_mode({}) == "off"
    assert GC.default_mode({"gate": {}}) == "off"
    assert GC.default_mode({"gate": {"default_mode": "loud"}}) == "off"
    assert GC.default_mode({"gate": "enforce"}) == "off"
    assert GC.default_mode({"gate": {"default_mode": "observe"}}) == "observe"
    assert GC.default_mode({"gate": {"default_mode": "enforce"}}) == "enforce"


def test_configured_default_applies_once_and_an_explicit_off_survives(tmp_path, monkeypatch):
    # An install that opted in: `[gate] default_mode = "enforce"` in its orchestra.toml (conftest stubs the loader).
    monkeypatch.setattr(notify_policy, "_config", lambda: {"operator": {"id": "alex"}, "gate": {"default_mode": "enforce"}})
    box = SupBox(tmp_path, monkeypatch, default_mode=None)
    assert box.start()["gate_mode"] == "enforce" and box.gate()["mode"] == "enforce"
    assert box.call("PUT", "/gate/mode", body={"mode": "off"})[0] == 200
    box.start()
    assert box.gate()["mode"] == "off"


def test_existing_mode_is_never_overwritten_at_startup(box):
    box.start()
    assert box.call("PUT", "/gate/mode", body={"mode": "off"})[0] == 200
    box.start()                                                          # a restart
    assert box.gate()["mode"] == "off"
    GC.write_gate(str(box.data), mode="observe")
    box.start()
    assert box.gate()["mode"] == "observe"


def test_every_write_bumps_rev(box):
    box.start()
    for method, path, body in [
        ("PUT", "/supervision/agents/dev-acme", {"hold_wait_s": 60}),
        ("POST", "/grants", {"scope": "agent", "scope_ref": "dev-acme", "category": "install"}),
        ("POST", "/supervision/roles", {"name": "R", "level": "check_in"}),
        ("PUT", "/gate/mode", {"mode": "observe"}),
    ]:
        rev = box.astore.rev()
        st, b = box.call(method, path, body=body)
        assert st in (200, 201), b
        assert box.astore.rev() > rev, path
