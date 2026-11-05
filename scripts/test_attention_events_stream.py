"""/events announces attention changes: two new digest segments (attention rev, live permission ids) and an
additive attention_rev in the SSE payload. The pending count (segment 1) is unchanged."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scripts.watch_gateway as G  # noqa: E402
from attention_testbox import Box  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    b = Box(tmp_path, monkeypatch)
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path / "calls")
    monkeypatch.setitem(G._PERM_IDS_CACHE, "at", 0.0)
    return b


def test_digest_gains_segments_and_keeps_the_pending_count(box):
    box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    d1 = G._events_digest(box.ledger)
    box.astore.add_done("gm", "Shipped", key="k")
    d2 = G._events_digest(box.ledger)
    assert d1.split("|")[0] == d2.split("|")[0] and d1 != d2 and len(d2.split("|")) == 5
    assert int(d2.split("|")[3]) == box.astore.rev()


def test_a_new_permission_prompt_changes_the_digest(box, monkeypatch):
    monkeypatch.setattr(G, "_PERM_IDS_CACHE_S", 0.05)
    d1 = G._events_digest(box.ledger)
    box.perm_rows = [{"id": "perm:gm:abc:1"}]
    time.sleep(0.1)
    d2 = G._events_digest(box.ledger)
    assert d1 != d2 and d2.split("|")[4] == "perm:gm:abc:1"


def test_the_permission_scan_is_shared_within_one_period(box, monkeypatch):
    calls = []
    monkeypatch.setattr(G, "_perm_pseudo_rows", lambda: calls.append(1) or [])
    monkeypatch.setattr(G, "_PERM_IDS_CACHE_S", 60)
    for _ in range(5):
        G._events_digest(box.ledger)
    assert len(calls) == 1


def test_a_broken_attention_read_never_breaks_the_digest(box, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(G, "AttentionStore", boom)
    monkeypatch.setattr(G, "_perm_pseudo_rows", boom)
    d = G._events_digest(box.ledger)
    assert d.split("|")[3:] == ["0", ""]


async def _first_data(box):
    async with TestClient(TestServer(G.build_app())) as c:
        r = await c.get("/events", headers=box.auth())
        line = await asyncio.wait_for(r.content.readline(), 5)
        r.close()
        return json.loads(line.decode()[len("data: "):])


def test_sse_payload_carries_attention_rev(box):
    box.astore.add_done("gm", "Shipped", key="k")
    d = asyncio.run(_first_data(box))
    assert set(d) == {"changed", "pending", "ts", "attention_rev"} and d["attention_rev"] == box.astore.rev()


def test_an_old_three_segment_digest_still_streams(box, monkeypatch):
    monkeypatch.setattr(G, "_events_digest", lambda store: "a1|")
    d = asyncio.run(_first_data(box))
    assert d["pending"] == 1 and d["attention_rev"] == 0


def test_unarmed_the_digest_keeps_its_old_shape_and_scans_nothing_new(tmp_path, monkeypatch):
    b = Box(tmp_path, monkeypatch, armed=False)
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path / "calls")
    calls = []
    monkeypatch.setattr(G, "_perm_pseudo_rows", lambda: calls.append(1) or [])
    monkeypatch.setitem(G._PERM_IDS_CACHE, "at", 0.0)
    assert len(G._events_digest(b.ledger).split("|")) == 3 and calls == []
