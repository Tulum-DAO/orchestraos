"""The finances snapshot from the tracker's READ-ONLY agent tools (tracker_snapshot.py), against a stub tracker:
the token is read from its file at every fetch and never logged, a slow tracker is omitted (never waited on past the
timeout), and a result is cached."""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.arturo import tracker_snapshot as T

MONTH = {"month": "2026-11", "income": 5000, "spending": 1234.5, "net": 3765.5, "has_accounts": True,
         "current_balance": 8000, "projected_end_of_month_balance": 7400, "unpaid_bills_left": 600,
         "by_category": [{"category": "Groceries", "amount": 400}], "top_merchants": [{"merchant": "Costco", "amount": 300, "count": 2}]}
UPCOMING = {"days": 14, "items": [{"name": "Rent", "kind": "bill", "date": "2026-11-21", "amount": -1500, "paid": False,
                                   "overdue": False, "autopay": True}], "unpaid_bills_total": 1500, "expected_income_total": 0}


class Stub:
    def __init__(self):
        self.seen, self.delay, self.status = [], 0.0, 200
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                stub.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                  "source": self.headers.get("X-Agent-Source"), "body": json.loads(body or b"{}")})
                time.sleep(stub.delay)
                out = MONTH if self.path.endswith("/month_summary") else UPCOMING
                data = json.dumps(out if stub.status == 200 else {"error": "unauthorized"}).encode()
                try:
                    self.send_response(stub.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture()
def stub(tmp_path, monkeypatch):
    s = Stub()
    tok = tmp_path / "agent-token"
    tok.write_text("tok-AAAA-secret\n")
    monkeypatch.setenv("SPENDING_AGENT_URL", s.url)
    monkeypatch.setenv("SPENDING_AGENT_TOKEN_FILE", str(tok))
    s.tok = tok
    yield s
    s.srv.shutdown()


def test_both_read_only_tools_with_the_token_and_the_source(stub):
    text = T.fetch(timeout=3)
    assert [r["path"] for r in stub.seen] == ["/api/agent/tools/month_summary", "/api/agent/tools/list_upcoming"]
    assert all(r["auth"] == "Bearer tok-AAAA-secret" and r["source"] == "arturo" for r in stub.seen)
    assert stub.seen[1]["body"] == {"days": 14}
    assert "income $5,000.00" in text and "Balance now $8,000.00" in text and "Rent $1,500.00 bill 2026-11-21 autopay" in text
    assert "tok-AAAA" not in text and len(text) <= T.MAX_CHARS


def test_the_token_is_read_from_its_file_at_every_fetch(stub):
    T.fetch(timeout=3)
    stub.tok.write_text("tok-BBBB-rotated")
    T.fetch(timeout=3)
    assert [r["auth"] for r in stub.seen] == ["Bearer tok-AAAA-secret"] * 2 + ["Bearer tok-BBBB-rotated"] * 2


def test_a_refusal_is_omitted_with_one_line_and_never_the_token(stub, caplog):
    stub.status = 401
    caplog.set_level(logging.DEBUG)
    snap = T.Snapshot()
    assert snap.get() is None
    lines = [r.getMessage() for r in caplog.records if r.name == "tracker_snapshot"]
    assert lines == ["tracker snapshot omitted: month_summary: HTTP 401"]
    assert "tok-AAAA" not in caplog.text and str(stub.tok) not in caplog.text


def test_a_slow_tracker_is_omitted_within_the_timeout(stub, caplog, monkeypatch):
    stub.delay = 2.0
    snap = T.Snapshot(fetch_fn=lambda: T.fetch(timeout=0.3))
    t0 = time.monotonic()
    assert snap.get() is None
    assert time.monotonic() - t0 < 1.0
    assert "tracker snapshot omitted: month_summary: timeout" in caplog.text


def test_no_token_file_is_omitted(stub, caplog):
    stub.tok.unlink()
    assert T.Snapshot().get() is None and stub.seen == []
    assert "token file unreadable" in caplog.text


def test_cached_then_stale_is_served_while_it_refreshes(stub):
    now = [100.0]
    snap = T.Snapshot(fetch_fn=lambda: T.fetch(timeout=3), ttl=60, clock=lambda: now[0])
    first = snap.get()
    assert first and len(stub.seen) == 2
    now[0] += 30
    assert snap.get() == first and len(stub.seen) == 2           # fresh: no fetch
    now[0] += 61
    stub.delay = 0.5
    t0 = time.monotonic()
    assert snap.get() == first                                    # stale: served at once...
    assert time.monotonic() - t0 < 0.3
    deadline = time.time() + 5
    while len(stub.seen) < 4 and time.time() < deadline:          # ...and refreshed behind it
        time.sleep(0.05)
    assert len(stub.seen) == 4


def test_a_second_caller_never_waits_on_a_fetch_in_flight():
    gate = threading.Event()

    def slow():
        gate.wait(5)
        return "snap"
    snap = T.Snapshot(fetch_fn=slow)
    th = threading.Thread(target=snap.get)
    th.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    assert snap.get() is None and time.monotonic() - t0 < 0.1
    gate.set()
    th.join(5)
    assert snap.get() == "snap"
