"""Data-dir sweep S4: a feature whose backing script does not ship says so honestly.

scripts/canary_leak_lint.py is not in this repo. The lineage gate used to run it anyway; python exited 2,
"0 leaking" never appeared, and the grade artifact recorded leaking=1: a leak that was never detected.
Absent now reports leaking=None, installed=False, and H1 still fails closed (no clean verdict either).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lineage_gate import cli, rubric  # noqa: E402


def test_absent_leak_lint_is_reported_as_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "CODE", str(tmp_path))          # a checkout without the lint
    r = cli._leak_lint("h.md", "c.json")
    assert r["installed"] is False and r["leaking"] is None and r["calibration_passed"] is False
    assert "not installed" in r["raw"]


def test_h1_fails_closed_without_the_leak_lint():
    g = rubric.gate_h1_no_answer_key(None, [{"q": "x", "source_pointer": "y"}],
                                     {"leaking": None, "calibration_passed": False, "installed": False})
    assert g["passed"] is False
