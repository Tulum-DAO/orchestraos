"""A seat's rotation settings (scripts/seat_settings.py): fallback order, validation, history. Temp data dir."""
from __future__ import annotations

import json
import os
import stat

import pytest

from scripts import seat_settings as SS


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    (tmp_path / "state").mkdir()
    return tmp_path


def test_builtin_when_nothing_is_set(root):
    assert SS.rotation_settings("qa-demo") == {"auto_compact": False, "show_compaction": True, "compact_at": 65, "guard_pct": 90,
                                                 "source": {"auto_compact": "builtin", "show_compaction": "builtin", "compact_at": "builtin"}}


def test_fallback_order_seat_then_fleet_default_then_builtin(root):
    (root / "state" / "seat-settings-default.json").write_text(json.dumps({"default": {"compact_at": 70, "show_compaction": False}}))
    s = SS.rotation_settings("qa-demo")
    assert (s["compact_at"], s["show_compaction"], s["source"]) == (70, False, {"auto_compact": "builtin", "compact_at": "default", "show_compaction": "default"})
    SS.update_seat("qa-demo", {"compact_at": 50}, by="sample-user")
    s = SS.rotation_settings("qa-demo")
    assert (s["compact_at"], s["source"]["compact_at"], s["show_compaction"], s["source"]["show_compaction"]) == (50, "seat", False, "default")
    assert SS.rotation_settings("dev-demo")["compact_at"] == 70, "per agent"
    # a stored value outside the range (or the wrong type) is ignored, never clamped
    (root / "state" / "seat-settings.json").write_text(json.dumps({"seats": {"qa-demo": {"compact_at": 99, "show_compaction": "yes"}}}))
    s = SS.rotation_settings("qa-demo")
    assert (s["compact_at"], s["source"]["compact_at"], s["show_compaction"]) == (70, "default", False)
    (root / "state" / "seat-settings-default.json").write_text("{not json")
    assert SS.rotation_settings("qa-demo")["source"]["compact_at"] == "builtin"


def test_validation(root):
    for bad in ({"compact_at": 39}, {"compact_at": 86}, {"compact_at": 65.5}, {"compact_at": True}, {"compact_at": "65"},
                {"show_compaction": 1}, {"auto_compact": "on"}, {"auto_compact": 0}, {"guard_pct": 95}, {"other": 1}):
        with pytest.raises(ValueError):
            SS.update_seat("qa-demo", bad, by="sample-user")
    assert not (root / "state" / "seat-settings.json").exists(), "a refused change writes nothing"
    assert SS.update_seat("qa-demo", {"compact_at": 40}, by=None)["compact_at"] == 40
    assert SS.update_seat("qa-demo", {"compact_at": 85}, by=None)["compact_at"] == 85


def test_history_and_file_mode(root):
    SS.update_seat("qa-demo", {"compact_at": 60, "show_compaction": False}, by="sample-user")
    SS.update_seat("qa-demo", {"compact_at": 60}, by="sample-user")          # no change: no row
    SS.update_seat("qa-demo", {"compact_at": 75}, by="example-user")
    h = SS.history("qa-demo")
    assert [(r["field"], r["old"], r["new"], r["by"]) for r in h] == [
        ("compact_at", 65, 60, "sample-user"), ("show_compaction", True, False, "sample-user"), ("compact_at", 60, 75, "example-user")]
    assert all(r["seat"] == "qa-demo" and r["at"] for r in h)
    p = root / "state" / "seat-settings.json"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert json.loads(p.read_text())["seats"] == {"qa-demo": {"compact_at": 75, "show_compaction": False}}
    assert not [f for f in os.listdir(root / "state") if f.startswith(".seat-settings-")], "no temp file left"


def test_auto_compact_is_off_by_default_and_compact_at_is_kept_while_off(root):
    assert SS.rotation_settings("qa-demo")["auto_compact"] is False
    s = SS.update_seat("qa-demo", {"compact_at": 55}, by="sample-user")
    assert (s["auto_compact"], s["compact_at"]) == (False, 55), "kept while off"
    s = SS.update_seat("qa-demo", {"auto_compact": True}, by="sample-user")
    assert (s["auto_compact"], s["source"]["auto_compact"], s["compact_at"]) == (True, "seat", 55)
    assert [(r["field"], r["old"], r["new"]) for r in SS.history("qa-demo")][-1] == ("auto_compact", False, True)


def test_migration_from_the_retired_rotation_switch(root):
    (root / "state" / "headless-seats.json").write_text(json.dumps({"seats": {
        "qa-demo": {"headless": True, "rotation": True}, "dev-demo": {"headless": True}, "pm-demo": {"rotation": True}}}))
    SS.update_seat("pm-demo", {"auto_compact": False}, by="sample-user")      # already decided: left alone
    assert SS.migrate_rotation_switch() == ["qa-demo"]
    assert SS.migrate_rotation_switch() == [], "idempotent"
    assert SS.rotation_settings("qa-demo")["auto_compact"] is True
    assert SS.rotation_settings("pm-demo")["auto_compact"] is False
    assert SS.rotation_settings("dev-demo")["auto_compact"] is False
    assert [(r["seat"], r["field"], r["new"], r["by"]) for r in SS.history() if r["by"] == "migration"] == [("qa-demo", "auto_compact", True, "migration")]
    assert json.loads((root / "state" / "headless-seats.json").read_text())["seats"]["qa-demo"]["rotation"] is True, "the old file is untouched"
