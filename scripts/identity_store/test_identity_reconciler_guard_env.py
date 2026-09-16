"""run_fleet_guards must DECLARE the guard-run context to the pytest subprocess it spawns
(ORCH_GUARD_RUN=1) so scripts/conftest.py skips its registry fingerprint compare — a live
promote / regenerator write during the run is not a hermeticity leak (gm msg_141aff5e)."""
import sys

sys.path.insert(0, "scripts")
from identity_store import identity_reconciler as ir  # noqa: E402

_PROBE = '''
import os
def test_guard_run_env_declared():
    assert os.environ.get("ORCH_GUARD_RUN") == "1"
'''


def test_default_guard_runner_exports_guard_run_env(tmp_path, monkeypatch):
    probe = tmp_path / "test_probe_env.py"
    probe.write_text(_PROBE)
    monkeypatch.setattr(ir, "FLEET_GUARD_TESTS", [str(probe)])
    monkeypatch.delenv("ORCH_GUARD_RUN", raising=False)
    rep = ir.run_fleet_guards(str(tmp_path))
    assert rep["returncode"] == 0, rep["stdout"] + rep["stderr"]
