# deliberation.py — Voice Deliberation Mode (proxy half). Spec §5 / app-dev-v4 msg_abacb44c.
#
# When an agent parks on a decision menu (AskUserQuestion / plan-mode / permission dialog), the
# detector emits a `pending_menu` and the gateway serves it (GET /agent-screen?session=X,
# has_pending_menu on /agents). This module lets Arturo (voice) SURFACE that decision into the
# per-turn context, DELIBERATE about it with the operator across turns, and ANSWER it by verbal-selecting an
# option through the gateway's EXISTING two-phase POST /agent-key (spoken "yes" = the confirm phase;
# a spoken yes confirms a SPECIFIC staged key, never bypasses — D8-coherent).
#
# All network I/O is injected (gw_get / gw_post callables) so the logic is pure + unit-testable.
import time as _time


def _fmt_options(options, max_opts=10):
    """"1) label · 2) label · ..." from options [{n,label,detail?}]. Truncates >max_opts."""
    parts = []
    for o in (options or [])[:max_opts]:
        n = str(o.get("n", "?"))
        label = str(o.get("label", "")).strip()
        parts.append(f"{n}) {label}")
    tail = ""
    extra = len(options or []) - max_opts
    if extra > 0:
        tail = f" …and {extra} more"
    return " · ".join(parts) + tail


def menu_blob_line(session, menu):
    """One per-turn context line for a parked decision, or None if no usable menu.
    Shape (contract): "DECISION PENDING on <session>: <question> — options: 1) .. 2) .. ; agent
    leans <selected_n>". Degrades honestly when labels/question are missing (kind+chrome only)."""
    if not isinstance(menu, dict):
        return None
    kind = menu.get("kind")
    question = (menu.get("question") or "").strip()
    options = menu.get("options") or []
    selected = menu.get("selected_n")
    if not kind and not options:
        return None
    head = f"DECISION PENDING on {session}"
    if question:
        head += f": {question}"
    if options:
        head += f" — options: {_fmt_options(options)}"
    elif kind:
        head += f" — {kind} menu on screen (labels unparsed; open terminal view for detail)"
    if selected:
        head += f" ; agent leans {selected}"
    return head


def approval_blob_line(row):
    """One per-turn context line for a PENDING approval ledger row (spec §3.3). Tagged as an
    approval + its from_agent/feature so Arturo knows to answer it via the ledger answer path
    (POST /approval-answers), NOT a pane keypress. Degrades honestly on sparse rows."""
    if not isinstance(row, dict):
        return None
    rid = row.get("id") or "?"
    q = (row.get("question") or row.get("summary") or "").strip()
    frm = row.get("from_agent")
    feat = row.get("feature")
    head = f"DECISION PENDING (approval {rid}"
    if frm:
        head += f" from {frm}"
    head += ")"
    if q:
        head += f": {q}"
    if feat:
        head += f" [feature: {feat}]"
    return head


def deliberation_context(gw_get, focused_session=None, max_detail=1, include_approvals=True):
    """Build the per-turn DECISIONS-PENDING block from TWO sources (spec §3.3):
      (1) LIVE PANES  — /agents `has_pending_menu`, then a FULL menu fetch for the focused parked
          agent (and at most max_detail others);
      (2) THE LEDGER  — pending approval rows (/pending-approvals), so Arturo can deliberate an
          approval even when its pane is GONE or it only exists as a bridged card.
    Same "DECISIONS AWAITING THE OPERATOR" block, tagged by source. Returns a string or None when NEITHER
    source has anything. `gw_get(path)` -> parsed JSON | None (injected). Runs on EVERY voice turn,
    so the caller passes a SHORT-timeout gw_get: a slow/unreachable gateway degrades to None/partial,
    never a hang."""
    lines = []

    # (1) live panes
    agents = gw_get("/agents")
    rows = None
    if isinstance(agents, dict):
        rows = agents.get("agents") if isinstance(agents.get("agents"), list) else agents.get("rows")
    if isinstance(rows, list):
        parked = [r for r in rows if isinstance(r, dict) and r.get("has_pending_menu")]

        def _sess(r):
            return r.get("session") or r.get("session_name") or r.get("name") or "?"

        parked.sort(key=lambda r: 0 if _sess(r) == focused_session else 1)
        detailed = 0
        for r in parked:
            sess = _sess(r)
            want_detail = (sess == focused_session) or (detailed < max_detail)
            if want_detail:
                scr = gw_get(f"/agent-screen?session={sess}")
                menu = scr.get("pending_menu") if isinstance(scr, dict) else None
                line = menu_blob_line(sess, menu)
                if line:
                    lines.append(line)
                    detailed += 1
                    continue
            lines.append(f"DECISION PENDING on {sess} (open it to see the options)")

    # (2) the ledger — pending approval rows (may have no live pane at all)
    if include_approvals:
        pa = gw_get("/pending-approvals")
        prows = pa.get("pending") if isinstance(pa, dict) else None
        for row in prows or []:
            line = approval_blob_line(row)
            if line:
                lines.append(line)

    if not lines:
        return None
    return "DECISIONS AWAITING THE OPERATOR:\n" + "\n".join(lines)


def classify_option(menu, option):
    """§1a: return the input_kind ('direct' | 'free_text' | 'chat') of the option whose n==option
    in a gateway-STAMPED pending_menu (GET /agent-screen stamps input_kind on every option). Safe
    'direct' default when the menu is missing/unstamped or the option isn't present — a direct digit
    is the non-destructive fallback (a bare digit on a free_text/chat option merely opens/ignores,
    it can't submit a wrong answer)."""
    if not isinstance(menu, dict):
        return "direct"
    key = str(option).strip()
    for o in menu.get("options") or []:
        if isinstance(o, dict) and str(o.get("n")) == key:
            return o.get("input_kind") or "direct"
    return "direct"


def answer_menu(gw_post, session, option, confirm=False, gw_get=None, text=None, input_kind=None):
    """Answer a parked menu via the gateway's two-phase POST /agent-key. Phase 1 (confirm=False)
    STAGES the key → gateway 428 needs_confirm → we return a spoken confirm prompt. Phase 2
    (confirm=True) SENDS it. Honest on 409 (menu gone) / 403 (key not allowed) / 404 (no session).
    `gw_post(path, body)` -> (status_code, parsed_json|text). Returns a SPOKEN-ready string.

    §1a INPUT-KIND ROUTING (input_kind stamped by the gateway, passed in by the proxy after a fresh
    /agent-screen classify):
      • 'chat'  ("Chat about this") — NOT a keypress answer at all; it means keep deliberating with
        the agent. We send NOTHING and tell the operator to keep talking it through (or pick a number).
      • 'free_text' ("Type something") — needs the operator's OWN words. We ride the gateway's verified
        three-phase (digit → literal text → Enter → verify) by POSTing {key, text, confirm}; a bare
        digit would only open the TUI field and strand it (DELIB-BUG-4 / the operator live-finding 07:27).
        No text yet → ask for the words, send nothing.
      • 'direct'/None — the existing digit two-phase + verify-after-answer settle (unchanged)."""
    input_kind = input_kind or "direct"

    # CHAT: a 'Chat about this' option is a keep-discussing affordance, not a selection. Never a keypress.
    if input_kind == "chat":
        return (f"Option {option} on {session} is a 'Chat about this' option — that's not a decision, "
                f"it's an invitation to keep talking it through with the agent. Tell me what you want "
                f"to explore and I'll carry it into the conversation, or pick a numbered option to decide.")

    # FREE_TEXT: needs the operator's typed words; ride the gateway's verified three-phase via {key, text}.
    if input_kind == "free_text":
        key = str(option).strip()
        if not text or not str(text).strip():
            return (f"Option {option} on {session} is a 'Type something' option — it needs your own "
                    f"words, not just a number. Tell me exactly what you want to type and I'll put it in.")
        typed = str(text).strip()
        body = {"session": session, "key": key, "text": typed, "confirm": bool(confirm)}
        code, resp = gw_post("/agent-key", body)
        if code == 428:
            return (f"CONFIRM_NEEDED: I'll type \u201c{typed}\u201d into option {key} on {session}. "
                    f"Say yes to confirm.")
        if code == 200:
            return f"Sent option {key} to {session} and typed your answer: \u201c{typed}\u201d."
        if code == 409:
            return (f"That menu on {session} is gone — it was answered already or the agent moved on. "
                    f"Nothing sent.")
        if code == 404:
            return f"I can't find the {session} session anymore. Nothing sent."
        _err = resp.get("error") if isinstance(resp, dict) else str(resp)
        return (f"Couldn't type your answer into {session}'s menu (code {code}): {_err}. Nothing sent.")

    # DIRECT digit path. DELIB-BUG-4 (the operator live vc_client_2d196728a2d7): /agent-key returns {ok,sent}
    # on a send-keys returncode WITHOUT checking the menu actually RESOLVED. VERIFY-AFTER-ANSWER: when
    # gw_get is provided, after a 200 we re-read /agent-screen; if the pending_menu is STILL present
    # the answer did NOT take -> report honestly instead of claiming success (voice analogue of
    # verified-inject). Note: with §1a input-kind routing above, a free_text/chat option is handled
    # before it ever reaches this digit path — this stays the fallback for a plain 'direct' selection.
    key = str(option).strip()
    body = {"session": session, "key": key, "confirm": bool(confirm)}
    code, resp = gw_post("/agent-key", body)
    if code == 200:
        # verify-after-answer: a resolved menu vanishes from the detector. If it's still there, the
        # keypress didn't answer it (free_text needs typed text; chat isn't a selection).
        # SETTLE (reviewer DEC-1786526381): the detector is a poll-and-scrape, so a legitimately
        # SUCCESSFUL direct answer can still show a STALE pending_menu on an immediate re-read. Poll
        # a few times over ~2.5s and only conclude "didn't go through" if the menu PERSISTS the whole
        # window — otherwise a good direct answer would falsely cry wolf. If the menu clears at any
        # point, it resolved → success.
        if confirm and gw_get is not None:
            still = False
            for _i in range(4):                      # ~0, 0.6, 1.2, 1.8s → up to ~2.4s, within budget
                try:
                    scr = gw_get(f"/agent-screen?session={session}")
                    present = isinstance(scr, dict) and bool(scr.get("pending_menu"))
                except Exception:
                    present = False                  # gateway error → don't cry wolf; assume sent
                if not present:
                    still = False
                    break                            # menu cleared → answer took
                still = True
                if _i < 3:
                    _time.sleep(0.6)
            if still:
                return (f"I sent option {key} to {session}, but its menu is STILL showing — it "
                        f"didn't go through. Some options need more than a keypress: a "
                        f"'Type something' option opens a text field that needs your typed answer, "
                        f"and a 'Chat about this' option isn't a direct selection. Tell me what you "
                        f"want to say and I'll handle it, or we can pick a numbered option.")
        return f"Sent option {key} to {session}."
    if code == 428:
        # staged — ask the operator to confirm (the spoken 'yes' becomes the confirm phase)
        return (f"CONFIRM_NEEDED: I'll send option {key} to {session}. Say yes to confirm.")
    if code == 409:
        return (f"That menu on {session} is gone — it was answered already or the agent moved on. "
                f"Nothing sent.")
    if code == 404:
        return f"I can't find the {session} session anymore. Nothing sent."
    if code == 403:
        _err = resp.get("error") if isinstance(resp, dict) else str(resp)
        return f"That key isn't allowed on {session}: {_err}. Nothing sent."
    _err = resp.get("error") if isinstance(resp, dict) else str(resp)
    return f"Couldn't answer {session}'s menu (code {code}): {_err}. Nothing sent."
