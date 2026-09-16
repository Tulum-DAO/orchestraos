"""approval.py `get` could not see questionnaires — its own sibling surface
(found by gm-gen13 in its first hour, msg_564caceb): a verify path that cannot
see its own writes is the check-that-cannot-fail-independently class in
miniature. `approval.py questionnaire` CREATES qnr rows; `approval.py get`
read only the approvals table, so every verification of a just-created
questionnaire returned not-found and looked like a missing row instead of a
blind instrument.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _get(qid):
    out = subprocess.run([sys.executable, "scripts/approval.py", "get", qid],
                         capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    return out.returncode, out.stdout


def test_get_resolves_a_real_questionnaire_id():
    """Real row, not a fixture: the live morning-package qnr."""
    import importlib.util
    sys.path.insert(0, str(ROOT / "scripts"))
    from questionnaire_schema import QuestionnaireStore
    rows = QuestionnaireStore().list_pending()
    if not rows:
        import pytest
        pytest.skip("no live questionnaire to verify against")
    qid = rows[0]["id"]
    rc, out = _get(qid)
    assert rc == 0, out
    assert json.loads(out)["id"] == qid


def test_get_still_reports_not_found_for_a_genuinely_missing_qnr():
    rc, out = _get("qnr_00000000_0")
    assert rc == 1
    assert json.loads(out)["error"] == "not found"
