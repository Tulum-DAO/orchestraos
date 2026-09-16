"""gm msg_86bcc168 item 1(b): bg_arm._wake_and_verify_ingest writes green_wake_failed_alarm
but NOTHING paged it — semantic-recall-wiring-dev sat PREWARMING 21:48-00:35 ET silently.
Page it through alarm_fn like green_quota: ONCE per episode, keyed on the green's sid
(fallback green alias), and clear the flag when a later wake succeeds."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "page-seat"


class _Alarm:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)


def _prewarming(tmp_path):
    st = BgStateStore(str(tmp_path), ROOT)
    st.write_state("PREWARMING", reason="setup")
    return st


def test_pages_once_per_green_episode(tmp_path):
    st = _prewarming(tmp_path)
    st.write_meta("green_wake_failed_alarm", True)
    st.write_meta("green_session_id", "sid-1")
    alarm, summary = _Alarm(), {"alarms": 0}
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    assert len(alarm.calls) == 1
    c = alarm.calls[0]
    assert c["kind"] == "green-wake-failed" and c["page"] is True and c["root"] == ROOT
    assert "sid-1" in c["detail"] and f"{ROOT}-g5" in c["detail"]
    assert summary["alarms"] == 1 and summary["green_wake_failed"] == 1


def test_new_green_sid_is_a_new_episode(tmp_path):
    st = _prewarming(tmp_path)
    st.write_meta("green_wake_failed_alarm", True)
    st.write_meta("green_session_id", "sid-1")
    alarm, summary = _Alarm(), {"alarms": 0}
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    st.write_meta("green_session_id", "sid-2")
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 6, summary)
    assert len(alarm.calls) == 2


def test_no_page_without_flag_and_fallback_key_without_sid(tmp_path):
    st = _prewarming(tmp_path)
    alarm, summary = _Alarm(), {"alarms": 0}
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    assert alarm.calls == []
    st.write_meta("green_wake_failed_alarm", True)       # no green_session_id captured yet
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    assert len(alarm.calls) == 1                          # keyed on the alias instead


def test_stale_flag_after_swap_does_not_page(tmp_path):
    """Live 2026-09-16: semantic-recall-wiring-dev sat DRAINED with green_wake_failed_alarm
    still True from the resolved stall — a resolved episode must never page."""
    st = BgStateStore(str(tmp_path), ROOT)
    st.write_state("DRAINED", reason="swapped")
    st.write_meta("green_wake_failed_alarm", True)
    st.write_meta("green_session_id", "sid-1")
    alarm, summary = _Alarm(), {"alarms": 0}
    bg_beat._page_green_wake_failed(st, alarm, ROOT, 5, summary)
    assert alarm.calls == []
