"""B5 by effect, hermetic: scripts/demo_seed_cards.py fills a fresh data dir with one card of
each kind (approval, menu, questionnaire, human task) from the demo seats, tagged
feature='demo' with the inert worker_kind='node', and is idempotent."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def _env(data, home):
    return dict(os.environ, ORCHESTRA_DIR=str(data), HOME=str(home),
                APPROVAL_DDL_ARMED="m20260825_answer_attribution,m20260825_human_task",
                PYTHONPATH=os.pathsep.join([os.path.join(ROOT, "scripts"), ROOT]))


def _seed(data, home):
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "demo_seed_cards.py")],
                          env=_env(data, home), capture_output=True, text=True, timeout=120)


def test_demo_seed_creates_one_card_of_each_kind_and_is_idempotent(tmp_path):
    data = tmp_path / "data"; (data / "state").mkdir(parents=True); home = tmp_path / "home"; home.mkdir()
    r = _seed(data, home)
    assert r.returncode == 0, r.stderr
    from approval_schema import ApprovalStore
    from questionnaire_schema import QuestionnaireStore
    a = ApprovalStore(db_path=str(data / "state" / "tasks.db"))
    q = QuestionnaireStore(db_path=str(data / "state" / "tasks.db"))
    def pending(store):
        c = store._conn()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM approval_requests WHERE status='pending'")]
        finally:
            c.close()
    rows = [x for x in pending(a) if x.get("feature") == "demo"]
    kinds = sorted((x.get("kind") or "approval") for x in rows)
    assert kinds == ["approval", "human_task", "menu"], kinds
    assert all(x["worker_kind"] == "node" for x in rows)
    assert all(x["from_agent"].startswith("demo-") for x in rows)
    qs = [x for x in q.list_pending() if x.get("feature") == "demo"]
    assert len(qs) == 1 and qs[0]["worker_kind"] == "node"
    import json
    index = json.load(open(data / "state" / "questionnaires" / "index.json"))
    assert [q["id"] for q in index] == ["demo-release-readiness"] and index[0]["status"] == "pending"
    assert (data / index[0]["html_file"]).exists() and "demo-release-readiness" in (data / index[0]["html_file"]).read_text()
    r2 = _seed(data, home)
    assert r2.returncode == 0, r2.stderr
    assert len([x for x in pending(a) if x.get("feature") == "demo"]) == 3
    assert len([x for x in q.list_pending() if x.get("feature") == "demo"]) == 1
    assert len(json.load(open(data / "state" / "questionnaires" / "index.json"))) == 1
