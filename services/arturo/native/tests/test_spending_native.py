"""The spending tool group on the NATIVE path (services/arturo/spending_tools.py through tools.ArturoTools), against a
stub tracker on an ephemeral local port serving the captured tools.json. Never the live tracker or its token file."""
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from services.arturo import spending_tools as st
from services.arturo.native.tool_ledger import ToolLedger
from services.arturo.native.tools import ArturoTools

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "spending_tools.json").read_text())
LIVE_TOKEN = "/home/shaw/apps/spending-tracker/data/agent-token"


# ---- a stub spending tracker on an ephemeral local port (never the live :8760) -------------------------------------
class StubTracker:
    """Serves the captured tools.json (mutable: a test can add a tool) and the tool endpoints with the tracker's own
    guards: bearer = the CURRENT token file, >$5,000 needs confirm (400), same amount+merchant within the window is
    duplicate:true unless allow_duplicate, unknown args 400."""

    def __init__(self, token_path, tools=None):
        self.token_path = token_path
        self.tools = json.loads(json.dumps(tools if tools is not None else FIXTURE))
        self.requests = []           # (method, path, headers dict, body dict)
        self.saved = []
        self.down = False
        self.rate_limited = False
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                stub.requests.append(("GET", self.path, dict(self.headers), None))
                if self.path == "/api/agent/tools.json":
                    return self._send(200, stub.tools)
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                stub.requests.append(("POST", self.path, dict(self.headers), body))
                code, obj = stub.handle(self.path, self.headers, body)
                return self._send(code, obj)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def handle(self, path, headers, body):
        tok = open(self.token_path).read().strip()
        got = headers.get("Authorization") or ""
        if got != "Bearer " + tok:
            return 401, {"error": "missing or wrong agent token"}
        name = path.rsplit("/", 1)[-1]
        if self.rate_limited and name in ("add_transaction", "save_recurring", "mark_paid", "undo_last_action"):
            return 429, {"error": "too many changes in a minute; try again shortly"}
        spec = {t["function"]["name"]: t["function"] for t in self.tools}.get(name)
        if spec is None:
            return 404, {"error": "not found"}
        unknown = set(body) - set(spec["parameters"].get("properties", {}))
        if unknown:
            return 400, {"error": "unknown argument(s): " + ", ".join(sorted(unknown))}
        if name == "add_transaction":
            amt = float(body["amount"])
            if amt > 5000 and body.get("confirm") is not True:
                return 400, {"error": f"${amt:,.2f} is unusually large. Repeat the amount back to the user and call "
                                      f"again with confirm=true."}
            m = str(body.get("merchant"))
            if not body.get("allow_duplicate"):
                for s in self.saved:
                    if s["amount"] == amt and s["description"].lower() == m.lower():
                        return 200, {"ok": True, "duplicate": True, "transaction": s,
                                     "message": "This looks like a repeat of a transaction just added, so nothing "
                                                "new was saved. Pass allow_duplicate=true if it really happened twice."}
            tx = {"id": len(self.saved) + 1, "date": "2026-11-19", "amount": -amt, "description": m,
                  "category": "Dining", "source": headers.get("X-Agent-Source")}
            self.saved.append(dict(tx, amount=amt))
            return 200, {"ok": True, "action_id": 40 + len(self.saved), "transaction": tx,
                         "message": f"Spent ${amt:,.2f} at {m} on Nov 19 (Dining)"}
        if name == "month_summary":
            return 200, {"month": "2026-11", "income": 5200.0, "spending": 3187.45, "net": 2012.55,
                         "by_category": [{"category": "Housing", "amount": 1800.0, "count": 1},
                                         {"category": "Dining", "amount": 412.3, "count": 17}],
                         "top_merchants": [{"merchant": f"Merchant {i}", "amount": 10.0 * i, "count": i}
                                           for i in range(1, 80)],
                         "current_balance": 9120.11, "has_accounts": True}
        if name == "list_upcoming":
            return 200, {"days": 14, "today": "2026-11-19", "items": [
                {"recurring_id": 1, "name": "Rent", "kind": "bill", "date": "2026-11-20", "amount": -1800.0,
                 "paid": False, "overdue": False},
                {"recurring_id": 2, "name": "Phone", "kind": "bill", "date": "2026-11-15", "amount": -65.0,
                 "paid": False, "overdue": True}], "unpaid_bills_total": 1865.0, "expected_income_total": 0.0}
        if name == "search_transactions":
            return 200, {"count": 2, "more": False, "total": -57.5, "transactions": [
                {"id": 7, "date": "2026-11-18", "amount": -42.5, "description": "Uber"},
                {"id": 6, "date": "2026-11-17", "amount": -15.0, "description": "Uber"}]}
        return 200, {"ok": True, "message": f"{name} ran", "action_id": 99}

    def posts(self, name=None):
        return [r for r in self.requests if r[0] == "POST" and (name is None or r[1].endswith("/" + name))]


# ---- capture EVERY log line (root + any logger that does not propagate), and the dollar forms of a result ---------------
class _AllLogs(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            self.lines.append(str(record.msg))


@pytest.fixture()
def all_logs():
    h = _AllLogs()
    targets = [logging.getLogger()] + [lg for lg in logging.Logger.manager.loggerDict.values()
                                        if isinstance(lg, logging.Logger) and not lg.propagate]
    saved = [(lg, lg.level) for lg in targets]
    for lg in targets:
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
    yield h
    for lg, lvl in saved:
        lg.removeHandler(h)
        lg.setLevel(lvl)


def _amount_forms(*texts):
    """Every way a dollar amount that appears in a spending RESULT could be written: $1,500.00 1,500.00 1500.00 1500."""
    import re as _re
    forms = set()
    for t in texts:
        for m in _re.findall(r"\$([\d,]+(?:\.\d+)?)", str(t)):
            plain = m.replace(",", "")
            v = float(plain)
            forms |= {"$" + m, m, plain, f"{v:.2f}", f"{v:,.2f}", str(int(v)) if v == int(v) else plain}
    return {f for f in forms if len(f.replace(",", "").replace(".", "").replace("$", "")) >= 3}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    tok = tmp_path / "agent-token"
    tok.write_text("tok-AAAA-first-secret-value\n")
    os.chmod(tok, 0o600)
    stub = StubTracker(str(tok))
    monkeypatch.setenv(st.TOKEN_FILE_ENV, str(tok))
    monkeypatch.setenv(st.URL_ENV, stub.url)
    monkeypatch.delenv(st.ENABLE_ENV, raising=False)
    stub.tok = tok
    assert str(tok) != LIVE_TOKEN and ":8760" not in stub.url
    yield stub
    stub.close()


class Fake:
    """The proxy executor: two plain tools; list_agents is a non-fleet lookup."""
    non_fleet = frozenset({"list_agents"})

    def __init__(self):
        self.calls = []

    def specs(self, fleet):
        base = {"list_agents": ("List agents", {"type": "object", "properties": {}})}
        if fleet:
            base["remember_note"] = ("Save a note", {"type": "object", "properties": {}})
        return base

    def __call__(self, name, args, principal, fenced=False, user_turns=None):
        self.calls.append(name)
        return "ok"


def _arturo(tmp_path, clock=None):
    sp = st.SpendingTools(clock=clock or Clock()).arm(background=False)
    t = ArturoTools(ToolLedger(tmp_path / "led"), executor=Fake(), spending=sp)
    words = {}
    t.user_fn = lambda conv: words.get(conv, "")
    t.words = words
    return t, sp


def _run(t, b, cid, name, args):
    return t.execute(b, cid, name, json.dumps(args))


def _next_turn(t, b, words):
    """The caller speaks again (the session calls new_turn), saying `words`."""
    time.sleep(0.002)
    t.new_turn(b.conversation_id)
    t.words[b.conversation_id] = words


# ---- generation (G1) ----------------------------------------------------------------------------------------------
def test_the_group_is_generated_from_tools_json_namespaced_and_classified(tracker, tmp_path):
    t, sp = _arturo(tmp_path)
    remote = {x["function"]["name"] for x in FIXTURE}
    specs = t.specs(t.bind("fleet", "iphone", "c1"))
    got = {n for n in specs if n.startswith("spending_")}
    assert got == {"spending_" + n for n in remote}
    for n in got:
        assert f"namespaced as {n}" in specs[n][0]
    reads = {n for n in got if sp.is_read(n)}
    assert reads == {"spending_list_upcoming", "spending_month_summary", "spending_search_transactions",
                     "spending_list_options"}
    assert all("READ-ONLY" in specs[n][0] for n in reads)
    assert all("CHANGES the spending tracker" in specs[n][0] for n in got - reads)
    # the tracker's own parameter schema is carried (strict additionalProperties, required)
    assert specs["spending_add_transaction"][1]["required"] == ["amount", "merchant"]


def test_a_tool_the_tracker_adds_appears_without_a_code_change_and_is_a_write_by_default(tracker, tmp_path):
    t, sp = _arturo(tmp_path)
    tracker.tools.append({"type": "function", "function": {
        "name": "set_budget", "description": "Set a monthly budget for a category.",
        "parameters": {"type": "object", "properties": {"category": {"type": "string"}, "amount": {"type": "number"}},
                       "required": ["category", "amount"], "additionalProperties": False}}})
    tracker.tools.append({"type": "function", "function": {
        "name": "list_budgets", "description": "Budgets.", "x-read-only": True,
        "parameters": {"type": "object", "properties": {}}}})
    assert sp.refresh()
    names = set(t.specs(t.bind("fleet", "iphone", "c1")))
    assert {"spending_set_budget", "spending_list_budgets"} <= names
    assert sp.is_write("spending_set_budget") and sp.is_read("spending_list_budgets")
    out = _run(t, t.bind("fleet", "iphone", "c2"), "call_b", "spending_set_budget", {"category": "Dining", "amount": 300})
    assert out["ok"] and tracker.posts("set_budget")


def test_a_tracker_down_at_startup_never_blocks_or_crashes_and_recovers_lazily(tmp_path, monkeypatch, caplog):
    tok = tmp_path / "agent-token"
    tok.write_text("tok-down\n")
    monkeypatch.setenv(st.TOKEN_FILE_ENV, str(tok))
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    monkeypatch.setenv(st.URL_ENV, f"http://127.0.0.1:{dead}")
    clock = Clock()
    caplog.set_level(logging.INFO, logger="arturo.spending")
    sp = st.SpendingTools(clock=clock, retry_s=(30.0,))
    t0 = time.monotonic()
    sp.arm()                                              # Arturo's startup path: background
    assert time.monotonic() - t0 < 0.5
    for _ in range(100):
        if not sp._refreshing:
            break
        time.sleep(0.02)
    t = ArturoTools(ToolLedger(tmp_path / "led"), executor=Fake(), spending=sp)
    names = set(t.specs(t.bind("fleet", "iphone", "c1")))
    assert names == {"list_agents", "remember_note"}       # started without the group
    assert not sp.refresh()                               # a second failure ...
    assert sum("unavailable" in r.getMessage() for r in caplog.records) == 1   # ... is logged once
    stub = StubTracker(str(tok))
    try:
        monkeypatch.setenv(st.URL_ENV, stub.url)
        assert not {n for n in t.specs(t.bind("fleet", "iphone", "c2")) if n.startswith("spending_")}  # backoff
        clock.t += 31
        t.specs(t.bind("fleet", "iphone", "c3"))          # lazy: kicks a background refresh, never waits on it
        for _ in range(200):
            if sp.names():
                break
            time.sleep(0.02)
        assert "spending_add_transaction" in t.specs(t.bind("fleet", "iphone", "c4"))
    finally:
        stub.close()


def test_import_and_an_unarmed_group_never_reach_any_tracker(tracker, tmp_path):
    sp = st.SpendingTools()
    t = ArturoTools(ToolLedger(tmp_path / "led"), executor=Fake(), spending=sp)
    assert not {n for n in t.specs(t.bind("fleet", "iphone", "c1")) if n.startswith("spending_")}
    assert tracker.requests == []


# ---- token + headers (G2) ------------------------------------------------------------------------------------------
def test_the_token_is_read_from_the_file_on_every_call(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    assert _run(t, b, "call_1", "spending_month_summary", {})["ok"]
    tracker.tok.write_text("tok-BBBB-rotated-secret\n")      # rotation: the tracker now accepts only the new one
    out = _run(t, b, "call_2", "spending_list_upcoming", {})
    assert out["ok"], out
    auths = [r[2]["Authorization"] for r in tracker.posts()]
    assert auths == ["Bearer tok-AAAA-first-secret-value", "Bearer tok-BBBB-rotated-secret"]


def test_the_token_never_reaches_logs_or_results_and_the_source_is_arturo(tracker, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    t, sp = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    outs = [_run(t, b, "call_1", "spending_month_summary", {}),
            _run(t, b, "call_2", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})]
    tracker.tok.write_text("tok-CCCC-other\n")
    outs.append(_run(t, b, "call_3", "spending_list_options", {}))
    blob = json.dumps(outs) + caplog.text + json.dumps(t.ledger.calls()) + json.dumps(sp.trace) + json.dumps(t.trace)
    for secret in ("tok-AAAA-first-secret-value", "tok-CCCC-other"):
        assert secret not in blob
    assert {r[2].get("X-Agent-Source") for r in tracker.posts()} == {"arturo"}


def test_a_missing_token_file_is_said_plainly_and_sends_nothing(tracker, tmp_path, monkeypatch):
    t, _ = _arturo(tmp_path)
    monkeypatch.setenv(st.TOKEN_FILE_ENV, str(tmp_path / "nope"))
    out = _run(t, t.bind("fleet", "iphone", "c1"), "call_1", "spending_month_summary", {})
    assert not out["ok"] and out["error"] == "NO_TOKEN" and tracker.posts() == []


# ---- voice shape (G3) ------------------------------------------------------------------------------------------------
def test_results_are_one_spoken_sentence_with_key_numbers_and_bounded_raw(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    out = _run(t, t.bind("fleet", "iphone", "c1"), "call_1", "spending_month_summary", {})
    assert out["ok"] and "\n" not in out["say"] and len(out["say"]) < 260
    assert out["say"].startswith("For 2026-11 you've spent $3,187.45 and taken in $5,200.00, net +$2,012.55.")
    assert out["numbers"]["spending"] == 3187.45 and out["numbers"]["net"] == 2012.55
    assert len(out["data"]) <= st.RAW_MAX + len("...(truncated)") and out["data"].endswith("...(truncated)")
    up = _run(t, t.bind("fleet", "iphone", "c1"), "call_2", "spending_list_upcoming", {})
    assert up["numbers"]["overdue"] == 1 and "1 overdue" in up["say"]
    t.new_turn("c1")
    w = _run(t, t.bind("fleet", "iphone", "c1"), "call_3", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    assert w["say"] == "Spent $12.00 at Chipotle on Nov 19 (Dining)." and w["numbers"]["action_id"] == 41
    assert "data" not in w


# ---- guards come back as questions; never auto-confirm (G4) ------------------------------------------------------------
def test_a_duplicate_comes_back_as_a_question(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    _run(t, b, "call_1", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    _next_turn(t, b, "twelve at chipotle")
    out = _run(t, b, "call_2", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    assert out["needs_answer"] and out["guard"] == "duplicate" and out["question"].endswith("?")
    assert "already logged" in out["question"] and "log it again anyway" in out["question"]
    assert "saved" not in out["say"] and not out["ok"] and len(tracker.saved) == 1


def test_a_large_amount_asks_and_a_model_set_confirm_is_stripped_on_the_same_turn(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    t.words["c1"] = "yes I spent six thousand at Apple"            # even a "yes" in the SAME turn is not an answer
    a = _run(t, b, "call_1", "spending_add_transaction", {"amount": 6000, "merchant": "Apple", "confirm": True})
    assert a["needs_answer"] and a["guard"] == "confirm" and "$6,000.00" in a["question"]
    # native: after that result the turn is fenced, so a same-turn retry never even reaches the tracker ...
    again = _run(t, b, "call_2", "spending_add_transaction", {"amount": 6000, "merchant": "Apple", "confirm": True})
    assert again["error"] == "FENCED" and tracker.saved == []
    # ... and the group itself strips a same-turn confirm even past the fence (the relay has its own fence)
    t._fenced.discard("c1")
    third = _run(t, b, "call_3", "spending_add_transaction", {"amount": 6000, "merchant": "Apple", "confirm": True})
    assert third["needs_answer"] and tracker.saved == []
    assert len(tracker.posts("add_transaction")) == 2
    assert all("confirm" not in r[3] for r in tracker.posts("add_transaction"))


def test_confirm_is_honoured_on_the_callers_next_turn_when_they_say_yes(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    t.words["c1"] = "I spent six thousand at Apple"
    assert _run(t, b, "call_1", "spending_add_transaction", {"amount": 6000, "merchant": "Apple"})["needs_answer"]
    _next_turn(t, b, "yes, that's right")
    out = _run(t, b, "call_2", "spending_add_transaction", {"amount": 6000, "merchant": "Apple", "confirm": True})
    assert out["ok"] and out["wrote"] and tracker.posts("add_transaction")[-1][3]["confirm"] is True
    # single use: the same confirmation cannot save a second one
    _next_turn(t, b, "yes")
    third = _run(t, b, "call_3", "spending_add_transaction", {"amount": 6000, "merchant": "Apple", "confirm": True})
    assert third["needs_answer"] and len(tracker.saved) == 1


def test_a_no_or_different_args_or_another_call_never_confirms(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    _run(t, b, "call_1", "spending_add_transaction", {"amount": 6000, "merchant": "Apple"})
    _next_turn(t, b, "no, wait")
    assert _run(t, b, "call_2", "spending_add_transaction",
                {"amount": 6000, "merchant": "Apple", "confirm": True})["needs_answer"]
    _next_turn(t, b, "yes")
    assert _run(t, b, "call_3", "spending_add_transaction",
                {"amount": 7000, "merchant": "Apple", "confirm": True})["needs_answer"]   # not what was asked about
    other = t.bind("fleet", "iphone", "c2")
    t.new_turn("c2")
    t.words["c2"] = "yes"
    assert _run(t, other, "call_4", "spending_add_transaction",
                {"amount": 6000, "merchant": "Apple", "confirm": True})["needs_answer"]   # another call's question
    assert tracker.saved == []


def test_a_confirmation_expires(tracker, tmp_path):
    clock = Clock()
    t, sp = _arturo(tmp_path, clock)
    b = t.bind("fleet", "iphone", "c1")
    _run(t, b, "call_1", "spending_add_transaction", {"amount": 6000, "merchant": "Apple"})
    clock.t += st.CONFIRM_TTL_S + 1
    _next_turn(t, b, "yes")
    assert _run(t, b, "call_2", "spending_add_transaction",
                {"amount": 6000, "merchant": "Apple", "confirm": True})["needs_answer"]


def test_a_large_duplicate_needs_both_answers_and_keeps_the_first(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    tracker.saved.append({"id": 1, "amount": 6000.0, "description": "Apple", "date": "2026-11-19"})
    _run(t, b, "call_1", "spending_add_transaction", {"amount": 6000, "merchant": "Apple"})
    _next_turn(t, b, "yes")
    dup = _run(t, b, "call_2", "spending_add_transaction", {"amount": 6000, "merchant": "Apple", "confirm": True})
    assert dup["guard"] == "duplicate"
    _next_turn(t, b, "yes, save it again")
    out = _run(t, b, "call_3", "spending_add_transaction",
               {"amount": 6000, "merchant": "Apple", "confirm": True, "allow_duplicate": True})
    assert out["ok"] and len(tracker.saved) == 2


# ---- the native path's own rails (G5) ------------------------------------------------------------------------------------
def test_only_verified_fleet_or_owner_callers_get_the_group(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    assert any(n.startswith("spending_") for n in t.specs(t.bind("owner:abc", "ipad", "c1")))
    dev = t.bind("device:x", "quest", "c2")
    assert not any(n.startswith("spending_") for n in t.specs(dev))
    assert _run(t, dev, "call_1", "spending_month_summary", {})["error"] == "TOOL_NOT_ALLOWED"
    assert tracker.posts() == []


def test_after_a_result_in_the_turn_lookups_run_but_writes_wait_for_the_caller(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    assert _run(t, b, "call_1", "spending_month_summary", {})["ok"]          # fence is up now
    assert _run(t, b, "call_2", "spending_list_upcoming", {})["ok"]          # a lookup still runs
    w = _run(t, b, "call_3", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    assert w["error"] == "FENCED" and tracker.saved == []
    _next_turn(t, b, "log twelve at chipotle")
    assert _run(t, b, "call_4", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})["ok"]


def test_a_duplicate_call_id_never_writes_twice(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    a = _run(t, b, "call_1", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    t.new_turn("c1")
    again = _run(t, b, "call_1", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    assert a["ok"] and again["ok"] and again["outcome"] == "ok" and len(tracker.posts("add_transaction")) == 1


def test_a_spending_write_backs_a_saved_claim_and_a_question_does_not(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    _run(t, b, "call_1", "spending_add_transaction", {"amount": 6000, "merchant": "Apple"})
    assert not t.backed("c1", time.time() - 5, ("action", "save"))
    b2 = t.bind("fleet", "iphone", "c2")
    _run(t, b2, "call_2", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    assert t.backed("c2", time.time() - 5, ("action", "save"))
    b3 = t.bind("fleet", "iphone", "c3")
    _run(t, b3, "call_3", "spending_month_summary", {})
    assert not t.backed("c3", time.time() - 5, ("action", "save"))          # a lookup never backs "I saved it"


# ---- no money in logs or ledger rows (G6) -----------------------------------------------------------------------------
def test_no_dollar_amount_from_a_result_reaches_any_log_line_or_ledger_row(tracker, tmp_path, all_logs):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    w = _run(t, b, "call_1", "spending_add_transaction",
             {"amount": 1500, "merchant": "Zanzibar Bistro", "note": "anniversary dinner", "date": "2026-11-18"})
    t.new_turn("c1")
    r = _run(t, b, "call_2", "spending_month_summary", {})
    assert w["ok"] and "$1,500.00" in w["say"] and r["ok"]                  # the MODEL still gets the amounts
    forms = _amount_forms(w["say"], r["say"])
    assert {"$1,500.00", "1500.00", "1500", "3187.45", "$5,200.00"} <= forms
    logged = "\n".join(all_logs.lines)
    stored = json.dumps(t.ledger.calls())
    assert "[spending spending_add_transaction write] arg_keys=['amount', 'date', 'merchant', 'note'] outcome=ok" in logged
    for f in sorted(forms) + ["Zanzibar", "anniversary", "2026-11-18"]:
        assert f not in logged, f"{f!r} reached a log line"
        assert f not in stored, f"{f!r} reached a ledger row"
    row = t.ledger.calls()[0]
    assert json.loads(row["result_json"])["outcome"] == "ok"
    assert json.loads(row["args_json"]) == {"arg_keys": ["amount", "date", "merchant", "note"]}


# ---- tracker quirks (docs/MCP.md) ----------------------------------------------------------------------------------------
def test_a_duplicate_is_said_as_already_logged_and_never_backs_a_saved_claim(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    tracker.saved.append({"id": 1, "amount": 12.0, "description": "Chipotle", "date": "2026-11-19"})
    b = t.bind("fleet", "iphone", "c1")
    out = _run(t, b, "call_1", "spending_add_transaction", {"amount": 12, "merchant": "Chipotle"})
    assert out["guard"] == "duplicate" and out["say"].startswith("That looks already logged")
    assert not t.backed("c1", time.time() - 5, ("action", "save"))


def test_a_rate_limit_is_said_plainly_and_never_retried(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    tracker.rate_limited = True
    out = _run(t, t.bind("fleet", "iphone", "c1"), "call_1", "spending_add_transaction", {"amount": 12, "merchant": "X"})
    assert out["error"] == "RATE_LIMITED" and out["say"] == "Too many changes in a minute; try again shortly."
    assert len(tracker.posts("add_transaction")) == 1


def test_month_and_search_summaries_say_they_may_include_pending_charges(tracker, tmp_path):
    t, _ = _arturo(tmp_path)
    b = t.bind("fleet", "iphone", "c1")
    for i, n in enumerate(("spending_month_summary", "spending_search_transactions")):
        assert _run(t, b, f"call_{i}", n, {})["say"].endswith("These figures may include pending charges.")


def test_the_server_hands_its_spending_group_to_the_tools_and_main_arms_one(tmp_path, monkeypatch):
    # the wiring the ArturoTools-level tests above skip: NativeServer(spending=...) -> its tools, and main() builds
    # an ARMED SpendingTools for it (never None, never unarmed)
    from services.arturo.native import server as srv
    sp = object()
    s = srv.NativeServer(tmp_path / "native", executor=None, transport_factory=lambda *a, **k: None,
                         usage_store=None, verify=lambda headers: True, spending=sp)
    assert s.tools.spending is sp
    seen = {}

    class Armed:
        def arm(self):
            seen["armed"] = True
            return self
    monkeypatch.setattr(st, "SpendingTools", Armed)
    def fake_server(state_dir, spending=None, **k):
        seen["spending"] = spending
        return type("S", (), {"app": lambda self: None})()
    monkeypatch.setattr(srv, "NativeServer", fake_server)
    monkeypatch.setattr(srv.web, "run_app", lambda *a, **k: None)
    monkeypatch.setattr(srv.logging, "basicConfig", lambda **k: None)
    srv.main([])
    assert seen.get("armed") is True and isinstance(seen.get("spending"), Armed)
