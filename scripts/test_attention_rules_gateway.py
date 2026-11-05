"""The timeout-rules routes: GET is read, writes are approve (a rule can answer cards)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scripts.watch_gateway as G  # noqa: E402
from attention_testbox import Box  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch)


def test_scopes():
    assert G.ROUTE_SCOPES[("GET", "/attention/rules")] == "read"
    for m, p in (("POST", "/attention/rules"), ("PUT", "/attention/rules/{id}"), ("DELETE", "/attention/rules/{id}")):
        assert G.ROUTE_SCOPES[(m, p)] == "approve"


def test_get_seeds_the_defaults_and_carries_what_settings_needs(box):
    st, d = box.call("GET", "/attention/rules", who="read")
    assert st == 200 and d["person"] == "alex"
    assert {(r["trigger"], r["enabled"]) for r in d["rules"]} == {("unanswered", 1), ("no_progress", 0)}
    assert d["min_after_s"] == {"no_progress": 600, "unanswered": 60}
    assert {a["type"]: a["available"] for a in d["available_actions"]}["hand_off"] is True
    assert all("available_actions" in r and "min_after_s" in r for r in d["rules"])


def test_a_read_only_device_cannot_write_rules(box):
    body = {"trigger": "unanswered", "after_s": 600, "actions": [{"type": "remind"}]}
    assert box.call("POST", "/attention/rules", who="read", body=body)[0] == 403


def test_create_update_delete_and_rev(box):
    r0 = box.astore.rev()
    st, d = box.call("POST", "/attention/rules", who="approve",
                     body={"trigger": "unanswered", "after_s": 600, "scope_agent": "qa-1",
                           "actions": [{"type": "remind", "repeat_every_s": 900}]})
    assert st == 200 and d["rule"]["scope_agent"] == "qa-1"
    rid = d["rule"]["id"]
    st, d = box.call("PUT", f"/attention/rules/{rid}", who="approve", body={"after_s": 1200, "enabled": False})
    assert st == 200 and (d["rule"]["after_s"], d["rule"]["enabled"]) == (1200, 0)
    assert box.call("DELETE", f"/attention/rules/{rid}", who="approve")[0] == 200
    assert box.call("DELETE", f"/attention/rules/{rid}", who="approve")[0] == 404
    assert box.call("PUT", "/attention/rules/rul_nope", who="approve", body={"after_s": 900})[0] == 404
    assert box.astore.rev() >= r0 + 3


def test_validation_is_422_with_the_reason(box):
    st, d = box.call("POST", "/attention/rules", body={"trigger": "unanswered", "after_s": 600,
                                                      "actions": [{"type": "hand_off", "to": "nobody-here"}]})
    assert st == 422 and d["error"] == "unknown_agent"
    st, d = box.call("POST", "/attention/rules", body={"trigger": "unanswered", "after_s": 600,
                                                      "scope_kind": "permission", "actions": [{"type": "expire"}]})
    assert st == 422 and d["error"] == "unsafe_scope"
    st, d = box.call("POST", "/attention/rules", body={"trigger": "unanswered", "after_s": 5,
                                                      "actions": [{"type": "remind"}]})
    assert st == 422 and d["error"] == "invalid"
    assert box.call("POST", "/attention/rules", body=["not", "an", "object"])[0] == 400


def test_unarmed_rules_say_so(tmp_path, monkeypatch):
    b = Box(tmp_path, monkeypatch, armed=False)
    st, d = b.call("GET", "/attention/rules")
    assert st == 200 and d["rules"] == [] and d["armed"] is False
    st, d = b.call("POST", "/attention/rules", body={"trigger": "unanswered", "after_s": 600,
                                                    "actions": [{"type": "remind"}]})
    assert st == 409 and d["error"] == "not_armed"


def test_enabled_must_be_a_boolean(box):
    base = {"trigger": "unanswered", "after_s": 600, "actions": [{"type": "remind"}]}
    for bad in (0, "false", None):
        st, d = box.call("POST", "/attention/rules", body={**base, "enabled": bad})
        assert st == 422 and d["error"] == "invalid", bad
    st, d = box.call("POST", "/attention/rules", body={**base, "enabled": False})
    assert st == 200 and d["rule"]["enabled"] == 0
    rid = d["rule"]["id"]
    assert box.call("PUT", f"/attention/rules/{rid}", body={"enabled": "yes"})[0] == 422
    assert box.call("PUT", f"/attention/rules/{rid}", body={"enabled": True})[1]["rule"]["enabled"] == 1
