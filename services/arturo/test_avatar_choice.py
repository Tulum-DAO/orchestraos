"""avatar_choice: each agent's saved look, keyed by agent id, with the hash default when none is saved."""
import json
import pathlib
import re

import pytest

from services.arturo import avatar_choice as ac

CORE_TS = pathlib.Path("dashboard/src/components/avatar/avatarCore.ts")


@pytest.fixture
def store(tmp_path):
    return ac.AvatarChoiceStore(path=tmp_path / "a.json", log_path=tmp_path / "a.log")


def test_the_tables_match_the_dashboard_core():
    """The shape order and the Bold palette are the dashboard's (the hash indexes into the shape order)."""
    src = CORE_TS.read_text()
    shapes_block = src[src.index("export const SHAPES"):src.index("export const SHAPE_KEYS")]
    assert tuple(re.findall(r"^\s+(\w+):\s+\{", shapes_block, re.M)) == ac.SHAPES
    bold_block = src[src.index("const BOLD"):src.index("export const PALETTES")]
    assert tuple(re.findall(r"hex: '(#[0-9A-F]{6})'", bold_block)) == ac.BOLD


def test_fnv1a_and_the_default_shape_match_the_dashboard_bit_for_bit():
    # values computed by avatarCore.ts (identityFor / fnv1a)
    want = {"arturo": ("star", 4061289222), "gm": ("square", 1142979185), "dev-client-a": ("flower", 4110329070),
            "pm-client-a": ("kitty", 2141150684), "qa-client-a": ("hexagon", 3230040547),
            "pm-sample-advisor": ("flower", 3685175118), "agent-0": ("hexagon", 3830737203), "x": ("pebble", 4245442695)}
    for agent, (shape, h) in want.items():
        assert ac.fnv1a(agent) == h, agent
        assert ac.default_look(agent) == {"shape": shape, "color": "ink", "eyeColor": "auto"}, agent
    assert ac.fnv1a("") == 0x811C9DC5


def test_no_record_is_the_default_look_and_not_chosen(store):
    st = store.state("arturo")
    assert st["chosen"] is False
    assert (st["shape"], st["color"], st["eyeColor"]) == ("star", "ink", "auto")
    assert store.all() == {}


def test_a_pick_persists_per_agent_and_keeps_the_fields_not_given(store, tmp_path):
    st = store.set("arturo", shape="heart", by="settings-web", device="fleet")
    assert st["chosen"] and st["shape"] == "heart" and st["color"] == "ink" and st["eyeColor"] == "auto"
    st = store.set("arturo", color="#1e6be6")                 # case-normalised
    assert st["shape"] == "heart" and st["color"] == "#1E6BE6"
    assert store.state("gm")["chosen"] is False              # keyed by agent: gm is untouched
    on_disk = json.loads((tmp_path / "a.json").read_text())
    assert set(on_disk) == {"arturo"}
    assert set(on_disk["arturo"]) == {"shape", "color", "eyeColor", "changed_at", "changed_by", "source"}


def test_bad_values_are_refused_with_a_plain_reason(store):
    for kw, msg in (({"shape": "triangle"}, "shape must be"), ({"color": "#123456"}, "color must be"),
                    ({"color": "red"}, "color must be"), ({"eye_color": "green"}, "eyeColor must be"), ({}, "nothing to change")):
        with pytest.raises(ValueError, match=msg):
            store.set("arturo", **kw)
    for bad in ("", "a/b", "../x", "x" * 81, "a b"):
        with pytest.raises(ValueError, match="agent must be"):
            store.set(bad, shape="star")
    assert store.all() == {}


def test_reset_returns_to_the_default(store):
    store.set("arturo", shape="heart", color="#E5243B")
    st = store.reset("arturo")
    assert st["chosen"] is False and st["shape"] == "star" and st["color"] == "ink"
    assert store.all() == {}


def test_every_write_is_audited_with_the_device_append_only(store, tmp_path):
    store.set("arturo", shape="heart", device="device:mac-1")
    store.reset("arturo", device="fleet")
    lines = [json.loads(x) for x in (tmp_path / "a.log").read_text().splitlines()]
    assert [x["action"] for x in lines] == ["set", "reset"]
    assert lines[0]["device"] == "device:mac-1" and lines[0]["shape"] == "heart"
    assert lines[1]["device"] == "fleet" and lines[1]["had_record"] is True


def test_a_hand_edited_bad_record_falls_back_to_the_default(store, tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"arturo": {"shape": "triangle"}, "gm": {"shape": "egg", "color": "ink", "eyeColor": "auto"},
                                                 "../evil": {"shape": "egg"}}))
    assert set(store.all()) == {"gm"}
    assert store.state("arturo")["chosen"] is False


def test_the_write_is_atomic_no_tmp_left_behind(store, tmp_path):
    store.set("arturo", shape="egg")
    assert not (tmp_path / "a.tmp").exists()
