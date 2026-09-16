"""RED-first tests for `wal probe` — the mechanical shadow-verify grader
(spec §3.3(3)). Green must return the seq+summary of the last K events and the
current working-set paths; the grader checks this against the WAL ITSELF — the
WAL is the answer key, there is NO LLM grader.
"""
from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.probe import probe_challenge, grade_probe


def _store(tmp_path):
    return WalStore(str(tmp_path / "l.db"))


def _ev(store, kind, summary, body_ref="p:0"):
    return store.append(ts=0.0, lineage_root="ios-watch-dev", generation=6,
                        sid="sid", runtime="claude", kind=kind, summary=summary,
                        body_ref=body_ref, source_path="p", source_off=0,
                        integrity="h")


def _seed(store):
    _ev(store, "prompt", "text (5 chars)")
    _ev(store, "tool_call", "Read")
    _ev(store, "file_mod", "M src/App.swift", body_ref="git:/cwd#src/App.swift")
    _ev(store, "tool_call", "Grep")
    _ev(store, "tool_result", "result (3 chars)")


def _truth_answer(store, k=3):
    evs = store.events("ios-watch-dev")[-k:]
    working = ["src/App.swift"]
    return {"last_events": [{"seq": r["seq"], "summary": r["summary"]}
                           for r in evs],
            "working_set": working}


def test_challenge_declares_k_and_working_set(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ch = probe_challenge(store, "ios-watch-dev", k=3)
    assert ch["k"] == 3
    assert ch["require_working_set"] is True


def test_correct_answer_passes(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    res = grade_probe(store, "ios-watch-dev", _truth_answer(store, 3), k=3)
    assert res["ok"] is True
    assert res["last_events_ok"] is True and res["working_set_ok"] is True


def test_wrong_seq_fails(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ans = _truth_answer(store, 3)
    ans["last_events"][0]["seq"] = 999
    res = grade_probe(store, "ios-watch-dev", ans, k=3)
    assert res["ok"] is False and res["last_events_ok"] is False


def test_wrong_summary_fails(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ans = _truth_answer(store, 3)
    ans["last_events"][-1]["summary"] = "fabricated"
    res = grade_probe(store, "ios-watch-dev", ans, k=3)
    assert res["ok"] is False and res["last_events_ok"] is False


def test_missing_working_set_path_fails(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ans = _truth_answer(store, 3)
    ans["working_set"] = []
    res = grade_probe(store, "ios-watch-dev", ans, k=3)
    assert res["ok"] is False and res["working_set_ok"] is False


def test_hallucinated_event_not_in_wal_fails(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ans = _truth_answer(store, 3)
    ans["last_events"].append({"seq": 6, "summary": "Bash (never happened)"})
    res = grade_probe(store, "ios-watch-dev", ans, k=3)
    # WAL is the answer key: an extra event Green invents is graded against truth
    assert res["ok"] is False and res["last_events_ok"] is False


# ---- delivered-scope-aware grading (bar#4 gap-c fix) ----------------------------

def _ev2(store, kind, summary, body_ref="p:0", root="ios-watch-dev"):
    return store.append(ts=0.0, lineage_root=root, generation=6, sid="sid",
                        runtime="claude", kind=kind, summary=summary,
                        body_ref=body_ref, source_path="p", source_off=0,
                        integrity="h")


def _seed8(store):
    """8 events, file_mod at seq 1,3,5,7 (working-set sources)."""
    _ev2(store, "file_mod", "M a.py", "git:/c#a.py")   # 1
    _ev2(store, "response", "r2")                        # 2
    _ev2(store, "file_mod", "M b.py", "git:/c#b.py")     # 3
    _ev2(store, "response", "r4")                         # 4
    _ev2(store, "file_mod", "M c.py", "git:/c#c.py")     # 5
    _ev2(store, "response", "r6")                          # 6
    _ev2(store, "file_mod", "M d.py", "git:/c#d.py")      # 7
    _ev2(store, "response", "r8")                          # 8


def _answer_in_scope(store, root, since, through, k):
    """The answer a FAITHFULLY-hydrated mid-life green produces: it saw only the
    delivered delta (since, through], so its last-K + working-set come from THAT
    slice — exactly what a scope-aware grader must accept."""
    slc = [r for r in store.events(root) if since < r["seq"] <= through]
    last = [{"seq": r["seq"], "summary": r["summary"]} for r in slc[-k:]]
    ws = []
    for r in slc:
        if r["kind"] == "file_mod" and r["body_ref"] and "#" in r["body_ref"]:
            p = r["body_ref"].split("#", 1)[1]
            if p and p not in ws:
                ws.append(p)
    return {"last_events": last, "working_set": ws}


def test_scope_grades_only_the_delivered_slice(tmp_path):
    store = _store(tmp_path)
    _seed8(store)
    scope = {"since_seq": 2, "through_seq": 6}
    ans = _answer_in_scope(store, "ios-watch-dev", 2, 6, k=3)   # seq 4,5,6
    res = grade_probe(store, "ios-watch-dev", ans, k=3, scope=scope)
    assert res["ok"] is True
    assert res["expected_last_events"] == [{"seq": 4, "summary": "r4"},
                                           {"seq": 5, "summary": "M c.py"},
                                           {"seq": 6, "summary": "r6"}]
    # working-set within scope = b.py (seq3 is <=2? no, 3>2) ... only file_mod in (2,6]: seq3,5
    assert res["expected_working_set"] == ["b.py", "c.py"]


def test_mid_life_green_false_fails_on_full_wal_but_passes_on_scope(tmp_path):
    """gap-c: the delivered delta is a STRICT SUBSET of the full WAL. Full-WAL
    grading (scope=None) false-fails a perfect mid-life green; scope grading passes."""
    store = _store(tmp_path)
    _seed8(store)
    # mid-life green: baseline B=4, delivered (4, 8]
    ans = _answer_in_scope(store, "ios-watch-dev", 4, 8, k=5)
    full = grade_probe(store, "ios-watch-dev", ans, k=5)               # no scope
    scoped = grade_probe(store, "ios-watch-dev", ans, k=5,
                         scope={"since_seq": 4, "through_seq": 8})
    assert full["ok"] is False        # false-fail without the fix
    assert scoped["ok"] is True       # correct under delivered scope


def test_scope_none_is_full_wal_backcompat(tmp_path):
    store = _store(tmp_path)
    _seed8(store)
    full_ans = _answer_in_scope(store, "ios-watch-dev", 0, 8, k=5)
    assert grade_probe(store, "ios-watch-dev", full_ans, k=5)["ok"] is True


def test_real_loss_within_scope_still_fails(tmp_path):
    """The fix must NOT weaken the signal: a delivery that DROPPED an event inside
    the scope (green's answer missing it) still fails under scope grading."""
    store = _store(tmp_path)
    _seed8(store)
    scope = {"since_seq": 2, "through_seq": 6}
    ans = _answer_in_scope(store, "ios-watch-dev", 2, 6, k=3)
    ans["last_events"] = ans["last_events"][1:]   # drop seq4 = lossy delivery
    assert grade_probe(store, "ios-watch-dev", ans, k=3, scope=scope)["ok"] is False


def test_scope_working_set_loss_fails(tmp_path):
    store = _store(tmp_path)
    _seed8(store)
    scope = {"since_seq": 2, "through_seq": 6}
    ans = _answer_in_scope(store, "ios-watch-dev", 2, 6, k=3)
    ans["working_set"] = ans["working_set"][:-1]   # drop c.py = lossy
    assert grade_probe(store, "ios-watch-dev", ans, k=3, scope=scope)["working_set_ok"] is False
