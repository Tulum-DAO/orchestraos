"""RED tests — completion-mode S3 confirm seam (DEC-1787808620 fix #1).

The completion PROVIDER must re-confirm a HELD readback-only successor on a later
beat gating on COMPREHENSION only (effect is a post-promote artifact). This exercises
the REAL rotation_gate through the REAL two_sample_confirm — only the completion_mode
flag differs. The spawn-time confirm (completion_mode=False) is UNCHANGED: the same
held successor still HOLDS there (effect absent + no live focus).
"""
from scripts.lineage_daemon import s3_confirm_seam as s3

_GT = {
    "goal": "ship the audience staging chart",
    "guards": ["single-trunk", "never co-edit agents.ts"],
    "open_loops": ["merge-9 holding land of 77eeb56", "filter-scaling fix owed to opus-11"],
    "hazards": ["prod test-row dddddddd must be cleaned", "staging logo-seed unexecuted"],
    "canary": [
        {"id": "cq1", "question": "which commit is merge-9 holding?", "answer": "77eeb56"},
        {"id": "cq2", "question": "what model does app-dev run?", "answer": "fable-5"},
    ],
}
_GOOD_EVIDENCE = {
    "readback": {
        "goal": "shipping the staging chart for audiences",
        "guards": ["stay single-trunk", "don't co-edit agents.ts"],
        "open_loops": ["merge-9 is sitting on the 77eeb56 land", "owe opus-11 the filter-scaling fix"],
        "hazards": ["clean the dddddddd prod test row", "logo-seed on staging still not run"],
    },
    "canary_answers": {"q1": "it holds 77eeb56", "q2": "app-dev runs fable-5"},
}
_HELD_OBSERVED = {"successor": "pm-x-g2", "oriented": True, "state": "working"}


def _held_seams():
    """A held readback-only successor: oriented + comprehended, NO live focus, and a
    declared first_effect (a commit) that does NOT exist yet. No gate_fn -> the factory
    default (REAL rotation_gate) is used."""
    def read_successor():
        return {"observed": dict(_HELD_OBSERVED), "evidence": _GOOD_EVIDENCE}
    return {
        "expected": {"focus_id": "FOCUS-9", "focus_canonical": "the memory viz",
                     "file_roots": ["scripts/memory/"], "next_actions": ["fix the schema"]},
        "ground_truth": _GT,
        "first_effect": {"kind": "commit", "target": "deadbeefcafe"},  # absent
        "read_successor": read_successor,
        "correction_fn": lambda observed, expected, nonce: f"correct {nonce}",
        "inject_correction": lambda succ, text, nonce: None,
        "settle_fn": lambda: None,
        "effect_runner": lambda argv, cwd=None: 1,   # effect check -> absent
        "max_rounds": 1,
    }


def test_completion_mode_confirms_held_successor():
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: _held_seams(),
                                completion_mode=True)
    out = fn("pm-x", "pm-x-g2")
    assert out["outcome"] == "confirmed", out


def test_spawn_mode_holds_same_held_successor():
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: _held_seams(),
                                completion_mode=False)
    out = fn("pm-x", "pm-x-g2")
    assert out["outcome"] == "held", out
