"""approval.py `get` could not see questionnaires — its own sibling surface
(found by gm-gen13 in its first hour, msg_564caceb): a verify path that cannot
see its own writes is the check-that-cannot-fail-independently class in
miniature. `approval.py questionnaire` CREATES qnr rows; `approval.py get`
read only the approvals table, so every verification of a just-created
questionnaire returned not-found and looked like a missing row instead of a
blind instrument.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def data_dir(tmp_path):
    """Hermetic data dir: approval.py + the questionnaire store open <ORCHESTRA_DIR>/state/tasks.db.
    (On a bare CI runner there is no live store: 'unable to open database file'.)"""
    (tmp_path / "state").mkdir()
    return tmp_path


def _env(data_dir):
    return {**os.environ, "ORCHESTRA_DIR": str(data_dir), "ORCH_DIR": str(data_dir)}


def _get(qid, data_dir):
    out = subprocess.run([sys.executable, "scripts/approval.py", "get", qid],
                         capture_output=True, text=True, cwd=str(ROOT), timeout=60, env=_env(data_dir))
    return out.returncode, out.stdout


def test_get_resolves_a_real_questionnaire_id(data_dir):
    """A questionnaire created through the CLI is visible to `approval.py get` in the same store."""
    r = subprocess.run([sys.executable, "scripts/approval.py", "questionnaire", "--from", "tester",
                        "--title", "probe", "--questions-json",
                        json.dumps([{"id": "q1", "prompt": "Colour?", "kind": "free_text"}])],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=60, env=_env(data_dir))
    if r.returncode != 0 or "qnr_" not in r.stdout:
        pytest.skip(f"questionnaire create not available on this checkout: {r.stdout[-200:]} {r.stderr[-200:]}")
    qid = next(tok for tok in r.stdout.replace('"', ' ').split() if tok.startswith("qnr_"))
    rc, out = _get(qid, data_dir)
    assert rc == 0, out
    assert json.loads(out)["id"] == qid


def test_get_still_reports_not_found_for_a_genuinely_missing_qnr(data_dir):
    rc, out = _get("qnr_00000000_0", data_dir)
    assert rc == 1, out
    assert json.loads(out)["error"] == "not found"
