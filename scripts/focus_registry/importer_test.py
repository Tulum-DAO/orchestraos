"""Integration: read an audit markdown file -> write the focus store (WS1)."""
from scripts.focus_registry.importer import import_from_audit, latest_audit_path
from scripts.focus_registry.store import load_store

_AUDIT = """### ACME-APP

| Focus | Agents | %done | Rel | Imp | Notes |
|---|---|---|---|---|---|
| **Meta destinations** | **meta-destinations (KEEP, live owner)** | 90% | H | H | Live lane. |
"""


def test_import_from_audit_reads_file_and_writes_store(tmp_path):
    audit = tmp_path / "FLEET_AUDIT.md"
    audit.write_text(_AUDIT)
    store_path = tmp_path / "focus-registry.json"
    import_from_audit(str(audit), str(store_path))
    store = load_store(str(store_path))
    ent = store["entities"]["focus:meta-destinations"]
    assert ent["owner"] == "agent:meta-destinations"
    assert ent["attrs"]["pct_done"] == 90
    assert store["source"] == str(audit)


def test_latest_audit_path_picks_newest_dated_file(tmp_path):
    (tmp_path / "FLEET_AUDIT_2026-08-10.md").write_text("x")
    (tmp_path / "FLEET_AUDIT_2026-08-14.md").write_text("y")
    (tmp_path / "FLEET_AUDIT_2026-08-12.md").write_text("z")
    assert latest_audit_path(str(tmp_path)).endswith("FLEET_AUDIT_2026-08-14.md")


def test_latest_audit_path_none_when_absent(tmp_path):
    assert latest_audit_path(str(tmp_path)) is None
