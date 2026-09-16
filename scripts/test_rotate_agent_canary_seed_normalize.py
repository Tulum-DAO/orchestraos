"""RED-first (gm msg_d75d0d3b / gpt-6-astra-agent g3->g4, 2026-09-16): rotate_agent seeded the
canary VERBATIM from the baton, so (a) c1..c5 ids reached the readback prompt and the
successor keyed its sections c1..c5 while the grader accepts q<N> only -> a complete readback
HELD as incomplete; (b) prose source_pointers ('jsonl:turn-1 (user transcript block ...)',
'jsonl: final assistant turn, 2026-09-06T~07:5x') were copied although they can never resolve
-> every question graded unpointable. author_canary already refuses such pointers; the baton
seed path must apply the same discipline: normalize ids to q1..qN (keep orig_id) and reduce a
pointer to its resolvable locator or drop it with a recorded defect."""
import json
import tempfile
import unittest
from pathlib import Path

import rotate_agent

BATON = '''# HANDOFF x g3 -> g4
## canary_questions
- {id: c1, question: "What does the auto-summary get wrong?", source_pointer: "jsonl:turn-1 (user transcript block, 'Financials' vs exchange ~03:14-05:08)", expected_answer: "payer reversed"}
- {id: c2, question: "Which rail carried the Wise portion?", source_pointer: "jsonl: final assistant turn, 2026-09-06T~07:5x-08:00Z", expected_answer: "bank-funded"}
- {id: c3, question: "Which msg closed it?", source_pointer: "jsonl:msg_23c16ad7", expected_answer: "x"}
## LAST-COMMIT-SHA
abc
'''


class SeedNormalizes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.saved = {k: getattr(rotate_agent, k) for k in ("HANDOFFS_DIR", "DOCS_DIR")}
        rotate_agent.HANDOFFS_DIR = root / "state" / "agent-handoffs"
        rotate_agent.DOCS_DIR = root / "docs"
        rotate_agent.DOCS_DIR.mkdir(parents=True)
        (rotate_agent.DOCS_DIR / "HANDOFF_x-next.md").write_text(BATON)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(rotate_agent, k, v)
        self.tmp.cleanup()

    def test_ids_normalized_to_q_keys_and_pointers_sanitized(self):
        p = rotate_agent.ensure_canary_artifact("x-g4", "x")
        qs = json.loads(p.read_text())["questions"]
        self.assertEqual([q["id"] for q in qs], ["q1", "q2", "q3"])
        self.assertEqual([q.get("orig_id") for q in qs], ["c1", "c2", "c3"])
        # prose after a resolvable locator is stripped; the locator survives
        self.assertEqual(qs[0]["source_pointer"], "jsonl:turn-1")
        # no resolvable locator at all -> pointer dropped, defect recorded (never a prose pointer)
        self.assertIsNone(qs[1].get("source_pointer"))
        self.assertEqual(qs[1].get("pointer_defect"), "unresolvable-form")
        self.assertIn("final assistant turn", qs[1].get("orig_source_pointer", ""))
        # a clean pointer is untouched
        self.assertEqual(qs[2]["source_pointer"], "jsonl:msg_23c16ad7")


if __name__ == "__main__":
    unittest.main()
