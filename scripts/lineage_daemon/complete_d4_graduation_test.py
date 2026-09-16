"""D4 gm MUST-PROVE (DEC-1787789209): the pre-promote ALIAS-name graduation
resolution is correct BY EFFECT — the REAL grader (rotation_gate_manual.record_readback)
writes `<alias>.comprehension.json` with mode:strict + pass:true, and the REAL consumer
(self_retire_gate.is_graduated_autoretire, reading `<seat.successor>.comprehension.json`
with seat.successor == the pre-promote alias) resolves THAT SAME file -> graduation True.

This is the producer->consumer proof the hand-written-artifact tests cannot give: it
proves the grader writes the artifact under the exact name the graduation gate looks up,
so a completion that grades an alias then checks graduation on that alias never SILENTLY
MISSES a real strict pass on the destructive gate. Wrong-name / supervised / absent all
resolve to False (the human card stays).
"""
import importlib.util
import json
from pathlib import Path

from scripts.lineage_daemon import self_retire_gate as SG

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "rotation_gate_manual", _ROOT / "scripts" / "rotation_gate_manual.py")
RG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RG)

_ALIAS = "pm-x-g3"          # the pre-promote alias name (== seat.successor at completion)
_READBACK = ("I absorbed the inherited goal, the standing guards, the open loops and "
             "the top hazards in my own words after reading the predecessor jsonl.")


def _produce_strict(handoffs_dir, alias=_ALIAS, *, strict=True):
    """Run the REAL grader to write <alias>.comprehension.json under handoffs_dir."""
    RG.record_readback(alias, _READBACK, [], {},
                       mode="strict" if strict else "supervised",
                       successor_sid="sess-live-L")


def _paths(tmp_path, handoffs_dir, *, lineage="pm-x", tier="T2", armed=("pm-x",)):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"agents": {lineage: {"tier": tier}}}))
    armf = tmp_path / "self_retire_armed"
    armf.write_text("\n".join(armed) + "\n")
    return dict(disabled_path=str(tmp_path / "SELF_RETIRE_DISABLED"),
                armed_path=str(armf), registry_path=str(reg),
                handoffs_dir=str(handoffs_dir))


def _seat(successor=_ALIAS):
    return {"agent_id": "pm-x-gen2", "successor": successor, "lineage_root": "pm-x"}


def test_d4_real_grader_alias_artifact_grants_graduation(tmp_path, monkeypatch):
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    monkeypatch.setattr(RG, "HANDOFFS_DIR", str(handoffs))
    _produce_strict(handoffs, strict=True)
    # by effect: the artifact the REAL grader wrote carries mode:strict + pass:true.
    art = json.loads((handoffs / f"{_ALIAS}.comprehension.json").read_text())
    assert art["mode"] == "strict" and art["pass"] is True
    # the consumer, seat.successor == the pre-promote alias -> resolves THAT file -> True.
    assert SG.is_graduated_autoretire(_seat(), **_paths(tmp_path, handoffs)) is True


def test_d4_wrong_name_misses_and_stays_carded(tmp_path, monkeypatch):
    """If the graduation lookup name diverges from the alias the grade was written
    under (the D5 hole), the artifact is not found -> False (card stays)."""
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    monkeypatch.setattr(RG, "HANDOFFS_DIR", str(handoffs))
    _produce_strict(handoffs, strict=True)          # artifact under pm-x-g3
    # a seat whose successor is the POST-promote canonical name (not the alias graded)
    assert SG.is_graduated_autoretire(
        _seat(successor="pm-x"), **_paths(tmp_path, handoffs)) is False


def test_d4_real_supervised_grade_stays_carded(tmp_path, monkeypatch):
    """A REAL grade written in supervised mode -> not a strict pass -> False."""
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    monkeypatch.setattr(RG, "HANDOFFS_DIR", str(handoffs))
    _produce_strict(handoffs, strict=False)         # supervised
    assert SG.is_graduated_autoretire(_seat(), **_paths(tmp_path, handoffs)) is False
