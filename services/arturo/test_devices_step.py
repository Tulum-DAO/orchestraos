"""Onboarding step 'devices' (the operator, 2026-10-08: "Arturo should give a multi-choice question at
the right point of what devices they have").

The page asks with a multi-select card after the team step; the answer comes back as a marked turn.
What this pins: the directive records the answer as an operator fact and gives one line per device
from FIXED facts only (no app is released yet: say so, never invent a download), and the fact
reaches every later turn through the operator context line.
"""
from services.arturo import onboarding as onb
from services.arturo import operator_store as ops


def test_the_directive_records_the_devices_with_the_operator_fact_tool():
    d = onb.directive("devices")
    assert "set_operator_fact" in d and "field='devices'" in d


def test_every_device_on_the_card_has_a_fixed_fact():
    for device in onb.DEVICES:
        assert device in onb.DEVICE_FACTS, device
        assert onb.DEVICE_FACTS[device] in onb.directive("devices")


def test_no_app_is_presented_as_released():
    d = onb.directive("devices").lower()
    for app in ("iphone", "ipad", "mac"):
        assert "not released" in onb.DEVICE_FACTS[{"iphone": "iPhone", "ipad": "iPad", "mac": "Mac"}[app]].lower()
    assert "do not invent" in d
    assert "no android app" in onb.DEVICE_FACTS["Android phone"].lower()


def test_the_watch_pairs_through_the_iphone():
    assert "iphone" in onb.DEVICE_FACTS["Apple Watch"].lower()


def test_the_directive_never_sends_the_operator_to_tmux_attach():
    assert "tmux attach" not in onb.directive("devices")


def test_devices_is_an_operator_fact_and_reaches_the_context(tmp_path):
    ops.set_fact(tmp_path, "devices", "iPhone, Apple Watch")
    assert ops.public(tmp_path)["devices"] == "iPhone, Apple Watch"
    assert "iPhone, Apple Watch" in ops.context_line(tmp_path)


def test_the_dashboard_card_lists_exactly_these_devices_in_this_order():
    # Two copies of one list (the card, and the facts the brain answers from): a difference is a
    # device the operator can pick that Arturo has no true sentence for, so it fails here.
    import pathlib
    import re
    ts = pathlib.Path("dashboard/src/lib/arturo.ts").read_text()
    m = re.search(r"export const DEVICE_OPTIONS = \[([^\]]*)\]", ts)
    assert m, "DEVICE_OPTIONS not found in dashboard/src/lib/arturo.ts"
    assert tuple(re.findall(r"'([^']*)'", m.group(1))) == onb.DEVICES
    assert re.search(r"export const DEVICE_ONLY_HERE = '([^']*)'", ts).group(1) == onb.DEVICES[-1]


# ---- guardrails (pm-tulumdao, after the #268 review): the step records and explains, nothing else ----
import importlib.util
import pathlib

import pytest


@pytest.fixture(scope="module")
def P():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("tool,args", [
    ("run_command", {"command": "orchestra pair"}),
    ("create_starter_team", {"project": "website"}),
    ("spawn_agent", {"session_name": "x", "machine": "vps"}),
    ("send_telegram", {"message": "hi"}),
])
def test_a_devices_turn_runs_no_tool_but_the_fact(P, tool, args):
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_dev1", "devices"))
    try:
        out = P.execute_tool(tool, args)
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT RUN"), out


def test_a_devices_turn_may_record_the_fact(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ARTURO_STATE", tmp_path)
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_dev2", "devices"))
    try:
        P.execute_tool("set_operator_fact", {"field": "devices", "value": "iPhone, Mac"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert ops.public(tmp_path)["devices"] == "iPhone, Mac"


def test_other_turns_are_not_limited_by_the_devices_rule(P):
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_dev3", "team"))
    try:
        out = P.execute_tool("decline_starter_team", {})
    finally:
        P._TEAM_TURN.reset(tok)
    assert not out.startswith("NOT RUN")


def test_the_directive_forbids_pairing_and_points_to_the_guide_by_link():
    d = onb.directive("devices")
    assert "orchestra pair" in d and "never" in d.lower()
    assert "https://github.com/Tulum-DAO/orchestraos/blob/main/docs/ONBOARDING.md" in d
    assert "testflight" not in d.lower() and "apps.apple.com" not in d.lower()
