#!/usr/bin/env python3
"""demo_seed_cards.py — fixture cards for a first dashboard open (checklist B5).

Run by `orchestra init --demo` with ORCHESTRA_DIR = the data dir. Creates ONE pending card
of each kind from the three demo seats: an approval, a menu decision, a questionnaire and a
"waiting on you" human task. Every row is tagged feature='demo' and uses the inert
worker_kind='node' (no pane), and the resume beats ACK a demo row the moment it is answered
— answering a demo card never injects anywhere. Idempotent: each card dedups on its op_key
while pending. Never notifies (no push, no Telegram).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from approval_schema import ApprovalStore          # noqa: E402
from questionnaire_schema import QuestionnaireStore  # noqa: E402

DEMO = "demo"


def seed(a: ApprovalStore, q: QuestionnaireStore) -> dict:
    made = {}
    made["approval"] = a.create(
        from_agent="demo-builder", worker_kind="node", feature=DEMO, op_key="demo-approval",
        question="Deploy the demo landing page to staging?",
        summary="A fixture approval. Approve or deny it from the dashboard; nothing is deployed.",
        options=["approve", "deny"], risk_level="low", reversibility="reversible")
    made["menu"] = a.create(
        from_agent="demo-planner", worker_kind="node", feature=DEMO, op_key="demo-menu", kind="menu",
        question="Which task should the builder take next?",
        summary="A fixture multi-choice decision rendered as a menu.",
        options=["Fix the flaky test", "Write the release notes", "Refactor the router", "Type something else"],
        menu={"question": "Which task should the builder take next?",
              "options": [{"n": 1, "label": "Fix the flaky test"},
                          {"n": 2, "label": "Write the release notes"},
                          {"n": 3, "label": "Refactor the router"},
                          {"n": 4, "label": "Type something else", "input_kind": "free_text"}],
              "source_session": "demo-planner"})
    made["human_task"] = a.create(
        from_agent="demo-reviewer", worker_kind="node", feature=DEMO, op_key="demo-human-task",
        kind="human_task", question="[WAITING ON YOU] Plug in the test phone",
        block_task="Plug in the test phone and unlock it so the reviewer can install the build.",
        blocks_what="demo-reviewer", options=["done", "cant", "snooze"],
        summary="A fixture human task: something only a person can do. Mark it done, can't, or snooze.")
    made["questionnaire"] = q.create(
        from_agent="demo-reviewer", worker_kind="node", feature=DEMO, op_key="demo-questionnaire",
        title="Release readiness check",
        summary="A fixture questionnaire with a menu question and a free-text question.",
        questions=[
            {"prompt": "Is the changelog reviewed?", "kind": "menu", "menu": {"options": ["yes", "no"]}},
            {"prompt": "Anything the release notes should call out?", "kind": "free_text", "optional": True},
        ])
    return made


DEMO_QNR_ID = "demo-release-readiness"
DEMO_QNR_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Release readiness check</title>
<style>body{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem;color:#222}
.field{margin:1.2rem 0}label{display:block;font-weight:600;margin-bottom:.4rem}textarea,select{width:100%;padding:.5rem;font:inherit}
button{padding:.6rem 1.2rem;font:inherit;border:0;border-radius:6px;background:#2563eb;color:#fff}#msg{margin-top:1rem;color:#166534}</style></head>
<body><h1>Release readiness check</h1><p>A fixture questionnaire seeded by <code>orchestra init --demo</code>. Submitting it only records your answers.</p>
<div class="field"><label for="q1">Is the changelog reviewed?</label><select id="q1"><option value="">choose…</option><option>yes</option><option>no</option></select></div>
<div class="field"><label for="q2">Anything the release notes should call out? (optional)</label><textarea id="q2" rows="3"></textarea></div>
<button id="submitBtn" onclick="submitAnswers()">Submit</button><div id="msg"></div>
<script>
const QID = "__QID__";
async function submitAnswers(){
  const answers = {changelog_reviewed: document.getElementById('q1').value, release_notes: document.getElementById('q2').value};
  if(!answers.changelog_reviewed){ document.getElementById('msg').textContent='Please answer the first question.'; return; }
  const btn=document.getElementById('submitBtn'); btn.disabled=true;
  const r = await fetch('/api/questionnaires/'+QID+'/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({answers, submitted_at: new Date().toISOString()})});
  document.getElementById('msg').textContent = r.ok ? 'Submitted. Thanks!' : 'Submit failed: '+r.status;
}
</script></body></html>
"""


def seed_dashboard_questionnaire(data_dir: str) -> bool:
    """The web dashboard lists questionnaires from <data>/state/questionnaires/index.json and
    serves the form from the html_file it names (the DB questionnaire above is the phone/watch
    surface). Write one demo entry + form; idempotent. Returns True when added."""
    import json
    qdir = os.path.join(data_dir, "state", "questionnaires")
    os.makedirs(qdir, exist_ok=True)
    index_path = os.path.join(qdir, "index.json")
    try:
        with open(index_path) as fh:
            index = json.load(fh) or []
    except (OSError, ValueError):
        index = []
    html_rel = os.path.join("state", "questionnaires", f"{DEMO_QNR_ID}.html")
    with open(os.path.join(data_dir, html_rel), "w") as fh:
        fh.write(DEMO_QNR_HTML.replace("__QID__", DEMO_QNR_ID))
    if any(q.get("id") == DEMO_QNR_ID for q in index):
        return False
    from datetime import datetime, timezone
    index.append({
        "id": DEMO_QNR_ID, "title": "Release readiness check",
        "description": "Fixture questionnaire: two questions, answers are only recorded.",
        "question_count": 2, "status": "pending",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": "demo-reviewer", "assigned_to": "operator",
        "html_file": html_rel, "response_file": None, "demo": True,
    })
    with open(index_path, "w") as fh:
        json.dump(index, fh, indent=2)
    return True


def main() -> int:
    a = ApprovalStore(); a.migrate()
    q = QuestionnaireStore(); q.migrate()
    made = seed(a, q)
    data_dir = os.environ.get("ORCHESTRA_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    made["dashboard_questionnaire"] = seed_dashboard_questionnaire(data_dir)
    print("[demo] cards: " + ", ".join(f"{k}={v}" for k, v in made.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
