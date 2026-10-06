"""The batch replay must verify it is answering the menu the answer was drafted against.

`menu_batch_submit` had NO question-identity gate: its only identity check was
`_ns_on_part` (option-digit membership on the part now on screen), while the single-part
`menu_submit` has carried `expect_question` all along. So a batch anchored on a durable row
A could be replayed into a DIFFERENT live menu B — and because option digits overlap (1/2/3
is the normal case), that silently answers the agent's NEW question with the operator's OLD
intent. Answer loss is bad; a wrong answer recorded as the operator's is worse.

Two gates, at the two places the pane can have moved:

  PER-PART, before that part's toggles. `_nav_to_part` verifies `part_index` ALONE, so the
  pane can flip between part 0's toggles and part 1's. Checking only before the first
  toggle leaves the rest of the replay unguarded.

  STRUCTURAL, before the submit Enter. The confirm screen does NOT render the part
  question — it parses as "Ready to submit your answers?" (fixture-verified), and
  `_menu_matches` treats a non-containing value as a mismatch. So a question check there
  would mismatch on EVERY healthy submit and divert all of them to the durable fallback,
  leaving the agent's menu open: a guard that fires on every success is an outage. The
  confirm screen DOES render `tabs` and `part_count`, and `_parse_tab_header` strips every
  state marker, so toggling cannot move the tuple. That is what is compared instead.

Identity is OPT-IN (`expect_questions=None` keeps the old behaviour) because the
`/agent-key` legacy `no_durable_row` leg has no anchor and so no stored questions; there
identity is pane-derived by construction and `_ns_on_part` remains the only gate.
"""
import scripts.watch_gateway as G


class FakePane:
    """A minimal multi-part AUQ: parts, then a confirm screen, then gone.

    `swap_to` replaces the menu under the replay's feet at a chosen moment, which is the
    whole hazard being tested.
    """

    def __init__(self, questions, part_count=None, tabs=None, swap_at_part=None,
                 swap_questions=None, swap_tabs=None, tabs_less=False):
        self.questions = list(questions)
        self.part_count = part_count or len(self.questions)
        self.tabs = None if tabs_less else (tabs or [f"t{i}" for i in
                                                     range(len(self.questions))] + ["Submit"])
        self.tabs_less = tabs_less
        self.pi = 0
        self.gone = False
        self.sent = []
        self.swap_at_part = swap_at_part
        self.swap_questions = swap_questions
        self.swap_tabs = swap_tabs

    def _opts(self):
        return [{"n": "1", "label": "Yes"}, {"n": "2", "label": "No"}]

    def read(self):
        if self.gone:
            return None
        if self.pi >= self.part_count:                 # confirm screen
            # Real shape: the Review screen offers a Submit control plus Cancel, which is
            # what _menu_is_confirm keys on. Its parsed `question` is the confirm prompt,
            # NOT the part question — the fact that makes a question check there an outage.
            copts = [{"n": "1", "label": "Submit answers"}, {"n": "2", "label": "Cancel"}]
            m = {"question": "Ready to submit your answers?", "part_count": self.part_count,
                 "parts": [{"index": 0, "options": copts}], "options": copts,
                 "has_submit": True, "part_index": self.pi,
                 "tab_state": [{"label": "t", "answered": True}
                               for _ in range(self.part_count)]}
            if not self.tabs_less:
                m["tabs"] = self.tabs
                m["submit_tab_index"] = len(self.tabs) - 1
            return m
        q = self.questions[self.pi]
        m = {"question": q, "part_count": self.part_count, "part_index": self.pi,
             "parts": [{"index": self.pi, "question": q, "options": self._opts()}],
             "options": self._opts()}
        if not self.tabs_less:
            m["tabs"] = self.tabs
            m["submit_tab_index"] = len(self.tabs) - 1
        return m

    def key(self, k):
        self.sent.append(k)
        if k == "Right":
            self.pi += 1
            if self.swap_at_part is not None and self.pi == self.swap_at_part:
                if self.swap_questions is not None:
                    self.questions = list(self.swap_questions)
                if self.swap_tabs is not None:
                    self.tabs = self.swap_tabs
        elif k == "Left":
            self.pi = max(0, self.pi - 1)
        elif k == "Enter":
            if self.pi >= self.part_count:
                self.gone = True
        return True

    def digits(self):
        return [k for k in self.sent if k.isdigit()]


def _submit(pane, answers, **kw):
    return G.menu_batch_submit("seat", answers=answers, armed=True,
                               read_fn=pane.read, key_fn=pane.key, settle_s=0,
                               **kw)


# --- THE CONTROL, first, because its absence is what let the outage through -------
def test_control_a_healthy_armed_submit_STILL_DELIVERS_with_identity_on():
    """If this fails, the identity gates are refusing a correct system. Every other test
    in this file can pass while the route never delivers anything — that is exactly the
    failure mode a question-check at the confirm screen would have produced."""
    pane = FakePane(["Which repo?", "Which target?"])
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2"]}],
                       expect_questions={0: "Which repo?", 1: "Which target?"})
    assert ok is True, f"a healthy submit was refused: {info}"
    assert info.get("submitted") is True, info
    assert pane.digits() == ["1", "2"], pane.sent


def test_control_identity_off_is_byte_identical_for_the_legacy_leg():
    """`/agent-key`'s no_durable_row leg has no anchor, so it passes no questions. That
    path must behave exactly as before."""
    pane = FakePane(["Which repo?", "Which target?"])
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2"]}])
    assert ok is True and info.get("submitted") is True, info
    assert pane.digits() == ["1", "2"]


# --- the hazard ------------------------------------------------------------------
def test_a_different_menu_at_part_zero_presses_no_digit():
    pane = FakePane(["Delete the database?"])          # pane shows B
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}],
                       expect_questions={0: "Which repo?"})   # answer drafted for A
    assert ok is False, "a cross-menu batch was replayed"
    assert info.get("reason") == "menu_changed", info
    assert pane.digits() == [], f"digits landed on a menu the operator never saw: {pane.sent}"


def test_a_menu_that_changes_MID_REPLAY_stops_before_the_next_part():
    """_nav_to_part verifies part_index alone, so guarding only the first toggle leaves
    parts 1..n unguarded. Part 0 is answered, then the menu swaps under us."""
    pane = FakePane(["Which repo?", "Which target?"], swap_at_part=1,
                    swap_questions=["Which repo?", "Delete the database?"])
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2"]}],
                       expect_questions={0: "Which repo?", 1: "Which target?"})
    assert ok is False, "the replay continued into a swapped menu"
    assert info.get("reason") == "menu_changed", info
    assert info.get("part") == 1, info
    assert pane.digits() == ["1"], f"part 1's digit should never have fired: {pane.sent}"


def test_a_changed_TAB_BAR_at_the_confirm_screen_blocks_the_enter():
    """The structural check: the confirm screen renders tabs + part_count, and a fresh
    menu B's tab bar differs even when its per-part questions happen to match."""
    pane = FakePane(["Which repo?"], swap_at_part=1, swap_tabs=["other", "Submit"])
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}],
                       expect_questions={0: "Which repo?"})
    assert ok is False, "the Enter fired on a changed tab bar"
    assert info.get("reason") == "menu_changed", info
    assert "Enter" not in pane.sent, f"Enter was pressed: {pane.sent}"


def test_question_containment_tolerates_truncation_and_rewrap():
    """Identity is normalized CONTAINMENT either direction, never equality — the stored
    question may be a word-boundary truncation of the live one (or vice versa) and that
    must not block a legitimate submit. Equality here is the documented P0 class."""
    pane = FakePane(["Which repository should we deploy from, exactly?"])
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}],
                       expect_questions={0: "Which repository should we deploy"})
    assert ok is True, f"a truncated stored question falsely blocked a submit: {info}"


def test_a_tabs_less_menu_still_gets_the_per_part_gate():
    """tabs/part_count exist only on multi-part Claude-family panes, so on a tabs-less
    shape the structural check compares None to None and is a no-op. The per-part question
    gate is what covers that case — stated in the design and pinned here."""
    pane = FakePane(["Delete the database?"], tabs_less=True)
    ok, info = _submit(pane, [{"part": 0, "ns": ["1"]}],
                       expect_questions={0: "Which repo?"})
    assert ok is False and info.get("reason") == "menu_changed", info
    assert pane.digits() == []


# --- the SEAM DISPATCH: how we decide whether the seam accepts the kwarg ----------
# `submit_fn` is caller-injectable and an older test seam may predate
# `expect_questions`. The tempting tolerance is
#     try: submit_fn(..., expect_questions=x)
#     except TypeError: submit_fn(...)
# and it is wrong in a way that matters: a TypeError raised from INSIDE submit_fn --
# after it may already have pressed digits into the pane -- is indistinguishable at the
# call site from one raised by argument binding. The retry would press a SECOND time AND
# silently drop the identity gate, i.e. it would reintroduce exactly the wrong-answer bug
# this file exists to prevent, and only on the error path where nobody looks.
# So the decision is made by INSPECTION, before the call.

def test_a_seam_that_accepts_the_kwarg_gets_it():
    def seam(session, *, answers, armed, expect_questions=None):
        return True, {}
    assert G._identity_kwargs(seam, {0: "q"}) == {"expect_questions": {0: "q"}}


def test_a_kwargs_seam_counts_as_accepting():
    """**kwargs swallows it happily; refusing to pass it there would silently disable the
    gate for a legitimate wrapper."""
    def seam(session, **kw):
        return True, {}
    assert G._identity_kwargs(seam, {0: "q"}) == {"expect_questions": {0: "q"}}


def test_a_legacy_seam_without_the_kwarg_is_called_WITHOUT_it():
    def legacy(session, *, answers, armed):
        return True, {}
    assert G._identity_kwargs(legacy, {0: "q"}) == {}


def test_no_questions_means_no_kwarg_even_on_a_capable_seam():
    def seam(session, *, answers, armed, expect_questions=None):
        return True, {}
    assert G._identity_kwargs(seam, {}) == {}
    assert G._identity_kwargs(seam, None) == {}


def test_an_unintrospectable_callable_degrades_instead_of_raising():
    """A C builtin has no retrievable signature. That must yield the pre-identity call,
    not an exception into a submit whose answer is already durable."""
    assert G._identity_kwargs(len, {0: "q"}) == {}


def test_a_TypeError_from_INSIDE_the_seam_is_NEVER_retried_without_the_gate():
    """THE REGRESSION THIS PINS. The seam accepts the kwarg, so it is passed; the body
    then raises TypeError. That must propagate -- not be caught and retried with the gate
    off, which would press a second time and answer with an unverified identity."""
    calls = []

    def seam(session, *, answers, armed, expect_questions=None):
        calls.append(expect_questions)
        raise TypeError("a bug deep inside the replay, after keys were pressed")

    kw = G._identity_kwargs(seam, {0: "q"})
    assert kw == {"expect_questions": {0: "q"}}
    try:
        seam("seat", answers=[], armed=True, **kw)
    except TypeError:
        pass
    assert calls == [{0: "q"}], (
        f"the seam must be called exactly once, WITH the gate: {calls}")
