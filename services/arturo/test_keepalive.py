from services.arturo import keepalive as ka


def test_band_classifier_matches_proven_bands():
    assert ka.band(3) == "inline"
    assert ka.band(15) == "narrated"
    assert ka.band(45) == "async"


def test_quiet_mode_forces_async_for_long_ops_until_probe_passes():
    st = ka.Narration(mode="quiet", probe_silent_ok=False)
    assert st.plan(estimate_s=15) == "async"
    assert st.plan(estimate_s=3) == "inline"


def test_backlog_discarded_on_completion():
    q = ka.AnnouncementQueue()
    q.note_progress("scanning… 100 agents")
    q.note_progress("scanning… 200 agents")
    q.on_complete("Found 3 stalled agents.")
    assert q.pending_progress() == []
    assert q.next_announcement() == (
        "By the way — Found 3 stalled agents. Want to hear what happened, "
        "or keep going and ask me later?")


def test_announcement_waits_until_current_exchange_resolves():
    q = ka.AnnouncementQueue()
    q.on_complete("Deploy finished.")
    assert q.has_pending() is True
    ann = q.next_announcement()
    assert "By the way — Deploy finished." in ann
    assert q.has_pending() is False
