"""RED tests — completion-mode confirm (DEC-1787808620 fix #1 + #3).

The spawn-time `rotation_gate` gates on floor AND comprehension AND effect: it proves
the successor is doing real live work (first_effect exists + live focus) BEFORE the
predecessor is killed. The multi-beat COMPLETION path re-confirms a HELD readback-only
successor whose only demonstrated artifact is context transfer — it has NO live focus
(parked by design) and its first_effect is a POST-promote artifact (item #4). Applying
the spawn-time contract to a held successor deadlocks (FINDING reasons #1 + #3).

`completion_mode=True` splits the gate: gate on COMPREHENSION only. Effect is NOT
required pre-promote (verified post-promote-else-rollback). The floor is satisfied via
observed.oriented == True instead of live focus membership — but genuine NOT-ORIENTED
and WRONG-FILE failures are KEPT. `completion_mode=False` (default) is byte-identical
to today.
"""
from scripts.focus_registry.gate import rotation_gate


# --- comprehension fixtures that PASS check_comprehension --------------------
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
    # keyed qN (what _split_readback_sections emits) vs cqN ground_truth ids
    "canary_answers": {"q1": "it holds 77eeb56", "q2": "app-dev runs fable-5"},
}

# A HELD readback-only successor: oriented (committed a readback), but NO live focus
# and its declared first_effect (a commit) does NOT exist yet.
_HELD_OBSERVED = {"successor": "pm-x-g2", "oriented": True, "state": "working"}
_EXPECTED = {"focus_id": "FOCUS-9", "focus_canonical": "the memory viz",
             "file_roots": ["scripts/memory/"], "next_actions": ["fix the schema"]}
_ABSENT_EFFECT = {"kind": "commit", "target": "deadbeefcafe"}  # git cat-file -> absent


def _gate(**over):
    args = dict(observed=_HELD_OBSERVED, expected=_EXPECTED,
                evidence=_GOOD_EVIDENCE, ground_truth=_GT,
                effect=_ABSENT_EFFECT, cwd=".",
                effect_runner=lambda argv, cwd=None: 1)  # every effect check -> absent
    args.update(over)
    return rotation_gate(**args)


def test_completion_mode_confirms_on_comprehension_without_effect():
    """A held successor: oriented + comprehended, NO live focus, first_effect absent.
    completion_mode confirms (effect not required pre-promote; focus floor exempt)."""
    r = _gate(completion_mode=True)
    assert r["confirmed"] is True, r["reasons"]


def test_default_mode_holds_same_held_successor():
    """Regression: WITHOUT completion_mode the SAME held successor does NOT confirm —
    the spawn-time contract still requires the effect + live focus."""
    r = _gate(completion_mode=False)
    assert r["confirmed"] is False
    assert any(x.startswith("effect-absent") for x in r["reasons"])
    assert any(x.startswith("floor:wrong focus") for x in r["reasons"])


def test_completion_mode_still_fails_not_oriented():
    """oriented==False is a GENUINE floor failure kept in completion mode."""
    r = _gate(completion_mode=True,
              observed={"successor": "pm-x-g2", "oriented": False, "state": "working"})
    assert r["confirmed"] is False
    assert any("not oriented" in x for x in r["reasons"])


def test_completion_mode_still_fails_wrong_file():
    """Editing outside the focus file roots is a GENUINE failure kept in completion mode."""
    r = _gate(completion_mode=True,
              observed={"successor": "pm-x-g2", "oriented": True, "state": "working",
                        "touched_files": ["totally/unrelated/x.py"]})
    assert r["confirmed"] is False
    assert any("wrong file area" in x for x in r["reasons"])


def test_completion_mode_still_requires_comprehension():
    """Comprehension is the near-sole absorption proof in completion mode — a
    not-absorbed readback still HOLDS (Hole 3 direction)."""
    bad = {"readback": {"goal": "x", "open_loops": [], "hazards": []},
           "canary_answers": {"q1": "nope", "q2": "nope"}}
    r = _gate(completion_mode=True, evidence=bad)
    assert r["confirmed"] is False
    assert "comprehension:not-absorbed" in r["reasons"]


def test_default_mode_effect_present_still_confirms():
    """completion_mode default off preserves the effect-present happy path exactly."""
    r = _gate(completion_mode=False,
              observed={"successor": "pm-x-g2", "oriented": True, "state": "working",
                        "works_on": "FOCUS-9"},
              effect_runner=lambda argv, cwd=None: 0)  # effect exists
    assert r["confirmed"] is True, r["reasons"]
