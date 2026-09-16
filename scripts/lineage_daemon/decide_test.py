from lineage_daemon.decide import decide


def _agent(agent_id="a", tier_class="T2", ctx=None, death=None):
    return {
        "agent_id": agent_id,
        "tier_class": tier_class,
        "ctx": ctx or {},
        "death": death or {},
    }


# --- death signal dominates ctx ---

def test_court_death_hard_rotate_regardless_of_ctx():
    a = _agent(ctx={"status_bar_pct": 20}, death={"court": True})
    d = decide(a)
    assert d["action"] == "hard_rotate"
    assert d["reason"] == "death:court"
    assert d["death"] == "court"


# --- ctx-driven tiers ---

def test_hard_ctx_hard_rotate():
    a = _agent(ctx={"status_bar_pct": 95})
    d = decide(a)
    assert d["action"] == "hard_rotate"
    assert d["reason"] == "ctx:HARD"
    assert d["tier"] == "HARD"
    assert d["death"] is None


def test_soft_ctx_soft_handoff():
    a = _agent(ctx={"status_bar_pct": 75})
    d = decide(a)
    assert d["action"] == "soft_handoff"
    assert d["reason"] == "ctx:SOFT"
    assert d["tier"] == "SOFT"


def test_ok_ctx_noop():
    a = _agent(ctx={"status_bar_pct": 20})
    d = decide(a)
    assert d["action"] == "noop"
    assert d["reason"] == "ctx:ok"
    assert d["tier"] == "ok"


# --- T0/T1 kill-gate ---

def test_t0_hard_needs_approval():
    a = _agent(tier_class="T0", ctx={"status_bar_pct": 95})
    d = decide(a)
    assert d["action"] == "hard_rotate"
    assert d["needs_approval"] is True


def test_t2_hard_no_approval():
    a = _agent(tier_class="T2", ctx={"status_bar_pct": 95})
    d = decide(a)
    assert d["action"] == "hard_rotate"
    assert d["needs_approval"] is False


def test_t1_court_death_hard_rotate_needs_approval():
    a = _agent(tier_class="T1", ctx={"status_bar_pct": 20}, death={"court": True})
    d = decide(a)
    assert d["action"] == "hard_rotate"
    assert d["reason"] == "death:court"
    assert d["needs_approval"] is True


def test_noop_never_needs_approval():
    a = _agent(tier_class="T0", ctx={"status_bar_pct": 20})
    d = decide(a)
    assert d["action"] == "noop"
    assert d["needs_approval"] is False


# --- structure ---

def test_return_shape():
    d = decide(_agent(agent_id="xyz"))
    assert set(d.keys()) == {
        "agent_id", "action", "reason", "needs_approval", "tier", "death"
    }
    assert d["agent_id"] == "xyz"


def test_soft_t1_needs_no_approval():
    # only hard_rotate is gated; SOFT on T1 proceeds unblocked
    a = _agent(tier_class="T1", ctx={"status_bar_pct": 75})
    d = decide(a)
    assert d["action"] == "soft_handoff"
    assert d["needs_approval"] is False
