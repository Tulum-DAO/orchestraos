from lineage_daemon.death import death_signal


def _healthy(**overrides):
    obs = {
        "court": False,
        "session_alive": True,
        "jsonl_growing": True,
        "state": "working",
        "state_age_s": 0,
    }
    obs.update(overrides)
    return obs


# --- signals in isolation ---

def test_court_signal():
    assert death_signal(_healthy(court=True)) == "court"

def test_oom_signal_session_dead():
    assert death_signal(_healthy(session_alive=False)) == "oom"

def test_wedged_signal_stalled_over_threshold():
    assert death_signal(_healthy(state="stalled", state_age_s=600)) == "wedged"

def test_wedged_signal_well_over_threshold():
    assert death_signal(_healthy(state="stalled", state_age_s=1200)) == "wedged"


# --- priority ordering ---

def test_court_beats_oom():
    obs = _healthy(court=True, session_alive=False)
    assert death_signal(obs) == "court"

def test_court_beats_wedged():
    obs = _healthy(court=True, state="stalled", state_age_s=999)
    assert death_signal(obs) == "court"

def test_oom_beats_wedged():
    obs = _healthy(session_alive=False, state="stalled", state_age_s=999)
    assert death_signal(obs) == "oom"

def test_all_three_court_wins():
    obs = _healthy(court=True, session_alive=False, state="stalled", state_age_s=999)
    assert death_signal(obs) == "court"


# --- wedged requires BOTH stalled AND >= 600s ---

def test_stalled_just_below_threshold_none():
    assert death_signal(_healthy(state="stalled", state_age_s=599)) is None

def test_idle_old_is_not_wedged():
    assert death_signal(_healthy(state="idle", state_age_s=9999)) is None

def test_working_old_is_not_wedged():
    assert death_signal(_healthy(state="working", state_age_s=9999)) is None


# --- exhausted (ctx-ceiling / auto-compact skull) ---

def test_exhausted_signal():
    assert death_signal(_healthy(exhausted=True)) == "exhausted"

def test_court_beats_exhausted():
    assert death_signal(_healthy(court=True, exhausted=True)) == "court"

def test_exhausted_beats_oom():
    assert death_signal(_healthy(exhausted=True, session_alive=False)) == "exhausted"

def test_exhausted_beats_wedged():
    obs = _healthy(exhausted=True, state="stalled", state_age_s=999)
    assert death_signal(obs) == "exhausted"

def test_exhausted_false_is_healthy():
    assert death_signal(_healthy(exhausted=False)) is None


# --- healthy ---

def test_healthy_none():
    assert death_signal(_healthy()) is None

def test_missing_keys_defaults_healthy():
    assert death_signal({}) is None
