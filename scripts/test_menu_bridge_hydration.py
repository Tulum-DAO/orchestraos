"""bridge_one active-walk hydration tests (P1 field regression, moved surface).

bridge_one must, for a multi-part passive capture (walk_complete false), run the
ACTIVE capture walk (injected here) to hydrate the full parts[] before storing —
else the bridged card has no parts[] and the client fail-safes to raw. The walk
is a navigation-only Right/Left + restore (menu_capture_walk); at bridge time the
menu is parked/debounced. Injected walk_fn keeps these tests hermetic (no tmux).
"""
import importlib, sys, os
sys.path.insert(0, os.path.dirname(__file__))
mb = importlib.import_module("menu_bridge")


class _FakeStore:
    def __init__(self): self.created = []
    def create(self, **kw):
        self.created.append(kw); return f"apr_{len(self.created)}"


def _cap(walk_complete=False):
    return {"question": "Which surfaces?",
            "options": [{"n": "1", "label": "iOS", "checked": False}],
            "checkbox": True, "has_submit": True,
            "tabs": ["Surfaces", "Priority", "Submit"], "submit_tab_index": 2,
            "multipart": True, "part_count": 2, "part_index": 0,
            "walk_complete": walk_complete,
            "parts": [{"index": 0, "question": "Which surfaces?", "select": "multi",
                       "options": [{"n": "1", "label": "iOS", "checked": False}]}]}


def _walked_payload():
    # what menu_capture_walk returns after hydrating both parts
    return {"walk_complete": True, "part_count": 2,
            "parts": [{"index": 0, "question": "Which surfaces?", "select": "multi",
                       "options": [{"n": "1", "label": "iOS", "checked": False}]},
                      {"index": 1, "question": "What priority?", "select": "single",
                       "options": [{"n": "1", "label": "P0"}]}]}


def test_bridge_one_hydrates_multipart_via_walk():
    store = _FakeStore()
    calls = []
    def walk_fn(session):
        calls.append(session); return _walked_payload()
    rid = mb.bridge_one(store, "e2e-menu-scratch", _cap(walk_complete=False),
                        first_seen_ts=0, now=100, state="working",
                        walk_fn=walk_fn, reread_fn=lambda x: _cap(walk_complete=False))
    assert rid == "apr_1"
    assert calls == ["e2e-menu-scratch"]                  # active walk ran
    stored = store.created[0]["menu"]
    assert stored["walk_complete"] is True
    assert [p["index"] for p in stored["parts"]] == [0, 1]   # BOTH parts stored
    assert stored["part_count"] == 2


def test_bridge_one_single_part_does_not_walk():
    store = _FakeStore()
    calls = []
    single = {"question": "Q", "options": [{"n": "1", "label": "A"}],
              "selected_n": "1"}
    rid = mb.bridge_one(store, "s", single, 0, 100, "working",
                        walk_fn=lambda x: calls.append(x))
    assert rid == "apr_1" and calls == []                 # no walk for single-part


def test_bridge_one_skips_unhydratable_multipart_passive():
    # §1.1 (spec b606468dc): a multipart menu the passive path can't hydrate
    # (walk_complete stays false) must NOT be surfaced to the approval page — it
    # renders there as a broken single-part card. Skip it; it stays answerable in
    # the in-agent view (which hydrates on demand). The passive cron's _noop_walk_fn
    # returns None, so hydration never completes -> skip.
    store = _FakeStore()
    rid = mb.bridge_one(store, "s", _cap(walk_complete=False), 0, 100, "working",
                        walk_fn=mb._noop_walk_fn,
                        reread_fn=lambda x: _cap(walk_complete=False))
    assert rid is None
    assert store.created == []


def test_bridge_one_walk_incomplete_skips_unhydratable_multipart():
    # §1.1: if the active walk can't complete (menu changed / gone), the multipart
    # stays walk_complete false -> SKIP (do not surface a broken single-part card).
    # Supersedes the old "store passive fail-safe" behavior. Never raises.
    store = _FakeStore()
    def walk_fn(session):
        return {"walk_complete": False, "part_count": 1, "parts": [],
                "reason": "menu_gone"}
    rid = mb.bridge_one(store, "s", _cap(walk_complete=False), 0, 100, "working",
                        walk_fn=walk_fn, reread_fn=lambda x: _cap(walk_complete=False))
    assert rid is None
    assert store.created == []


def test_bridge_one_walk_error_skips_unhydratable_multipart():
    # §1.1: a walk error also leaves the multipart un-hydrated -> SKIP, never raise.
    store = _FakeStore()
    def walk_fn(session):
        raise RuntimeError("gateway down")
    rid = mb.bridge_one(store, "s", _cap(walk_complete=False), 0, 100, "working",
                        walk_fn=walk_fn, reread_fn=lambda x: _cap(walk_complete=False))
    assert rid is None
    assert store.created == []


def test_bridge_one_not_bridged_when_gate_fails():
    store = _FakeStore()
    # debounce not elapsed -> should_bridge False -> no walk, no create
    calls = []
    rid = mb.bridge_one(store, "s", _cap(), first_seen_ts=99, now=100,
                        state="working", walk_fn=lambda x: calls.append(x))
    assert rid is None and store.created == [] and calls == []


# --- §4 mid-navigation guard  ----------------------
# Immediately before the walk, re-read the pane; SKIP hydration (defer to next
# tick, store passive) if the menu's on-screen part is NOT part 0 — never Right/
# Left a menu the operator just started driving. Structural, not probabilistic.

def _cap_partidx(idx):
    c = _cap(walk_complete=False)
    c["part_index"] = idx
    return c

def _reread_other_question(x):
    # the operator navigated: the on-screen question is now a DIFFERENT part's question
    # (real menus can't report part_index, so identity is the question text).
    c = _cap(walk_complete=False)
    c["question"] = "What priority is this?"      # != the captured part-0 question
    return c

def test_bridge_one_skips_walk_when_question_changed():
    # reread shows a different part's question (the operator navigated) -> defer, passive
    store = _FakeStore()
    walked = []
    rid = mb.bridge_one(store, "s", _cap(walk_complete=False), 0, 100, "working",
                        walk_fn=lambda x: walked.append(x) or _walked_payload(),
                        reread_fn=_reread_other_question)
    assert walked == []                                   # walk NOT run (mid-nav defer)
    assert rid is None and store.created == []            # §1.1 skip (un-hydrated multipart)

def test_bridge_one_walks_when_reread_same_question():
    # reread shows the SAME part-0 question -> still on part 0 -> walk proceeds
    store = _FakeStore()
    walked = []
    rid = mb.bridge_one(store, "s", _cap(walk_complete=False), 0, 100, "working",
                        walk_fn=lambda x: walked.append(x) or _walked_payload(),
                        reread_fn=lambda x: _cap(walk_complete=False))
    assert walked == ["s"]                                 # same question -> walk
    assert store.created[0]["menu"]["walk_complete"] is True

def test_bridge_one_skips_walk_when_reread_menu_gone():
    # reread returns None (menu vanished) -> defer (never walk); §1.1: the multipart
    # stays un-hydrated -> SKIP (not surfaced). Supersedes the old passive-store.
    store = _FakeStore()
    walked = []
    rid = mb.bridge_one(store, "s", _cap(walk_complete=False), 0, 100, "working",
                        walk_fn=lambda x: walked.append(x) or _walked_payload(),
                        reread_fn=lambda x: None)
    assert walked == []                                   # walk NOT run
    assert rid is None and store.created == []            # §1.1 skip
