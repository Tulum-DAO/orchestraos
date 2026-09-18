# test_deliberation.py — Voice Deliberation Mode proxy half (spec §5).
from services.arturo import deliberation as dl


# ---- menu_blob_line ----
def _menu(**kw):
    d = {"kind": "options", "question": "Deploy acme to prod?",
         "options": [{"n": "1", "label": "Deploy now"}, {"n": "2", "label": "Hold for review"}],
         "selected_n": "2"}
    d.update(kw)
    return d


def test_blob_line_full():
    line = dl.menu_blob_line("acme-dev", _menu())
    assert "DECISION PENDING on acme-dev" in line
    assert "Deploy acme to prod?" in line
    assert "1) Deploy now" in line and "2) Hold for review" in line
    assert "leans 2" in line


def test_blob_line_degrades_without_options():
    line = dl.menu_blob_line("x", {"kind": "permission", "question": "Allow bash?"})
    assert "permission menu on screen" in line and "Allow bash?" in line


def test_blob_line_none_when_empty():
    assert dl.menu_blob_line("x", {}) is None
    assert dl.menu_blob_line("x", None) is None


def test_blob_line_truncates_many_options():
    opts = [{"n": str(i), "label": f"opt{i}"} for i in range(1, 14)]
    line = dl.menu_blob_line("x", {"kind": "options", "question": "pick", "options": opts})
    assert "and 3 more" in line


# ---- deliberation_context (injected gw_get) ----
def _fake_gw(agents_rows, screens):
    def gw(path):
        if path == "/agents":
            return {"agents": agents_rows}
        if path.startswith("/agent-screen?session="):
            sess = path.split("=", 1)[1]
            return screens.get(sess)
        return None
    return gw


def test_context_none_when_nothing_parked():
    gw = _fake_gw([{"session": "a", "has_pending_menu": False}], {})
    assert dl.deliberation_context(gw) is None


def test_context_focused_gets_full_detail():
    rows = [{"session": "acme-dev", "has_pending_menu": True},
            {"session": "globex-dev", "has_pending_menu": True}]
    screens = {"acme-dev": {"pending_menu": _menu()},
               "globex-dev": {"pending_menu": _menu(question="Ship globex?")}}
    out = dl.deliberation_context(_fake_gw(rows, screens), focused_session="acme-dev")
    assert "DECISIONS AWAITING THE OPERATOR" in out
    # focused agent is detailed + appears first
    assert out.index("acme-dev") < out.index("globex-dev")
    assert "Deploy acme to prod?" in out


def test_context_others_name_only_beyond_detail_cap():
    rows = [{"session": "a", "has_pending_menu": True},
            {"session": "b", "has_pending_menu": True},
            {"session": "c", "has_pending_menu": True}]
    screens = {s: {"pending_menu": _menu()} for s in ("a", "b", "c")}
    out = dl.deliberation_context(_fake_gw(rows, screens), focused_session="a", max_detail=1)
    # focused 'a' detailed; b/c beyond cap → name-only
    assert "open it to see the options" in out


# ---- deliberation_context ALSO reads the approval ledger (spec §3.3) ----
def _fake_gw_full(agents_rows=None, screens=None, pending=None):
    def gw(path):
        if path == "/agents":
            return {"agents": agents_rows or []}
        if path == "/pending-approvals":
            return {"ok": True, "pending": pending or []}
        if path.startswith("/agent-screen?session="):
            sess = path.split("=", 1)[1]
            return (screens or {}).get(sess)
        return None
    return gw


def test_context_reads_pending_approvals_when_no_panes():
    # no live parked pane, but a pending approval row exists (its pane may be gone / bridged card)
    pending = [{"id": "apr_77", "from_agent": "pm-adaptiv-payments",
                "question": "Deploy adaptiv to prod?", "feature": "payments-deploy"}]
    out = dl.deliberation_context(_fake_gw_full(pending=pending))
    assert out is not None
    assert "DECISIONS AWAITING THE OPERATOR" in out
    assert "Deploy adaptiv to prod?" in out
    assert "pm-adaptiv-payments" in out
    # tagged by source so Arturo answers it via the approval path, not a keypress
    assert "approval" in out.lower()


def test_context_unions_panes_and_approvals():
    rows = [{"session": "acme-dev", "has_pending_menu": True}]
    screens = {"acme-dev": {"pending_menu": _menu()}}
    pending = [{"id": "apr_9", "from_agent": "gm", "question": "Ship the explorer?"}]
    out = dl.deliberation_context(_fake_gw_full(rows, screens, pending),
                                  focused_session="acme-dev")
    assert "Deploy acme to prod?" in out          # live pane menu
    assert "Ship the explorer?" in out               # ledger approval
    assert "apr_9" in out


def test_context_none_when_neither_source():
    out = dl.deliberation_context(_fake_gw_full(agents_rows=[], pending=[]))
    assert out is None


# ---- answer_menu two-phase ----
def _fake_post(code, resp=None):
    calls = []
    def post(path, body):
        calls.append((path, body))
        return code, (resp if resp is not None else {})
    return post, calls


def test_answer_phase1_stages_and_asks_confirm():
    post, calls = _fake_post(428, {"needs_confirm": True})
    out = dl.answer_menu(post, "acme-dev", "2", confirm=False)
    assert out.startswith("CONFIRM_NEEDED")
    assert calls[0][1]["confirm"] is False and calls[0][1]["key"] == "2"


def test_answer_phase2_sends():
    post, calls = _fake_post(200, {"ok": True, "sent": "2"})
    out = dl.answer_menu(post, "acme-dev", "2", confirm=True)
    assert "Sent option 2 to acme-dev" in out
    assert calls[0][1]["confirm"] is True


def test_answer_verify_menu_resolved_confirms_success(monkeypatch):
    # DELIB-BUG-4: 200 + menu GONE on re-read -> genuine success.
    monkeypatch.setattr(dl._time, "sleep", lambda *_: None)
    post, _ = _fake_post(200, {"ok": True, "sent": "1"})
    gw_get = lambda path: {"pane": "done", "pending_menu": None}   # menu resolved
    out = dl.answer_menu(post, "acme-dev", "1", confirm=True, gw_get=gw_get)
    assert "Sent option 1 to acme-dev" in out


def test_answer_verify_settle_clears_after_lag(monkeypatch):
    # DELIB-BUG-4 settle: menu STALE on first poll (detector lag) then clears -> SUCCESS, no cry-wolf.
    monkeypatch.setattr(dl._time, "sleep", lambda *_: None)
    post, _ = _fake_post(200, {"ok": True, "sent": "2"})
    seq = [{"pending_menu": {"kind": "options"}},    # 1st poll: stale
           {"pending_menu": None}]                   # 2nd poll: cleared
    def gw_get(path):
        return seq.pop(0) if seq else {"pending_menu": None}
    out = dl.answer_menu(post, "acme-dev", "2", confirm=True, gw_get=gw_get)
    assert "Sent option 2 to acme-dev" in out     # resolved within the settle window


def test_answer_verify_menu_persists_reports_honestly(monkeypatch):
    # DELIB-BUG-4: menu present the WHOLE settle window (opt 5 chat / opt 4 free-text) -> honest.
    monkeypatch.setattr(dl._time, "sleep", lambda *_: None)
    post, _ = _fake_post(200, {"ok": True, "sent": "5"})
    still_menu = {"kind": "options", "question": "pick", "options": [{"n": "1", "label": "a"}]}
    gw_get = lambda path: {"pane": "menu still up", "pending_menu": still_menu}
    out = dl.answer_menu(post, "acme-dev", "5", confirm=True, gw_get=gw_get)
    assert "didn't go through" in out
    assert "Type something" in out and "Chat about this" in out
    assert "Sent option 5 to acme-dev" not in out


def test_answer_verify_skipped_on_stage_phase():
    # phase 1 (confirm=False) must NOT verify (nothing sent yet).
    post, _ = _fake_post(428, {"needs_confirm": True})
    called = {"n": 0}
    def gw_get(path):
        called["n"] += 1
        return {"pending_menu": None}
    out = dl.answer_menu(post, "acme-dev", "2", confirm=False, gw_get=gw_get)
    assert out.startswith("CONFIRM_NEEDED")
    assert called["n"] == 0                          # no verify on stage


def test_answer_409_menu_gone_honest():
    post, _ = _fake_post(409, {"error": "no pending menu on screen"})
    out = dl.answer_menu(post, "acme-dev", "1", confirm=True)
    assert "gone" in out and "Nothing sent" in out


def test_answer_403_key_not_allowed():
    post, _ = _fake_post(403, {"error": "key not enabled"})
    out = dl.answer_menu(post, "acme-dev", "5", confirm=True)
    assert "isn't allowed" in out and "Nothing sent" in out


def test_answer_404_no_session():
    post, _ = _fake_post(404, {"error": "no such session"})
    out = dl.answer_menu(post, "gone-agent", "1", confirm=True)
    assert "can't find" in out and "Nothing sent" in out


# ---- §1a: classify_option (input_kind of a stamped pending_menu option) ----
def _stamped_menu():
    # gateway /agent-screen stamps input_kind onto every option (direct|free_text|chat)
    return {"kind": "options", "question": "Deploy?",
            "options": [{"n": "1", "label": "Deploy now", "input_kind": "direct"},
                        {"n": "2", "label": "Hold", "input_kind": "direct"},
                        {"n": "3", "label": "Type something", "input_kind": "free_text"},
                        {"n": "4", "label": "Chat about this", "input_kind": "chat"}]}


def test_classify_option_free_text():
    assert dl.classify_option(_stamped_menu(), "3") == "free_text"


def test_classify_option_chat():
    assert dl.classify_option(_stamped_menu(), "4") == "chat"


def test_classify_option_direct():
    assert dl.classify_option(_stamped_menu(), "1") == "direct"


def test_classify_option_defaults_direct_when_missing():
    # option not on the menu, or an unstamped/absent menu → safe 'direct' default
    assert dl.classify_option(_stamped_menu(), "9") == "direct"
    assert dl.classify_option(None, "1") == "direct"
    assert dl.classify_option({"options": [{"n": "1", "label": "x"}]}, "1") == "direct"


# ---- §1a: answer_menu CHAT = keep discussing (NOT a keypress) ----
def test_answer_chat_option_keeps_discussing_no_post():
    post, calls = _fake_post(200, {"ok": True})
    out = dl.answer_menu(post, "acme-dev", "4", confirm=True, input_kind="chat")
    # a 'Chat about this' option is not a selection — Arturo must NOT send a keypress
    assert len(calls) == 0
    assert "keep" in out.lower() or "talk" in out.lower()
    assert "Sent option" not in out


# ---- §1a: answer_menu FREE_TEXT = verified three-phase via gateway {key,text} ----
def test_answer_free_text_without_text_asks_for_words():
    # free_text option selected but the operator hasn't given the words yet → prompt, don't send
    post, calls = _fake_post(200, {"ok": True})
    out = dl.answer_menu(post, "acme-dev", "3", confirm=True, input_kind="free_text")
    assert len(calls) == 0
    assert "type" in out.lower() and ("what" in out.lower() or "words" in out.lower())


def test_answer_free_text_phase1_stages_with_text():
    # phase 1 (confirm=False) → gateway 428 → spoken confirm that ECHOES the exact wording
    post, calls = _fake_post(428, {"needs_confirm": True})
    out = dl.answer_menu(post, "acme-dev", "3", confirm=False,
                         input_kind="free_text", text="redeploy with the hotfix")
    assert out.startswith("CONFIRM_NEEDED")
    assert "redeploy with the hotfix" in out
    assert calls[0][1]["confirm"] is False
    assert calls[0][1]["text"] == "redeploy with the hotfix"
    assert calls[0][1]["key"] == "3"


def test_answer_free_text_phase2_sends_text():
    # phase 2 (confirm=True) → gateway 200 → success, body carries text+confirm
    post, calls = _fake_post(200, {"ok": True, "sent": "3", "text_len": 24})
    out = dl.answer_menu(post, "acme-dev", "3", confirm=True,
                         input_kind="free_text", text="redeploy with the hotfix")
    assert "redeploy with the hotfix" in out
    assert "Sent" in out or "Typed" in out
    assert calls[0][1]["confirm"] is True and calls[0][1]["text"] == "redeploy with the hotfix"


def test_answer_free_text_409_menu_gone():
    post, _ = _fake_post(409, {"error": "menu no longer on screen"})
    out = dl.answer_menu(post, "acme-dev", "3", confirm=True,
                         input_kind="free_text", text="do X")
    assert "gone" in out and "Nothing sent" in out


def test_answer_free_text_400_not_free_text_honest():
    # gateway rejects text on a non-free_text option → report honestly, don't claim success
    post, _ = _fake_post(400, {"error": "text is only valid for a free_text option"})
    out = dl.answer_menu(post, "acme-dev", "3", confirm=True,
                         input_kind="free_text", text="do X")
    assert "Nothing sent" in out
    assert "Sent" not in out.split("Nothing sent")[0] if "Sent" in out else True


def test_answer_free_text_502_submit_failed_honest():
    post, _ = _fake_post(502, {"error": "free-text submit failed: enter_not_registered (phase 3)"})
    out = dl.answer_menu(post, "acme-dev", "3", confirm=True,
                         input_kind="free_text", text="do X")
    assert "Nothing sent" in out


def test_answer_direct_unchanged_when_input_kind_direct():
    # regression guard: explicit direct input_kind → the existing digit two-phase path
    post, calls = _fake_post(200, {"ok": True, "sent": "1"})
    out = dl.answer_menu(post, "acme-dev", "1", confirm=True, input_kind="direct")
    assert "Sent option 1 to acme-dev" in out
    assert calls[0][1].get("text") is None
