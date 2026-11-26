"""A native call opens with the thread above it and hears the chat that arrives during it (Shaw 2026-11-25: "you
can't read your own messages, you can't read what the GM is saying ... you can't even remember the last call").

The thread (the page's chat + Arturo's earlier calls, every line stamped) goes into BOTH prompts of session.start,
after the rules; it loads while the provider connects and never holds a call. The chat that lands during the call
reaches the session as stamped appends, one per tick, only after session.started. ARTURO_NATIVE_THREAD_CONTEXT=0
turns both off.
"""
import asyncio
import json
import re

from services.arturo import thread_context as tc
from services.arturo.native import call_context
from services.arturo.native.tests.test_native import FakeProvider, HDRS, _client, _run, _server


class _Feed:
    def __init__(self, agent):
        self.agent, self.polls = agent, 0

    def poll(self):
        self.polls += 1
        return ["[2026-11-25T17:40:00Z 12:40 Local] chat gm (typed, during this call): the dream fix merged"] \
            if self.polls == 1 else []


async def _start(c, start):
    ws = await c.ws_connect("/native/ws", headers=HDRS)
    await ws.send_str(json.dumps(start))
    for _ in range(100):
        if FakeProvider.instances and FakeProvider.instances[0].sent:
            break
        await asyncio.sleep(0.02)
    return ws, FakeProvider.instances[0]


def _go(tmp_path, monkeypatch, start, after_start, loader=None, flag=None):
    if flag is not None:
        monkeypatch.setenv(call_context.FLAG, flag)
    feeds, asked = [], []

    async def go():
        s = _server(tmp_path, monkeypatch)
        s.thread_loader = loader or (lambda agent: asked.append(agent) or f"THREAD for {agent}: last call said hi")
        s.chat_feed_factory = lambda agent: feeds.append(_Feed(agent)) or feeds[-1]
        c = await _client(s)
        try:
            ws, p = await _start(c, start)
            await after_start(ws, p)
            await ws.send_str(json.dumps({"type": "disconnect"}))
            await ws.close()
        finally:
            await c.close()
        return p
    return _run(go()), feeds, asked


async def _started(ws, p):
    p.push({"type": "session.started"})
    await asyncio.sleep(0.5)


def test_the_thread_opens_the_call_in_both_prompts(tmp_path, monkeypatch):
    p, _, asked = _go(tmp_path, monkeypatch, {"type": "start"}, _started)
    start = p.sent[0]
    assert start["type"] == "session.start"
    assert asked == ["gm"]                                          # the client page's chat is gm's (10-20 ruling)
    live, backend = start["session"]["instructions"], start["session"]["delegation"]["responses"]["instructions"]
    fenced = f"{tc.PAST_OPEN}\nTHREAD for gm: last call said hi\n{tc.PAST_CLOSE}"
    assert backend.rstrip().endswith(fenced)                              # after the rules
    paras = live.strip().split("\n\n")                                   # live: after the rules, before START
    assert paras[-1].startswith("START:") and paras[-2] == fenced


def test_the_page_names_whose_chat_it_is(tmp_path, monkeypatch):
    _, feeds, asked = _go(tmp_path, monkeypatch, {"type": "start", "agent": "pm-demo"}, _started)
    assert asked == ["pm-demo"] and feeds[0].agent == "pm-demo"
    _, _, asked = _go(tmp_path, monkeypatch, {"type": "start", "agent": "../etc"}, _started)
    assert asked == ["gm"]                                          # a malformed id is ignored


def test_chat_during_the_call_reaches_the_session_stamped_after_it_started(tmp_path, monkeypatch):
    async def before_and_after(ws, p):
        await asyncio.sleep(0.4)                                    # not started yet: nothing appended
        assert not [f for f in p.sent if f["type"].endswith(".append") and "dream fix" in f.get("content", "")]
        await _started(ws, p)
    p, _, _ = _go(tmp_path, monkeypatch, {"type": "start"}, before_and_after)
    notes = [f for f in p.sent if f["type"] == "session.thinking.append" and "dream fix" in f["content"]]   # the default kind
    assert len(notes) == 1 and notes[0]["content"].startswith("[2026-11-25T17:40:00Z 12:40 Local] chat gm")
    rep = json.loads(next((tmp_path / "native" / "calls").glob("*.json")).read_text())
    assert rep["thread"]["chat"] == "gm" and rep["thread"]["feed_sent"] == 1 and rep["thread"]["opening_tokens"] > 0


def test_a_slow_or_broken_thread_never_holds_the_call(tmp_path, monkeypatch):
    def boom(agent):
        raise OSError("api down")
    p, _, _ = _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=boom)
    assert p.sent[0]["type"] == "session.start"
    assert "THREAD" not in p.sent[0]["session"]["instructions"]


def test_the_escape_hatch_turns_both_off(tmp_path, monkeypatch):
    p, feeds, asked = _go(tmp_path, monkeypatch, {"type": "start"}, _started, flag="0")
    assert asked == [] and feeds == []
    assert "THREAD" not in p.sent[0]["session"]["instructions"]


def test_the_feed_stamps_new_chat_and_moves_its_cursor():
    items = [{"kind": "text", "role": "user", "text": "before", "ts": "2026-11-25T17:00:00Z", "audience": "user"},
             {"kind": "text", "role": "assistant", "text": "after", "ts": "2026-11-25T17:40:00Z", "audience": "user"}]
    since = 1795626600.0                 # 2026-11-25T17:10:00Z
    f = call_context.ChatFeed("gm", since=since, fetch=lambda a, n: items, tz="UTC", tz_label="UTC", operator="Shaw")
    assert f.poll() == ["[2026-11-25T17:40:00Z 17:40 UTC] chat gm (typed, during this call): after"]
    assert f.poll() == []                                           # never twice


def test_the_feed_is_capped_per_call():
    items = [{"kind": "text", "role": "user", "text": f"m{i}", "ts": f"2026-11-25T17:{i:02d}:00Z", "audience": "user"}
             for i in range(10)]
    f = call_context.ChatFeed("gm", since=0, fetch=lambda a, n: items, tz="UTC", tz_label="UTC", operator="S",
                              max_notes=3)
    assert len(f.poll()) == 3 and f.poll() == []


def test_a_tool_runs_knowing_its_call_and_the_pages_thread(tmp_path, monkeypatch):
    # gm_command tags its message to gm with these, so gm's answer lands in the thread the call came from
    from services.arturo.native.tests.test_native import FakeExecutor
    ex = FakeExecutor(reply="gm has it")

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        s.thread_loader = lambda agent: ""
        s.chat_feed_factory = lambda agent: None
        c = await _client(s)
        try:
            ws, p = await _start(c, {"type": "start", "agent": "pm-demo"})
            p.push({"type": "session.started"})
            p.push({"type": "session.input_transcript.delta", "delta": "ask the gm if the fix is in"})
            p.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
            p.push({"type": "response.event", "delegation_id": "d1", "event": {
                "type": "response.output_item.done",
                "item": {"type": "function_call", "call_id": "call_G", "name": "list_agents", "arguments": "{}"}}})
            p.push({"type": "response.event", "delegation_id": "d1", "event": {
                "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                           "usage": {"input_tokens": 10, "output_tokens": 2}}}})
            for _ in range(60):
                if ex.calls:
                    break
                await asyncio.sleep(0.05)
            await ws.send_str(json.dumps({"type": "disconnect"}))
            await ws.close()
        finally:
            await c.close()
    _run(go())
    assert ex.calls and ex.call_seen["thread_id"] == "pm-demo chat"
    assert ex.call_seen["conversation_id"].startswith("native-")


def test_the_proxy_turn_carries_the_call_and_thread():
    from services.arturo.native.tools import ProxyExecutor
    seen = {}

    class _Mod:
        def _as_turn(self, turn, fn, *a, **k):
            seen.update(turn)
            return "ok"
        execute_tool = None
    ProxyExecutor(_Mod())("gm_command", {"prompt": "x"}, "fleet",
                          call={"conversation_id": "native-1", "thread_id": "gm chat"})
    assert seen["conversation_id"] == "native-1" and seen["thread_id"] == "gm chat" and seen["principal"] == "fleet"


def test_run_sh_turns_on_the_thread_and_the_knowledge_facts_but_no_proxy_daemon():
    import pathlib
    sh = (pathlib.Path(__file__).resolve().parents[1] / "run.sh").read_text()
    assert 'export ARTURO_KNOWLEDGE_FACTS="${ARTURO_KNOWLEDGE_FACTS:-1}"' in sh
    assert 'export ARTURO_NATIVE_THREAD_CONTEXT="${ARTURO_NATIVE_THREAD_CONTEXT:-1}"' in sh
    assert "unset ARTURO_STREAM_RELAY ARTURO_WARM_VOICES_CACHE ARTURO_FACTS_RECALL" in sh


def test_the_opening_comes_from_the_api_route_and_falls_back_to_the_python_twin(tmp_path, monkeypatch):
    # the route redacts with the full redactor and this install's token; the twin is the same contract, offline
    monkeypatch.delenv(call_context.FLAG, raising=False)
    asked = []
    out = call_context.opening("gm", route=lambda agent, budget, **k: asked.append((agent, budget)) or "FROM ROUTE",
                               load=lambda **k: "FROM TWIN", directory=tmp_path)
    assert out == "FROM ROUTE" and asked == [("gm", call_context.budget_tokens())]   # nothing older: no re-read

    def down(agent, budget, **k):
        raise OSError("api down")
    seen = {}
    out = call_context.opening("gm", route=down, load=lambda **k: seen.update(k) or "FROM TWIN", directory=tmp_path)
    assert out == "FROM TWIN" and seen["chat_agent"] == "gm" and seen["call_owner"] is None


def test_the_route_is_asked_for_arturos_calls_with_the_budget(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return "THREAD …".encode()
    monkeypatch.setattr(call_context.urllib.request, "urlopen",
                        lambda url, timeout: seen.update(url=url, timeout=timeout) or _Resp())
    monkeypatch.setenv("ORCH_API_URL", "http://127.0.0.1:8888")
    assert call_context.fetch_route("gm", 6000) == "THREAD …"
    assert seen["url"] == "http://127.0.0.1:8888/api/agents/gm/call-context?calls=arturo&budget=6000"
    assert seen["timeout"] <= 5


def test_the_thread_goes_before_the_start_rule_so_arturo_still_greets_first():
    # live probe 2026-11-25 (native-5f12346ce59e): with the thread after the START rule, Arturo never greeted;
    # 10-16 probe: the greet only fires when START is the LAST thing in the live instructions.
    from services.arturo.native import native_config
    f = native_config.session_start_frame([], context="THREAD: chat Shaw: hello")
    live = f["session"]["instructions"]
    assert live.strip().split("\n\n")[-1].startswith("START:")
    assert "THREAD: chat Shaw: hello" in live and live.index("THREAD:") < live.rindex("START:")
    assert "THREAD: chat Shaw: hello" in f["session"]["delegation"]["responses"]["instructions"]


def test_the_past_context_boundary_sits_right_before_the_start_rule():
    # live probes 2026-11-25: the thread on killed the greet (native-90a7e00cd3d1), off greeted at 1.5 s
    # (native-8f4a14b348a8). The boundary says the thread is over and this call is new; START stays last. An opening
    # from an older API route (no fence) gets the boundary added here, once.
    from services.arturo import thread_context as tc
    from services.arturo.native import native_config
    for ctx in ("THREAD: call vc_x Shaw: you see that, right",
                f"{tc.PAST_OPEN}\nTHREAD: call vc_x Shaw: you see that, right\n{tc.PAST_CLOSE}"):
        f = native_config.session_start_frame([], context=ctx)
        live = f["session"]["instructions"]
        paras = live.strip().split("\n\n")
        assert paras[-1].startswith("START:")
        assert paras[-2].rstrip().endswith(tc.PAST_CLOSE) and paras[-2].count(tc.PAST_CLOSE) == 1
        assert paras[-2].startswith(tc.PAST_OPEN) and paras[-2].count(tc.PAST_OPEN) == 1
        backend = f["session"]["delegation"]["responses"]["instructions"].rstrip()
        assert backend.endswith(tc.PAST_CLOSE) and backend.count(tc.PAST_CLOSE) == 1


def test_the_chat_feed_defaults_to_thinking_the_kind_measured_voiced(monkeypatch):
    # live probe 2026-11-25 native-90a7e00cd3d1: a thinking note was acked and voiced ~5 s after it was sent;
    # instructions never completed (context_injection_incomplete). instructions stays available by env.
    monkeypatch.delenv(call_context.FEED_KIND_ENV, raising=False)
    assert call_context.feed_kind() == "thinking"
    monkeypatch.setenv(call_context.FEED_KIND_ENV, "instructions")
    assert call_context.feed_kind() == "instructions"
    monkeypatch.setenv(call_context.FEED_KIND_ENV, "bogus")
    assert call_context.feed_kind() == "thinking"


def test_the_opening_budget_defaults_to_25_stamped_turns(monkeypatch):
    # Shaw 2026-11-25 (gm msg_3a7542b2): a call opens with the last 20-25 turns across chat and calls, stamped; this
    # overrides the under-3 s greet bar. Measured on the real gm thread: 2000 -> 22 verbatim turns, 2500 -> 32.
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    assert call_context.budget_tokens() == call_context.DEFAULT_BUDGET_TOKENS == 2500
    assert call_context.MIN_TURNS == 25 and call_context.FLOOR_BUDGET_TOKENS == 4000
    monkeypatch.setenv("ARTURO_NATIVE_CONTEXT_TOKENS", "6000")
    assert call_context.budget_tokens() == 6000
    monkeypatch.setenv("ARTURO_NATIVE_CONTEXT_TOKENS", "junk")
    assert call_context.budget_tokens() == 2500


class _TwinRead(BaseException):
    """Not an Exception: call_context's `except Exception` cannot swallow it, so a twin read turns the test red."""


def _NO_TWIN(**k):
    raise _TwinRead("the twin was read: a floor test must never reach the live call journals")


def _opening(n_verbatim, n_summ=0):
    from services.arturo import thread_context as tc
    lines = [tc.PAST_OPEN, "THREAD: header"]
    if n_summ:
        lines += ["EARLIER (summarized):"] + [f"- [2026-11-25T10:0{i}:00Z 05:0{i} Local] call vc_old{i}, 3 phrase(s) not shown; x"
                                             for i in range(n_summ)]
    lines.append("RECENT (verbatim):")
    for i in range(n_verbatim):
        where = "chat" if i % 2 else f"call vc_c{i % 3}"
        lines.append(f"[2026-11-25T17:{i:02d}:00Z 12:{i:02d} Local] {where} Operator: line {i}")
    return "\n".join(lines + [tc.PAST_CLOSE])


def test_the_opening_is_described_by_turns_span_and_sources():
    d = call_context.describe(_opening(6, n_summ=2))
    assert d["turns"] == 6 and d["summarized"] == 2
    assert d["first"] == "2026-11-25T17:00:00Z" and d["last"] == "2026-11-25T17:05:00Z"
    assert d["chat_lines"] == 3 and d["call_lines"] == 3
    assert d["calls"] == ["vc_c0", "vc_c1", "vc_c2", "vc_old0", "vc_old1"]
    assert call_context.describe("") == {"turns": 0, "summarized": 0, "first": None, "last": None,
                                         "chat_lines": 0, "call_lines": 0, "calls": [], "chat_error": False}


def test_an_opening_short_of_25_turns_is_asked_again_at_the_floor(monkeypatch):
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    asked = []

    def route(agent, budget, **k):
        asked.append(budget)
        return _opening(12, n_summ=3) if budget < 4000 else _opening(30)
    out = call_context.opening("gm", route=route, load=_NO_TWIN)
    assert asked == [2500, 4000] and call_context.describe(out)["turns"] == 25      # 2500 * 25 / 12 is over the floor
    assert "- (5 older item(s) omitted)" in out                      # 30 read, the newest 25 kept, the cut counted
    asked.clear()
    call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(26), load=_NO_TWIN)
    assert asked == [2500]                                           # enough: one read, no second trip
    asked.clear()
    monkeypatch.setenv("ARTURO_NATIVE_CONTEXT_TOKENS", "1500")       # an explicit setting is taken as meant
    call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(5, n_summ=2), load=_NO_TWIN)
    assert asked == [1500]


def test_the_call_report_says_what_the_opening_carried(tmp_path, monkeypatch):
    p, _, _ = _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=lambda agent: _opening(7, n_summ=1))
    rep = json.loads(next((tmp_path / "native" / "calls").glob("*.json")).read_text())
    th = rep["thread"]
    assert th["turns"] == 7 and th["summarized"] == 1 and th["chat_lines"] == 3 and th["call_lines"] == 4
    assert th["first"] == "2026-11-25T17:00:00Z" and th["last"] == "2026-11-25T17:06:00Z"
    assert "vc_old0" in th["calls"] and th["opening_tokens"] > 0


def test_the_floor_re_read_is_skipped_when_nothing_older_exists_or_the_first_read_was_slow(monkeypatch):
    # Claude review of #638 (findings 1-2): a thread with no EARLIER block has nothing more to fetch, and a slow first
    # read must not push the opening past the server's OPENING_WAIT_S and lose the thread it already has.
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    asked = []
    out = call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(12), load=_NO_TWIN)      # short, nothing older
    assert asked == [2500] and call_context.describe(out)["turns"] == 12
    asked.clear()
    clock = iter([0.0, call_context.FLOOR_MAX_ELAPSED_S + 0.5])
    monkeypatch.setattr(call_context.time, "monotonic", lambda: next(clock))
    out = call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(12, n_summ=3), load=_NO_TWIN)
    assert asked == [2500] and call_context.describe(out)["turns"] == 12


def test_a_floor_read_that_is_not_better_keeps_the_first_text(monkeypatch):
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    first = _opening(12, n_summ=3)
    out = call_context.opening("gm", route=lambda a, b, **k: first if b < 4000 else "", load=lambda **k: "")
    assert out == first


def test_a_junk_context_setting_keeps_the_floor_on(monkeypatch):
    monkeypatch.setenv("ARTURO_NATIVE_CONTEXT_TOKENS", "junk")
    asked = []
    call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(12, n_summ=3), load=_NO_TWIN)
    assert asked == [2500, 4000]


def test_describe_reads_the_channel_and_call_id_from_the_stamp_not_the_words():
    from services.arturo import thread_context as tc
    text = "\n".join([tc.PAST_OPEN, "RECENT (verbatim):",
                      "[2026-11-25T17:00:00Z 12:00 Local] call vc_a Operator: he said ] chat  and call vc_fake, ok",
                      "[2026-11-25T17:01:00Z 12:01 Local] chat Operator: mention ] call vc_other here", tc.PAST_CLOSE])
    d = call_context.describe(text)
    assert (d["chat_lines"], d["call_lines"], d["calls"]) == (1, 1, ["vc_a"])


def test_the_floor_is_exactly_25_turns(monkeypatch):
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    for have, reads in ((24, 2), (25, 1)):
        asked = []
        call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(have if b == 2500 else 40, n_summ=2),
                             load=_NO_TWIN)
        assert len(asked) == reads, (have, asked)


def test_the_second_read_is_sized_to_reach_25_not_the_whole_floor(monkeypatch):
    # Claude review of #638 (finding 1): at 4000 the real gm thread carries ~59 turns, so one long reply would swing a
    # call between 25 and 59. The second read asks for what 25 turns cost at the first read's rate, capped at the floor.
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    asked = []
    out = call_context.opening("gm", route=lambda a, b, **k: asked.append(b) or _opening(20 if b == 2500 else 26, n_summ=2),
                               load=_NO_TWIN)
    assert asked == [2500, 3125] and call_context.describe(out)["turns"] == 25
    assert out.info == {"budget": 2500, "reread": 3125, "reread_result": "kept", "source": "route", "short": False}


def test_a_second_read_with_no_more_turns_keeps_the_first(monkeypatch):
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    first = _opening(12, n_summ=3)
    for second in (_opening(12, n_summ=3), _opening(8), ""):
        out = call_context.opening("gm", route=lambda a, b, **k: first if b == 2500 else second, load=lambda **k: "")
        assert out == first and out.info["reread"] == 4000 and out.info["short"] is True
        assert out.info["reread_result"] == ("failed" if second == "" else "not_more")


def test_the_opening_says_where_it_came_from(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)

    def down(agent, budget, **k):
        raise OSError("api down")
    out = call_context.opening("gm", route=down, load=lambda **k: _opening(3), directory=tmp_path)
    assert out.info["source"] == "twin" and out.info["reread"] is None
    out = call_context.opening("gm", route=down, load=lambda **k: "", directory=tmp_path)
    assert out == "" and out.info["source"] == "none"


def test_summarized_counts_the_omitted_items_and_a_failed_chat_read_is_named():
    text = _opening(2, n_summ=2).replace("RECENT (verbatim):", "- (4 older item(s) omitted)\nRECENT (verbatim):")
    assert call_context.describe(text)["summarized"] == 6
    assert call_context.describe(_opening(2))["chat_error"] is False
    broken = _opening(2).replace("RECENT (verbatim):", "(gm chat could not be read: URLError)\nRECENT (verbatim):")
    assert call_context.describe(broken)["chat_error"] is True


def test_describe_reads_every_shared_contract_case_as_rendered():
    # describe() parses text the API route renders: every verbatim line of every shared case is a turn, so a format
    # change on either side turns this red
    import pathlib
    cases = json.loads((pathlib.Path(__file__).resolve().parents[4] / "contract" / "call-context-cases.json")
                       .read_text())["cases"]
    for c in cases:
        body = c["expected"].splitlines()
        # the fixture's own shape, not describe()'s rule: every RECENT line but the chat-unreadable note is a turn
        recent = [ln for ln in (body[body.index("RECENT (verbatim):") + 1:-1] if "RECENT (verbatim):" in body else [])
                  if "chat could not be read" not in ln]
        earlier = (body[body.index("EARLIER (summarized):") + 1:body.index("RECENT (verbatim):")]
                   if "EARLIER (summarized):" in body else [])
        omitted = sum(int(re.search(r"\d+", ln).group()) for ln in earlier if "older item(s) omitted" in ln)
        d = call_context.describe(c["expected"])
        assert recent == [] or d["turns"] > 0, c["name"]                 # a stamp change on both sides is not zero == zero
        assert d["turns"] == d["chat_lines"] + d["call_lines"] == len(recent), c["name"]
        assert d["chat_lines"] == sum(ln.split("] ", 1)[1].startswith("chat ") for ln in recent), c["name"]
        assert d["summarized"] == len(earlier) - (1 if omitted else 0) + omitted, c["name"]
        quoted = (set(re.findall(r"^\[[^\]]*\] call (\S+) ", "\n".join(recent), re.M))
                  | set(re.findall(r"^- \[[^\]]*\] call ([^,\s]+),", "\n".join(earlier), re.M)))
        assert set(d["calls"]) == quoted, c["name"]
        assert d["chat_error"] is ("could not be read" in c["expected"]), c["name"]


def test_the_call_report_says_how_the_opening_was_read_and_a_timeout_skip(tmp_path, monkeypatch):
    import time as _t
    from services.arturo.native import server as srv
    opened = call_context.Opening(_opening(4), {"budget": 2500, "reread": None, "source": "route", "short": True})
    _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=lambda agent: opened)
    reports = tmp_path / "native" / "calls"
    th = json.loads(next(reports.glob("*.json")).read_text())["thread"]
    assert th["budget"] == 2500 and th["source"] == "route" and th["reread"] is None and th["skipped"] is None
    assert th["turns"] == 4 and th["short"] is True
    for f in reports.glob("*.json"):
        f.unlink()
    monkeypatch.setattr(srv, "OPENING_WAIT_S", 0.1)
    _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=lambda agent: _t.sleep(0.6) or opened)
    th = json.loads(next(reports.glob("*.json")).read_text())["thread"]
    assert th["skipped"] == "timeout" and th["turns"] == 0 and th["source"] is None


def test_the_second_read_is_route_only_and_bounded_by_the_deadline(monkeypatch):
    # Claude re-review of #638 (A): a first read at 1.5 s plus a stalled second read must not blow the server's 6 s
    # wait and lose the good first opening; the second read gets what is left of READ_DEADLINE_S, from the route.
    from services.arturo.native import server as srv
    assert call_context.READ_DEADLINE_S < srv.OPENING_WAIT_S
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    clock = iter([0.0, 1.5])
    monkeypatch.setattr(call_context.time, "monotonic", lambda: next(clock))
    seen, first = [], _opening(12, n_summ=3)

    def route(agent, budget, timeout=None):
        seen.append((budget, timeout))
        if budget == 2500:
            return first
        raise TimeoutError("stalled")
    out = call_context.opening("gm", route=route, load=_NO_TWIN)       # a twin read on the second trip goes red
    assert out == first and out.info["reread_result"] == "failed" and out.info["source"] == "route"
    assert seen == [(2500, 4.0), (4000, call_context.READ_DEADLINE_S - 1.5)]


def test_a_first_read_from_the_twin_gets_no_second_read(monkeypatch):
    monkeypatch.delenv("ARTURO_NATIVE_CONTEXT_TOKENS", raising=False)
    loads = []

    def down(agent, budget, **k):
        raise OSError("api down")
    out = call_context.opening("gm", route=down,
                               load=lambda **k: loads.append(k["budget_tokens"]) or _opening(12, n_summ=3))
    assert loads == [2500] and out.info["source"] == "twin" and out.info["reread"] is None


def test_an_empty_opening_reaches_the_report_with_how_it_was_read_and_a_loader_error_is_named(tmp_path, monkeypatch):
    # Claude re-review of #638 (B): "nothing could be read" must not look like the flag being off
    none = call_context.Opening("", {"budget": 2500, "reread": None, "reread_result": None, "source": "none",
                                     "short": True})
    _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=lambda agent: none)
    reports = tmp_path / "native" / "calls"
    th = json.loads(next(reports.glob("*.json")).read_text())["thread"]
    assert th["source"] == "none" and th["budget"] == 2500 and th["short"] is True and th["skipped"] is None
    for f in reports.glob("*.json"):
        f.unlink()

    def boom(agent):
        raise RuntimeError("loader broke")
    _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=boom)
    th = json.loads(next(reports.glob("*.json")).read_text())["thread"]
    assert th["skipped"] == "error" and th["source"] is None
