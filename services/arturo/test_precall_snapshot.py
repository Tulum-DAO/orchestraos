from services.arturo import precall_snapshot as ps


def test_snapshot_composes_all_local_parts():
    snap = ps.build(
        page="Approvals",
        gm_tail=lambda: ["operator: ship it", "gm: shipped"],
        approvals_count=lambda: 4,
        fleet_line=lambda: "12 working, 3 waiting",
    )
    assert "Approvals" in snap
    assert "4" in snap
    assert "12 working, 3 waiting" in snap
    assert "shipped" in snap


def test_snapshot_degrades_when_a_source_throws():
    snap = ps.build(
        page="Agents",
        gm_tail=lambda: (_ for _ in ()).throw(RuntimeError("x")),
        approvals_count=lambda: 0,
        fleet_line=lambda: "idle",
    )
    assert "Agents" in snap and "idle" in snap
