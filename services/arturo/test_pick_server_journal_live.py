# test_pick_server_journal_live.py — regression for the client-finalize merge-linkage miss (2026-09-06).
#
# BUG (live-caught on the operator's build-162 call): client /finalize-call returned merged server=None, so
# gm's inject dropped the call's 5 tool turns AND the orphaned live server journal risked a delayed
# watchdog double-inject. Root cause: pick_server_journal's time-window fallback uses _overlaps,
# which returns False if ANY endpoint is None. The matching server journal was still status=live
# (no ended_at yet) at client-finalize time, so the overlap check skipped it. The 900s finalize
# fix widened this window (server journals stay live longer), making the miss the common case.
# Fix: a LIVE server journal (ended_at None) that started within the client's window must still be
# treated as overlapping (it's the ongoing physical call).
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _fm():
    spec = importlib.util.spec_from_file_location(
        "finalize_merge_undertest", str(REPO / "services" / "arturo" / "finalize_merge.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_links_live_server_journal_without_ended_at():
    fm = _fm()
    # client finalized at hangup: started 01:40:12, ended 01:42:50 (unix-ish)
    client = {"source": "client", "conv_id": "conv_client_xyz",
              "started_at": 1788673212.0, "ended_at": 1788673370.0}
    # the matching SERVER journal is STILL LIVE (no ended_at), started 5s after the client, holds tools.
    server_live = {"source": "funnel", "conv_id": None,
                   "started_at": 1788673217.0, "ended_at": None,
                   "turns": [{"role": "tool", "tool": "remember_note"},
                             {"role": "tool", "tool": "inject_message"}]}
    candidates = [("vc_server_live", server_live)]
    picked = fm.pick_server_journal(client, candidates, surface_server_ids=set())
    assert picked == "vc_server_live", f"live server journal not linked (got {picked!r}) — tools would drop from gm inject"


def test_still_ignores_non_overlapping_live_journal():
    fm = _fm()
    client = {"source": "client", "conv_id": "c1",
              "started_at": 1788673212.0, "ended_at": 1788673370.0}
    # a live journal that started LONG AFTER the client's window ended = a different call, must NOT link.
    other_live = {"source": "funnel", "conv_id": None,
                  "started_at": 1788690000.0, "ended_at": None, "turns": []}
    picked = fm.pick_server_journal(client, [("vc_other", other_live)], surface_server_ids=set())
    assert picked is None, f"linked an unrelated later live call (got {picked!r})"


def test_conv_id_match_still_primary():
    fm = _fm()
    client = {"source": "client", "conv_id": "shared_conv",
              "started_at": 1788673212.0, "ended_at": 1788673370.0}
    server = {"source": "funnel", "conv_id": "shared_conv",
              "started_at": 1788673217.0, "ended_at": None, "turns": []}
    picked = fm.pick_server_journal(client, [("vc_by_conv", server)], surface_server_ids=set())
    assert picked == "vc_by_conv"
