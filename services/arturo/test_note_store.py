# test_note_store.py — VQ-10 remember_note supersede/conflict resolution.
from services.arturo import note_store as ns


def test_same_subject_contradiction_supersedes():
    existing = [{"note": "The acme deploy is broken and failing QA"}]
    new = {"note": "The acme deploy is fixed now and passing QA"}
    out, superseded = ns.supersede_notes(existing, new)
    assert superseded == 1
    assert len(out) == 1
    assert out[0] is new                         # only the newer truth survives


def test_unrelated_notes_coexist():
    existing = [{"note": "the operator prefers terse voice replies"}]
    new = {"note": "The globex one-pager needs a mobile fix"}
    out, superseded = ns.supersede_notes(existing, new)
    assert superseded == 0
    assert len(out) == 2


def test_exact_duplicate_dropped():
    existing = [{"note": "Never use port 5052"}]
    new = {"note": "never use PORT 5052"}
    out, superseded = ns.supersede_notes(existing, new)
    assert superseded == 1
    assert len(out) == 1 and out[0] is new


def test_multiple_prior_twins_all_superseded():
    existing = [
        {"note": "acme billing bandit is unstable"},
        {"note": "acme billing bandit throws 500s"},
        {"note": "the operator is in Eastern time"},
    ]
    new = {"note": "acme billing bandit is stable and shipped"}
    out, superseded = ns.supersede_notes(existing, new)
    assert superseded == 2                        # both bandit notes replaced
    texts = [n["note"] for n in out]
    assert "the operator is in Eastern time" in texts     # unrelated survives
    assert new in out


def test_cap_enforced():
    existing = [{"note": f"unrelated topic number {i} alpha{i}"} for i in range(60)]
    new = {"note": "a brand new distinct subject zzz"}
    out, _ = ns.supersede_notes(existing, new, cap=50)
    assert len(out) == 50
    assert out[-1] is new


def test_same_subject_threshold_conservative():
    # partial word overlap below threshold should NOT supersede
    a = "the acme audience builder pricing model"
    b = "the globex leaders proposal deck"
    assert not ns.same_subject(a, b)
    # clear same-subject
    assert ns.same_subject("jarvis voice latency wall", "jarvis voice latency is fixed")
