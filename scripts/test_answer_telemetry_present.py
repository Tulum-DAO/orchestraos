"""B1 run-2 finding C: answer_telemetry was imported by watch_gateway, approval_resume and
questionnaire_resume but never shipped — every answered approval logged an ImportError."""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_answer_telemetry_imports_and_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("ANSWER_TELEMETRY_PATH", str(tmp_path / "t.jsonl"))
    mod = importlib.import_module("answer_telemetry")
    row = {"id": "apr_x_1", "from_agent": "hello", "answered_at": "2026-09-16T00:00:00+00:00", "kind": None, "worker_kind": "pane"}
    mod.log_submit(row)
    mod.log_delivery(row, lane="pane_inject", ok=True, reason="delivered", via="inject")
    lines = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[1]["lane"] == "pane_inject"
