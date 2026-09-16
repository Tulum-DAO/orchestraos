"""Fork B watch gateway — the only endpoint the OrchestraUltra watch app talks to.

Free-provisioning (no $99, no Push entitlement) means the watch cannot receive
APNs pushes. Instead it POLLS this gateway for pending approvals and fires a
LOCAL notification (which needs no paid entitlement) carrying the native APPROVAL
category, then POSTs the tapped answer back here.

Exposed publicly via `tailscale funnel` (so the Watch Ultra reaches it on
cellular). Every route requires a bearer token (constant-time compared); TLS is
terminated on this VPS by Tailscale, so approval content stays encrypted to the
box. The token lives at ~/.config/jarvis/watch-gateway-token (chmod 600);
generate with `python3 watch_gateway.py --make-token`.

Routes:
  GET  /pending-approvals -> pending approval_requests rows (id, from_agent,
                             question, options, created_at)
  POST /approval-answers  -> {id, answer, text?} -> record_answer + fire_resume
                             (id-bound + answer-once; coexists with the ntfy
                              listener writing the same ledger)
  GET  /history           -> answered decisions, newest-first (?limit=, iOS
                             history surface; additive, read-only)
  GET  /approvals/{id}    -> one full ledger row (detail screens)
  GET  /agents            -> fleet liveness (registry ∩ tmux ∩ activity), 5s cache
  GET  /projects          -> client command-center projection (state/clients/*)
  GET  /pipeline          -> deals by stage from client.json
  GET  /briefing          -> deltas for the Arturo briefing (?since= ISO ts)
  GET  /health            -> {ok, pending}

P1 endpoints (spec docs/orchestraos-full-spec.md §7.2) are ALL additive +
read-only; the v1 watch contract above is frozen.

Nothing here runs automatically — starting it (under tmux/systemd) + turning on
funnel is an explicit step.
"""
from __future__ import annotations
import os, re, sys, json, hmac, hashlib, secrets
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore
import approval_resume
import questionnaire_resume
import logging
# Module logger for the voice-lane endcall/fallback paths (fixes the `log` NameError:
# 4 log.{info,warning,error} calls in the 2840-2892 endcall region referenced an
# undefined `log`, so the Arturo finalize FALLBACK path crashed instead of logging —
# ios-watch-dev caught it 2026-08-25; only fired on the error path, hence latent).
log = logging.getLogger("watch_gateway")
try:
    from services.arturo.gemini_live_bridge import GeminiLiveSession
except Exception as _e:
    GeminiLiveSession = None

TOKEN_FILE = Path(os.environ.get("WATCH_GATEWAY_TOKEN_FILE",
                                 os.path.expanduser("~/.config/jarvis/watch-gateway-token")))
GATEWAY_HOST = os.environ.get("WATCH_GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.environ.get("WATCH_GATEWAY_PORT", "9091"))


def gateway_token() -> str:
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def _authorized(request) -> bool:
    """Constant-time bearer check. Refuses when no token is provisioned."""
    expected = gateway_token()
    if not expected:
        return False
    got = request.headers.get("Authorization", "")
    if not got.startswith("Bearer "):
        return False
    return hmac.compare_digest(got[len("Bearer "):], expected)


def _json(obj, status=200):
    from aiohttp import web
    return web.json_response(obj, status=status)


def _evidence(r):
    """Parse the evidence JSON col; None for plain approvals. Dual-use: completion
    rows carry the done-brief (§3.4); commitment human_task rows carry
    {"due_ts","due_source"} for the countdown chip (DEC-1789352893701528)."""
    ev = r.get("evidence")
    if isinstance(ev, str) and ev:
        try: return json.loads(ev)
        except ValueError: return None
    return ev if isinstance(ev, dict) else None


def _menu(r):
    """Parse the menu JSON col (app-watch-decision-surface §1) — the bridge's
    stored pane-menu capture, passed verbatim. None for non-menu rows."""
    m = r.get("menu")
    if isinstance(m, str) and m:
        try:
            m = json.loads(m)
        except ValueError:
            return None
    if isinstance(m, dict):
        if "options" in m and isinstance(m["options"], list):
            cleaned = []
            for i, opt in enumerate(m["options"]):
                if isinstance(opt, dict):
                    opt_c = dict(opt)
                    if "n" in opt_c:
                        opt_c["n"] = str(opt_c["n"])
                    else:
                        opt_c["n"] = str(i + 1)
                    if "input_kind" not in opt_c:
                        opt_c["input_kind"] = "direct"
                    cleaned.append(opt_c)
                elif isinstance(opt, str):
                    cleaned.append({"n": str(i + 1), "label": opt, "input_kind": "direct"})
            m["options"] = cleaned
        return m
    return None


# §1a answer-payload validation matrix. Returns an error string (→ 400) or
# None when the option answer is valid for this row. The gateway is the
# type-safety gate (§6a): option_n must be a *string* digit index matching a
# real captured option, before it ever reaches /agent-key.
ANSWER_TEXT_MAX = 2000


def _validate_option_answer(row, option_n, answer_text):
    if row.get("kind") != "menu":
        return "answer 'option' is only valid on menu rows"
    menu = _menu(row) or {}
    opts = menu.get("options") or []
    if not isinstance(option_n, str) or not option_n.isdigit():
        return "option_n must be a stringified digit"
    opt = next((o for o in opts if isinstance(o, dict) and o.get("n") == option_n), None)
    if opt is None:
        return f"option_n {option_n} does not match a captured option"
    input_kind = opt.get("input_kind") or "direct"
    if input_kind == "chat":
        return "chat options are not answerable here — open the agent chat"
    # R7 (§3.2): answer_text is ALWAYS legal (≤2000) on any option. free_text
    # REQUIRES it; direct treats it as commentary (stored, digit still fires).
    if answer_text and len(answer_text) > ANSWER_TEXT_MAX:
        return f"answer_text exceeds {ANSWER_TEXT_MAX} chars"
    if input_kind == "free_text" and not answer_text:
        return "answer_text required for a free_text option"
    return None


def _free_text_option_n(row):
    """R7: the n of the row's free_text-class option (the built-in escape a
    free-form answer routes through), or None if the capture has none."""
    menu = _menu(row) or {}
    for o in (menu.get("options") or []):
        if isinstance(o, dict) and (o.get("input_kind") or "direct") == "free_text":
            return o.get("n")
    return None


# ---------------------------------------------------------------------------
# Per-instance permission-prompt identity (DEC-1786771513). A permission prompt's
# content key digest = sha256(session|question)[:16] is NOT unique across
# consecutive same-question prompts ("Do you want to proceed?" repeats). The
# instance ledger adds instance_n: STABLE while the same prompt is on screen,
# BUMPED when one is answered/cleared and a new same-question prompt appears. One
# id then threads all 3 surfaces (chat .id(), approvals perm: id, D2 op_key).
#
# The gateway is the SOLE writer (asyncio-single-threaded; sees both menu
# surfaces + every /agent-key answer). Durable JSON so the D2 cron can READ it.
# FAIL-OPEN everywhere: any ledger error -> fall back to instance_n=1 / base id,
# never break the feed (bound condition #1). instance_n resetting to 1 on a lost
# ledger is benign (one refresh/re-notify, never a stale-armed card).

PERM_INSTANCE_FLAP_GRACE_S = 2.5   # D-C ruling: below the 8s menuHold grace, above one poll
_PERM_INSTANCE_LEDGER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state", "perm-instance-ledger.json")


def _load_instance_ledger(path=None):
    """Load the instance ledger. FAIL-OPEN: missing/corrupt -> {} (never raises)."""
    path = path or _PERM_INSTANCE_LEDGER
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_instance_ledger(path, ledger):
    """Atomic write (never a torn read on a concurrent D2 cron read)."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(ledger, f)
        os.replace(tmp, path)
    except OSError as e:  # noqa: BLE001 — persistence is best-effort, never fatal
        print(f"[watch_gateway] instance ledger save failed: {e}", file=sys.stderr)


def _instance_step(ledger, session, digest, present, now,
                   answered_signal=False, flap_grace_s=PERM_INSTANCE_FLAP_GRACE_S):
    """Advance the (session,digest) instance state by ONE observation. PURE (no
    I/O): mutates `ledger` in place, returns the current instance_n.

    Transitions:
      - never-seen + absent            -> 0 (nothing to track; no entry created)
      - never-seen + present           -> instance_n=1, phase=present
      - present + present              -> STABLE (same instance_n)
      - present + absent + answered    -> phase=gone IMMEDIATELY (an answer is a
                                          confirmed clear; closes the <1s hole)
      - present + absent + not-answered-> gone_since=now; flip to gone only once
                                          absence >= flap_grace_s (a single dropped
                                          scrape must NOT fabricate a new instance)
      - gone + present                 -> NEW instance (instance_n += 1)
    `answered_signal` is set by handle_agent_key on a successful digit send for the
    current prompt, observed in-process (not via a lucky poll)."""
    key = f"{session}|{digest}"
    e = ledger.get(key)
    if e is None:
        if not present:
            return 0
        ledger[key] = {"instance_n": 1, "phase": "present", "answered": False,
                       "last_present_ts": now, "gone_since": None}
        return 1
    if present:
        if e.get("phase") == "gone":
            e["instance_n"] = int(e.get("instance_n", 0)) + 1
            e["phase"] = "present"; e["answered"] = False
            e["gone_since"] = None
        e["last_present_ts"] = now
        if answered_signal:
            e["answered"] = True
        return int(e.get("instance_n", 1))
    # absent this observation
    if e.get("phase") != "gone":
        if e.get("answered"):
            e["phase"] = "gone"; e["gone_since"] = now
        else:
            if e.get("gone_since") is None:
                e["gone_since"] = now
            elif (now - e["gone_since"]) >= flap_grace_s:
                e["phase"] = "gone"
    return int(e.get("instance_n", 1))


def _stamp_instance(session, menu, ledger=None, now=None, answered_signal=False,
                    persist=True):
    """Resolve instance_n for a live permission `menu` and return
    (instance_n, digest). Loads+persists the durable ledger unless a ledger dict
    is injected. FAIL-OPEN: any error -> (1, digest) so the surfaces still get a
    stable-enough id and the feed never breaks."""
    import time as _time
    q = (menu or {}).get("question") or ""
    digest = hashlib.sha256((session + "|" + q).encode()).hexdigest()[:16]
    try:
        now = _time.time() if now is None else now
        own = ledger is None
        led = _load_instance_ledger() if own else ledger
        n = _instance_step(led, session, digest, present=True, now=now,
                           answered_signal=answered_signal)
        if own and persist:
            _save_instance_ledger(_PERM_INSTANCE_LEDGER, led)
        return (n or 1), digest
    except Exception as e:  # noqa: BLE001 — never let identity break the surface
        print(f"[watch_gateway] instance stamp failed for {session}: {e}", file=sys.stderr)
        return 1, digest


def _mark_instance_answered(session, question):
    """Record that the current (session,question) permission prompt was ANSWERED.
    An answer is a CONFIRMED dismissal, so we flip phase->gone IMMEDIATELY (not
    just set a flag): the next same-question prompt then bumps instance_n on its
    first present observation, with no dependence on a lucky absence poll landing
    in the <1s gap. Called from handle_agent_key on a successful digit send.
    Best-effort / fail-open — the answer path never fails on this."""
    try:
        digest = hashlib.sha256((session + "|" + question).encode()).hexdigest()[:16]
        led = _load_instance_ledger()
        import time as _time
        e = led.get(f"{session}|{digest}")
        if e is not None:
            e["answered"] = True
            e["phase"] = "gone"
            e["gone_since"] = _time.time()
            _save_instance_ledger(_PERM_INSTANCE_LEDGER, led)
    except Exception as e:  # noqa: BLE001 — never let the answer path fail on this
        print(f"[watch_gateway] mark-answered failed for {session}: {e}", file=sys.stderr)


def _perm_pseudo_row(session, menu):
    """Additive read-time PERMISSION pseudo-row (D3) from a live detector
    pending_menu whose kind=='permission'. OPTION-ONLY: every option is forced
    input_kind='direct' — native permission prompts are a fixed Yes/Yes-always/No
    set with NO free-text slot (the operator field finding 2026-08-15), distinct from
    menu/questionnaire cards. Answered via /agent-key (live pane), never the
    ledger; the 'perm:' id prefix is what the /answer guard + client transport
    switch key off so it can never be routed to the ledger by accident."""
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    q = menu.get("question") or ""
    # Per-instance identity (DEC-1786771513): instance_n distinguishes consecutive
    # same-question prompts. digest is the content key; the id carries the instance
    # so distinct prompts are distinct approvals rows (no dedup-to-one collision).
    _n, _digest = _stamp_instance(session, menu)
    opt_objs = [{"n": o["n"], "label": o.get("label", ""), "input_kind": "direct"}
                for o in (menu.get("options") or [])
                if isinstance(o, dict) and o.get("n")]
    captured = menu.get("captured_at")
    created_iso = _dt.fromtimestamp(
        captured if isinstance(captured, (int, float)) else _time.time(),
        _tz.utc).isoformat()
    # DECODE-SAFE SHAPE (fix 2026-08-15): top-level `options` MUST be a list of
    # STRING labels — every other approval row is that shape, and the iOS/watch
    # clients decode the whole /pending-approvals array as [PendingApproval] where
    # options is [String]; a list-of-objects here would fail the WHOLE array decode
    # (not just this row) on any client. The structured options ride the `menu`
    # field (MenuData shape) exactly like bridged kind='menu' rows — so the client
    # reuses the same OptionRowsView path, and OLD clients still decode (they just
    # ignore the unknown kind='permission'). Additive + backward-compatible.
    row = {"id": f"perm:{session}:{_digest}:{_n}", "kind": "permission",
           "from_agent": session, "session": session, "question": q,
           "options": [o["label"] for o in opt_objs],
           "menu": {"question": q, "options": opt_objs,
                    "selected_n": menu.get("selected_n"),
                    "source_session": session, "captured_at": captured},
           "selected_n": menu.get("selected_n"),
           "chrome": menu.get("chrome"), "instance_id": f"{_digest}:{_n}",
           "created_at": created_iso, "urgency": 2}
    row["priority_score"] = _priority_score(row)
    return row


def _perm_pseudo_rows():
    """Scan the live fleet for permission prompts -> additive pseudo-rows.
    BOUNDED (one detector read per session, the same seam /agents uses) and
    FAIL-OPEN: any error yields an empty permission set, so a detector hiccup can
    NEVER break the whole approvals feed (gm gate #2; mirrors the _qnr_pseudo_row
    try/except in handle_pending). Per-session errors are skipped individually so
    one bad scrape doesn't sink the rest."""
    rows = []
    present_keys, scanned = set(), set()
    try:
        ast = _agent_status()
        # Fast path: if _agents_cache has data, target only sessions that flag has_pending_menu
        # instead of sequentially executing subprocesses across the entire live fleet (60+ sessions).
        cached_data = _agents_cache.get("data")
        if isinstance(cached_data, list):
            scanned = {r.get("tmux_session") or r.get("id") for r in cached_data if (r.get("tmux_session") or r.get("id"))}
            target_sessions = [r.get("tmux_session") or r.get("id") for r in cached_data if r.get("has_pending_menu")]
        else:
            target_sessions = _tmux_session_names()
            scanned = set(target_sessions)

        for sess in target_sessions:
            if not sess:
                continue
            try:
                st = ast.get_agent_status(sess)
                menu = st.get("pending_menu") if isinstance(st, dict) else None
                if isinstance(menu, dict) and menu.get("kind") == "permission":
                    row = _perm_pseudo_row(sess, menu)
                    rows.append(row)
                    # remember (session|digest) so the reap step below can mark
                    # every OTHER tracked prompt on a scanned session absent.
                    q = menu.get("question") or ""
                    present_keys.add(f"{sess}|{hashlib.sha256((sess+'|'+q).encode()).hexdigest()[:16]}")
            except Exception as e:  # noqa: BLE001 — one bad session never sinks the scan
                print(f"[watch_gateway] perm pseudo-row skip {sess}: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — detector hiccup never breaks the feed
        print(f"[watch_gateway] perm pseudo-row scan failed: {e}", file=sys.stderr)
        return []
    # ABSENCE RECONCILE: this sweep saw every live session, so a tracked prompt on
    # a scanned session that is NOT present now has cleared — observe its absence
    # so a self-cleared prompt (no /agent-key answer) still transitions present->
    # gone (flap-grace bounded) and the replacement bumps. Fail-open.
    _reap_absent_instances(present_keys, scanned)
    return rows


def _reap_absent_instances(present_keys, scanned_sessions, now=None):
    """Observe absence for tracked (session|digest) keys whose session WAS scanned
    this sweep but whose prompt is not present — the self-clear path (no answer
    signal). Only reaps keys of scanned sessions (a session we didn't scan may
    just be offline this beat; don't fabricate absence for it). Fail-open."""
    import time as _time
    try:
        now = _time.time() if now is None else now
        led = _load_instance_ledger()
        changed = False
        for key, e in led.items():
            sess = key.split("|", 1)[0]
            if sess in scanned_sessions and key not in present_keys \
                    and isinstance(e, dict) and e.get("phase") != "gone":
                digest = key.split("|", 1)[1] if "|" in key else ""
                _instance_step(led, sess, digest, present=False, now=now)
                changed = True
        if changed:
            _save_instance_ledger(_PERM_INSTANCE_LEDGER, led)
    except Exception as e:  # noqa: BLE001 — reconcile is best-effort
        print(f"[watch_gateway] instance reap failed: {e}", file=sys.stderr)


def _client_hydrates_multipart(request):
    """§1.1(b) condition 3: True iff the client advertises multi-part hydration via
    the X-Client-Capabilities header (comma/space token list containing
    'hydrates-multipart'). An ABSENT/empty header => False — an old client that
    never sends it MUST be treated as non-capable (default SAFE)."""
    try:
        cap = request.headers.get("X-Client-Capabilities", "") or ""
    except Exception:  # noqa: BLE001 — a header read failure defaults SAFE
        cap = ""
    toks = {t.strip().lower() for t in re.split(r"[,\s]+", cap) if t.strip()}
    return "hydrates-multipart" in toks


def _failsafe_unhydrated_multipart(out_row, capable):
    """§1.1(b) condition 3 — capability-gated fail-safe. `out_row` is a /pending
    output dict. If it is a multi-part menu NOT yet hydrated (walk_complete:false)
    and the client is NOT capable of hydrating it, rewrite it to a READ-ONLY echo
    (no tappable options + explicit read_only flag) so the raw un-hydratable card
    is NEVER returned as submittable — the release stays safe WITHOUT an atomic
    server/client deploy. A capable client gets the raw card and hydrates it via
    /agent-menu-capture. Non-menu / single-part / already-hydrated rows pass
    through untouched. Pure (returns a new dict on rewrite; never mutates input)."""
    menu = out_row.get("menu")
    if not isinstance(menu, dict):
        return out_row
    if not (menu.get("multipart") and not menu.get("walk_complete")):
        return out_row                                   # single-part / hydrated
    if capable:
        return out_row                                   # capable => raw, it hydrates
    echo = dict(out_row)
    m = dict(menu)
    m["read_only"] = True                                # new clients: render read-only
    m["fail_safe"] = True
    m["options"] = []                                    # nothing tappable (safe for ANY client)
    m["notice"] = "Multi-part menu — open the agent to answer."
    echo["menu"] = m
    echo["options"] = []
    return echo


def gate_menu_rows_for_client(rows, request):
    """THE SINGLE CHOKEPOINT (§1.1(b) cond3, gm scope 2026-08-26): every approval/
    menu row that egresses to a client passes through here before it is serialized.
    Reads the client capability ONCE, then applies the read-only fail-safe to each
    un-hydrated multi-part row (_failsafe_unhydrated_multipart is a per-row no-op for
    single-part / already-hydrated / capable-client rows).

    Any route that returns an approval/menu row to a client MUST serve through this
    one seam — a SECOND un-gated egress is the exact failure mode that produced the
    detail-endpoint gap (handle_pending gated the list but handle_approval_detail
    served raw; caught live on the operator's build-154, 2026-08-26). Centralizing the gate
    here means a future egress is gated by construction, not by remembering to wire
    it. Accepts a list; single-row callers pass [row] and take [0].
    """
    capable = _client_hydrates_multipart(request)
    return [_failsafe_unhydrated_multipart(r, capable) for r in rows]


def _options_n_str(opts):
    """Serve-boundary guard: flat feed `options[].n` -> str. The client decodes
    the WHOLE pending array strictly (MenuData.Option.n:String; one int-n row
    nils every card = surface dark-out). _menu() already coerces the menu blob;
    this closes the same class on the flat column, which reached the wire
    verbatim and was guarded by producer discipline only (approval.py coerces
    at CLI-create, but non-CLI writers can store int n). 2026-09-10 hardening,
    ob commission msg_1e8eb9f1."""
    if not isinstance(opts, list):
        return opts
    out = []
    for o in opts:
        if isinstance(o, dict) and o.get("n") is not None and not isinstance(o["n"], str):
            o = {**o, "n": str(o["n"])}
        out.append(o)
    return out


async def handle_pending(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    store = ApprovalStore(); store.migrate()
    rows = store.pending_to_notify()
    out = [{"id": r["id"], "from_agent": r["from_agent"], "question": r["question"],
            "options": _options_n_str(
                json.loads(r["options"]) if isinstance(r["options"], str) else r["options"]),
            "created_at": r["created_at"],
            "summary": r.get("summary"), "risk_level": r.get("risk_level"),
            "reversibility": r.get("reversibility"), "feature": r.get("feature"),
            "kind": r.get("kind"), "evidence": _evidence(r),
            "menu": _menu(r),
            # §6.1 human_task feed export (additive via .get -> legacy/unarmed
            # rows emit None; old clients ignore the unknown keys). Without these
            # the client can render neither the block_task text, the blocks_what
            # subtext/deep-link, nor the snoozed state.
            "block_task": r.get("block_task"), "blocks_what": r.get("blocks_what"),
            "snoozed_until": r.get("snoozed_until"),
            "priority_score": _priority_score(r)} for r in rows]
    # §4.1: pending questionnaires ride the SAME queue as additive pseudo-rows
    # (kind='questionnaire'). Old clients ignore the unknown kind.
    try:
        qstore = QuestionnaireStore(); qstore.migrate()
        out.extend(_qnr_pseudo_row(q) for q in qstore.list_pending())
    except Exception as e:  # noqa: BLE001 — never let questionnaires break the approvals feed
        print(f"[watch_gateway] questionnaire pseudo-row merge failed: {e}", file=sys.stderr)
    # D3: live native permission prompts ride the SAME queue as additive pseudo-
    # rows (kind='permission'), answered via /agent-key. _perm_pseudo_rows is
    # itself fail-open; this extra guard is defense-in-depth.
    try:
        out.extend(_perm_pseudo_rows())
    except Exception as e:  # noqa: BLE001 — never let the permission scan break the feed
        print(f"[watch_gateway] permission pseudo-row merge failed: {e}", file=sys.stderr)
    # §1.1(b) condition 3 — CAPABILITY-GATED FAIL-SAFE via the single chokepoint
    # (gate_menu_rows_for_client): a client that can't hydrate a multi-part card
    # (no 'hydrates-multipart' capability, incl. an ABSENT header = default SAFE)
    # NEVER receives the raw un-hydrated multi-part card as submittable — it is
    # rewritten to a read-only echo. A capable client gets it raw + hydrates via
    # /agent-menu-capture. This makes store-but-mark safe WITHOUT an atomic
    # server/client deploy. The detail endpoint serves through the SAME seam.
    out = gate_menu_rows_for_client(out, request)
    # R8 (§3.1): the queue is PRIORITY-SORTED server-side, ties -> oldest first.
    # The ONE ordering brain — the app renders server order (triageRank retired).
    out.sort(key=lambda r: (-r["priority_score"], r["created_at"]))
    return _json({"ok": True, "pending": out})


def _gateway_answer_tags(body):
    """R7d spoof guard for the AUTHENTICATED bearer edge (handle_answer).

    The gateway's bearer token authenticates the operator, so the principal is set
    SERVER-SIDE and a client-claimed `answered_by` in the request body is
    IGNORED (an agent must not be able to POST answered_by='operator'). `surface`
    is provenance-only, but a client on THIS (watch bearer) edge must not be
    able to forge a DIFFERENT authenticated edge, so it is constrained to
    watch|phone (the app declares which); anything else falls back to 'watch'.

    Returns (surface, answered_by). Never trust the body for the principal —
    surface is audit-only and NEVER gates an authz decision (§ trust boundary)."""
    surface = (body or {}).get("surface")
    if surface not in ("watch", "phone"):
        surface = "watch"
    return surface, "operator"


def apply_answer(store, rid, answer, *, text=None, option_n=None,
                 answer_text=None, fire=True, surface=None, answered_by=None):
    """The ONE answer state machine, shared by EVERY transport (SPEC
    all-model-parity §5: validation lives in one place, consumed not
    re-implemented). `handle_answer` (the gateway /approval-answers HTTP
    handler) parses the request then delegates here; `approval.py answer`
    (which the rewritten web API shells to — the §4 single-service boundary)
    calls it directly. This is what makes "one state machine, two transports"
    literally true: the menu / option / free-text / qnr_ / perm: validation and
    the record_answer -> fire_resume ledger transition exist in exactly one
    function, so a Claude-created and a Gemini-created row resolve identically
    regardless of which surface answers them.

    `store` is supplied by the caller (the gateway builds the live
    ApprovalStore; the CLI/tests build a scratch one via APPROVAL_DB_PATH) —
    apply_answer NEVER self-selects the DB, preserving the single-writer service
    boundary.

    Returns a transport-neutral dict {ok, status, applied?, error?, id,
    resume_row?}. `status` is the HTTP status the gateway maps back 1:1
    (byte-equivalent to the pre-refactor inline handler). When `fire` is True
    (the CLI/web path) the resume fires synchronously; the async gateway passes
    fire=False and schedules approval_resume.fire_resume in its executor from
    `resume_row`, so it never blocks the event loop as before.
    """
    rid = str(rid or "").strip()
    answer = str(answer or "").strip()
    text = str(text).strip() if text else None
    if not rid or not answer:
        return {"ok": False, "status": 400, "error": "id and answer required", "id": rid}
    # §3.2 legacy-answer guard: questionnaire pseudo-rows answer via their own
    # endpoints (options-are-the-actions generalized to the container).
    if rid.startswith("qnr_"):
        return {"ok": False, "status": 400,
                "error": "answer via /questionnaires/:id", "id": rid}
    # §D3 transport-unambiguity guard: permission pseudo-rows are LIVE-PANE prompts
    # answered via /agent-key (send-keys the digit), NOT the ledger resume path.
    # A future dev must not be able to wire one to /answer by accident (mirrors
    # the qnr_ guard above). The 'perm:' id prefix is the discriminator.
    if rid.startswith("perm:"):
        return {"ok": False, "status": 400,
                "error": "permission prompts answer via /agent-key (live pane)",
                "id": rid}

    # §6 human_task EARLY intercept (SERVER spec): a human_task row's three verbs
    # (done|cant|snooze) never touch the generic option/menu/record_answer path.
    # done/cant record + fire the house-style resume digest; snooze stamps
    # snoozed_until (status stays 'pending', NO resume). Symmetric guard below:
    # these three verbs are human_task-ONLY — on a normal row they'd otherwise
    # fall through to record_answer as a bogus answer.
    _r0 = store.get(rid)
    if _r0 is not None and _r0.get("kind") == "human_task":
        if answer not in ("done", "cant", "snooze"):
            return {"ok": False, "status": 400,
                    "error": "human_task verbs are done|cant|snooze", "id": rid}
        if answer == "snooze":
            store.snooze(rid)
            return {"ok": True, "status": 200, "applied": False, "snoozed": True, "id": rid}
        applied = store.record_answer(rid, answer, text, surface=surface, answered_by=answered_by)
        if applied and fire:
            try:
                approval_resume.fire_resume(store.get(rid), store)
            except Exception as e:  # noqa: BLE001 — answer durable even if resume hiccups
                print(f"[watch_gateway] human_task resume failed for {rid}: {e}", file=sys.stderr)
        return {"ok": True, "status": 200, "applied": applied, "id": rid}
    if _r0 is not None and answer in ("done", "cant", "snooze"):
        return {"ok": False, "status": 400,
                "error": "done|cant|snooze are human_task-only verbs", "id": rid}

    # §1a option answers ({answer:"option", option_n, answer_text?}) validate
    # against the row's captured menu BEFORE any state changes.
    resolved_option_n = None
    if answer == "option":
        resolved_option_n = option_n
        at = str(answer_text).strip() if answer_text is not None else None
        row = store.get(rid)
        if resolved_option_n is None:
            # R7: option-less is a FREE-FORM answer — only valid with text, and
            # only on a menu carrying a free_text-class option (the escape it
            # rides through the three-phase resume). Resolve option_n to it.
            if not at:
                return {"ok": False, "status": 400,
                        "error": "option_n or answer_text required", "id": rid}
            if row is not None:
                if row.get("kind") != "menu":
                    return {"ok": False, "status": 400,
                            "error": "answer 'option' is only valid on menu rows",
                            "id": rid}
                resolved_option_n = _free_text_option_n(row)
                if resolved_option_n is None:
                    return {"ok": False, "status": 400,
                            "error": "this menu has no free-text option to "
                                     "answer in your own words", "id": rid}
            # unknown id (row is None): option_n stays None -> record_answer no-ops
        if row is not None and resolved_option_n is not None:
            err = _validate_option_answer(row, resolved_option_n, at)
            if err:
                return {"ok": False, "status": 400, "error": err, "id": rid}
        # unknown id falls through: record_answer no-ops -> clean applied=False
        text = at or None
    else:
        # §2: on a menu row the options ARE the actions — legacy answers
        # (approve/deny/defer, e.g. a stale notification quick-action) must
        # not bind to it.
        row = store.get(rid)
        if row is not None and row.get("kind") == "menu":
            return {"ok": False, "status": 400,
                    "error": "menu rows answer via answer=='option' only", "id": rid}

    # R7d: the edge's provenance tag rides through to the ONE core. surface/
    # answered_by are provenance-only (audit/forensics) — record_answer never
    # branches on them; a missing tag defaults to surface='unknown' (back-compat).
    applied = store.record_answer(rid, answer, text, option_n=resolved_option_n,
                                  surface=surface, answered_by=answered_by)  # id-bound + answer-once
    resume_row = None
    if applied:
        row = store.get(rid)
        # R7b telemetry: the submit-tap (answer recorded). Best-effort, never
        # breaks the answer path — the delivery-confirmation lines are emitted
        # from approval_resume's per-lane outcomes.
        try:
            import answer_telemetry
            answer_telemetry.log_submit(row)
        except Exception as e:  # noqa: BLE001
            print(f"[watch_gateway] answer_telemetry submit failed for {rid}: {e}",
                  file=sys.stderr)
        # Completion semantics (§3.4): accept = archive (no parked agent to
        # resume — self-ack so the watchdog never nags); respond = deliver the
        # follow-up to the agent through the normal resume path.
        if (row.get("kind") == "completion") and answer in ("accept", "approve"):
            store.ack(rid)
            # G3 (DEC-1787032722): the accept must still close the loop toward
            # from_agent — held-row notify (shadow-logs unless gm's flag is on).
            # Best-effort: a notify hiccup never breaks the answer.
            try:
                g3_accept_notify(
                    row, enabled=os.environ.get("G3_ACCEPT_NOTIFY_ENABLED") == "1")
            except Exception as e:  # noqa: BLE001
                print(f"[watch_gateway] g3_accept_notify failed for {rid}: {e}",
                      file=sys.stderr)
        elif row.get("kind") == "proposal":
            # R7a §2.2: a proposal shadow row has no live pane to resume — the
            # decision drives the learning.db lifecycle (approve -> rules-insert
            # -> applied; deny -> rejected) through the ONE canonical answer,
            # then SELF-ACKS so the watchdog never nags. The handler is
            # idempotent on the answer side (status-guarded transition), so a
            # racing double-answer applies the rule exactly once. Best-effort:
            # a learning.db hiccup never breaks the durable answer.
            try:
                import proposal_shadow
                proposal_shadow.apply_proposal_decision(row, answer, text=text)
            except Exception as e:  # noqa: BLE001
                print(f"[watch_gateway] proposal decision failed for {rid}: {e}",
                      file=sys.stderr)
            store.ack(rid)
        else:
            resume_row = row
            if fire:
                try:
                    approval_resume.fire_resume(row, store)
                except Exception as e:  # noqa: BLE001 — answer is durable even if resume hiccups
                    print(f"[watch_gateway] fire_resume failed for {rid}: {e}",
                          file=sys.stderr)
    # applied=False -> unknown/already-answered id: report cleanly, not an error.
    return {"ok": True, "status": 200, "applied": applied, "id": rid,
            "resume_row": resume_row}


async def handle_answer(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)

    store = ApprovalStore(); store.migrate()
    # R7d: this is the authenticated bearer edge — derive the provenance tags
    # SERVER-SIDE (principal='operator' from the bearer, surface=watch|phone as the
    # app declares). A client-claimed answered_by is ignored (spoof guard).
    surface, answered_by = _gateway_answer_tags(data)
    # Delegate the validation + transition to the ONE shared core; fire=False so
    # the resume runs in the executor below (the menu free-text resume sleeps
    # between phases — must not block the event loop, byte-equivalent to before).
    result = apply_answer(store, data.get("id", ""), data.get("answer", ""),
                          text=data.get("text"), option_n=data.get("option_n"),
                          answer_text=data.get("answer_text"), fire=False,
                          surface=surface, answered_by=answered_by)
    if not result["ok"]:
        return _json({"ok": False, "error": result["error"]}, status=result["status"])
    resume_row = result.get("resume_row")
    if resume_row is not None:
        try:
            import asyncio
            await asyncio.get_event_loop().run_in_executor(
                None, approval_resume.fire_resume, resume_row, store)
        except Exception as e:  # noqa: BLE001 — answer is durable even if resume hiccups
            print(f"[watch_gateway] fire_resume failed for {result['id']}: {e}", file=sys.stderr)
    return _json({"ok": True, "applied": result["applied"], "id": result["id"],
                  "snoozed": result.get("snoozed")})


async def handle_history(request):
    """Answered decisions, newest-first (for the iOS history surface). Additive,
    read-only; leaves the pending/answer/resume flow untouched. Optional ?limit=
    (default 50, capped 200)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        limit = max(1, min(200, int(request.query.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    store = ApprovalStore(); store.migrate()
    rows = store.history(limit)
    out = [{"id": r["id"], "from_agent": r["from_agent"], "question": r["question"],
            "options": json.loads(r["options"]) if isinstance(r["options"], str) else r["options"],
            "created_at": r["created_at"], "answered_at": r.get("answered_at"),
            "answer": r.get("answer"), "answer_text": r.get("answer_text"),
            "status": r.get("status"),
            "summary": r.get("summary"), "risk_level": r.get("risk_level"),
            "reversibility": r.get("reversibility"), "feature": r.get("feature"),
            "kind": r.get("kind"), "evidence": _evidence(r),
            # §6.1 human_task feed export (additive; None on legacy/unarmed rows).
            "block_task": r.get("block_task"), "blocks_what": r.get("blocks_what"),
            "snoozed_until": r.get("snoozed_until")} for r in rows]
    # R8 everything-to-history: submitted/discarded questionnaires appear as
    # pseudo-rows (kind='questionnaire') — nothing vanishes invisibly. Old
    # clients ignore the unknown kind (same additive discipline as /pending).
    try:
        qstore = QuestionnaireStore(); qstore.migrate()
        for q in qstore.history(limit):
            out.append({
                "id": q["id"], "kind": "questionnaire", "from_agent": q["from_agent"],
                "question": q["title"], "options": [], "created_at": q["created_at"],
                "answered_at": q.get("submitted_at") or q.get("discarded_at"),
                "answer": None, "answer_text": None, "status": q["status"],
                "summary": q.get("summary"), "feature": q.get("feature"),
                "questionnaire": {"question_count": q["question_count"],
                                  "answered_count": q.get("answered_count", 0),
                                  "draft_rev": q["draft_rev"]},
            })
        out.sort(key=lambda r: r.get("answered_at") or "", reverse=True)
        out = out[:limit]
    except Exception as e:  # noqa: BLE001 — never let questionnaires break the history feed
        print(f"[watch_gateway] questionnaire history merge failed: {e}", file=sys.stderr)
    return _json({"ok": True, "history": out})


async def handle_approval_detail(request):
    """One full ledger row, any status — the iOS detail screen (pending or past)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    rid = request.match_info.get("id", "")
    store = ApprovalStore(); store.migrate()
    row = store.get(rid)
    if not row:
        return _json({"ok": False, "error": "not found"}, status=404)
    if isinstance(row.get("options"), str):
        try: row["options"] = json.loads(row["options"])
        except ValueError: pass
    row["evidence"] = _evidence(row)
    row["menu"] = _menu(row)
    # §1.1(b) condition 3 — CAPABILITY-GATED FAIL-SAFE through the SAME chokepoint
    # as the list (gate_menu_rows_for_client). Without it, a non-capable client's
    # detail screen (GET /approvals/{id}) renders a raw un-hydrated multi-part
    # part-0 card as submittable — a flat part-0 answer to a 3-part question (the
    # wrong-answer class this lane kills; caught live on the operator's build-154,
    # 2026-08-26). Single-row -> pass [row], take [0]. Answered/past + single-part +
    # already-hydrated rows are no-ops.
    row = gate_menu_rows_for_client([row], request)[0]
    return _json({"ok": True, "approval": row})


# ---------------------------------------------------------------------------
# P1 fleet/business read endpoints (additive, read-only)
# ---------------------------------------------------------------------------

ORCH_DIR = Path(os.environ.get("ORCH_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
_agents_cache = {"at": 0.0, "data": None, "refreshing": False, "reg_mtime": None, "event_mtime": None}
AGENTS_CACHE_S = 15.0   # ANSI-parse of the whole fleet takes ~6s; serve stale + refresh in bg


def _load_agent_status_mod():
    """Import scripts/agent-status.py (dash in name -> importlib). This is the
    REAL detector built by the orchestra-builder/Full-Audit line: it parses the
    Claude Code TUI's ANSI codes + checks for a live claude process on the pane
    tty. tmux session_activity is NOT a valid liveness signal (UI redraws)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent_status", str(ORCH_DIR / "scripts" / "agent-status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_agent_status_mod = None

def _agent_status():
    global _agent_status_mod
    if _agent_status_mod is None:
        _agent_status_mod = _load_agent_status_mod()
    return _agent_status_mod


def _tmux_session_names():
    import subprocess
    try:
        out = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                             capture_output=True, text=True, timeout=5).stdout
        return [s for s in out.splitlines() if s]
    except Exception:
        return []


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


# Detector state -> app state. waiting_permission is surfaced as its own
# "waiting" state (it needs the operator); thinking folds into working. v2 detector
# (agent-state-truth) adds stranded_input + stalled — passed through.
_STATE_MAP = {"working": "working", "thinking": "working", "waiting_permission": "waiting",
              "idle": "idle", "stopped": "stopped", "unknown": "idle",
              "stranded_input": "stranded", "stalled": "stalled"}


# Voice-call annotation (§7.3): Arturo voice summaries + end-call transcripts route to canonical
# Claude gm (the operator decision 2026-08-25, single-gm; revert of the 08-21 gemini-gm reroute). The deep-brain
# / Live-engine host (gemini-orchestra-dev) is a SEPARATE concern (Tier-B, held) — this is delivery target only.
VOICE_BRAIN_SESSION = os.environ.get("VOICE_BRAIN_SESSION", "gm")
VOICE_STALE_AGE_S = 4 * 3600      # "live" older than this ...
VOICE_STALE_MTIME_S = 600         # ... with no file movement this long = stale


def _select_newest_live(entries, now, stale_age_s=VOICE_STALE_AGE_S, stale_mtime_s=VOICE_STALE_MTIME_S):
    """Pure selector (no IO): from `entries` = list of (call_dict, mtime), return the call_dict of
    the newest (max-mtime = most-recently-appended) status=='live' call, or None. Determinism when
    >1 is live: the most-recently-appended wins. Skips a crash-leftover stale file (a 'live' file
    started > stale_age_s ago whose mtime froze > stale_mtime_s ago is a proxy-crash remnant, not a
    call). Shared by _live_voice_call (annotation) and handle_active_voice_call (BUG1 client poll)."""
    for c, mtime in sorted(entries, key=lambda cm: cm[1], reverse=True):
        if not isinstance(c, dict) or c.get("status") != "live":
            continue
        started = c.get("started_at") or mtime
        if now - started > stale_age_s and now - mtime > stale_mtime_s:
            continue
        return c
    return None


def _live_voice_call():
    """Newest status=="live" call JSON -> {call_id, started_at} | None.
    ANNOTATION only — never a detector state (injection gates key on state).
    Stale guard: a live file older than 4h whose mtime froze >10m is a proxy
    crash leftover, not a call; skip it (primary fix = proxy drop-watchdog)."""
    import time as _time
    try:
        files = sorted((ORCH_DIR / "state" / "voice-calls").glob("vc_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    entries = []
    for p in files[:10]:
        c = _load_json(p)
        if isinstance(c, dict) and not c.get("call_id"):
            c = {**c, "call_id": p.stem}      # preserve the original stem fallback
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        entries.append((c, mtime))
    c = _select_newest_live(entries, _time.time())
    if c is None:
        return None
    return {"call_id": c.get("call_id"), "started_at": c.get("started_at")}


def _resolve_sess_meta(reg_agents):
    """Map tmux_session -> (agent_name, meta), resolving collisions in favor of the
    ALIVE agent (gm identity incident 2026-08-16). Two registry records can share a
    session name when a rotation retires a predecessor but leaves its tmux_session
    pointing at the reused canonical name (e.g. 'gm'). Plain last-writer-wins could
    then label the LIVE pane with the RETIRED corpse's identity/generation. Rule: a
    non-retired record always wins the key over a retired one; among same-status
    records, last wins (legacy behavior). Missing tmux_session keys by agent name."""
    RETIRED = {"retired", "dead", "archived", "stopped"}
    sm = {}
    for name, meta in (reg_agents.items() if isinstance(reg_agents, dict) else []):
        if not isinstance(meta, dict):
            continue
        key = meta.get("tmux_session") or name
        incoming_retired = str(meta.get("status") or "").lower() in RETIRED
        if key in sm:
            _, cur_meta = sm[key]
            cur_retired = str(cur_meta.get("status") or "").lower() in RETIRED
            # keep the incumbent only if it's alive and the newcomer is retired
            if incoming_retired and not cur_retired:
                continue
        sm[key] = (name, meta)
    return sm


def compute_agents():
    """Fleet liveness: the agent-status.py ANSI/process detector per live tmux
    session, merged with registry.json metadata (tier/machine/model/lineage)."""
    ast = _agent_status()
    voice = _live_voice_call()
    reg = _load_json(ORCH_DIR / "registry.json") or {}
    reg_agents = reg.get("agents", {}) if isinstance(reg, dict) else {}
    sess_meta = _resolve_sess_meta(reg_agents)
    sess_store = _load_json(ORCH_DIR / "state" / "agent-sessions.json") or {}

    def _generation_for(name, meta):
        """DB-FIRST (gm msg_95e1eaf3): registry.json is the identity-store projection and
        wins; agent-sessions.json then state/agents/<id>.json only fill a MISSING value."""
        g = meta.get("generation")
        if g not in (None, ""):
            return g
        srow = sess_store.get(name) if isinstance(sess_store, dict) else None
        if isinstance(srow, dict) and srow.get("generation") not in (None, ""):
            return srow.get("generation")
        blob = _load_json(ORCH_DIR / "state" / "agents" / f"{name}.json")
        if isinstance(blob, dict) and blob.get("generation") not in (None, ""):
            return blob.get("generation")
        return g

    out, seen = [], set()
    for sess in _tmux_session_names():
        seen.add(sess)
        st = ast.get_agent_status(sess)
        name, meta = sess_meta.get(sess, (sess, {}))
        ctx = (st.get("context_pct") or "").rstrip("%")
        out.append({
            "id": name, "tier": meta.get("tier"), "machine": meta.get("machine") or "vps",
            "model": st.get("model") or meta.get("model"),
            "generation": _generation_for(name, meta),
            "state": _STATE_MAP.get(st.get("state"), "idle"),
            "activity": st.get("activity") or "",
            "tool": st.get("tool") or "",
            "context_pct": int(ctx) if ctx.isdigit() else None,
            "cpu": (st.get("process") or {}).get("cpu"),
            "tmux_session": sess,
            "unregistered": sess not in sess_meta,
            # v2 detector additive fields (agent-state-truth 2026-08-09)
            "confidence": st.get("confidence"),
            "state_age_s": st.get("state_age_s"),
            "stranded_age_s": (st.get("stranded") or {}).get("age_s"),
            "stranded_text": (st.get("stranded") or {}).get("text"),
            # §7.3: additive annotation, only ever on the voice-brain row
            "voice_call": voice if sess == VOICE_BRAIN_SESSION else None,
            # §2: cheap badge signal — True when detector emits pending_menu
            **({"has_pending_menu": True} if st.get("pending_menu") else {}),
        })

    # Registered agents with NO tmux session: "crashed" (red) ONLY if the agent
    # last self-reported as alive — always_on relics whose own state blob says
    # stopped/retired are long-dead registry leftovers, not fresh crashes
    # (GM triage 2026-08-09: kai-gm etc. dead since April). Those read offline.
    for sess, (name, meta) in sess_meta.items():
        if sess in seen:
            continue
        self_status = ""
        blob = _load_json(ORCH_DIR / "state" / "agents" / f"{name}.json")
        if isinstance(blob, dict):
            self_status = str(blob.get("status") or "")
        relic = self_status in ("stopped", "retired", "dead", "archived")
        crashed = bool(meta.get("always_on")) and not relic
        out.append({
            "id": name, "tier": meta.get("tier"), "machine": meta.get("machine"),
            "model": meta.get("model"), "generation": _generation_for(name, meta),
            "state": "crashed" if crashed else "offline",
            "activity": "No tmux session" if crashed else
                        (f"Not running (self-reported {self_status})" if relic else "Not running"),
            "tool": "", "context_pct": None,
            "cpu": None, "tmux_session": sess, "unregistered": False,
        })

    # Recency (the operator 2026-08-09: agents ordered by most recently used): the
    # pane's hook-event file carries the ts of the last hook event.
    get_pane = getattr(ast, "get_pane_id", None)
    for a in out:
        ts = 0.0
        if a["state"] not in ("offline", "crashed") and callable(get_pane):
            try:
                pane = (get_pane(a["tmux_session"]) or "").lstrip("%")
                if pane:
                    ev = _load_json(ORCH_DIR / "state" / "agent-events" / "panes" / f"{pane}.json")
                    if isinstance(ev, dict):
                        ts = float(ev.get("ts") or 0)
            except Exception:
                pass
        a["last_used_ts"] = ts

    rank = {"waiting": 0, "stranded": 1, "stalled": 2, "crashed": 3, "stopped": 4,
            "working": 5, "idle": 6, "offline": 7}
    out.sort(key=lambda a: (rank.get(a["state"], 9), -a.get("last_used_ts", 0), a["id"]))
    return out


def _registry_mtime():
    """Cheap stat of registry.json (0.0 on any error). A promotion/rotation
    rewrites this file; the /agents cache keys revalidation on it so a dead
    generation's fingerprint can't be served after a successor takes the name
    (the gm gen-9->10 incident 2026-08-16; PM-driver design §6.6 item 4)."""
    try:
        return (ORCH_DIR / "registry.json").stat().st_mtime
    except OSError:
        return 0.0


def _newest_event_mtime():
    """Cheap max-mtime of the Tier-0 status-event files
    (state/agent-events/panes/<pane>.json, written by state-event-hook.py on
    UserPromptSubmit->working / Stop->idle). The /agents cache keys revalidation
    on it so a status flip surfaces on the very next poll instead of waiting out
    AGENTS_CACHE_S — the message-send / agent-stop immediacy fix. 0.0 on any
    error (dir absent -> falls back to the TTL, no behaviour change)."""
    try:
        newest = 0.0
        d = ORCH_DIR / "state" / "agent-events" / "panes"
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(".json"):
                    m = e.stat().st_mtime
                    if m > newest:
                        newest = m
        return newest
    except OSError:
        return 0.0


def _refresh_agents_cache():
    import time as _time
    # Snapshot registry mtime BEFORE the ~6s scan so a rotation landing DURING a
    # refresh isn't masked (the stamped mtime is <= the data it produced; a later
    # write bumps it and re-triggers). compute_agents reads registry.json itself.
    reg_mtime = _registry_mtime()
    # Snapshot the newest status-event mtime BEFORE the scan (same reasoning as
    # reg_mtime): an event landing DURING the refresh bumps it and re-triggers,
    # so a status flip is never masked by an in-flight scan.
    event_mtime = _newest_event_mtime()
    try:
        data = compute_agents()
        _agents_cache["data"] = data
        _agents_cache["at"] = _time.time()
        _agents_cache["reg_mtime"] = reg_mtime
        _agents_cache["event_mtime"] = event_mtime
    finally:
        _agents_cache["refreshing"] = False


async def handle_agents(request):
    """Stale-while-revalidate: never block a request on the ~6s fleet scan.
    Revalidation fires on the TTL OR when registry.json mtime changes (a
    promotion/rotation), so a name-reuse is visible within a poll — never a dead
    generation's row (gm gen-9->10 stale-cache incident)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    import asyncio, time as _time
    stale = (_agents_cache["data"] is None
             or _time.time() - _agents_cache["at"] > AGENTS_CACHE_S
             or _agents_cache["reg_mtime"] != _registry_mtime()
             or _agents_cache.get("event_mtime") != _newest_event_mtime())
    if stale and not _agents_cache["refreshing"]:
        _agents_cache["refreshing"] = True
        if _agents_cache["data"] is None:
            # First call ever: compute synchronously (off the event loop).
            await asyncio.get_event_loop().run_in_executor(None, _refresh_agents_cache)
        else:
            asyncio.get_event_loop().run_in_executor(None, _refresh_agents_cache)
    return _json({"ok": True, "agents": _agents_cache["data"] or [],
                  "as_of": _agents_cache["at"]})


def _iter_clients():
    for p in sorted((ORCH_DIR / "state" / "clients").glob("*/client.json")):
        c = _load_json(p)
        if isinstance(c, dict):
            c.setdefault("slug", p.parent.name)   # some client.json lack slug
            if not c["slug"]:
                c["slug"] = p.parent.name
            yield c


def _project_links(c):
    links = []
    for u in (c.get("deployed_urls") or []):
        if isinstance(u, dict) and u.get("url"):
            links.append({"label": u.get("label") or "Live", "url": u["url"], "kind": "live"})
    for s in (c.get("netlify_sites") or []):
        if isinstance(s, str):
            links.append({"label": s, "url": f"https://{s}.netlify.app", "kind": "live"})
    return links[:6]   # keep cards sane


async def handle_projects(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    projects = []
    for c in _iter_clients():
        projects.append({
            "slug": c.get("slug"), "name": c.get("name") or c.get("slug"),
            "status": c.get("status"), "pm_agent": c.get("pm_agent"),
            "meta": (c.get("service_scope") or c.get("industry") or ""),
            "links": _project_links(c),
        })
    # actives first, then prospects, then the rest
    order = {"active": 0, "pilot": 1, "prospect": 2}
    projects.sort(key=lambda p: (order.get(p["status"], 3), p["slug"] or ""))
    return _json({"ok": True, "projects": projects})


# ---------------------------------------------------------------------------
# CRM People passthrough (crm-pipeline-split spec §4.4) — iOS keeps its single
# bearer origin; the gateway proxies the Node API's /api/people CRM routes.
# ---------------------------------------------------------------------------

API_ORIGIN = "http://127.0.0.1:8891"
_PERSON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_PEOPLE_QUERY_KEYS = ("relationship", "project", "search", "sort", "company_slug")


async def _api_get(path):
    """GET the local Node API. Returns (status, parsed_json). Raises on transport."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(API_ORIGIN + path) as r:
            return r.status, await r.json(content_type=None)


async def handle_people(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    from urllib.parse import urlencode
    q = {k: request.query[k] for k in _PEOPLE_QUERY_KEYS if request.query.get(k)}
    path = "/api/people?" + (urlencode(q) if q else "search=")
    try:
        status, body = await _api_get(path)
    except Exception:
        return _json({"ok": False, "error": "crm api unreachable"}, status=503)
    if status != 200 or not isinstance(body, dict):
        return _json({"ok": False, "error": f"crm api {status}"}, status=502)
    return _json({"ok": True, "people": body.get("people") or [],
                  "total": body.get("total", 0)})


async def handle_person_detail(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    pid = request.match_info.get("person_id") or ""
    if not _PERSON_ID_RE.match(pid):
        return _json({"ok": False, "error": "bad person id"}, status=400)
    try:
        status, body = await _api_get(f"/api/people/{pid}")
    except Exception:
        return _json({"ok": False, "error": "crm api unreachable"}, status=503)
    if status == 404:
        return _json({"ok": False, "error": "not found"}, status=404)
    if status != 200 or not isinstance(body, dict):
        return _json({"ok": False, "error": f"crm api {status}"}, status=502)
    out = {"ok": True}
    out.update(body)
    return _json(out)


def _deal_value(deal):
    """Best single number for a deal card: monthly if set, else upfront. None -> 0."""
    if not isinstance(deal, dict):
        return 0
    for k in ("monthly", "upfront", "pixel"):
        v = deal.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return 0


async def handle_pipeline(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    stages = {"active": [], "pilot": [], "prospect": []}
    for c in _iter_clients():
        st = c.get("status")
        if st not in stages:
            continue
        val = _deal_value(c.get("deal"))
        stages[st].append({
            "client": c.get("slug"), "name": c.get("name") or c.get("slug"),
            "sub": (c.get("deal") or {}).get("notes") or c.get("service_scope") or "",
            "value": val,
        })
    def block(name, key):
        deals = sorted(stages[key], key=lambda d: -d["value"])
        return {"name": name, "amount": sum(d["value"] for d in deals), "deals": deals}
    out = [block("Active", "active"), block("Pilot", "pilot"), block("Prospect", "prospect")]
    return _json({"ok": True, "total": sum(b["amount"] for b in out), "stages": out})


async def handle_voice_memory(request):
    """GET /voice-memory — Arturo's existing call record (READ-ONLY, additive).
    Serves state/voice-memory.json {recent_transcripts, notes} so the iOS
    Arturo tab opens with the 491-turn relationship history, never day-zero
    (gm correction 2026-08-16; one-identity ruling)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        p = ORCH_DIR / "state" / "voice-memory.json"
        d = json.loads(p.read_text()) if p.exists() else {}
        return _json({"ok": True,
                      "recent_transcripts": d.get("recent_transcripts") or [],
                      "notes": d.get("notes") or []})
    except Exception as e:  # noqa: BLE001 — history is enrichment, never a 500
        print(f"[watch_gateway] voice-memory read failed: {e}", file=sys.stderr)
        return _json({"ok": True, "recent_transcripts": [], "notes": []})


async def handle_briefing(request):
    """Deltas for the Arturo 'while you were away' card. ?since=<ISO ts> optional."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    since = request.query.get("since") or ""
    store = ApprovalStore(); store.migrate()
    pending = store.pending_to_notify()
    answered = store.history(50)
    if since:
        answered = [r for r in answered if (r.get("answered_at") or "") > since]
    # Use the agents cache (the fleet scan takes ~6s; never block the briefing).
    agents = _agents_cache["data"] or []
    crashed = [a["id"] for a in agents if a["state"] in ("crashed", "stopped")]
    working = sum(1 for a in agents if a["state"] == "working")
    stranded = [{"id": a["id"], "age_s": a.get("stranded_age_s"),
                 "text": (a.get("stranded_text") or "")[:80]}
                for a in agents if a["state"] == "stranded"]
    stalled = [a["id"] for a in agents if a["state"] == "stalled"]
    return _json({"ok": True,
                  "pending": len(pending),
                  "answered_since": [{"id": r["id"], "from_agent": r["from_agent"],
                                      "question": r["question"], "answer": r.get("answer"),
                                      "answered_at": r.get("answered_at")} for r in answered[:10]],
                  "agents_working": working, "agents_crashed": crashed[:10],
                  "agents_stranded": stranded[:10], "agents_stalled": stalled[:10]})


# ---------------------------------------------------------------------------
# P2: verified-injection chat engine (the operator-approved interim path 2026-08-09 —
# the old message-router was slow/brittle/duplicative; this path must be
# (a) verified-submit, (b) single-delivery, (c) never interrupt a mid-turn
# agent). Arturo == the "gm" session; per-agent chat targets any agent session.
# The detector-state gate (only inject when the pane is a Claude prompt at
# "idle"/"waiting") inherently protects service panes (python procs -> stopped).
# ---------------------------------------------------------------------------

INJECT_INGEST_WAIT_S = float(os.environ.get("WATCH_GATEWAY_INGEST_WAIT", "1.0"))
INJECTABLE_STATES = {"idle"}          # never mid-turn (working/thinking), never stopped


def _tmux(*args, timeout=5):
    import subprocess
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)


def _capture_pane(session, lines=60, ansi=False):
    """lines>0 includes scrollback (content reads); lines=0 = visible screen
    ONLY — required for any liveness/guard check, because old spinner frames
    ('esc to interrupt') persist in scrollback after a turn ends."""
    args = ["capture-pane", "-t", session, "-p"]
    if lines > 0:
        args += ["-S", f"-{lines}"]
    if ansi:
        args.insert(3, "-e")
    r = _tmux(*args)
    return r.stdout if r.returncode == 0 else None


def _strip_ansi(text):
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text or "")


# ---------------------------------------------------------------------------
# Per-session send mutex (ghost-suggestion spec §3b.6 / F3). Serializes the
# gateway's own keystroke bursts to a given pane so /agent-suggest's two-phase
# Tab->Enter can't interleave with /agent-message or /agent-key. Cross-process
# writers (the message-router cron, the idle-stall-watchdog nudge) are OUT of
# scope by design — the /agent-suggest phase-B re-capture verify catches a
# pre-Enter interleave from them; the rest is accepted residual (spec §3b.6).
# ---------------------------------------------------------------------------
import threading as _threading

_SEND_LOCKS_GUARD = _threading.Lock()
_SEND_LOCKS: dict = {}


def _session_send_lock(session):
    """Return the per-session send lock, creating it once (thread-safe)."""
    with _SEND_LOCKS_GUARD:
        lk = _SEND_LOCKS.get(session)
        if lk is None:
            lk = _SEND_LOCKS[session] = _threading.Lock()
        return lk


# Session-killing slash-commands that must NEVER ride a message inject into an
# agent's TUI (2026-08-11 incident: a bare "/exit" reached gm's composer —
# likely a stale app-queue flush — and gracefully killed the session mid-turn).
# the operator ruling (same night): BLOCKLIST, not a blanket slash guard — /compact,
# /clear, /model etc. are legitimate operator actions from chat; only the
# session-enders are refused. /quit is /exit's alias — same kill, same block.
_SLASH_CMD_RE = re.compile(r"^\s*/(exit|quit)\b", re.IGNORECASE)


# --- chip-dodge (task #16, gm msg_bcd00a9d / the operator field report) --------------
# Empirical (scratch probe g8-chipprobe-43854, 2026-08-18, ~212-col pane):
# single-line send-keys -l literal at 800 chars, CHIPS at 900+; 800-char
# 8-line payload CHIPS; tiny multiline literal. Chip onset is WIDTH-DEPENDENT
# CLI behavior, so the dodge triggers conservatively below the observed onset.
# Over-threshold payloads become: full body -> /tmp pointer file + a ONE-LINE
# banner (the digest-gate pattern) — mechanism, not sender memory.
CHIP_DODGE_MAX_CHARS = 600
CHIP_DODGE_MAX_LINES = 3          # payloads with >2 newlines dodge
CHIP_DODGE_TMP_DIR = os.environ.get("CHIP_DODGE_TMP_DIR", "/tmp")


def chipsafe_text(text, tmp_dir=None):
    """Returns (send_text, pointer_path|None). Under-threshold text passes
    through byte-identical; over-threshold text is written whole to a pointer
    file and replaced by a one-line banner telling the recipient where to
    read. Fail-open: if the pointer write fails, the original text is sent
    (chip risk beats silent loss)."""
    t = text or ""
    if len(t) <= CHIP_DODGE_MAX_CHARS and t.count("\n") < CHIP_DODGE_MAX_LINES:
        return t, None
    import time as _t
    path = os.path.join(tmp_dir or CHIP_DODGE_TMP_DIR,
                        f"agent-inject-{int(_t.time())}-{secrets.token_hex(4)}.md")
    try:
        with open(path, "w") as fh:
            fh.write(t)
    except OSError:
        return t, None
    head = " ".join(t.split())[:120]
    banner = (f"[LONG-MSG chip-dodge] {head}… — FULL TEXT ({len(t)} chars): "
              f"read {path}")
    return banner, path


def _is_prompt_line(line: str) -> bool:
    """True if line starts with any recognized runtime prompt char (Claude ❯, Gemini >, Codex ›)."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("❯") or s.startswith("›") or s.startswith("\u203a"):
        return True
    if s.startswith(">") and not s.startswith(">>"):
        return True
    return False


def verified_inject(session, text, force=False):
    """Inject text into an agent pane with VERIFIED submit.

    Returns (ok, info). The merge-3 failure class = Enter swallowed by the
    composer's async paste ingest -> instruction sits unsubmitted. Sequence:
      1. detector gate: only when the pane is an idle Claude prompt
      2. startup grace gate: process uptime >= 10s (ensures stdin listener attached)
      3. send text literally (-l; no key interpretation, normalized)
      4. wait for composer ingest
      5. Enter; verify the composer actually cleared (text left the input box);
         retry Enter once if still idle/unsubmitted, then report honestly.

    force=True (human override, the operator 2026-08-09): ghost suggestions in the
    composer used to be indistinguishable from typed text and always blocked.
    The v2 detector now discriminates by STYLE (ghosts render dim/SGR-2, typed
    text renders default — agent-state-truth, verified live 2026-08-09), so
    ghosts no longer block automatically. force remains the human override for
    detector-state stranded_input / edge cases. It NEVER skips the active-turn
    gate.
    """
    import time as _time
    # Session-killer guard: refuse /exit and /quit as message text (see
    # _SLASH_CMD_RE note). Other slash commands pass through per the operator's ruling.
    if _SLASH_CMD_RE.match(text or ""):
        return False, {"reason": "slash_command_refused",
                       "detail": "session-ending command (/exit, /quit) refused "
                                 "on the message path — kill sessions via "
                                 "orchestra-builder/terminal, not chat"}
    st = _agent_status().get_agent_status(session)
    state = _STATE_MAP.get(st.get("state"), st.get("state"))
    allowed = INJECTABLE_STATES | ({"stranded_input"} if force else set())
    if st.get("state") not in allowed:
        info = {"reason": "busy", "state": state,
                "activity": st.get("activity") or ""}
        if isinstance(st.get("stranded"), dict):
            info["stranded"] = st["stranded"]      # surface text + age honestly
        return False, info

    # Startup grace gate: require process uptime >= 10.0s so CLI readline/ink raw mode listeners are attached.
    uptime_fn = getattr(_agent_status(), "get_process_uptime_s", None)
    if callable(uptime_fn):
        uptime_s = uptime_fn(session)
        if uptime_s is not None and uptime_s < 10:
            return False, {"reason": "initializing", "state": state, "uptime_s": uptime_s,
                           "activity": f"Agent process is initializing ({uptime_s}s < 10s)"}

    # Belt-and-braces mid-turn guard (2026-08-09 incident: an unknown spinner
    # gerund made the detector read a mid-turn pane as idle). GLYPH-ANCHORED:
    # real spinner lines start with a spinner glyph; a quoted "esc to interrupt"
    # in agent output must not permanently block a finished agent (audit F1).
    pre_raw = _capture_pane(session, lines=0, ansi=True) or ""
    pre = _strip_ansi(pre_raw)

    # Auto-dismiss modal rating prompts (e.g. Antigravity "How's the CLI experience so far? [0] Skip")
    if any("How's the CLI experience so far" in ln or "[0] Skip" in ln for ln in pre.splitlines()):
        with _session_send_lock(session):
            _tmux("send-keys", "-t", session, "0")
            _time.sleep(0.5)
            pre_raw = _capture_pane(session, lines=0, ansi=True) or ""
            pre = _strip_ansi(pre_raw)

    if any((ln.strip()[:1] in "✻✽✶✳✢✺✹✸✷⚹·*+⣾⣽⣻⢿⡿⣟⣯⣷●▸" and ("esc to interrupt" in ln or "esc to cancel" in ln or any(c in ln for c in "⣾⣽⣻⢿⡿⣟⣯⣷")))
           or "esc to cancel" in ln
           or "esc to interrupt" in ln
           for ln in pre.splitlines()):
        return False, {"reason": "busy", "state": "working", "activity": "Active turn"}
    # NEVER paste over typed-but-unsubmitted input (standing the operator rule: he
    # sometimes types directly into agent sessions — that text is sacred).
    # Dim-styled ghost suggestions are NOT typed input and do not block (the
    # detector's composer_typed_text does the style walk when available).
    if not force:
        typed = None
        helper = getattr(_agent_status(), "composer_typed_text", None)
        if callable(helper):
            comp_raw = [ln for ln in pre_raw.splitlines()
                        if _is_prompt_line(_strip_ansi(ln))]
            typed = helper(comp_raw[-1]) if comp_raw else ""
        if typed is None:   # fallback (detector without helper): any text blocks
            pre_composers = [ln for ln in pre.splitlines() if _is_prompt_line(ln)]
            typed = (pre_composers[-1].strip().lstrip("❯>›\u203a").strip()
                     if pre_composers else "")
        if typed:
            return False, {"reason": "busy", "state": state,
                           "activity": "Composer has unsubmitted text",
                           "composer_text": typed[:120]}

    # task #16: over-threshold payloads dodge the paste-chip mechanically —
    # the pane receives a one-line banner + /tmp pointer, never a chippable body.
    # (Slash-guard above ran on the ORIGINAL text; the banner cannot be a slash.)
    text = (text or "").rstrip("\r\n")
    text, _chip_pointer = chipsafe_text(text)

    marker = text.strip().splitlines()[0][:40] if text.strip() else ""
    # Per-session send mutex (F3): serialize this keystroke burst against a
    # concurrent /agent-suggest (two-phase Tab->Enter) or /agent-key on the
    # same pane so their keystrokes can't interleave.
    with _session_send_lock(session):
        r = _tmux("send-keys", "-t", session, "-l", text)
        if r.returncode != 0:
            return False, {"reason": "send_failed", "state": state}
        _time.sleep(INJECT_INGEST_WAIT_S)

        for attempt in (1, 2):
            if attempt > 1:
                # On retry, check if still idle/unsubmitted before sending another Enter
                st_retry = _agent_status().get_agent_status(session)
                if st_retry.get("state") not in allowed:
                    break
            _tmux("send-keys", "-t", session, "Enter")
            _time.sleep(2.5 if attempt == 1 else 2.0)
            pane = _strip_ansi(_capture_pane(session, lines=0) or "")   # visible only
            # Submitted messages ALSO render as "❯ text" or "> text" in scrollback — only the
            # BOTTOM-MOST prompt line is the live composer. Submit is verified when that
            # line no longer holds the text (or a new turn visibly started).
            composer_lines = [ln for ln in pane.splitlines() if _is_prompt_line(ln)]
            last_composer = composer_lines[-1] if composer_lines else ""
            live_composer = last_composer
            try:
                find_chrome_fn = getattr(_agent_status(), "_find_chrome", None)
                if callable(find_chrome_fn):
                    chrome = find_chrome_fn(pane.splitlines())
                    if chrome and "composer_text" in chrome:
                        live_composer = chrome["composer_text"]
            except Exception:
                pass

            still_in_composer = bool(marker) and marker in live_composer
            # defensive (task #16): a chip in the composer = payload landed but
            # unsubmitted — keep retrying Enter, never read it as submitted.
            if any(p in live_composer or p in last_composer for p in ("[Pasted text", "[Pasted Content", "[Pasted")):
                still_in_composer = True
            turn_started = "esc to interrupt" in pane or "esc to cancel" in pane or any(c in pane for c in "⣾⣽⣻⢿⡿⣟⣯⣷")
            if turn_started or not still_in_composer:
                ok_info = {"state": state, "attempts": attempt}
                if _chip_pointer:
                    ok_info["chip_dodge_pointer"] = _chip_pointer
                return True, ok_info
        fail_info = {"reason": "unverified_submit", "state": state}
        if _chip_pointer:
            fail_info["chip_dodge_pointer"] = _chip_pointer
        return False, fail_info


# ---------------------------------------------------------------------------
# Ghost-suggestion accept (spec 2026-08-15 §3b): POST /agent-suggest accepts the
# CLI-suggested ghost currently in an agent's composer and submits it. The keys
# sent are Tab+Enter only (no new text authority beyond /agent-message); the
# suggestion_text is COMPARE-ONLY. Fail-closed two-phase send: a stale/raced
# chip can only 409, never stomp the operator's typed input.
#
# Failure-residue note (F5): a Tab-accepted-but-unsubmitted composer (phase-B
# mismatch -> no Enter) reads to the detector as `stranded_input` and can draw
# an idle-stall-watchdog nudge — benign but noisy. Phase-C submit-verify mostly
# prevents it; a watchdog "stale composer" escalation on chip residue is NOT a
# new bug.
# ---------------------------------------------------------------------------

# Conservative accept->ingest wait, sized off the D-1 live probe (Tab=accept,
# ingest <=0.21s; Enter=submit ~0.28s). Use 0.3-0.5s before the phase-B verify.
SUGGEST_INGEST_WAIT_S = float(os.environ.get("WATCH_GATEWAY_SUGGEST_WAIT", "0.4"))
SUGGEST_AUDIT_PATH = os.environ.get(
    "WATCH_GATEWAY_SUGGEST_AUDIT",
    str(Path(__file__).resolve().parent.parent / "logs" / "agent-suggest-audit.jsonl"))


def _audit_suggest(record: dict):
    """Append a provenance row (F7). A submitted ghost is attributable to an
    'app-accepted suggestion', never mislabeled as the operator-typed (ghosts can
    fabricate consent language — reference_ghost_vs_typed_composer)."""
    try:
        p = Path(SUGGEST_AUDIT_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _bottom_composer_typed(session):
    """Default-styled (TYPED) text of the bottom-most composer line on the
    VISIBLE screen. '' if the composer is empty/ghost-only; None if the detector
    style helper is unavailable (=> caller treats as unverifiable -> fail-closed)."""
    helper = getattr(_agent_status(), "composer_typed_text", None)
    if not callable(helper):
        return None
    pre_raw = _capture_pane(session, lines=0, ansi=True) or ""
    comp = [ln for ln in pre_raw.splitlines()
            if _is_prompt_line(_strip_ansi(ln))]
    return helper(comp[-1]) if comp else ""


def _accepted_matches(typed, suggestion_text) -> bool:
    """Phase-B verify: the composer now holds the accepted suggestion as typed
    text with nothing extra. Ellipsis/prefix-tolerant — an over-wide ghost
    renders truncated on screen but accepts full (spec §3b.4b)."""
    t = (typed or "").strip().rstrip("…").rstrip(".").rstrip()
    if not t:
        return False
    s = (suggestion_text or "").strip()
    return t == s or s.startswith(t)


def suggest_accept(session, suggestion_text):
    """Two-phase accept of the current ghost suggestion. Returns (status, body).
    Holds the per-session send mutex for the whole Tab->verify->Enter->verify
    sequence (F3). Fail-closed: any ambiguity -> refuse BEFORE Enter."""
    import time as _time
    with _session_send_lock(session):
        # (§3b.2) re-fetch the detector — must STILL be idle with the SAME ghost.
        st = _agent_status().get_agent_status(session)
        state = st.get("state") if isinstance(st, dict) else "unknown"
        ghost = (st.get("composer_ghost") or "") if isinstance(st, dict) else ""
        if state != "idle":
            _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                            "ts": _time.time(), "outcome": "refused_not_idle",
                            "state": state, "source": "app-accepted suggestion"})
            return 409, {"ok": False, "error": "agent not idle",
                         "state": _STATE_MAP.get(state, state)}
        if not ghost or ghost != suggestion_text:
            _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                            "ts": _time.time(), "outcome": "refused_stale_ghost",
                            "source": "app-accepted suggestion"})
            return 409, {"ok": False, "error": "suggestion changed or gone"}
        # (§3b.3) NEVER stomp typed input. idle+ghost already implies none by
        # construction (a typed composer reads as stranded_input, not idle); this
        # is belt-and-braces against a race between the detector fetch and now.
        typed = _bottom_composer_typed(session)
        if typed:
            _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                            "ts": _time.time(), "outcome": "refused_typed_present",
                            "typed": typed[:120], "source": "app-accepted suggestion"})
            return 409, {"ok": False, "error": "composer has typed text",
                         "composer_text": typed[:120]}
        # (§3b.4a) Tab accepts the ghost into the composer (NOT a digit).
        r = _tmux("send-keys", "-t", session, "Tab")
        if r.returncode != 0:
            _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                            "ts": _time.time(), "outcome": "send_failed_tab",
                            "source": "app-accepted suggestion"})
            return 502, {"ok": False, "error": "tmux send-keys Tab failed"}
        _time.sleep(SUGGEST_INGEST_WAIT_S)
        # (§3b.4b) re-capture + verify; on mismatch STOP — no Enter (F1/F4). Worst
        # case = accepted-but-unsubmitted text the operator can see and edit, never a wrong
        # submit. A missing style helper -> '' -> mismatch -> refuse (fail-closed).
        accepted = _bottom_composer_typed(session) or ""
        if not _accepted_matches(accepted, suggestion_text):
            _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                            "ts": _time.time(), "outcome": "refused_phaseb_mismatch",
                            "captured": accepted[:120], "source": "app-accepted suggestion"})
            return 409, {"ok": False, "error": "accept verify failed (no submit)",
                         "captured": accepted[:120]}
        # (§3b.4c) Enter + verified-submit (retry once — Enter-swallow hazard).
        marker = suggestion_text.strip().splitlines()[0][:40] if suggestion_text.strip() else ""
        for attempt in (1, 2):
            _tmux("send-keys", "-t", session, "Enter")
            _time.sleep(0.8 if attempt == 1 else 1.6)
            pane = _strip_ansi(_capture_pane(session, lines=0) or "")
            comp = [ln for ln in pane.splitlines() if _is_prompt_line(ln)]
            last = comp[-1] if comp else ""
            still = bool(marker) and marker in last
            if "esc to interrupt" in pane or not still:
                _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                                "ts": _time.time(), "outcome": "submitted",
                                "attempts": attempt, "source": "app-accepted suggestion"})
                return 200, {"ok": True, "submitted": True, "attempts": attempt}
        _audit_suggest({"session": session, "suggestion_text": suggestion_text,
                        "ts": _time.time(), "outcome": "unverified_submit",
                        "source": "app-accepted suggestion"})
        return 502, {"ok": False, "error": "unverified submit"}


# ---------------------------------------------------------------------------
# Menu-resume transport (app-watch-decision-surface §1a + AGY #1): the ledger's
# fire_resume delivers a menu answer as a KEYPRESS (direct) or a three-phase
# digit→text→Enter sequence (free_text) into the source pane. Sync functions —
# approval_resume imports them lazily (in-process from handle_answer via
# executor, out-of-process from the watchdog). Staleness: the CAPTURED question
# must still be the menu on screen, else the wrong menu would take the digit.
# ---------------------------------------------------------------------------

MENU_FIELD_OPEN_WAIT_S = float(os.environ.get("WATCH_GATEWAY_MENU_FIELD_WAIT", "0.8"))

# §1a classification — THE one place labels are classed (spec: "Classification
# lives in the BRIDGE (one place, not per-surface)"; for live pending_menu the
# gateway passthrough is that place, and menu-bridge.py imports this helper).
# Frozen AskUserQuestion built-in classes; live captures vary trailing period.
_FREE_TEXT_CLASSES = ("type something", "write-in", "write in", "custom answer", "other")
_CHAT_CLASS = "chat about this"


def _is_gemini_session(session: str) -> bool:
    if session.startswith("gemini-") or session.startswith("gemini_"):
        return True
    try:
        st = _agent_status().get_agent_status(session)
        if isinstance(st, dict) and st.get("process", {}).get("runtime") == "gemini":
            return True
    except Exception:
        pass
    return False


def _is_codex_session(session: str) -> bool:
    if session.startswith("codex-") or session.startswith("codex_"):
        return True
    try:
        st = _agent_status().get_agent_status(session)
        if isinstance(st, dict) and st.get("process", {}).get("runtime") == "codex":
            return True
    except Exception:
        pass
    return False


def _classify_input_kind(opt):
    """direct | free_text | chat for one option, by its (frozen AUQ) label."""
    label = str(opt.get("label", "")).strip().rstrip(".").lower()
    if any(label.startswith(c) for c in _FREE_TEXT_CLASSES) or label in _FREE_TEXT_CLASSES:
        return "free_text"
    if label == _CHAT_CLASS:
        return "chat"
    return "direct"


def stamp_input_kinds(menu):
    """Stamp input_kind onto each option of a pending_menu/bridge capture.
    Mutates + returns the menu (None-safe). direct | free_text | chat.

    Stamps the flat `options` AND every `parts[].options` (build-150 F-NB1): the
    client renders parts[].options for multi-part / multi-select menus, and those
    were previously UNSTAMPED -> defaulted to 'direct' -> the 'type something'
    free-text option rendered as a selectable toggle instead of a text box."""
    if not isinstance(menu, dict):
        return menu

    def _stamp_list(opts):
        for opt in (opts or []):
            if isinstance(opt, dict):
                opt["input_kind"] = _classify_input_kind(opt)

    _stamp_list(menu.get("options"))
    for part in (menu.get("parts") or []):
        if isinstance(part, dict):
            _stamp_list(part.get("options"))
    return menu


def _current_menu(session):
    st = _agent_status().get_agent_status(session)
    menu = st.get("pending_menu") if isinstance(st, dict) else None
    if not menu and _is_codex_session(session):
        try:
            from codex_menu_parser import parse_codex_menu
            pane = _capture_pane(session, lines=30, ansi=True)
            if pane:
                parsed = parse_codex_menu(pane)
                if parsed.get("kind") != "unknown" and parsed.get("options"):
                    menu = {
                        "kind": parsed.get("kind"),
                        "question": parsed.get("title") or "Codex Prompt",
                        "options": [{"n": str(o.get("num", i + 1) or (i + 1)), "label": o.get("label") or o.get("text") or ""} for i, o in enumerate(parsed["options"])],
                        "source_session": session
                    }
        except Exception:
            pass
    return menu


def _q_norm(s):
    """Normalize a menu question for identity: collapse whitespace, drop the
    visible truncation ellipsis."""
    return " ".join((s or "").replace("\u2026", " ").split())


def _menu_matches(session, expect_question):
    """Same-menu identity, VERSION-TOLERANT (P0 fix, v3 msg_667fb1b8 / the operator
    field-caught 07:24): the question-identity seam has two consumers on
    different deploy cadences (fresh-per-cron bridge vs this long-running
    process). Strict equality false-failed the instant a4137c218 changed the
    capture format — 'menu_gone' 6ms after the operator's tap. Identity is now
    normalized CONTAINMENT in either direction (an old tail-line capture is a
    substring of the new full-block capture, and vice versa), which stays
    correct across capture-format versions while still refusing a genuinely
    different menu. Never use strict equality across a deployable seam."""
    menu = _current_menu(session)
    if not menu:
        return False
    if expect_question:
        a = _q_norm(menu.get("question"))
        b = _q_norm(expect_question)
        if not a or not (a in b or b in a):
            return False
    return True


# Wall-clock waits for the commit-verify loop (post-Enter). Mirrors the
# menu_resume_free_text phase-3 cadence: a quick check, then a longer one.
MENU_COMMIT_VERIFY_WAITS_S = (0.8, 1.6)


def menu_resume_keypress(session, key, expect_question=None, commit=True):
    """Answer a still-present menu by option digit. Returns (ok, info);
    reason 'menu_gone' == the 409 class -> caller marks resolved_elsewhere.

    commit=True (the direct watch/app answer path): the digit SELECTS the
    option and Enter COMMITS it. Claude Code 2.1.260's AskUserQuestion footer
    reads 'Enter to select · ↑/↓ to navigate · Esc to cancel' (see fixture
    scripts/fixtures/chrome-2.1.260/decision_menu.pane.txt) — a bare digit only
    highlights, it does NOT submit. We send digit+Enter, then VERIFY the menu
    actually resolved before reporting success; an un-resolved menu returns
    reason 'unverified_submit' rather than a false 'delivered' (the silent
    no-op class that lost every direct menu answer on 2.1.260). gemini/codex
    already commit with Enter and keep their existing immediate-return contract.

    commit=False: digit-ONLY, no Enter, no verify. This is phase-1 of
    menu_resume_free_text — the digit must OPEN the option's 'Type something'
    text field WITHOUT committing; a blanket Enter would submit that option
    with an empty body (strictly worse than the original bug)."""
    if session not in _tmux_session_names():
        return False, {"reason": "no_session"}
    if not _menu_matches(session, expect_question):
        return False, {"reason": "menu_gone"}
    if _is_gemini_session(session) or _is_codex_session(session):
        r = _tmux("send-keys", "-t", session, key, "Enter")
        if r.returncode != 0:
            return False, {"reason": "send_failed"}
        return True, {"sent": key}
    # Claude Code branch.
    if not commit:
        r = _tmux("send-keys", "-t", session, key)   # open the field; do NOT commit
        if r.returncode != 0:
            return False, {"reason": "send_failed"}
        return True, {"sent": key}
    r = _tmux("send-keys", "-t", session, key, "Enter")  # select + commit
    if r.returncode != 0:
        return False, {"reason": "send_failed"}
    import time as _time
    for attempt, wait_s in enumerate(MENU_COMMIT_VERIFY_WAITS_S, start=1):
        _time.sleep(wait_s)
        if not _menu_matches(session, expect_question):
            return True, {"sent": key, "verified": True, "attempts": attempt}
    return False, {"reason": "unverified_submit"}


def menu_resume_free_text(session, key, text, expect_question=None):
    """Three-phase free_text answer (§1a): (1) digit opens the option's text
    field, (2) literal text inject, (3) Enter + verify the menu resolved.
    No idle-gate (the field is EXPECTED open) but composer-verify discipline
    holds: any unverified phase returns an honest failure, never a silent
    half-submit. Phase-1-ok/phase-2-fail leaves text in the composer — the
    stranded machinery surfaces that; we still report reason+phase here."""
    import time as _time
    # Phase-1 is digit-ONLY: OPEN the option's text field, do NOT commit
    # (commit=False). A blanket Enter here would submit the 'Type something'
    # option with an empty body. Phase-3 below does the Enter + verify.
    ok, info = menu_resume_keypress(session, key, expect_question, commit=False)
    if not ok:
        info.setdefault("phase", 1)
        return False, info
    _time.sleep(MENU_FIELD_OPEN_WAIT_S)          # let the text field open
    r = _tmux("send-keys", "-t", session, "-l", text)
    if r.returncode != 0:
        return False, {"reason": "send_failed", "phase": 2}
    _time.sleep(INJECT_INGEST_WAIT_S)
    for attempt in (1, 2):
        r = _tmux("send-keys", "-t", session, "Enter")
        if r.returncode != 0:
            return False, {"reason": "send_failed", "phase": 3}
        _time.sleep(0.8 if attempt == 1 else 1.6)
        if not _menu_matches(session, expect_question):
            return True, {"attempts": attempt}   # menu resolved — submit verified
    return False, {"reason": "unverified_submit", "phase": 3}


# ---------------------------------------------------------------------------
# Contract B — permission "Respond-with-text" parity (DEC-1786882851 CONSENSUS).
# Native permission prompts answer by digit today; Claude Code offers a "No, and
# tell Claude what to do differently" option that accepts free text. This resolves
# that option by LABEL substring (never positional — positional would select "No"
# on a textless variant and dump the instruction into the live composer), then
# three-phase types the operator's instruction. Gated by PERM_RESPOND_ARMED (dry-run
# resolves + reports would-type, presses NO keys — disabled->arm discipline,
# mirrors MENU_SUBMIT_ARMED). Fail-closed with DISTINCT reasons (council Q3).
# ---------------------------------------------------------------------------
_PERM_TEXT_OPTION_MARKERS = ("tell claude", "differently")


def _perm_text_option_n(menu):
    """The option digit of the 'No, and tell Claude what to do differently'
    variant, matched by case-insensitive LABEL substring. None if no such option
    (a textless Yes/No prompt). Never positional (council Q2)."""
    for opt in (menu.get("options") or []):
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label", "")).lower()
        if any(m in label for m in _PERM_TEXT_OPTION_MARKERS):
            return opt.get("n")
    return None


def permission_respond(session, text, *, armed=False, read_fn=None, key_fn=None,
                       type_fn=None, gone_fn=None, settle_s=None):
    """Answer a native permission prompt with free-text instruction. Returns
    (ok, info). Fail-closed with DISTINCT reasons (council Q3):
      not_permission_prompt — target isn't a live permission prompt
      menu_gone             — prompt vanished before we could answer
      no_text_option        — this variant has no 'tell Claude differently' slot
      send_failed           — a low-level key/type send failed
      unverified_submit     — typed but the prompt didn't advance (phase 3)
    Dry-run (armed False): resolve the option + report would_select/would_type,
    press ZERO keys. Pure over injectable fns for hermetic tests."""
    import time as _time
    settle_s = MENU_FIELD_OPEN_WAIT_S if settle_s is None else settle_s
    read_fn = read_fn or (lambda: _current_menu(session))
    key_fn = key_fn or (lambda k: _tmux("send-keys", "-t", session, k).returncode == 0)
    type_fn = type_fn or (lambda t: _tmux("send-keys", "-t", session, "-l", t).returncode == 0)
    gone_fn = gone_fn or (lambda: not _menu_matches(session, question))

    menu = read_fn()
    if not isinstance(menu, dict):
        return False, {"reason": "menu_gone"}
    if menu.get("kind") != "permission":
        return False, {"reason": "not_permission_prompt"}
    question = menu.get("question") or ""
    option_n = _perm_text_option_n(menu)
    if option_n is None:
        # FAIL-CLOSED, zero keystrokes — never guess an option on a textless prompt.
        return False, {"reason": "no_text_option", "question": question}

    if not armed:
        return True, {"armed": False, "dry_run": True, "option_n": option_n,
                      "would_select": option_n, "would_type": text,
                      "question": question}

    # ARMED: three-phase type under the caller's per-session lock (handler wraps).
    # phase 1: select the text option (digit opens its field).
    if not key_fn(option_n):
        return False, {"reason": "send_failed", "phase": 1}
    _time.sleep(settle_s)
    # phase 2: literal text.
    if not type_fn(text):
        return False, {"reason": "send_failed", "phase": 2}
    _time.sleep(INJECT_INGEST_WAIT_S)
    # phase 3: Enter + verify the prompt advanced (dismissed).
    for attempt in (1, 2):
        if not key_fn("Enter"):
            return False, {"reason": "send_failed", "phase": 3}
        _time.sleep(0.8 if attempt == 1 else 1.6)
        if gone_fn():
            _mark_instance_answered(session, question)   # instance-ledger parity
            return True, {"armed": True, "option_n": option_n, "attempts": attempt}
    return False, {"reason": "unverified_submit", "phase": 3}


def _menu_on_submit_tab(menu):
    """True when the parsed menu shows the Submit tab is active — its option list
    collapses to a single 'Submit answers' control (verified live 2026-08-16)."""
    if not isinstance(menu, dict):
        return False
    opts = menu.get("options") or []
    return len(opts) == 1 and "submit" in str(opts[0].get("label", "")).lower()


def _menu_is_confirm(menu):
    """True when the parsed menu is a submit/confirm-shaped screen — ANY option is a
    'Submit answers' control. BROADER than `_menu_on_submit_tab` (which requires the
    list to collapse to exactly one option): the real 'Review your answers' screen
    also offers a 'Cancel' option, so it has two. Used to fail-closed BEFORE the
    Enter-confirm loop (congruence DEC-1787565310, AGY leg): a blind Enter on a
    still-live checkbox part (no 'submit' option) would toggle a preset / re-open a
    field, so drive the confirm chain ONLY once a submit control is on screen."""
    if not isinstance(menu, dict):
        return False
    return any("submit" in str(o.get("label", "")).lower()
               for o in (menu.get("options") or []))


def menu_capture_walk(session, *, read_fn=None, key_fn=None, settle_s=0.8,
                      max_parts=12):
    """ACTIVE multi-part capture (DEC-1786866488 §A, C1): press Right to page a
    multi-part AskUserQuestion, capturing each part, until the Submit tab is
    reached. This is the SEPARATE explicit entrypoint — the PASSIVE detector
    (get_agent_status) NEVER presses keys; only this gateway-invoked walk does,
    under the per-session lock so it can't race a concurrent walk/submit.

    Right is navigation-only (never Enter/digit — never mutates an answer).
    Bounded by max_parts; abort-on-anomaly (a read that no longer parses as the
    menu) serves what was captured with walk_complete False so clients fail-safe.
    Pure over read_fn (-> parsed pending_menu|None) / key_fn (sends one key)."""
    import time as _time
    read_fn = read_fn or (lambda: _current_menu(session))
    key_fn = key_fn or (lambda k: _tmux("send-keys", "-t", session, k).returncode == 0)

    def _run():
        parts = []
        menu = read_fn()
        if not isinstance(menu, dict):
            return {"parts": [], "part_count": 0, "walk_complete": False,
                    "reason": "menu_gone"}
        # single-part (no more tabs to page): serve immediately.
        if not menu.get("multipart") and menu.get("walk_complete", True):
            return {"parts": list(menu.get("parts") or []),
                    "part_count": menu.get("part_count", 1), "walk_complete": True}
        # §1.1(b) condition 2 — MID-NAVIGATION GUARD. The CLIENT-triggered walk
        # (approval-page/watch hydration) actuates Right/Left on a LIVE pane the operator
        # may be driving. bridge_one already guards its own active-walk path; the
        # client walk must carry the equivalent 'parked on part-0' guard, or an
        # approval-page render would Right/Left over his live keys and its Left-
        # restore would land him where he didn't choose (D3/F11). With the §1.3
        # focus fix part_index is now RELIABLE, so a non-zero part_index means the operator
        # is mid-navigation -> abort-defer (walk_complete:false, serve fail-safe),
        # pressing ZERO keys. The next request retries once he's back on part 0.
        if menu.get("part_index", 0) != 0:
            return {"parts": [], "part_count": menu.get("part_count", 1),
                    "walk_complete": False, "reason": "not_on_part_zero"}
        # Drive by the KNOWN part_count (from the non-Submit tab count), NOT by
        # interpreting the Submit page: the real Submit tab is a CONFIRM page
        # ("Ready to submit your answers? / Submit answers / Cancel", TWO options),
        # so shape-sniffing it would wrap the tab bar forever (live E2E finding
        # 2026-08-16). Capture exactly `target` question-parts, pressing Right
        # between each. The passive parser can't read an absolute part index off a
        # question-name tab bar (no "k of M") — it emits index 0 for every part —
        # so the walk assigns its OWN sequential index by step.
        want = menu.get("part_count") or 1
        capped = min(want, max_parts)             # never Right past the safety bound
        rights, reason = 0, None
        for step in range(capped):
            if not isinstance(menu, dict):
                reason = "menu_gone"
                break
            if _menu_on_submit_tab(menu):        # early-out if we somehow reached it
                break
            cur = (menu.get("parts") or [None])[0]
            if isinstance(cur, dict):
                cur = dict(cur)
                cur["index"] = step               # authoritative sequential index
                # tab_label per-part BY POSITION: the parser can't tell which tab
                # is active from the stripped header (it labels every part with the
                # first tab), so attribute from tabs[step] — the non-Submit tabs in
                # order (v14 live E2E off-by-one nit 2026-08-16).
                _tabs = [t for i, t in enumerate(menu.get("tabs") or [])
                         if i != menu.get("submit_tab_index")]
                if step < len(_tabs):
                    cur["tab_label"] = _tabs[step]
                parts.append(cur)
            if step == capped - 1:
                break                             # captured the last part; don't Right off it
            if not key_fn("Right"):               # advance to the next part
                reason = "send_failed"
                break
            rights += 1
            if settle_s:
                _time.sleep(settle_s)
            menu = read_fn()
        # IDEMPOTENT: press Left back the same count so the menu is left on part 0
        # (a fresh client capture starts on part 0; the walk must not displace the
        # surface, and a re-run must yield the same — live E2E finding 2026-08-16).
        for _ in range(rights):
            key_fn("Left")
            if settle_s:
                _time.sleep(settle_s)
        # complete only if we captured ALL the wanted parts (part_count within the
        # bound); part_count > max_parts (or an abort) => incomplete + reason.
        complete = reason is None and len(parts) >= want
        out = {"parts": parts, "part_count": len(parts), "walk_complete": complete}
        if not complete:
            out["reason"] = reason or "max_parts_bound"
        return out

    lock = _session_send_lock(session)
    with lock:
        return _run()


def _part_current_checked(menu):
    """The set of currently-checked option n's on the menu's current part."""
    part = (menu.get("parts") or [None])[0] if isinstance(menu, dict) else None
    opts = (part or {}).get("options") or (menu.get("options") if isinstance(menu, dict) else []) or []
    return {o["n"] for o in opts if isinstance(o, dict) and o.get("checked")}


def _part_free_text_n(menu):
    """The option `n` of the current part's free-text row, or None. Matches the
    stamped input_kind first, else the free-text-class LABEL directly — covers
    Claude's "Type something" AND agy's "Write-in..." (F6) even on an unstamped
    capture (stamp_input_kinds may not have run on a raw hydration read)."""
    part = (menu.get("parts") or [None])[0] if isinstance(menu, dict) else None
    opts = (part or {}).get("options") or (menu.get("options") if isinstance(menu, dict) else []) or []
    for o in opts:
        if not isinstance(o, dict):
            continue
        if o.get("input_kind") == "free_text":
            return o.get("n")
        lab = str(o.get("label", "")).strip().rstrip(".").lower()
        if lab in _FREE_TEXT_CLASSES:          # "type something" | "write-in" | …
            return o.get("n")
    return None


# --- build-150 free-text-in-multi-select: cursor-nav injection (DEC-1787556508) ---
# Root cause (the operator on-device, F-NB3): pressing the free-text option's NUMBER toggles
# it but never MOVES the TUI cursor into its text field, so the typed text lands
# nowhere. The fix navigates the cursor onto the option, verifies focus, types,
# closes the field with a POSITIVE close-proof, THEN toggles presets. Every phase is
# fail-closed (no blind keys — a stray toggle digit into an open field is the hazard).

def _part_option_ns(menu):
    """Ordered option n's of the current on-screen part (top-to-bottom) — the
    axis for Down/Up cursor navigation."""
    part = (menu.get("parts") or [None])[0] if isinstance(menu, dict) else None
    opts = (part or {}).get("options") or (menu.get("options") if isinstance(menu, dict) else []) or []
    return [o.get("n") for o in opts if isinstance(o, dict) and o.get("n") is not None]


def _cursor_n(menu):
    """The currently-focused option n (the ❯ cursor / selected_n), or None."""
    return menu.get("selected_n") if isinstance(menu, dict) else None


def _free_text_value_present(menu, typed_text):
    """Positive-ingest signal: the typed text (head) now appears in the current
    part's option labels — the AUQ renders the entered value on the 'type
    something' row once it is accepted."""
    needle = (typed_text or "").strip()[:24].lower()
    if not needle:
        return False
    part = (menu.get("parts") or [None])[0] if isinstance(menu, dict) else None
    opts = (part or {}).get("options") or (menu.get("options") if isinstance(menu, dict) else []) or []
    return any(needle in str(o.get("label", "")).lower()
               for o in opts if isinstance(o, dict))


def _nav_cursor_to(target_n, *, read_fn, key_fn, settle_s, max_steps=16):
    """Move the AUQ cursor onto option `target_n` with Down/Up WITHIN the part
    (NOT Right — Right pages to another part). Re-reads a FRESH parse after each
    step (parts is always the 1-element current part; distance is never cached).
    Fail-closed (False, unverified_focus) if the cursor doesn't land."""
    import time as _time
    for _ in range(max_steps + 1):
        menu = read_fn()
        if not isinstance(menu, dict):
            return False, {"reason": "menu_gone"}
        cur = _cursor_n(menu)
        if cur == target_n:
            return True, {}
        order = _part_option_ns(menu)
        if cur is None or cur not in order or target_n not in order:
            return False, {"reason": "unverified_focus", "detail": "cursor/target off-part"}
        if not key_fn("Down" if order.index(target_n) > order.index(cur) else "Up"):
            return False, {"reason": "send_failed", "detail": "nav"}
        if settle_s:
            _time.sleep(settle_s)
    return False, {"reason": "unverified_focus"}


def _inject_part_free_text(target_n, text, *, read_fn, key_fn, type_fn, settle_s):
    """Steps 2-4 for ONE part's free text: navigate the cursor onto the free-text
    option (HARD focus-verify), OPEN+type, POSITIVELY prove the value ingested, then
    commit-without-dismissing by NAVIGATING AWAY (an arrow) — never Esc. Distinct
    fail-closed reason at every unverified phase; presses NO menu-dismissing key."""
    # ===================== AUQ KEYSTROKE FACTS — PROVEN BY EFFECT =====================
    # 2026-08-24, PRIVATE scratch AUQs iwd-gen2-ftscratch / iwd-gen2-ft2 (captured
    # after EVERY keystroke). Esc/Enter/arrow/digit semantics are EMPIRICAL — 56 green
    # UNIT tests never caught them:
    #  1. On the focused "Type something" row, `Enter` BOTH checks the box ([ ]->[✔])
    #     AND enters inline edit mode. One key does both (no separate open/digit step).
    #  2. Typed chars land INLINE, replacing the placeholder: the row renders "[✔] <text>".
    #  3. COMMIT WITHOUT DISMISSING = an ARROW key (Up/Down): it exits edit mode and the
    #     typed value PERSISTS as the checked answer; the menu stays fully alive.
    #     - `Esc` = "cancel" -> DISMISSES THE WHOLE MENU (gen1 armed run: menu_gone/
    #       post_close/freetext0). NEVER press it here.
    #     - a submit-`Enter` would fire the confirm chain. NEVER use it to commit text.
    #  4. Bare digits toggle presets by ABSOLUTE number, cursor-independent, and are NOT
    #     absorbed into the field once edit mode is exited (verified: "1" toggled opt1
    #     while "[✔] KTEXT" stayed intact). So the caller's digit toggles are safe AFTER
    #     this returns. Full e2e twice landed the free text in the resolved answer.
    # Supersedes DEC-1787556508's Esc "close-step". Effect-proof:
    #   .workspace/canary-r6/surface-test/freetext-keystroke-EFFECT-PROOF.md
    # =================================================================================
    import time as _time
    # (2) navigate the cursor onto the free-text option (Down/Up within the part).
    ok, info = _nav_cursor_to(target_n, read_fn=read_fn, key_fn=key_fn,
                              settle_s=settle_s)
    if not ok:
        return False, {**info, "phase": "nav_freetext"}
    # (3a) Enter OPENS the field + checks the row (one key, proven by effect).
    if not key_fn("Enter"):
        return False, {"reason": "send_failed", "phase": "open_field"}
    if settle_s:
        _time.sleep(settle_s)
    # (3b) type the answer literally -> lands inline on the row.
    if not type_fn(text):
        return False, {"reason": "send_failed", "phase": "type"}
    if settle_s:
        _time.sleep(settle_s)
    # (3c) POSITIVE-INGEST proof: the typed head now shows on the current part's row.
    menu = read_fn()
    if not isinstance(menu, dict):
        return False, {"reason": "menu_gone", "phase": "post_type"}
    if not _free_text_value_present(menu, text):
        return False, {"reason": "free_text_not_ingested", "phase": "post_type"}
    # (4) COMMIT WITHOUT DISMISSING: an arrow exits edit mode; value persists. NOT Esc.
    #     GEOMETRY-AWARE direction (congruence DEC-1787565310, both peers): use Up
    #     normally, but Down when the free-text row is the TOP option — where Up is a
    #     no-op that would leave edit mode OPEN. Derived from the current part's order.
    order = _part_option_ns(menu)
    commit_key = "Down" if (order and order[0] == target_n) else "Up"
    if not key_fn(commit_key):
        return False, {"reason": "send_failed", "phase": "commit"}
    if settle_s:
        _time.sleep(settle_s)
    # (4b) POSITIVE close-proof requires BOTH signals (congruence DEC-1787565310):
    #   (i) the cursor actually MOVED OFF the free-text row -> edit mode is EXITED, so
    #       a subsequent preset digit toggle can't be absorbed into a still-open field
    #       (value-presence alone would falsely pass while the field stayed open); and
    #   (ii) the typed value SURVIVED. Menu must still be present. Else fail-closed.
    menu = read_fn()
    if not isinstance(menu, dict):
        return False, {"reason": "menu_gone", "phase": "post_commit"}
    if _cursor_n(menu) == target_n:
        return False, {"reason": "free_text_edit_not_exited", "phase": "post_commit"}
    if not _free_text_value_present(menu, text):
        return False, {"reason": "free_text_lost_on_commit", "phase": "post_commit"}
    return True, {"free_text_n": target_n, "committed": True, "commit_key": commit_key}


# --- F6: agy/Gemini native multi-select ANSWER contract (2026-08-24) ----------
# PROVEN BY EFFECT on a private agy scratch (iwd-f6-agy); full proof:
#   .workspace/canary-r6/surface-test/f6-agy-answer-contract-EFFECT-PROOF.md
# agy's contract differs from Claude's AUQ (which menu_batch_submit implements):
#   * toggle a preset = SPACE on the focused row (option NUMBER also toggles+jumps);
#   * write-in text   = nav onto the "Write-in..." row -> ENTER opens an edit field
#     ("Your answer:") -> TYPE (direct-type withOUT the Enter is dropped);
#   * SUBMIT the whole menu = ENTER (one key; agy has NO Submit tab / confirm chain);
#   * ORDER: toggle ALL presets FIRST, THEN the write-in — once the edit field is
#     open, space/number/nav keys type into the field instead of toggling.
# The Claude path FAILS on agy (F6): it toggles by number then hunts a Submit tab via
# Right/confirm-chain agy lacks, and its free-text path presses an ARROW to commit,
# which on agy navigates AWAY from the write-in without committing + never presses the
# final Enter -> "Couldn't submit". Every phase here is fail-closed; no key is pressed
# that could dismiss without submitting.

def _agy_menu_gone(menu):
    """True when the agy menu has resolved (left the screen / no longer parses as a
    live options menu)."""
    return not isinstance(menu, dict) or menu.get("kind") not in ("options", "yes_no")


def _session_pane_text(session):
    """Raw pane text of the session (ANSI-stripped) — used to verify the agy
    write-in value, which renders on a "Your answer:" line the menu parser does
    NOT fold into the options dict."""
    cp = _tmux("capture-pane", "-p", "-t", session)
    return cp.stdout if getattr(cp, "returncode", 1) == 0 else ""


def _agy_writein_field_text(pane_text):
    """The text agy has entered into the OPEN write-in edit field — the content of
    the 'Your answer:' region ONLY (F6 congruence DEC-1787608678, both legs): an
    UNanchored whole-pane substring scan false-positives when the write-in value
    coincides with an option label / the question text, submitting a half-answered
    menu. Anchor the ingest proof to the field. Returns the joined text AFTER the
    'Your answer:' marker (its inline remainder + following non-chrome lines) up to
    the footer / a blank, lowercased; '' if the field marker isn't present."""
    lines = (pane_text or "").splitlines()
    out, capturing = [], False
    for ln in lines:
        s = ln.rstrip()
        low = s.strip().lower()
        if not capturing:
            idx = low.find("your answer:")
            if idx != -1:
                capturing = True
                rest = s.strip()[idx + len("your answer:"):].strip()
                if rest:
                    out.append(rest)
            continue
        if not s.strip():
            break                                  # blank ends the field region
        if any(h in low for h in ("enter submit", "esc ", "↑/↓", "navigate",
                                   "esc to cancel")):
            break                                  # footer chrome ends it
        out.append(s.strip())
    return " ".join(out).lower()


def menu_submit_agy(session, *, answers, armed=False, read_fn=None, key_fn=None,
                    type_fn=None, settle_s=0.8, text_present_fn=None):
    """Submit a SINGLE-part agy/Gemini native multi-select by its own contract.
    answers = the SAME batch shape as menu_batch_submit ([{part, ns, text?}]); only
    part 0 is used (agy renders one 'Question 1/1' at a time). DRY-RUN by default.
    text_present_fn(text)->bool proves the write-in value ingested (agy shows it on
    a 'Your answer:' line, NOT in the parsed options); defaults to a raw pane scan."""
    import time as _time
    read_fn = read_fn or (lambda: _current_menu(session))
    key_fn = key_fn or (lambda k: _tmux("send-keys", "-t", session, k).returncode == 0)
    type_fn = type_fn or (
        lambda txt: _tmux("send-keys", "-t", session, "-l", txt).returncode == 0)
    if text_present_fn is None:
        def text_present_fn(t):
            # ANCHORED to the 'Your answer:' field (F6 congruence): an unanchored
            # whole-pane scan false-positives when the write-in value matches an
            # option label / question text, submitting a half-answered menu.
            needle = (t or "").strip()[:32].lower()
            if not needle:
                return False
            return needle in _agy_writein_field_text(_session_pane_text(session))
    ans = next((a for a in (answers or []) if isinstance(a, dict)
                and a.get("part", 0) == 0), (answers or [{}])[0] if answers else {})
    text = ans.get("text")

    def _run():
        menu = read_fn()
        if not isinstance(menu, dict):
            return False, {"reason": "menu_gone"}
        ft_n = _part_free_text_n(menu)                 # the "Write-in..." row n
        want = set(ans.get("ns") or [])
        want.discard(ft_n)                             # write-in is typed, never toggled
        have = _part_current_checked(menu)
        have.discard(ft_n)
        delta = sorted(want ^ have)                    # preset rows needing a toggle
        if not armed:
            return True, {"would_submit": True, "dry_run": True, "family": "agy",
                          "plan": {"toggle": delta, "text": text or None,
                                   "free_text_n": ft_n, "submit": "Enter"}}

        # (1) toggle preset DELTA — nav onto each row, SPACE. BEFORE any write-in edit.
        for n in delta:
            ok, info = _nav_cursor_to(n, read_fn=read_fn, key_fn=key_fn, settle_s=settle_s)
            if not ok:
                return False, {**info, "phase": f"nav_toggle{n}"}
            if not key_fn("Space"):
                return False, {"reason": "send_failed", "phase": f"toggle{n}"}
            if settle_s:
                _time.sleep(settle_s)
            m2 = read_fn()                             # verify the row flipped as intended
            if not isinstance(m2, dict):
                return False, {"reason": "menu_gone", "phase": f"post_toggle{n}"}
            now_checked = n in _part_current_checked(m2)
            if now_checked != (n in want):
                return False, {"reason": "toggle_unverified", "phase": f"toggle{n}",
                               "detail": f"n={n} checked={now_checked} want={n in want}"}

        # (2) write-in: nav onto the Write-in row -> Enter (open edit) -> type -> verify.
        if text:
            if ft_n is None:
                return False, {"reason": "no_free_text_option", "phase": "text"}
            ok, info = _nav_cursor_to(ft_n, read_fn=read_fn, key_fn=key_fn, settle_s=settle_s)
            if not ok:
                return False, {**info, "phase": "nav_writein"}
            if not key_fn("Enter"):                    # OPEN the write-in edit field
                return False, {"reason": "send_failed", "phase": "open_writein"}
            if settle_s:
                _time.sleep(settle_s)
            if not type_fn(text):                      # type into the open field
                return False, {"reason": "send_failed", "phase": "type"}
            if settle_s:
                _time.sleep(settle_s)
            # POSITIVE-ingest proof: the value shows on agy's "Your answer:" line
            # (raw pane), and the menu must still be present (not submitted early).
            if _agy_menu_gone(read_fn()):
                return False, {"reason": "menu_gone", "phase": "post_type"}
            if not text_present_fn(text):
                return False, {"reason": "writein_not_ingested", "phase": "post_type"}

        # (3) SUBMIT = a SINGLE Enter, then POLL for the async resolve — NO second
        # Enter. agy processes the submit asynchronously ("Thinking…" for several
        # seconds), so the old 2-attempt×settle window false-negatived (the menu DID
        # submit but wasn't gone YET -> 'unverified_submit' -> 502 -> the app's
        # "Couldn't submit — answer in the terminal" while the answer actually
        # landed, the operator 2026-08-24). A blind second Enter also leaked into agy's
        # composer. Press once; poll patiently up to ~20s (client HTTP timeout=60s).
        if not key_fn("Enter"):
            return False, {"reason": "send_failed", "phase": "submit"}
        max_polls = 25                          # ~20s at settle_s=0.8; 0 in unit tests
        for i in range(1, max_polls + 1):
            if settle_s:
                _time.sleep(settle_s)
            if _agy_menu_gone(read_fn()):
                return True, {"submitted": True, "family": "agy", "polls": i,
                              "toggled": delta, "wrote": bool(text)}
        return False, {"reason": "unverified_submit", "phase": "submit", "family": "agy"}

    with _session_send_lock(session):
        return _run()


# --- multi-part DOUBLE-ADVANCE fix (§1.1b Stage-2, the operator on-device 2026-08-26) --------
# The old menu_batch_submit ARMED replay blind-pressed Right after each part. A
# SINGLE-SELECT part AUTO-ADVANCES the tab on selection, so the blind Right advanced a
# SECOND time and SKIPPED the next part, landing on the Review screen (which
# _menu_on_submit_tab missed — Review has TWO options, not one). The loop then processed
# Review as a phantom part -> 502 {no_free_text_option, phase:text2} and, worse, would
# have submitted the skipped part BLANK. Fix: navigate to the EXPECTED part_index with
# identity verification (the _on_original_part philosophy applied to forward nav), never
# blind-advance, and gate Submit on a POSITIVE all-parts-answered assertion.

def _nav_to_part(target_pi, *, read_fn, key_fn, settle_s, max_steps=24):
    """Move the multi-part tab bar to `part_index == target_pi`, VERIFIED per read.
    Right when the current index is below target; Left when above (or on the
    confirm/Review screen, whose index is >= part_count). Re-reads a FRESH parse after
    each step — never blind-advances, so a single-select auto-advance can't desync it.
    Fail-closed (False, reason) if it can't land."""
    import time as _time
    for _ in range(max_steps + 1):
        menu = read_fn()
        if not isinstance(menu, dict):
            return False, {"reason": "menu_gone"}
        on_confirm = _menu_is_confirm(menu) or _menu_on_submit_tab(menu)
        # A question part with no explicit part_index is on its first/only part (single-
        # part menus omit it); default to 0 so single-part nav lands without a stray key.
        cur = menu.get("part_index")
        if cur is None and not on_confirm:
            cur = 0
        if not on_confirm and cur == target_pi:
            return True, {}
        step = "Left" if (on_confirm or (isinstance(cur, int) and cur > target_pi)) else "Right"
        if not key_fn(step):
            return False, {"reason": "send_failed", "detail": f"nav_{step.lower()}"}
        if settle_s:
            _time.sleep(settle_s)
    return False, {"reason": "nav_unverified", "detail": f"could not reach part {target_pi}"}


def _nav_to_confirm(*, read_fn, key_fn, settle_s, max_steps=24):
    """Advance (Right-only) to the confirm/Review or Submit screen, VERIFIED per read.
    Stops the instant a submit control is on screen so the caller's confirm chain can
    drive Enter — never blind-Enters a live question part."""
    import time as _time
    for _ in range(max_steps + 1):
        menu = read_fn()
        if not isinstance(menu, dict):
            return True, {"gone": True}
        if _menu_is_confirm(menu) or _menu_on_submit_tab(menu):
            return True, {}
        if not key_fn("Right"):
            return False, {"reason": "send_failed", "detail": "nav_confirm"}
        if settle_s:
            _time.sleep(settle_s)
    return False, {"reason": "nav_unverified", "detail": "no confirm screen reached"}


def menu_batch_submit(session, *, answers, armed=False, read_fn=None, key_fn=None,
                      type_fn=None, settle_s=0.8, max_parts=12, text_present_fn=None):
    """Generalized multi-part submit (DEC-1786866488 §C). ONE batch answers[]:
    [{part, ns:[...], text?}, …]. Replays deterministically: per part apply the
    DELTA digit toggles vs the on-screen `checked` state, Right to the next part,
    then on the Submit tab Enter through the confirm chain; verify menu gone.

    DRY-RUN by default (armed=False): compute + return the plan, press NOTHING.
    Armed is a SEPARATE gate from the single-part MENU_SUBMIT_ARMED — this moved
    multi-part surface must not inherit the single-part arm silently. Toggles are
    the client's intent replayed here at submit; the client does NOT inject
    per-tap digits (punch-list §2). Runs under the per-session lock. Fail-closed:
    menu gone / unreadable -> honest failure, nothing further pressed."""
    import time as _time
    read_fn = read_fn or (lambda: _current_menu(session))
    key_fn = key_fn or (lambda k: _tmux("send-keys", "-t", session, k).returncode == 0)
    type_fn = type_fn or (
        lambda txt: _tmux("send-keys", "-t", session, "-l", txt).returncode == 0)
    by_part = {a["part"]: a for a in (answers or []) if isinstance(a, dict)}

    # F6: agy/Gemini menus use a DIFFERENT answer contract (space-toggle /
    # Enter-open-write-in / Enter-submit, no Submit tab). Dispatch to the
    # agy-aware submitter BEFORE taking the per-session lock (menu_submit_agy
    # takes its own — the lock is non-reentrant, so dispatching inside _run's
    # locked region would DEADLOCK). The Claude AUQ replay below can't drive agy.
    _fam_probe = read_fn()
    if isinstance(_fam_probe, dict) and _fam_probe.get("menu_family") == "agy":
        return menu_submit_agy(
            session, answers=answers, armed=armed, read_fn=read_fn,
            key_fn=key_fn, type_fn=type_fn, settle_s=settle_s,
            text_present_fn=text_present_fn)

    def _run():
        menu = read_fn()
        if not isinstance(menu, dict):
            return False, {"reason": "menu_gone"}
        n_parts = menu.get("part_count") or len(by_part) or 1
        plan = []
        for pi in range(min(n_parts, max_parts)):
            ans = by_part.get(pi) or {}
            want = set(ans.get("ns") or [])
            step = {"part": pi, "toggle": sorted(want)}
            # Per-part free-text (v14 field-test pre-arm fix): carry the own-words
            # `text` into the plan + resolve the free-text option digit so the
            # armed replay selects it and types. Was SILENTLY DROPPED before.
            if ans.get("text"):
                step["text"] = ans["text"]
                step["free_text_n"] = _part_free_text_n(menu)  # None-ok in dry-run
            plan.append(step)
        if not armed:
            return True, {"would_submit": True, "dry_run": True, "plan": plan}

        # ARMED replay — IDENTITY-VERIFIED navigation (double-advance fix 2026-08-26).
        # PRE-FLIGHT (zero keypress): the batch must answer EVERY part. An incomplete
        # batch (a part with neither ns nor text) can never verified-submit -> fail-
        # closed BEFORE actuating anything. This is the load-bearing anti-silent-under-
        # answer guard (§1): the old blind-Right loop would submit a skipped part BLANK.
        n_all = min(n_parts, max_parts)
        pre_missing = [pi for pi in range(n_all)
                       if not ((by_part.get(pi) or {}).get("ns")
                               or (by_part.get(pi) or {}).get("text"))]
        if pre_missing:
            return False, {"reason": "parts_unanswered", "phase": "pre_submit",
                           "parts": pre_missing}

        for pi in range(n_all):
            # Navigate to the EXPECTED part (Right/Left as needed), NOT a blind advance —
            # tolerates a single-select auto-advance instead of double-advancing past it.
            ok, info = _nav_to_part(pi, read_fn=read_fn, key_fn=key_fn, settle_s=settle_s)
            if not ok:
                return False, {**info, "phase": f"nav_part{pi}"}
            menu = read_fn()
            if not isinstance(menu, dict):
                return False, {"reason": "menu_gone", "phase": f"part{pi}"}
            ans = by_part.get(pi) or {}
            text = ans.get("text")
            ft_n = _part_free_text_n(menu)           # from the fresh current-part read
            want = set(ans.get("ns") or [])
            want.discard(ft_n)                       # free-text is injected, never toggled
            # (1)-(4) FREE-TEXT FIRST: cursor-nav -> focus-verify -> type -> close +
            # positive close-proof, BEFORE any toggle digit (else the digit is typed
            # into an open field — the DEC-1787556508 hazard).
            if text:
                if ft_n is None:
                    return False, {"reason": "no_free_text_option", "phase": f"text{pi}"}
                ok, info = _inject_part_free_text(
                    ft_n, text, read_fn=read_fn, key_fn=key_fn,
                    type_fn=type_fn, settle_s=settle_s)
                if not ok:
                    info["phase"] = f"freetext{pi}"
                    return False, info
            # (5) preset delta toggles — ONLY after the field is proven closed.
            menu = read_fn()
            if not isinstance(menu, dict):
                return False, {"reason": "menu_gone", "phase": f"togglepart{pi}"}
            have = _part_current_checked(menu)
            have.discard(ft_n)                       # never toggle the free-text row off
            for n in sorted(want ^ have):            # DELTA toggles only
                if not key_fn(n):
                    return False, {"reason": "send_failed", "phase": f"toggle{pi}"}
                if settle_s:
                    _time.sleep(settle_s)
            # NO blind Right here — the next iteration's _nav_to_part positions us,
            # absorbing a single-select auto-advance (the double-advance root cause).

        # ALL-PARTS-ANSWERED is guarded on TWO legs (§1.1b Stage-2, bar-3 ruling):
        #  (i) PRE-FLIGHT (above): the batch must answer every part (zero keypress) +
        #      identity-verified _nav_to_part VISITED + applied every part — the
        #      double-advance can no longer silently skip one.
        #  (ii) CONFIRM-TIME PANE-TRUTH assertion (below): the positive check at the
        #      ONLY race-free point. A per-part pane RE-READ was rejected (it fail-closes
        #      on unreflected just-applied toggles = false-block a valid submit). Instead,
        #      at the confirm/Review screen — one read, where every part rendered its
        #      state long ago so reflection timing is a non-issue — read the tab bar's
        #      per-part ☒/☐ markers. ANY question-tab still ☐ => a keypress silently
        #      didn't land => fail-closed, do NOT Submit. FAIL-OPEN on marker ambiguity
        #      (a tab with no recognizable marker => answered:None => not asserted), so a
        #      capture variant that doesn't render markers degrades to leg (i), never a
        #      false block.
        ok, info = _nav_to_confirm(read_fn=read_fn, key_fn=key_fn, settle_s=settle_s)
        if not ok:
            return False, {**info, "phase": "nav_confirm"}

        _cmenu = read_fn()
        if isinstance(_cmenu, dict):
            _ts = _cmenu.get("tab_state")
            if isinstance(_ts, list) and _ts:
                # explicit ☐ only (answered is False); answered None = ambiguous = fail-open.
                unanswered = [i for i, t in enumerate(_ts)
                              if isinstance(t, dict) and t.get("answered") is False]
                if unanswered:
                    return False, {"reason": "parts_unanswered", "phase": "confirm",
                                   "parts": unanswered}

        # Submit tab: Enter through the confirm chain, DRIVEN BY on-screen state
        # (verify the menu is gone; the "Ready to submit? -> 1. Submit answers"
        # extra confirm is variant-dependent, so drive by what's on screen, not a
        # fixed count). Retry once, honest failure with the phase it died in.
        # PRECONDITION (congruence DEC-1787565310, AGY leg): only start driving Enter
        # once a submit control is on screen. If the menu is already gone -> submitted;
        # if it's still a live NON-confirm part (e.g. Right failed to advance) ->
        # fail-closed, NEVER blind-Enter into a checkbox part.
        _pre = read_fn()
        if _pre is None:
            return True, {"submitted": True, "attempts": 0}
        if isinstance(_pre, dict) and not _menu_is_confirm(_pre):
            return False, {"reason": "not_on_submit_tab", "phase": "pre_submit"}
        for attempt in range(1, 4):
            menu = read_fn()
            if not isinstance(menu, dict):
                return True, {"submitted": True, "attempts": attempt}
            if not key_fn("Enter"):
                return False, {"reason": "send_failed", "phase": "submit"}
            if settle_s:
                _time.sleep(settle_s)
        if not isinstance(read_fn(), dict):
            return True, {"submitted": True, "attempts": 3}
        return False, {"reason": "unverified_submit", "phase": "submit"}

    with _session_send_lock(session):
        return _run()


# --- condition-6: DURABLE-FIRST multi-part SUBMIT (Bug-3 answer-loss fix) ------
# DEC-1787700374. The live incident (2026-08-30): the operator answered a multi-part AUQ,
# the client POSTed the batch into the LIVE PANE (menu_batch_submit), the pane
# menu had already closed, the submit 409'd `menu_gone`, and the ENTIRE batch of
# answers evaporated with NO durable fallback. Plain approvals survive because
# they post to the durable approval_requests ledger; multi-part menus did not.
#
# Fix: persist the answer batch onto the durable ledger row FIRST (validated
# against the condition-1 hydrated parts[]), THEN best-effort pane replay. A
# `menu_gone` AFTER persist = answer RECORDED + surfaced for delivery, returned
# as SUCCESS-WITH-NOTICE, never loss. Durable persist is NOT gated by the arm
# (dry-run + durable-persist ship before MENU_MULTIPART_SUBMIT_ARMED); only the
# live-pane replay is armed.

def _validate_batch_answer(row, answers):
    """Validate a multi-part answer batch against the row's CONDITION-1 HYDRATED
    parts[]. Returns an error string, or None if the batch is answerable + would
    persist a COMPLETE answer. Fail-closed: a batch that cannot be validated
    against a fully-walked parts[] is REFUSED (never a half-write onto a
    part-0-only capture — that is exactly the answer-loss / HTTP-400 class
    condition-1 exists to prevent)."""
    if not isinstance(row, dict) or row.get("kind") != "menu":
        return "not a menu row"
    menu = _menu(row) or {}
    parts = menu.get("parts")
    if not menu.get("walk_complete") or not isinstance(parts, list) or not parts:
        return "menu_not_hydrated"                      # can't validate a batch yet
    if not isinstance(answers, list) or not answers:
        return "answers[] required"
    by_index = {}
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("index"), int):
            by_index[p["index"]] = p
    part_count = menu.get("part_count") or len(by_index)
    answered = set()
    for a in answers:
        if not isinstance(a, dict):
            return "each answer must be an object"
        pi = a.get("part")
        if not isinstance(pi, int) or pi not in by_index:
            return f"answer references unknown part {pi!r}"
        part = by_index[pi]
        valid_ns = {str(o.get("n")) for o in (part.get("options") or [])
                    if isinstance(o, dict)}
        ns = a.get("ns") or []
        if not isinstance(ns, list):
            return f"part {pi}: ns must be a list"
        for n in ns:
            if str(n) not in valid_ns:
                return f"part {pi}: option {n!r} is not a captured option"
        has_text = bool(a.get("text"))
        if not ns and not has_text:
            return f"part {pi}: no option selected and no own-words text"
        answered.add(pi)
    # COMPLETE-answer guard (mirrors menu_batch_submit's pre-flight): every part
    # must be answered, else the durable record would be a silent under-answer.
    missing = [i for i in range(part_count) if i not in answered]
    if missing:
        return f"parts unanswered: {missing}"
    return None


def durable_first_batch_submit(session, answers, *, store, armed=False,
                               submit_fn=None, resume_fn=None,
                               surface=None, answered_by=None):
    """condition-6 orchestrator. Persist the multi-part answer batch to the
    durable approval_requests row FIRST, THEN best-effort live-pane replay.

    Returns (ok, info). ok=False (answer NOT persisted — an honest client-fixable
    refusal, never a silent drop): no_durable_row | batch_validation. ok=True =
    the answer is DURABLE (armed) or a dry-run plan (unarmed); info distinguishes:
      - delivered=True                        pane replay landed it live (armed).
      - delivered=False, delivery=<reason>    armed pane replay could not deliver
                                              (menu_gone/etc) -> SUCCESS-WITH-
                                              NOTICE + a durable delivery fallback
                                              (resume_fn) was fired. NEVER loss.
      - durable=False, dry_run=True           UNARMED: pure dry-run passthrough,
                                              nothing persisted, nothing pressed
                                              (byte-identical to pre-condition-6).

    ARM DISCIPLINE (D3/F11-safe): the durable PERSIST + the fallback digest inject
    engage ONLY on an ARMED submit. Unarmed, the AUQ menu is still on the operator's pane
    (he is looking at it) — persisting-and-clearing it or injecting a digest into
    a pane showing a menu would violate D3/F11 and silently clear an undelivered
    card. So unarmed is a pure dry-run; the arm (MENU_MULTIPART_SUBMIT_ARMED)
    covers the live submit, exactly as today. Seams injectable for hermetic tests.
    """
    submit_fn = submit_fn or menu_batch_submit
    if resume_fn is None:
        import approval_resume
        resume_fn = lambda row, ans: approval_resume.fire_menu_batch_resume(
            row, ans, store=store)

    # UNARMED -> pure dry-run passthrough. No store touch, no clear, no inject:
    # the menu is still live on the operator's pane; the durable-first machinery engages
    # only when a REAL (armed) submit is being attempted.
    if not armed:
        ok, sinfo = submit_fn(session, answers=answers, armed=False)
        if ok:
            # BACK-COMPAT (multipart-menu-client-dev msg_c103f0c9): fielded
            # clients (156/157/160) key dry-run on `ok && !armed && would_submit`.
            # Pass the submit_fn result THROUGH (preserving would_submit + plan)
            # so a dry-run never falls into the plain-ok branch that would clear
            # the draft + resolve the card = phantom answer. Additive fields on top.
            return True, {**sinfo, "durable": False, "delivered": False,
                          "dry_run": True, "id": None}
        return False, {"multipart": True, **sinfo}

    # 1) locate the durable anchor (store-but-mark row, condition-1 hydrated).
    row = store.pending_menu_row_for_session(session)
    if row is None:
        # No pending anchor: either already answered (idempotent client retry) or
        # never bridged. If the newest menu row for this session is already a
        # persisted batch, treat as already-durable (answer-once) — NOT loss.
        latest = store.latest_menu_row_for_session(session)
        if latest is not None and latest.get("answer") == "batch":
            return True, {"durable": True, "already": True, "delivered": False,
                          "id": latest["id"]}
        # Else honest failure — the client keeps its draft
        # (multipart-menu-client-dev C1-C3), NEVER a pane-only lossy submit.
        return False, {"reason": "no_durable_row", "session": session}

    # 2) validate the batch against the hydrated parts[] BEFORE any write.
    err = _validate_batch_answer(row, answers)
    if err:
        return False, {"reason": "batch_validation", "detail": err, "id": row["id"]}

    # 3) DURABLE-FIRST: persist the batch onto the row (answer-once). THIS is the
    #    no-loss point — everything after is best-effort delivery.
    applied = store.record_batch_answer(row["id"], answers,
                                        surface=surface, answered_by=answered_by)
    if not applied:
        # a racing submit already persisted the batch -> already durable, not loss.
        return True, {"durable": True, "already": True, "delivered": False,
                      "id": row["id"]}
    row = store.get(row["id"])                           # refresh (now 'answered')

    # 4) best-effort live-pane replay (the preferred delivery when the menu is
    #    still up). The answer is ALREADY durable, so any failure below is a
    #    delivery problem, never answer loss.
    ok, sinfo = submit_fn(session, answers=answers, armed=True)
    if ok and not sinfo.get("dry_run"):
        # delivered live. Resolve any stale mirror dupes for the session (the row
        # we persisted is 'answered', not 'pending', so the resolver skips it).
        try:
            store.resolve_pending_menus_for_session(session)
        except Exception as e:  # noqa: BLE001 — delivery already durable
            print(f"[watch_gateway] durable_first: mirror-resolve failed "
                  f"({session}): {e}", file=sys.stderr)
        return True, {"durable": True, "delivered": True, "id": row["id"],
                      "replay": sinfo}

    # 5) pane replay could NOT deliver (menu_gone / unverified / send_failed).
    #    The answer is already DURABLE — surface it for delivery (durable msg +
    #    best-effort inject into from_agent's live head) and return SUCCESS-WITH-
    #    NOTICE so the client shows "answer landed, the agent will process it".
    delivery = sinfo.get("reason", "not_delivered")
    try:
        resume_fn(row, answers)
    except Exception as e:  # noqa: BLE001 — the answer is durable even if the
        # fallback delivery hiccups; the watchdog re-fires an 'answered' row.
        print(f"[watch_gateway] durable_first: fallback delivery failed "
              f"({row['id']}): {e}", file=sys.stderr)
    notice = ("Answer recorded \u2014 the live menu had already closed, so the "
              "agent will process your answer from the durable record.")
    return True, {"durable": True, "delivered": False, "delivery": delivery,
                  "notice": notice, "id": row["id"], "replay": sinfo}


def menu_submit(session, *, expect_question=None, dry_run=True,
                read_fn=None, key_fn=None, settle_s=0.8):
    """Commit a multi-tab AskUserQuestion by navigating to the Submit tab then
    Enter — NOT an Enter-in-place, which only toggles the highlighted checkbox
    (DEC-1786849794 §3z; learned unblocking the live gm 2026-08-16).

    DRY-RUN (default): navigate Right until the Submit tab is active, VERIFY it,
    then log 'would submit' and RETURN WITHOUT pressing Enter. Armed: Enter +
    verify the menu left the screen, retry once, then honest failure.

    FAIL-CLOSED (COND-2): commits ONLY on an unambiguous has_submit menu whose
    question still matches expect_question (never commit mid-answer on a menu that
    changed under us); any ambiguity — no submit tab, changed question, gone menu,
    submit tab unreachable within the bounded nav — returns a reason and touches
    NOTHING further. Pure over injected seams (read_fn -> parsed menu|None;
    key_fn(k) -> sends one key) so it's hermetically testable; the caller wires
    the real tmux/detector seams.

    LIVE-ARM GATE: dry_run defaults True; the armed path going into production is a
    SEPARATE the operator arm-go — never auto-fired."""
    import time as _time
    read_fn = read_fn or (lambda: _current_menu(session))
    key_fn = key_fn or (lambda k: _tmux("send-keys", "-t", session, k).returncode == 0)

    menu = read_fn()
    if not isinstance(menu, dict):
        return False, {"reason": "menu_gone"}
    if not menu.get("has_submit"):
        return False, {"reason": "no_submit_tab"}
    if expect_question and menu.get("question") != expect_question:
        return False, {"reason": "menu_changed"}

    # Navigate Right to the Submit tab, bounded by the tab count (+1 slack).
    max_nav = len(menu.get("tabs") or []) + 1
    rights = 0
    while not _menu_on_submit_tab(menu) and rights < max_nav:
        if not key_fn("Right"):
            return False, {"reason": "send_failed", "phase": "nav"}
        rights += 1
        if settle_s:
            _time.sleep(settle_s)
        menu = read_fn()
        if not isinstance(menu, dict):
            return False, {"reason": "menu_gone", "phase": "nav"}
    if not _menu_on_submit_tab(menu):
        return False, {"reason": "submit_tab_unreached", "nav_rights": rights}

    if dry_run:
        return True, {"would_submit": True, "dry_run": True, "nav_rights": rights}

    # Armed: press Enter on the Submit tab, verify the menu left the screen.
    for attempt in (1, 2):
        if not key_fn("Enter"):
            return False, {"reason": "send_failed", "phase": "submit"}
        if settle_s:
            _time.sleep(settle_s if attempt == 1 else settle_s * 2)
        if not isinstance(read_fn(), dict):
            return True, {"submitted": True, "nav_rights": rights, "attempts": attempt}
    return False, {"reason": "unverified_submit", "nav_rights": rights}


# ---------------------------------------------------------------------------
# S3.0 — Protected-sessions choke-point (Universal Decision-Surface Pipeline
# spec §3.0-1). A gm-owned registry names sessions automation must NEVER
# actuate (the operator-live surfaces, *-fieldtest, e2e-menu-scratch, gm). The gateway
# is the single auth'd actuation choke-point: an automation-origin caller
# (self-identified via the X-Actor header) targeting a protected session is
# refused server-side (403) on /agent-message, /agent-key, /agent-menu-capture
# — regardless of which script asks. Interactive agents driving their OWN pane,
# and the operator's app/watch answering a surface (no automation actor), are
# unaffected. SHIPS DISABLED (enabled:false) — gm confirms seed names + flips
# enabled after a clean shadow window. Fail-OPEN on a missing/broken registry:
# enforcement only ever ADDS 403s, and only when explicitly enabled.
# ---------------------------------------------------------------------------
PROTECTED_SESSIONS_PATH = ORCH_DIR / "state" / "protected-sessions.json"
_PROTECTED_CACHE = {"mtime": None, "cfg": None}
_PROTECTED_DEFAULT = {"enabled": False, "protected": [], "protected_prefixes": []}


def _protected_cfg():
    """Hot-reloaded protected-sessions registry (mtime-cached)."""
    try:
        mtime = PROTECTED_SESSIONS_PATH.stat().st_mtime
    except OSError:
        _PROTECTED_CACHE["mtime"] = None
        _PROTECTED_CACHE["cfg"] = None
        return dict(_PROTECTED_DEFAULT)
    if _PROTECTED_CACHE["mtime"] != mtime or _PROTECTED_CACHE["cfg"] is None:
        try:
            cfg = json.loads(PROTECTED_SESSIONS_PATH.read_text())
            if not isinstance(cfg, dict):
                cfg = dict(_PROTECTED_DEFAULT)
        except (OSError, ValueError):
            cfg = dict(_PROTECTED_DEFAULT)
        _PROTECTED_CACHE["mtime"] = mtime
        _PROTECTED_CACHE["cfg"] = cfg
    return _PROTECTED_CACHE["cfg"]


def _session_is_protected(session, cfg=None):
    cfg = cfg if cfg is not None else _protected_cfg()
    if session in (cfg.get("protected") or []):
        return True
    for pref in (cfg.get("protected_prefixes") or []):
        if pref and session.startswith(pref):
            return True
    return False


def _parse_actor(request):
    """(class, name) from the X-Actor header. Format '<class>:<name>' or
    '<class>'. class in {automation, interactive, shaw, app}. Absent -> (None,
    None). Only the 'automation' class is ever refused."""
    raw = (request.headers.get("X-Actor", "") or "").strip()
    if not raw:
        return None, None
    if ":" in raw:
        cls, name = raw.split(":", 1)
        return cls.strip().lower(), name.strip()
    return raw.lower(), None


def _protected_refusal(request, session):
    """None if allowed; a refusal dict (-> 403) if the protected-sessions
    choke-point blocks this actuation. OFF unless the registry has
    enabled:true. Only automation-origin callers are refused; interactive
    self-drive + the operator's app are unaffected."""
    cfg = _protected_cfg()
    if not cfg.get("enabled"):
        return None
    if not _session_is_protected(session, cfg):
        return None
    actor_class, actor_name = _parse_actor(request)
    if actor_class == "automation":
        return {"ok": False, "error": "protected session",
                "reason": "automation_actuation_refused",
                "session": session, "actor": actor_name or "automation"}
    return None


# S3.0-(b) — deploy-currency guard (spec §3.0-2). /health returns a code-version
# stamp (content-sha + mtime of the serving modules) so every arm-flip/respawn
# can verify the running code is current without proc-start-vs-mtime archaeology.
_SERVING_MODULES = ("watch_gateway.py", "agent-status.py", "menu_bridge.py",
                    "menu_bridge_core.py", "approval_notify.py")


def _code_version_stamp():
    here = Path(__file__).resolve().parent
    modules = {}
    combined = hashlib.sha256()
    for name in _SERVING_MODULES:
        p = here / name
        try:
            data = p.read_bytes()
            sha = hashlib.sha256(data).hexdigest()[:12]
            mtime = round(p.stat().st_mtime, 3)
        except OSError:
            sha, mtime = None, None
        modules[name] = {"sha": sha, "mtime": mtime}
        if sha:
            combined.update(sha.encode())
    return {"stamp": combined.hexdigest()[:12], "modules": modules}


# ---------------------------------------------------------------------------
# §9.6 P0 accept-and-hold (queue-slice) — the operator's live queueing regression fix.
# DEC-1786881813 CONSENSUS; gm-blessed. Today /agent-message 409s when the
# target is mid-turn, so the operator's message reads as "not sent". Instead: for a
# capability-signaling client (body accepts:["held"]), the PURE mid-turn
# 'working' refusal becomes a DURABLE-FIRST accept — persist the message as a
# msg_store row (row = truth) and return 200 held_for_boundary; the boundary/
# router path delivers it once, verified, at the target's turn end.
# BINDING CONTRACT (initiative-pm-architect-v2 + v14 msg_82b5ed49):
#   1. held != delivered — distinct {held:true, state:"held_for_boundary"} so the
#      client renders the amber 'queued' bubble, not a sent tick.
#   2. STRANDED/unsubmitted-composer 409 is PRESERVED with its composer-holds
#      payload — ONLY the pure working refusal (state=="working", no stranded
#      dict, no composer_text) converts; the composer-holds class never does.
#   3. MIXED-CLIENT GUARD — held returned ONLY when "held" ∈ accepts; a
#      non-signaling (pre-97) client keeps the full legacy 409 contract, so it
#      never reads a held 200 as 'not sent' -> no duplicate-delivery.
# ---------------------------------------------------------------------------
def _held_store():
    from msg_store import MessageStore
    return MessageStore()


def g3_accept_notify(row, *, enabled, hold_fn=None, log_fn=None):
    """G3 accept-notify (DEC-1787032722 Piece 4; universal-pipeline G3
    correction): EVERY accepted completion notifies from_agent — the 6ms
    silent-auto-ack killed rider intent (the operator's accept was a go-signal;
    resume_attempts:0, nothing delivered). One line, via the PROVEN held-row
    boundary lane (hold-if-working, bundle, once-only, stamp) — no parallel
    delivery mechanism, and structurally zero Telegram (gm's notify-only bind).

    enabled False (G3_ACCEPT_NOTIFY_ENABLED unset/!=1, the default) = SHADOW:
    log would-notify, write nothing. Returns the held row id or None."""
    _log = log_fn or (lambda m: print(m, file=sys.stderr))
    body = (f"[completion ACCEPTED {row['id']}] the operator accepted your completion: "
            f"'{row.get('question', '')}' — loop closed.")
    if row.get("answer_text"):
        # the exact silent-death class: intent riding the accept must surface
        body += f" Rider note from the operator: {row['answer_text']}"
    body = body.replace("\n", " ")          # ONE line — rides bundling cleanly
    if not enabled:
        _log(f"[G3] would-notify from_agent={row.get('from_agent')} "
             f"row={row.get('id')} (shadow, G3_ACCEPT_NOTIFY_ENABLED off)")
        return None
    return (hold_fn or _hold_for_boundary)(row["from_agent"], body,
                                           "operator", origin="g3-accept-notify")


def _hold_for_boundary(session, text, actor, origin="app"):
    """Durable-first: persist a held mid-turn send as a msg_store row (row =
    truth) for raw + verified delivery at the target's turn boundary. Returns the
    row id.

    ATTRIBUTION (owner_pm ruling msg_b972282f): from_agent = the human PRINCIPAL
    'operator' (matching tenant_id), NOT 'shaw-direct' (a registry session identity
    that would tangle resolve/lineage). the operator's chat is principal traffic, exempt
    from triage — a plain row + correct from_agent is the whole requirement; the
    boundary-inject attribution line reads as from the operator. A genuine agent driving
    via X-Actor still attributes to itself. The origin SURFACE (app/dashboard)
    rides metadata.source. deliver_raw -> RAW verified-inject (a direct chat
    message, not the router's [MSG] envelope); held_for_boundary tags it for the
    boundary-delivery path + the guard-(a) held-SLA escalation."""
    store = _held_store()
    return store.send(
        from_agent=actor or "operator",
        to_agent=session,
        type="held_message",
        body=text,
        priority="high",
        source="gateway_hold",
        metadata={"held_for_boundary": True, "deliver_raw": True,
                  "source": origin or "app", "accepts": ["held"]},
    )


async def handle_agent_message(request):
    """POST /agent-message {session, text, accepts?:["held"]} -> verified inject,
    or (mid-turn working + accepts:["held"]) a durable-first accept-and-hold."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    session = str(data.get("session", "")).strip()
    text = str(data.get("text", "")).strip()
    force = bool(data.get("force"))
    if not session or not text:
        return _json({"ok": False, "error": "session and text required"}, status=400)
    refusal = _protected_refusal(request, session)
    if refusal is not None:
        return _json(refusal, status=403)
    if session not in _tmux_session_names():
        return _json({"ok": False, "error": "no such session"}, status=404)

    accepts = data.get("accepts")
    held_supported = isinstance(accepts, list) and "held" in accepts

    import asyncio
    ok, info = await asyncio.get_event_loop().run_in_executor(
        None, verified_inject, session, text, force)
    if not ok and info.get("reason") == "busy":
        # §9.6 P0: ONLY the pure mid-turn 'working' refusal converts to hold.
        # A stranded dict or an unsubmitted composer_text keeps the legacy 409
        # (binding 2) so v14's composer-holds branch keeps rendering.
        mid_turn_working = (info.get("state") == "working"
                            and not info.get("stranded")
                            and not info.get("composer_text"))
        if held_supported and mid_turn_working:
            _cls, _name = _parse_actor(request)
            origin = str(data.get("source") or "app").strip() or "app"
            try:
                msg_id = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _hold_for_boundary(session, text, _name or _cls, origin))
            except Exception as e:  # noqa: BLE001 — never lie 'held' on a failed durable write
                return _json({"ok": False, "busy": True, "hold_failed": str(e), **info},
                             status=409)
            return _json({"ok": True, "delivered": False, "held": True,
                          "state": "held_for_boundary", "message_id": msg_id},
                         status=200)
        return _json({"ok": False, "busy": True, **info}, status=409)
    return _json({"ok": ok, "delivered": ok, **info},
                 status=200 if ok else 502)


async def handle_agent_interrupt(request):
    """POST /agent-interrupt {session} -> send Escape to a WORKING agent (the
    app's stop button). Gated the opposite way from inject: only allowed while
    the agent is mid-turn — Esc on an idle composer could clear typed text."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    session = str(data.get("session", "")).strip()
    if not session:
        return _json({"ok": False, "error": "session required"}, status=400)
    if session not in _tmux_session_names():
        return _json({"ok": False, "error": "no such session"}, status=404)
    st = _agent_status().get_agent_status(session)
    if st.get("state") not in ("working", "thinking"):
        return _json({"ok": False, "error": "not mid-turn",
                      "state": _STATE_MAP.get(st.get("state"), st.get("state"))}, status=409)
    r = _tmux("send-keys", "-t", session, "Escape")
    # Stop-button linger fix (the operator field report 2026-08-18): a user interrupt
    # never fires the CLI Stop hook, so the pane's hook event stays 'working'
    # and the detector reports "Active turn" for up to HOOK_WORKING_TTL_S
    # (300s) — holding queued/held mail. The interrupt source KNOWS the turn
    # ended: record an authoritative Stop/idle pane event. Safe: the hook
    # override only applies when the screen reads idle, so this can never mask
    # a turn the Escape failed to stop (screen-working always wins).
    hook_cleared = False
    if r.returncode == 0:
        try:
            hook_cleared = bool(_agent_status().mark_turn_interrupted(session))
        except Exception:  # noqa: BLE001 — never break the interrupt response
            hook_cleared = False
    return _json({"ok": r.returncode == 0, "interrupted": r.returncode == 0,
                  "hook_cleared": hook_cleared})


async def handle_agent_menu_capture(request):
    """POST /agent-menu-capture {session} -> the full multi-part menu payload.

    Runs the ACTIVE capture walk (DEC-1786866488 §A): presses Right to page a
    multi-part AskUserQuestion and returns parts[] + walk_complete. This is the
    SEPARATE explicit entrypoint (the passive detector never presses keys); the
    client calls it once to hydrate a multi-part surface. Read-only w.r.t. the
    agent's ANSWER (Right is navigation-only, never Enter/digit)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    session = str(data.get("session", "")).strip()
    if not session:
        return _json({"ok": False, "error": "session required"}, status=400)
    refusal = _protected_refusal(request, session)
    if refusal is not None:
        return _json(refusal, status=403)
    if session not in _tmux_session_names():
        return _json({"ok": False, "error": "no such session"}, status=404)
    import asyncio
    # §1.1(b) condition 4 — LEDGER CACHE READ: if a pending menu row for this
    # session is already fully hydrated (walk_complete:true), serve it WITHOUT
    # actuating the live pane. Solves in-flight coalescing: once the first walk
    # wrote back, every later request (any surface) reads the stored payload.
    store = ApprovalStore(); store.migrate()
    cached = await asyncio.get_event_loop().run_in_executor(
        None, lambda: store.cached_hydration(session))
    if isinstance(cached, dict):
        return _json({"ok": True, "session": session, "cached": True,
                      "parts": cached.get("parts") or [],
                      "part_count": cached.get("part_count", len(cached.get("parts") or [])),
                      "walk_complete": True})
    out = await asyncio.get_event_loop().run_in_executor(
        None, lambda: menu_capture_walk(session))
    # §1.1(b) condition 1 — LEDGER WRITE-BACK, STRICTLY gated on walk_complete:true.
    # A successful walk hydrates the pending row (full parts[] + walk_complete,
    # clears needs_hydration) so a later part-2 answer validates (AGY-caught 400)
    # and the ledger becomes the cache. An aborted/partial walk writes NOTHING.
    if isinstance(out, dict) and out.get("walk_complete") and out.get("parts"):
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: store.hydrate_menu(session, out))
        except Exception as e:  # noqa: BLE001 — write-back best-effort, never 500 the walk
            print(f"[watch_gateway] hydrate_menu write-back failed ({session}): {e}",
                  file=sys.stderr)
    return _json({"ok": True, "session": session, **out})


async def handle_agent_screen(request):
    """GET /agent-screen?session=&lines= -> ANSI-stripped pane tail + live state.
    The chat surface's honest 'reply view': what the agent's screen shows now."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    session = request.query.get("session", "").strip()
    if not session:
        return _json({"ok": False, "error": "session required"}, status=400)
    if session not in _tmux_session_names():
        return _json({"ok": False, "error": "no such session"}, status=404)
    try:
        lines = max(10, min(200, int(request.query.get("lines", 60))))
    except (TypeError, ValueError):
        lines = 60
    import asyncio
    loop = asyncio.get_event_loop()
    pane = await loop.run_in_executor(None, _capture_pane, session, lines)
    st = await loop.run_in_executor(None, _agent_status().get_agent_status, session)
    out = {"ok": True, "session": session,
           "screen": _strip_ansi(pane or ""),
           "state": _STATE_MAP.get(st.get("state"), st.get("state")),
           "activity": st.get("activity") or "",
           "context_pct": st.get("context_pct") or "",
           # Rich live-turn detail (spinner line telemetry) for the
           # app's status strip: "Catapulting · 2m 36s · ↑766 tokens".
           "elapsed": st.get("elapsed") or "",
           "tokens": st.get("tokens") or "",
           "tool": st.get("tool") or ""}
    # §2: pass pending_menu when the detector emits it (additive/nullable),
    # stamped with §1a input_kind so the chat card can render free_text/chat
    # affordances (the operator live-finding 07:27: un-stamped "Type something." armed
    # as direct → stray digits typed into the TUI field).
    if isinstance(st, dict) and st.get("pending_menu"):
        pm = stamp_input_kinds(dict(st["pending_menu"]))
        # Per-instance identity (DEC-1786771513): stamp instance_id on a PERMISSION
        # menu so the iOS chat card can .id(instance_id) and reset its @State
        # armed/selected when a new same-question prompt replaces an answered one
        # (the operator's pre-armed-option-2 symptom). Additive, permission-only; other
        # menu kinds are unaffected.
        if pm.get("kind") == "permission":
            _n, _digest = _stamp_instance(session, pm)
            pm["instance_id"] = f"{_digest}:{_n}"
        out["pending_menu"] = pm
    # Ghost-suggestion chip (spec 2026-08-15 §3a.2): surface the CLI-suggested
    # ghost as additive nullable `suggested_prompt`. get_agent_status only emits
    # composer_ghost when the FINAL state is idle AND zero typed text (fail-closed,
    # F2) -> a mid-turn or typed-composer screen yields no chip. Untruncated value
    # (F6): the client truncates for display only; /agent-suggest compares full.
    if isinstance(st, dict) and st.get("composer_ghost"):
        out["suggested_prompt"] = st["composer_ghost"]
    return _json(out)


API_URL = os.environ.get("ORCH_API_URL", "http://127.0.0.1:8888")

# ---------------------------------------------------------------------------
# P3: /upload — bearer-gated streaming proxy to the API's /api/uploads
# (SPEC_ios-attach §B). The API's multer route stays the single owner of
# storage/filenames/allowlist; the gateway adds the funnel-grade wrapper:
# auth, 25MB cap, magic-byte sniff, rate limit. Write-only (no read-back).
# ---------------------------------------------------------------------------

# 100MB (was 25): the operator 2026-08-10 — video glitch recordings ride /upload to any
# agent (a 6s capture is ~8MB; ~1min fits under 100MB). Matches the API multer cap.
UPLOAD_MAX_BYTES = int(os.environ.get("WATCH_GATEWAY_UPLOAD_MAX", str(100 * 1024 * 1024)))
UPLOAD_RATE_N, UPLOAD_RATE_WINDOW_S = 20, 300          # 20 uploads / 5 min
_upload_times: list = []

# Executable magic bytes / shebang — rejected regardless of extension (§B.2.3).
_EXEC_MAGICS = (b"\x7fELF", b"MZ", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"#!")


def _upload_rate_ok() -> bool:
    import time as _time
    now = _time.time()
    _upload_times[:] = [t for t in _upload_times if now - t < UPLOAD_RATE_WINDOW_S]
    if len(_upload_times) >= UPLOAD_RATE_N:
        return False
    _upload_times.append(now)
    return True


async def handle_upload(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    if not _upload_rate_ok():
        return _json({"ok": False, "error": "rate limited"}, status=429)

    import aiohttp
    try:
        reader = await request.multipart()
    except Exception:
        return _json({"ok": False, "error": "multipart body required"}, status=400)

    field = await reader.next()
    while field is not None and field.name != "file":
        field = await reader.next()
    if field is None:
        return _json({"ok": False, "error": "field 'file' required"}, status=400)

    filename = field.filename or "upload.bin"
    # Stream chunks, enforcing the cap + sniffing the head as we go.
    chunks, total, head = [], 0, b""
    while True:
        chunk = await field.read_chunk(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > UPLOAD_MAX_BYTES:
            return _json({"ok": False, "error": f"file exceeds {UPLOAD_MAX_BYTES // (1024*1024)}MB cap"},
                         status=413)
        if len(head) < 4096:
            head += chunk[: 4096 - len(head)]
        chunks.append(chunk)
    if total == 0:
        return _json({"ok": False, "error": "empty file"}, status=400)
    if any(head.startswith(m) for m in _EXEC_MAGICS):
        return _json({"ok": False, "error": "executable content rejected"}, status=400)

    data = aiohttp.FormData()
    data.add_field("file", b"".join(chunks), filename=filename,
                   content_type=field.headers.get("Content-Type", "application/octet-stream"))
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{API_URL}/api/uploads", data=data,
                              timeout=aiohttp.ClientTimeout(total=60)) as r:
                body = await r.json(content_type=None)
                if r.status == 200:
                    body["ok"] = True
                    body["size"] = body.get("size", total)
                return _json(body, status=r.status)
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": f"api unreachable: {e}"}, status=502)


# Watch push-to-talk: a PTT utterance is tiny (<=30 s of 16k mono AAC ~ 60 KB), so cap well below the
# 100 MB general upload cap. The core STT->brain->TTS turn runs on arturo-proxy (:5071) where the brain
# lives; this route is the thin AUTHENTICATED front door that forwards on loopback (same trust model as
# ARTURO_FINALIZE_URL). See services/arturo/SPEC_watch-ptt-endpoint.md.
PTT_MAX_BYTES = int(os.environ.get("WATCH_GATEWAY_PTT_MAX", str(1_000_000)))
ARTURO_PTT_URL = os.environ.get("ARTURO_PTT_URL", "http://127.0.0.1:5071/ptt")


async def handle_arturo_ptt(request):
    """POST /arturo/ptt — Bearer-authed thin proxy. Reads the multipart {audio, conversation_id,
    turn_id}, forwards to the loopback arturo-proxy /ptt, and returns its JSON verbatim (reply_text,
    stt_text, audio base64 mp3, or an honest error code)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    import aiohttp
    try:
        reader = await request.multipart()
    except Exception:
        return _json({"ok": False, "error": "multipart body required"}, status=400)

    audio_bytes = None
    audio_filename = "audio.m4a"
    audio_ct = "application/octet-stream"
    conversation_id = ""
    turn_id = ""
    field = await reader.next()
    while field is not None:
        if field.name == "audio":
            chunks, total = [], 0
            while True:
                chunk = await field.read_chunk(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > PTT_MAX_BYTES:
                    return _json({"ok": False, "error": "too_large"}, status=413)
                chunks.append(chunk)
            audio_bytes = b"".join(chunks)
            audio_filename = field.filename or "audio.m4a"
            audio_ct = field.headers.get("Content-Type", "application/octet-stream")
        elif field.name == "conversation_id":
            conversation_id = (await field.text())[:200]
        elif field.name == "turn_id":
            turn_id = (await field.text())[:200]
        field = await reader.next()

    if not audio_bytes:
        return _json({"ok": False, "error": "audio field required"}, status=400)

    data = aiohttp.FormData()
    data.add_field("audio", audio_bytes, filename=audio_filename, content_type=audio_ct)
    data.add_field("conversation_id", conversation_id)
    data.add_field("turn_id", turn_id)
    import asyncio
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(ARTURO_PTT_URL, data=data,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = await r.json(content_type=None)
                return _json(body, status=r.status)
    except asyncio.TimeoutError:
        log.warning("handle_arturo_ptt: upstream :5071/ptt timed out")
        return _json({"ok": False, "error": "timeout"}, status=504)
    except Exception as e:  # noqa: BLE001
        # log the detail; do NOT leak the raw exception string to the client
        log.error(f"handle_arturo_ptt: upstream unreachable: {e}")
        return _json({"ok": False, "error": "arturo unreachable"}, status=502)


# --- v2/(b) Watch-Arturo stream relay thin proxies (ARCHITECTURE-B.md; auth+forward ONLY,
# all relay logic lives on :5071 behind ARTURO_STREAM_RELAY; flag-off :5071 404s and these
# forward that 404 honestly). Same trust model as /arturo/ptt: Bearer here, loopback upstream.
ARTURO_PTT_STREAM_BASE = os.environ.get("ARTURO_PTT_STREAM_BASE", "http://127.0.0.1:5071/ptt/stream")
PTT_STREAM_CHUNK_MAX = 256 * 1024


async def _stream_forward(request, method, path, body=None, params=None, timeout_s=30):
    import aiohttp
    import asyncio
    headers = {"X-Conversation-Id": request.headers.get("X-Conversation-Id", "")}
    # item(1) surface stamp (DEC-1789341362142252): forward the phone's X-Surface: phone through to
    # :5071 so the relay journal attributes the call. Additive; the watch sends none (defaults watch).
    _surface = request.headers.get("X-Surface")
    if _surface:
        headers["X-Surface"] = _surface
    try:
        async with aiohttp.ClientSession() as s:
            async with s.request(method, f"{ARTURO_PTT_STREAM_BASE}{path}", data=body,
                                 params=params, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                out = await r.json(content_type=None)
                return _json(out, status=r.status)
    except asyncio.TimeoutError:
        return _json({"ok": False, "error": "timeout"}, status=504)
    except Exception as e:  # noqa: BLE001
        log.error(f"ptt/stream forward {path}: upstream unreachable: {e}")
        return _json({"ok": False, "error": "arturo unreachable"}, status=502)


async def handle_arturo_ptt_stream_audio(request):
    """POST /arturo/ptt/stream/audio — raw PCM delta body + X-Conversation-Id header."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    body = await request.read()
    if len(body) > PTT_STREAM_CHUNK_MAX:
        return _json({"ok": False, "error": "too_large"}, status=413)
    return await _stream_forward(request, "POST", "/audio", body=body, timeout_s=10)


async def handle_arturo_ptt_stream_events(request):
    """GET /arturo/ptt/stream/events?cursor=&wait= — long-poll cursor forward."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    params = {k: request.query.get(k, "") for k in ("cursor", "wait", "conversation_id") if k in request.query}
    return await _stream_forward(request, "GET", "/events", params=params, timeout_s=35)


async def handle_arturo_ptt_stream_end(request):
    """POST /arturo/ptt/stream/end — close the conversation's relay (drives server finalize)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    return await _stream_forward(request, "POST", "/end", timeout_s=15)


ARTURO_PTT_VENDOR_URL = os.environ.get("ARTURO_PTT_VENDOR_URL", "http://127.0.0.1:5071/ptt/vendor")


async def handle_arturo_ptt_vendor(request):
    """GET/PUT /arturo/ptt/vendor — runtime voice vendor (spec §4.1). Bearer here, loopback
    upstream; a PUT takes effect on the NEXT conversation, no restart."""
    import aiohttp
    import asyncio
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    body = await request.read() if request.method == "PUT" else None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.request(request.method, ARTURO_PTT_VENDOR_URL, data=body,
                                 headers={"Content-Type": "application/json", "X-Vendor-Source": "settings"},
                                 timeout=aiohttp.ClientTimeout(total=8)) as r:
                out = await r.json(content_type=None)
                return _json(out, status=r.status)
    except asyncio.TimeoutError:
        return _json({"ok": False, "error": "timeout"}, status=504)
    except Exception as e:  # noqa: BLE001
        log.error(f"ptt/vendor forward: upstream unreachable: {e}")
        return _json({"ok": False, "error": "arturo unreachable"}, status=502)


ARTURO_PTT_VOICE_BASE = os.environ.get("ARTURO_PTT_VOICE_BASE", "http://127.0.0.1:5071")


async def handle_arturo_ptt_voice(request):
    """GET /arturo/ptt/voices, GET/PUT /arturo/ptt/voice — the operator's Hume voice picker
    (contract msg_f5b57c9d). Bearer here, loopback upstream; a PUT takes effect on the
    NEXT conversation via session_settings.voice_id, no restart."""
    import aiohttp
    import asyncio
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    upstream = (ARTURO_PTT_VOICE_BASE + "/ptt/voices?" + request.query_string
                if request.path.endswith("/voices")
                else ARTURO_PTT_VOICE_BASE + "/ptt/voice")
    body = await request.read() if request.method == "PUT" else None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.request(request.method, upstream, data=body,
                                 headers={"Content-Type": "application/json",
                                          "X-Voice-Source": "settings"},
                                 timeout=aiohttp.ClientTimeout(total=20)) as r:
                out = await r.json(content_type=None)
                return _json(out, status=r.status)
    except asyncio.TimeoutError:
        return _json({"ok": False, "error": "timeout"}, status=504)
    except Exception as e:  # noqa: BLE001
        log.error(f"ptt/voice forward: upstream unreachable: {e}")
        return _json({"ok": False, "error": "arturo unreachable"}, status=502)


async def handle_transcript(request):
    """GET /transcript?agent=&limit= — thin proxy to the web API's committed
    transcript router (api/src/routes/chat-transcript.ts), so iOS renders the
    SAME chat grammar as web from the funnel without a second implementation."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    agent = request.query.get("agent", "").strip()
    if not agent:
        return _json({"ok": False, "error": "agent required"}, status=400)
    try:
        limit = max(1, min(500, int(request.query.get("limit", 120))))
    except (TypeError, ValueError):
        limit = 120
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{API_URL}/api/agents/{agent}/transcript",
                             params={"limit": str(limit)},
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                body = await r.json(content_type=None)
                return _json(body, status=r.status)
    except Exception as e:  # noqa: BLE001 — surface cleanly to the app
        return _json({"ok": False, "error": f"api unreachable: {e}"}, status=502)


# ---------------------------------------------------------------------------
# Arturo voice mode (spec docs/superpowers/specs/2026-08-09-arturo-voice-mode-
# design.md §3): thin bearer-gated reads of the proxy-written per-call JSONs.
# ---------------------------------------------------------------------------

VOICE_CALLS_DIR = ORCH_DIR / "state" / "voice-calls"
# call_id is proxy-generated ("vc_" + token). STRICT allowlist — the id becomes
# a filename, so this is the entire path-traversal defense (no /, ., %, NUL).
# \Z not $ — Python's $ also matches before a trailing newline (%0A smuggling).
_CALL_ID_RE = re.compile(r"^vc_[A-Za-z0-9_-]{4,64}\Z")
VOICE_RATE_N, VOICE_RATE_WINDOW_S = 120, 60      # app polls ~1/s while a call is live
_voice_times: list = []


def _voice_rate_ok() -> bool:
    import time as _time
    now = _time.time()
    _voice_times[:] = [t for t in _voice_times if now - t < VOICE_RATE_WINDOW_S]
    if len(_voice_times) >= VOICE_RATE_N:
        return False
    _voice_times.append(now)
    return True


async def handle_voice_call(request):
    """GET /voice-call?call_id= -> one per-call transcript JSON (pill caption,
    live-turns zone, work-view). Read-only; the proxy owns all writes."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    if not _voice_rate_ok():
        return _json({"ok": False, "error": "rate limited"}, status=429)
    call_id = request.query.get("call_id", "").strip()
    if not _CALL_ID_RE.match(call_id):
        return _json({"ok": False, "error": "invalid call_id"}, status=400)
    path = VOICE_CALLS_DIR / f"{call_id}.json"
    try:
        call = json.loads(path.read_text())
    except FileNotFoundError:
        return _json({"ok": False, "error": "not found"}, status=404)
    except (OSError, ValueError):
        # mid-write race on a live turn append -> tell the poller to just retry
        return _json({"ok": False, "error": "transient read failure"}, status=503)
    if not isinstance(call, dict):
        return _json({"ok": False, "error": "malformed call file"}, status=502)
    return _json({"ok": True, "call": call})


async def handle_active_voice_call(request):
    """GET /active-voice-call -> the single most-recently-appended status=='live' call, FULL turns;
    404 if none. BUG1 joint fix (build-164): the EL voice client can't map its ElevenLabs conv_id to
    the server vc_ journal, so it polls the wrong id and never sees role==tool turns. Arturo is
    single-user (the operator) -> exactly one live call, so the client fetches THE active call by identity.
    Reuses _live_voice_call's newest-live + crash-leftover stale-guard (via _select_newest_live) for
    determinism when >1 is live. Read-only; the proxy owns all writes."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    if not _voice_rate_ok():
        return _json({"ok": False, "error": "rate limited"}, status=429)
    live = _live_voice_call()
    call_id = (live or {}).get("call_id") or ""
    if not _CALL_ID_RE.match(call_id):
        return _json({"ok": False, "error": "no active voice call"}, status=404)
    path = VOICE_CALLS_DIR / f"{call_id}.json"
    try:
        call = json.loads(path.read_text())
    except FileNotFoundError:
        return _json({"ok": False, "error": "no active voice call"}, status=404)
    except (OSError, ValueError):
        # mid-write race on a live turn append -> tell the poller to just retry
        return _json({"ok": False, "error": "transient read failure"}, status=503)
    if not isinstance(call, dict):
        return _json({"ok": False, "error": "malformed call file"}, status=502)
    return _json({"ok": True, "call": call})


async def handle_voice_calls(request):
    """GET /voice-calls?active=1 -> id/status/started_at index (reconnect-on-
    relaunch). Lists metadata only — full turns come from /voice-call."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    if not _voice_rate_ok():
        return _json({"ok": False, "error": "rate limited"}, status=429)
    active_only = request.query.get("active") in ("1", "true")
    out = []
    try:
        files = sorted(VOICE_CALLS_DIR.glob("vc_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:50]
    except OSError:
        files = []
    for p in files:
        if not _CALL_ID_RE.match(p.stem):
            continue                      # ignore junk that snuck into the dir
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(c, dict):
            continue
        status = c.get("status")
        if active_only and status != "live":
            continue
        out.append({"call_id": c.get("call_id") or p.stem, "status": status,
                    "started_at": c.get("started_at"), "page": c.get("page"),
                    "turns": len(c.get("turns") or [])})
    return _json({"ok": True, "calls": out})


async def handle_presence(request):
    """POST /presence {viewing: "<agent>"|null} — the app reports which agent
    the operator is looking at RIGHT NOW (web detail view / iOS foregrounded agent), or
    null when he leaves the view. Writes the flat focus signal
    state/shaw-presence.json {"viewing","viewed_at"} that park-idle.py already
    consumes (shaw_viewing_agent, 8b view-protection) — the app-emit side that was
    the missing dependency. Also the trigger the client uses to scope its fast
    single-pane poll (/agent-screen) to the ONE viewed agent, so typing/interrupt
    status flips within ~1s WITHOUT a fleet-wide fast loop.

    MERGE, never overwrite: shaw-presence.json also carries telegram-written keys
    (status/last_telegram_message); we only set/clear viewing+viewed_at."""
    import json as _json_mod, tempfile, os as _os
    from datetime import datetime as _dt, timezone as _tz
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    viewing = data.get("viewing")
    if viewing is not None:
        viewing = str(viewing)[:200].strip() or None
    path = ORCH_DIR / "state" / "shaw-presence.json"
    try:
        cur = _json_mod.loads(path.read_text())
        if not isinstance(cur, dict):
            cur = {}
    except (OSError, ValueError):
        cur = {}
    now_iso = _dt.now(_tz.utc).isoformat()
    if viewing:
        cur["viewing"] = viewing
        cur["viewed_at"] = now_iso
    else:
        cur.pop("viewing", None)   # leaving the view -> clear focus (park-idle fails open)
        cur.pop("viewed_at", None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with _os.fdopen(fd, "w") as f:
            f.write(_json_mod.dumps(cur))
        _os.replace(tmp, str(path))
    except OSError as e:
        return _json({"ok": False, "error": f"write failed: {e}"}, status=500)
    return _json({"ok": True, "viewing": cur.get("viewing")})


async def handle_voice_call_ended(request):
    """POST /voice-call-ended — iOS posts the authoritative transcript the moment
    the operator ends the call.  Kills the 120s-silence guessing window.
    Journal written here is client-authoritative: status ended, source client."""
    import hashlib, time as _time
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)

    # --- required fields ---
    started_at = data.get("started_at")
    if not isinstance(started_at, (int, float)) or started_at <= 0:
        return _json({"ok": False, "error": "started_at required (epoch float > 0)"}, status=400)
    turns_raw = data.get("turns")
    if not isinstance(turns_raw, list):
        return _json({"ok": False, "error": "turns must be a list"}, status=400)

    # --- optional fields ---
    ended_at = data.get("ended_at")
    if not isinstance(ended_at, (int, float)):
        ended_at = _time.time()
    conv_id = data.get("conv_id")
    if conv_id is not None:
        conv_id = str(conv_id)[:200]

    # --- call_id ---
    if conv_id:
        call_id = "vc_client_" + hashlib.sha1(conv_id.encode()).hexdigest()[:12]
    else:
        call_id = "vc_client_" + hashlib.sha1(str(float(started_at)).encode()).hexdigest()[:12]

    # --- sanitize turns (cap 200) ---
    sanitized = []
    for raw in turns_raw[:200]:
        if not isinstance(raw, dict):
            continue
        role = raw.get("role")
        if role == "agent":
            role = "arturo"
        elif role != "user" and role != "arturo":
            role = "user"
        text = str(raw.get("text") or "")[:2000]
        ts = raw.get("ts")
        if not isinstance(ts, (int, float)):
            ts = None
        sanitized.append({"role": role, "text": text, "ts": ts})

    # --- build journal ---
    journal = {
        "call_id": call_id,
        "started_at": float(started_at),
        "ended_at": float(ended_at),
        "page": "client",
        "status": "ended",
        "source": "client",
        "turns": sanitized,
        "summary": f"Client transcript: {len(sanitized)} turns.",
    }
    if conv_id:
        journal["conv_id"] = conv_id

    # --- atomic write (idempotent: same call_id → overwrite is fine) ---
    VOICE_CALLS_DIR.mkdir(parents=True, exist_ok=True)
    dest = VOICE_CALLS_DIR / f"{call_id}.json"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(journal, indent=1))
    os.replace(tmp, dest)

    # --- trigger swap (jarvis-v5 seam): the client journal IS the end-of-call
    # signal — notify the Arturo proxy to finalize NOW instead of waiting for
    # its 120s-idle watchdog (~2.5 min the operator-felt lag + orphaned server shards).
    # If the proxy is down or fails, immediately fall back to direct gateway injection.
    notified = False
    try:
        notified = await _notify_arturo_finalize(conv_id, call_id)
    except Exception as _ne:
        log.warning(f"notify_arturo_finalize failed ({call_id}): {_ne}")
    
    if not notified:
        try:
            await _fallback_direct_inject_voice_call(call_id)
        except Exception as _fe:
            log.error(f"fallback direct inject failed ({call_id}): {_fe}")

    return _json({"ok": True, "call_id": call_id})


ARTURO_FINALIZE_URL = "http://127.0.0.1:5071/finalize-call"


async def _notify_arturo_finalize(conv_id, call_id):
    """Fire the proxy's loopback finalize hop (contract: {conv_id, call_id},
    best-effort, 3s cap). Loopback trust boundary — no bearer."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(ARTURO_FINALIZE_URL,
                          json={"conv_id": conv_id, "call_id": call_id}) as resp:
            return resp.status == 200


async def _fallback_direct_inject_voice_call(call_id):
    """Direct gateway fallback to inject full transcript & journal into VOICE_BRAIN_SESSION
    if arturo-proxy (:5071) is unreachable."""
    import asyncio
    loop = asyncio.get_running_loop()
    def _do():
        try:
            from services.arturo import endcall as _endcall
            dest = VOICE_CALLS_DIR / f"{call_id}.json"
            if not dest.exists():
                return
            d = json.loads(dest.read_text())
            if d.get("gm_injected") or d.get("gm_inject_suppressed"):
                return
            turns = d.get("turns") or []
            summary = d.get("summary") or f"Client transcript: {len(turns)} turns."
            marker = _endcall.build_marker(call_id, str(dest.resolve()))
            transcript = _endcall.build_full_transcript(turns)
            target_session = os.environ.get("VOICE_BRAIN_SESSION", "gm")
            ok, _ = _endcall.inject_to_gm(target_session, summary, marker, transcript=transcript)
            if ok:
                d["gm_injected"] = True
                dest.write_text(json.dumps(d, indent=1))
                log.info(f"fallback_direct_inject_voice_call: injected {call_id} to {target_session}")
        except Exception as e:
            log.error(f"fallback_direct_inject_voice_call error ({call_id}): {e}")
    await loop.run_in_executor(None, _do)


# ---------------------------------------------------------------------------
# §B OPTIONS-ANSWER: POST /agent-key — send a digit (1-9) into a pending menu.
# v1 keys: digits ONLY. Every other key (escape/tab/enter/arrows) -> 403.
# The hazardous-key policy table remains agent-state-truth's future build;
# this handler intentionally punts on non-digit keys until that ships.
# ---------------------------------------------------------------------------

ALLOWED_AGENT_KEYS = set("123456789")


async def handle_agent_key(request):
    """POST /agent-key {session, key, confirm?: bool}
    Two-phase digit-into-menu: confirm absent/false -> 428 needs_confirm;
    confirm true + menu present -> tmux send-keys the digit (no Enter —
    Claude Code menus commit on digit)."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)

    session = str(data.get("session", "")).strip()
    key = str(data.get("key", "")).strip()
    answer = str(data.get("answer", "")).strip()
    confirm = bool(data.get("confirm"))
    text = data.get("text")
    text = str(text).strip() if text is not None else None

    if not session:
        return _json({"ok": False, "error": "session required"}, status=400)
    refusal = _protected_refusal(request, session)
    if refusal is not None:
        return _json(refusal, status=403)
    if text is not None and len(text) > ANSWER_TEXT_MAX:
        return _json({"ok": False, "error": f"text exceeds {ANSWER_TEXT_MAX} chars"},
                     status=400)

    # RESPOND action (Contract B, DEC-1786882851): answer a permission prompt with
    # a free-text instruction ("No, and tell Claude what to do differently").
    # Two-phase like digits/submit; gated by PERM_RESPOND_ARMED (dry-run until
    # the operator arms). Distinct fail-closed reasons -> distinct HTTP codes (council Q3).
    if answer == "respond":
        if not text:
            return _json({"ok": False, "error": "text required for respond"}, status=400)
        if session not in _tmux_session_names():
            return _json({"ok": False, "error": "no such session"}, status=404)
        if not confirm:
            return _json({"ok": False, "needs_confirm": True,
                          "confirm_text": f"Send this instruction to {session}?"},
                         status=428)
        armed = os.environ.get("PERM_RESPOND_ARMED") == "1"
        import asyncio
        with _session_send_lock(session):            # no interleave w/ walk/suggest/key
            ok, info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: permission_respond(session, text, armed=armed))
        if ok:
            return _json({"ok": True, "armed": armed, **info})
        code = {"no_text_option": 422, "not_permission_prompt": 400,
                "menu_gone": 409}.get(info.get("reason"), 502)
        return _json({"ok": False, **info}, status=code)

    if not key:
        return _json({"ok": False, "error": "session and key required"}, status=400)

    # SUBMIT action (DEC-1786849794): commit a multi-tab AskUserQuestion by
    # navigating to the Submit tab then Enter. Two-phase like digits. DRY-RUN
    # unless the operator has armed it (MENU_SUBMIT_ARMED env) — the LIVE submit is a
    # SEPARATE the operator arm-go; until then the verb navigates + verifies + logs
    # 'would submit' but never presses Enter. Fail-closed inside menu_submit.
    if key == "submit":
        if session not in _tmux_session_names():
            return _json({"ok": False, "error": "no such session"}, status=404)
        if not confirm:
            return _json({"ok": False, "needs_confirm": True,
                          "confirm_text": f"Submit answers to {session}?"}, status=428)
        import asyncio
        answers = data.get("answers")
        # MULTI-PART batch (DEC-1786866488 §C): answers[] -> menu_batch_submit,
        # gated by its OWN arm flag (MENU_MULTIPART_SUBMIT_ARMED) — a moved
        # multi-part surface must NOT inherit the single-part MENU_SUBMIT_ARMED.
        if isinstance(answers, list):
            armed = os.environ.get("MENU_MULTIPART_SUBMIT_ARMED") == "1"
            # condition-6 (DEC-1787700374): DURABLE-FIRST multi-part submit —
            # persist the batch onto the durable approval_requests row BEFORE the
            # live-pane replay, so a `menu_gone` can never evaporate the operator's answers
            # (the Bug-3 answer-loss class). Requires a durable anchor (a store-but-
            # mark row); when absent (store-but-mark off) it returns no_durable_row
            # and we FALL BACK to the legacy pane-only path — byte-identical to
            # today, a strict no-regression during the store-but-mark rollout.
            def _durable_submit():
                st = ApprovalStore(); st.migrate()
                dok, dinfo = durable_first_batch_submit(
                    session, answers, store=st, armed=armed)
                if not dok and dinfo.get("reason") == "no_durable_row":
                    # LEGACY pane-only path (no durable anchor yet). Preserves the
                    # pre-condition-6 behavior exactly, including the F6-polish-1
                    # mirror-resolve on a verified armed submit.
                    lok, linfo = menu_batch_submit(session, answers=answers, armed=armed)
                    if lok and armed:
                        try:
                            n = st.resolve_pending_menus_for_session(session)
                            if n:
                                print(f"[watch_gateway] menu_batch_submit: resolved {n} "
                                      f"mirror row(s) for {session} on verified submit",
                                      file=sys.stderr)
                        except Exception as _re:
                            print(f"[watch_gateway] menu_batch_submit mirror-resolve "
                                  f"failed ({session}): {_re}", file=sys.stderr)
                    return "legacy", lok, linfo
                return "durable", dok, dinfo
            leg, ok, info = await asyncio.get_event_loop().run_in_executor(
                None, _durable_submit)
            if ok:
                # A menu_gone AFTER a durable persist is SUCCESS-WITH-NOTICE (200),
                # NOT a 409-loss — that is the Bug-3 fix. The client keys on
                # delivered/notice to show "answer landed, agent will process it".
                return _json({"ok": True, "armed": armed, "multipart": True, **info})
            if leg == "durable":
                # answer NOT persisted — an honest, client-fixable refusal.
                code = 422 if info.get("reason") == "batch_validation" else 409
            else:
                code = 409 if info.get("reason") == "menu_gone" else 502
            return _json({"ok": False, "multipart": True, **info}, status=code)
        # SINGLE-PART (DEC-1786849794): navigate to Submit tab then Enter, gated
        # by MENU_SUBMIT_ARMED.
        armed = os.environ.get("MENU_SUBMIT_ARMED") == "1"
        expect_q = text if text else None      # optional question-match guard
        ok, info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: menu_submit(session, expect_question=expect_q,
                                      dry_run=not armed))
        if ok:
            return _json({"ok": True, "armed": armed, **info})
        code = 409 if info.get("reason") in (
            "menu_gone", "menu_changed", "no_submit_tab",
            "submit_tab_unreached") else 502
        return _json({"ok": False, **info}, status=code)

    # v1: digits only. Non-digit keys are gated behind the hazardous-key
    # policy table — a future agent-state-truth build.
    if key not in ALLOWED_AGENT_KEYS:
        return _json({"ok": False,
                      "error": "key not enabled \u2014 awaiting keybar policy build"},
                     status=403)

    if session not in _tmux_session_names():
        return _json({"ok": False, "error": "no such session"}, status=404)

    # Fetch detector status (same seam handle_agent_screen uses) to check
    # for a pending_menu — the menu IS the state proof that a digit is safe.
    import asyncio
    st = await asyncio.get_event_loop().run_in_executor(
        None, _agent_status().get_agent_status, session)
    if not (isinstance(st, dict) and st.get("pending_menu")):
        return _json({"ok": False,
                      "error": "no pending menu on screen",
                      "state": _STATE_MAP.get(st.get("state"), st.get("state"))
                      if isinstance(st, dict) else "unknown"},
                     status=409)

    # Two-phase: first call (confirm absent/false) -> preview; second call
    # (confirm=true) -> actually send the key.
    if not confirm:
        return _json({"ok": False, "needs_confirm": True,
                      "confirm_text": f"Send {key} to {session}?"},
                     status=428)

    # §1a free-text on the CHAT lane: `text` present -> the target option must
    # be a free_text-class option, and the answer rides the verified
    # three-phase (digit -> literal text -> Enter) — never a bare digit that
    # strands an open TUI field (the operator live-finding 07:27).
    menu = stamp_input_kinds(dict(st["pending_menu"]))
    opt = next((o for o in (menu.get("options") or [])
                if isinstance(o, dict) and o.get("n") == key), None)
    if text:
        if not opt or opt.get("input_kind") != "free_text":
            return _json({"ok": False,
                          "error": "text is only valid for a free_text option "
                                   "(\u201cType something\u201d)"}, status=400)
        ok, info = await asyncio.get_event_loop().run_in_executor(
            None, menu_resume_free_text, session, key, text, menu.get("question"))
        if ok:
            return _json({"ok": True, "sent": key, "text_len": len(text),
                          **{k: v for k, v in info.items() if k == "attempts"}})
        if info.get("reason") == "menu_gone":
            return _json({"ok": False, "error": "menu no longer on screen"}, status=409)
        phase = f" (phase {info['phase']})" if info.get("phase") else ""
        return _json({"ok": False,
                      "error": f"free-text submit failed: {info.get('reason', 'unknown')}{phase}"},
                     status=502)

    # Send the digit — NO Enter for Claude Code (menus commit on digit);
    # with Enter for Gemini / Antigravity (menus commit on digit + Enter).
    # Held under the per-session send mutex (F3).
    with _session_send_lock(session):
        if _is_gemini_session(session):
            r = _tmux("send-keys", "-t", session, key, "Enter")
        else:
            r = _tmux("send-keys", "-t", session, key)
    if r.returncode != 0:
        return _json({"ok": False, "error": "tmux send-keys failed"}, status=502)
    # Per-instance identity (DEC-1786771513): an answer is a CONFIRMED clear.
    # Mark this permission prompt answered in-process so the next same-question
    # prompt bumps instance_n PROMPTLY (closes the <1s poll-gap hole that a
    # scan-only reset would miss). Permission-only; best-effort, never fatal.
    if menu.get("kind") == "permission":
        _mark_instance_answered(session, menu.get("question") or "")
    return _json({"ok": True, "sent": key})


async def handle_agent_suggest(request):
    """POST /agent-suggest {session, suggestion_text} — accept the CLI ghost
    suggestion currently in `session`'s composer and submit it (spec §3b).

    Fail-closed: re-verifies idle + unchanged ghost + no typed text, then a
    two-phase Tab->verify->Enter with no-Enter-on-mismatch. Bearer-gated;
    gateway-owned send (the app NEVER raw send-keys). suggestion_text is the
    FULL untruncated value from /agent-screen (F6) — compare-only."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    session = str(data.get("session", "")).strip()
    suggestion_text = data.get("suggestion_text")
    suggestion_text = str(suggestion_text) if suggestion_text is not None else ""
    if not session or not suggestion_text.strip():
        return _json({"ok": False, "error": "session and suggestion_text required"},
                     status=400)
    if len(suggestion_text) > ANSWER_TEXT_MAX:
        return _json({"ok": False,
                      "error": f"suggestion_text exceeds {ANSWER_TEXT_MAX} chars"},
                     status=400)
    if session not in _tmux_session_names():
        return _json({"ok": False, "error": "no such session"}, status=404)
    import asyncio
    status, body = await asyncio.get_event_loop().run_in_executor(
        None, suggest_accept, session, suggestion_text)
    return _json(body, status=status)


async def handle_health(request):
    store = ApprovalStore(); store.migrate()
    return _json({"ok": True, "pending": len(store.pending_to_notify()),
                  "code_version": _code_version_stamp()})


# ---------------------------------------------------------------------------
# Active surface (voice-surface-context spec) — which OrchestraOS screen the operator
# has open. Written by the iOS app (later: web SPA), read by the arturo-proxy
# for the per-turn "SHAW'S SCREEN" readout + read_screen_context tool.
# ---------------------------------------------------------------------------

SURFACE_PATH = ORCH_DIR / "state" / "arturo" / "active-surface.json"
SURFACE_DEVICES = ("ios", "web", "watch")   # +watch (presence; gm-routed msg_8c52636a)
SURFACE_ROLES = ("base", "overlay", "sheet", "badge")
# Null-route defense (gm msg_ebcd9f5f (b)): a device reporting route:null (wrist-
# down resign-active, backgrounded) must not become `current` while another
# device has a NON-null route fresher than this. Mirrors
# services/arturo/surface.STALE_S (age > STALE_S is stale on the read side).
SURFACE_STALE_S = 120
SURFACE_MAX_LAYERS = 10
# Focus rule (composite-surface spec §2): top-down, first resolvable entity
# whose kind isn't a non-focus kind. Mirrors services/arturo/surface.pick_focus.
_SURFACE_NON_FOCUS_KINDS = {"voice", "arturo", "approval_count"}
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,80}$")


def _sanitize_layer(raw):
    """One stack layer, bounded. Returns the clean dict or None if unusable."""
    if not isinstance(raw, dict):
        return None
    try:
        z = int(raw.get("z", 0))
    except (TypeError, ValueError):
        z = 0
    role = raw.get("role")
    if role not in SURFACE_ROLES:
        role = "base"
    route = raw.get("route")
    route = str(route)[:200] if route is not None else None
    hint = raw.get("hint")
    hint = str(hint)[:300] if hint else None
    ent = raw.get("entity")
    entity = None
    if isinstance(ent, dict) and ent.get("kind"):
        eid = ent.get("id")
        entity = {"kind": str(ent["kind"])[:40],
                  "id": str(eid)[:120] if eid is not None else None}
    if route is None and entity is None and hint is None:
        return None
    return {"z": z, "role": role, "route": route, "entity": entity, "hint": hint}


def _surface_focused(stack):
    """The focused {kind, id} per the §2 rule, or None."""
    for layer in sorted(stack, key=lambda l: l.get("z", 0), reverse=True):
        ent = layer.get("entity") or {}
        kind = ent.get("kind")
        if kind and kind not in _SURFACE_NON_FOCUS_KINDS:
            return ent
    return None


def _surface_current(devices, now):
    """Pick `current` from the per-device slots. Freshest updated_at wins — EXCEPT
    a null-route freshest yields to the freshest device whose route is non-null
    and no older than SURFACE_STALE_S (null-route defense). A null-route device
    with no such live peer still wins as before (single device, all-null, stale
    peer) so `current` is never stranded."""
    ranked = sorted(devices.values(), key=lambda d: d.get("updated_at", 0), reverse=True)
    freshest = ranked[0]
    if freshest.get("route") is not None:
        return freshest
    for d in ranked[1:]:
        if d.get("route") is not None and (now - d.get("updated_at", 0)) <= SURFACE_STALE_S:
            return d
    return freshest


async def handle_surface_post(request):
    import hashlib
    import time
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    device = data.get("device")
    if device not in SURFACE_DEVICES:
        return _json({"ok": False, "error": "device must be ios|web|watch"}, status=400)

    raw_stack = data.get("stack")
    if isinstance(raw_stack, list) and raw_stack:
        stack = [l for l in (_sanitize_layer(r) for r in raw_stack[:SURFACE_MAX_LAYERS]) if l]
        stack.sort(key=lambda l: l["z"])
        focused = _surface_focused(stack)
        # Legacy readers (proxy _read_surface_line) keep seeing route/hint —
        # derived from the focused layer, else the base layer.
        src = None
        if focused:
            src = next((l for l in stack if l.get("entity") == focused), None)
        if src is None:
            src = stack[0] if stack else None
        route = src.get("route") if src else None
        hint = src.get("hint") if src else None
    else:
        # Legacy flat {route, hint} — validated as before, wrapped as one base layer.
        route = data.get("route")
        if route is not None:
            route = str(route)[:200]
            if not route.startswith("/"):
                return _json({"ok": False, "error": "route must start with /"}, status=400)
        hint = data.get("hint")
        hint = str(hint)[:300] if hint else None
        stack = ([{"z": 0, "role": "base", "route": route, "entity": None, "hint": hint}]
                 if route else [])
        focused = None

    now = time.time()
    try:
        doc = json.loads(SURFACE_PATH.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("devices"), dict):
            doc = {"devices": {}}
    except (OSError, ValueError):
        doc = {"devices": {}}
    doc["devices"][device] = {"route": route, "hint": hint, "device": device,
                              "stack": stack, "focused": focused, "updated_at": now}
    doc["current"] = _surface_current(doc["devices"], now)
    SURFACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SURFACE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    os.replace(tmp, SURFACE_PATH)

    # Surface-event sidecar (§4.2): while an Arturo call is live the app sends
    # the ElevenLabs conv_id; events append to the sidecar keyed like the
    # client journal (vc_client_<sha1[:12]>) — the mid-call buffer, folded into
    # the record at finalize. Bad conv_id -> surface still recorded, no sidecar.
    conv_id = data.get("conv_id")
    if conv_id and stack and _CONV_ID_RE.match(str(conv_id)):
        key = hashlib.sha1(str(conv_id).encode()).hexdigest()[:12]
        side = VOICE_CALLS_DIR / f"vc_client_{key}.surface.jsonl"
        try:
            VOICE_CALLS_DIR.mkdir(parents=True, exist_ok=True)
            with side.open("a") as fh:
                fh.write(json.dumps({"ts": now, "device": device,
                                     "focused": focused, "stack": stack}) + "\n")
        except OSError:
            pass  # sidecar is best-effort; SEE state already persisted
    return _json({"ok": True})


# ---------------------------------------------------------------------------
# SSE events (spec §7.3 P2) — push "something changed" signals so the app can
# refresh instantly while foregrounded instead of riding the 5s poll. The
# stream carries CHANGE DIGESTS only (pending ids + voice-call ids), never row
# data — clients refetch through the normal endpoints. Polling remains the
# backstop; this endpoint failing must never take anything down with it.
# ---------------------------------------------------------------------------

EVENTS_TICK_S = 2.0        # digest check cadence
EVENTS_KEEPALIVE_S = 15.0  # comment frame so funnel/NAT never idles us out


def _events_digest(store):
    """Cheap change fingerprint: pending approval ids + live voice-call ids.
    (Agent liveness intentionally excluded — the fleet scan is heavy and the
    Agents tab already has its own 5s cadence.)"""
    pend = ",".join(sorted(r["id"] for r in store.pending_to_notify()))
    # §3 SSE: a draft saved on the watch pings the phone — carry qnr draft_rev so
    # the digest changes on every draft write. KEPT IN ITS OWN SEGMENT so the
    # pending-approvals count (digest.split('|')[0]) stays approvals-only.
    qnrs = ""
    try:
        qstore = QuestionnaireStore(); qstore.migrate()
        qnrs = ",".join(f"{q['id']}:{q['status']}:{q['draft_rev']}"
                        for q in sorted(qstore.list_pending(), key=lambda r: r["id"]))
    except Exception:
        pass
    calls = []
    try:
        for p in sorted(VOICE_CALLS_DIR.glob("vc_*.json")):
            try:
                c = json.loads(p.read_text())
                calls.append(f"{p.stem}:{c.get('status')}:{len(c.get('turns') or [])}")
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return pend + "|" + ",".join(calls) + "|" + qnrs


async def handle_events(request):
    import asyncio, time
    from aiohttp import web
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })
    await resp.prepare(request)
    store = ApprovalStore(); store.migrate()
    last = None
    last_beat = 0.0
    try:
        while True:
            digest = await asyncio.get_event_loop().run_in_executor(None, _events_digest, store)
            now = time.time()
            if digest != last:
                last = digest
                pending_n = digest.split("|", 1)[0]
                n = len(pending_n.split(",")) if pending_n else 0
                await resp.write(
                    f"data: {json.dumps({'changed': True, 'pending': n, 'ts': now})}\n\n".encode())
            elif now - last_beat >= EVENTS_KEEPALIVE_S:
                await resp.write(b": keepalive\n\n")
                last_beat = now
            if digest != last or last_beat == 0.0:
                last_beat = now
            await asyncio.sleep(EVENTS_TICK_S)
    except (ConnectionResetError, asyncio.CancelledError):
        pass   # client went away — normal lifecycle, not an error
    return resp


# ---------------------------------------------------------------------------
# Questionnaires (native questionnaire system spec §3) — the CONTAINER of N
# grouped questions. Fully additive: approvals/menus/completions untouched.
# ---------------------------------------------------------------------------

_RISK_SCORE = {"high": 40, "medium": 30, "low": 20}
_REVERSIBILITY_SCORE = {"irreversible": 15, "hard": 8}


def _priority_score(row):
    """Server-side priority_score (spec §3.1, R8) shared by ALL kinds — the ONE
    brain replacing the app's client-side triageRank:
      urgency*30 + risk{high:40,medium:30,low:20,none:10}
      + reversibility{irreversible:+15,hard:+8}
      + kind{menu:+5,questionnaire:+5}   (a parked agent is blocked-waiting)
      + min(15, age_hours/6)             (slow age boost — old items surface, never expire)
    Ties -> oldest first (the sort key, not the score)."""
    from datetime import datetime as _dt, timezone as _tz
    score = int(row.get("urgency") or 0) * 30
    score += _RISK_SCORE.get(row.get("risk_level"), 10)
    score += _REVERSIBILITY_SCORE.get(row.get("reversibility"), 0)
    if row.get("kind") in ("menu", "questionnaire", "permission"):
        score += 5
    try:
        age_s = (_dt.now(_tz.utc) - _dt.fromisoformat(row["created_at"])).total_seconds()
        age_h = age_s / 3600.0
        score += min(15, age_h / 6)
        # Interactive Recency Elevator: fresh cards created in the last 2h jump to the
        # front of the operator's Watch/iPhone queue (+100) so real-time turn actions are never
        # occluded by stale days-old questionnaires or backlog items.
        if age_s < 7200:
            score += 100
    except (ValueError, TypeError, KeyError):
        pass
    # §2 snooze floor (SERVER spec, the operator Q2): a snoozed human_task sinks BELOW
    # every fresh card but never disappears (stays status='pending'). This is the
    # ONE ordering brain honored on phone AND watch. Additive & behavior-
    # preserving: fires ONLY for a human_task whose snoozed_until is still in the
    # future; every other row/kind scores exactly as today.
    su = row.get("snoozed_until")
    if row.get("kind") == "human_task" and su:
        try:
            if _dt.fromisoformat(su) > _dt.now(_tz.utc):
                score -= 1000   # sink below fresh cards, stays visible
        except (ValueError, TypeError):
            pass
    return round(score, 2)


def _qnr_pseudo_row(row):
    """Additive pseudo-row for /pending-approvals (spec §4.1) — the ONE card
    appears in the existing queue; old clients ignore the unknown kind."""
    return {
        "id": row["id"], "kind": "questionnaire",
        "from_agent": row["from_agent"], "question": row["title"],
        "options": [], "created_at": row["created_at"],
        "summary": row.get("summary"), "feature": row.get("feature"),
        "questionnaire": {"question_count": row["question_count"],
                          "answered_count": row.get("answered_count", 0),
                          "draft_rev": row["draft_rev"]},
        "priority_score": _priority_score({**row, "kind": "questionnaire"}),
    }


def _validate_qnr_answer(question, option_n, answer_text):
    """Per-question §1a validation of ONE provided answer (draft or submit delta).
    Partial/empty is fine (draft) — this only rejects INVALID shapes. Error str/None."""
    if answer_text is not None and len(answer_text) > ANSWER_TEXT_MAX:
        return f"answer_text exceeds {ANSWER_TEXT_MAX} chars"
    if question.get("kind") == "free_text":
        if option_n is not None:
            return "free_text question takes no option_n"
        return None
    menu = question.get("menu") or {}
    opts = menu.get("options") or []
    if option_n is None:
        # the operator ruling 2026-08-14 (text-is-a-valid-answer): answer_text ALONE is a
        # complete answer on ANY question, regardless of the menu's option shapes.
        # Questionnaires are ledger-native (no TUI keypress to route through), so
        # no free_text-class option is required. (P0: Q7 text-only 400'd here.)
        return None
    if not isinstance(option_n, str) or not option_n.isdigit():
        return "option_n must be a stringified digit"
    opt = next((o for o in opts if o.get("n") == option_n), None)
    if opt is None:
        return f"option_n {option_n} does not match a captured option"
    ik = opt.get("input_kind") or "direct"
    if ik == "chat":
        return "chat options are excluded from questionnaires"
    if ik == "free_text" and not answer_text:
        return "answer_text required for a free_text option"
    return None


def _validate_qnr_delta(questions, answers):
    """Validate every provided answer against its question. Error str/None."""
    qbyn = {q["n"]: q for q in questions}
    for n, a in (answers or {}).items():
        try:
            q = qbyn.get(int(n))
        except (TypeError, ValueError):
            return f"bad question index {n!r}"
        if not q:
            return f"no question {n}"
        opt = a.get("option_n")
        txt = a.get("answer_text")
        txt = str(txt).strip() if txt is not None else None
        err = _validate_qnr_answer(q, opt, txt)
        if err:
            return f"q{n}: {err}"
    return None


async def handle_questionnaires(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    store = QuestionnaireStore(); store.migrate()
    rows = store.list_pending()
    for r in rows:
        r["priority_score"] = _priority_score({**r, "kind": "questionnaire"})
    rows.sort(key=lambda r: (-r["priority_score"], r["created_at"]))
    out = [{"id": r["id"], "title": r["title"], "from_agent": r["from_agent"],
            "question_count": r["question_count"], "answered_count": r["answered_count"],
            "draft_rev": r["draft_rev"], "created_at": r["created_at"],
            "feature": r.get("feature"), "urgency": r["urgency"],
            "priority_score": r["priority_score"]} for r in rows]
    return _json({"ok": True, "questionnaires": out})


async def handle_questionnaire_detail(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    store = QuestionnaireStore(); store.migrate()
    d = store.get(request.match_info["id"])
    if not d:
        return _json({"ok": False, "error": "not found"}, status=404)
    return _json({"ok": True, "questionnaire": d})


async def handle_questionnaire_draft(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    qid = request.match_info["id"]
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "error": "bad json"}, status=400)
    answers = data.get("answers") or {}
    store = QuestionnaireStore(); store.migrate()
    d = store.get(qid)
    if not d:
        return _json({"ok": False, "error": "not found"}, status=404)
    if d["status"] != "pending":
        return _json({"ok": False, "error": f"questionnaire already {d['status']}"}, status=400)
    err = _validate_qnr_delta(d["questions"], answers)
    if err:
        return _json({"ok": False, "error": err}, status=400)
    r = store.save_draft(qid, answers, base_rev=data.get("base_rev"),
                         surface=data.get("surface"))
    if r is None:
        return _json({"ok": False, "error": "not found"}, status=404)
    if r.get("error"):
        return _json({"ok": False, "error": r["error"]}, status=400)
    return _json({"ok": True, "draft_rev": r["draft_rev"], "conflicts": r["conflicts"]})


async def handle_questionnaire_submit(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    qid = request.match_info["id"]
    try:
        data = await request.json()
    except Exception:
        data = {}
    answers = (data or {}).get("answers") or {}
    store = QuestionnaireStore(); store.migrate()
    d = store.get(qid)
    if not d:
        return _json({"ok": False, "error": "not found"}, status=404)
    err = _validate_qnr_delta(d["questions"], answers)
    if err:
        return _json({"ok": False, "error": err}, status=400)
    r = store.submit(qid, answers, surface=(data or {}).get("surface"))
    if r is None:
        return _json({"ok": False, "error": "not found"}, status=404)
    if r.get("applied"):
        try:
            import asyncio
            row = store.get(qid)
            await asyncio.get_event_loop().run_in_executor(
                None, questionnaire_resume.deliver, row, store)
        except Exception as e:  # noqa: BLE001 — answers are durable even if resume hiccups
            print(f"[watch_gateway] qnr deliver failed for {qid}: {e}", file=sys.stderr)
        return _json({"ok": True, "applied": True, "id": qid})
    body = {"ok": True, "applied": False, "id": qid}
    if "missing" in r:
        body["missing"] = r["missing"]
    if "status" in r:
        body["status"] = r["status"]
    return _json(body)


async def handle_questionnaire_discard(request):
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    qid = request.match_info["id"]
    store = QuestionnaireStore(); store.migrate()
    d = store.get(qid)
    if not d:
        return _json({"ok": False, "error": "not found"}, status=404)
    ok = store.discard(qid)
    if ok:
        try:
            import asyncio
            row = store.get(qid)
            await asyncio.get_event_loop().run_in_executor(
                None, questionnaire_resume.deliver_discarded, row, store)
        except Exception as e:  # noqa: BLE001
            print(f"[watch_gateway] qnr discard-notify failed for {qid}: {e}", file=sys.stderr)
    return _json({"ok": True, "applied": ok, "id": qid})


async def handle_approval_discard(request):
    """R8: guarded pending->discarded for approvals/decisions (the questionnaire
    discard's sibling). Saves to history + fires ONE honest notify to the emitter
    so a parked agent never waits forever on a dismissed ask."""
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    rid = request.match_info["id"]
    store = ApprovalStore(); store.migrate()
    row = store.get(rid)
    if not row:
        return _json({"ok": False, "error": "not found"}, status=404)
    ok = store.discard(rid)
    if ok:
        try:
            import asyncio
            row = store.get(rid)
            await asyncio.get_event_loop().run_in_executor(
                None, approval_resume.deliver_discarded, row, store)
        except Exception as e:  # noqa: BLE001 — the discard is durable even if the notify hiccups
            print(f"[watch_gateway] approval discard-notify failed for {rid}: {e}", file=sys.stderr)
    return _json({"ok": True, "applied": ok, "id": rid})


def _live_token_from_request(request) -> str:
    """Extract the /live bearer: `Authorization: Bearer` is preferred (what
    api/src/routes/voice-live.ts's browser proxy sends, security review
    commit 4840bf6247 — the token must never sit in a URL/query string that
    could land in an access log). The legacy `?token=` query param still
    works additively (the iOS client may depend on it; removal is a later
    gm/OB decision after verifying that client) but logs a one-line
    deprecation notice whenever it's actually the path used."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    token = request.query.get("token") or ""
    if token:
        print("[watch_gateway] DEPRECATED: /live authenticated via ?token= query "
              "param — migrate the caller to Authorization: Bearer", file=sys.stderr)
    return token


async def handle_gemini_live(request):
    """Gemini 2.5 Flash Native Audio Multimodal Live WebSocket gateway.
    Streams 16kHz PCM audio up from iOS mic, 24kHz PCM down to speaker.
    Supports voice selection (?voice=Fenrir|Charon|Puck|Orus|Aoede|Kore)."""
    from aiohttp import web
    token = _live_token_from_request(request)
    expected = gateway_token()
    if not token or not expected or not hmac.compare_digest(token, expected):
        return web.Response(status=401, text="unauthorized")

    voice = request.query.get("voice", "Fenrir")
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    try:
        live_session_cls = GeminiLiveSession
        if live_session_cls is None:
            from services.arturo.gemini_live_bridge import GeminiLiveSession as live_session_cls
        session = live_session_cls(ws, voice_name=voice)
        await session.run()
    except Exception as e:
        print(f"[watch_gateway] gemini live session error: {e}", file=sys.stderr)
    return ws


def build_app():
    from aiohttp import web
    # client_max_size MUST cover uploads — aiohttp's default is 1MB and would
    # silently 413 every photo (SPEC_ios-attach §B.1). Cap + small overhead.
    app = web.Application(client_max_size=UPLOAD_MAX_BYTES + 1024 * 1024)
    app.router.add_get("/pending-approvals", handle_pending)
    app.router.add_post("/approval-answers", handle_answer)
    app.router.add_get("/history", handle_history)
    app.router.add_get("/approvals/{id}", handle_approval_detail)
    app.router.add_post("/approvals/{id}/discard", handle_approval_discard)
    app.router.add_get("/agents", handle_agents)
    app.router.add_get("/projects", handle_projects)
    app.router.add_get("/people", handle_people)
    app.router.add_get("/people/{person_id}", handle_person_detail)
    app.router.add_get("/pipeline", handle_pipeline)
    app.router.add_get("/briefing", handle_briefing)
    app.router.add_get("/voice-memory", handle_voice_memory)
    app.router.add_post("/agent-message", handle_agent_message)
    app.router.add_post("/agent-interrupt", handle_agent_interrupt)
    app.router.add_post("/presence", handle_presence)
    app.router.add_post("/agent-key", handle_agent_key)
    app.router.add_post("/agent-suggest", handle_agent_suggest)
    app.router.add_post("/agent-menu-capture", handle_agent_menu_capture)
    app.router.add_get("/agent-screen", handle_agent_screen)
    app.router.add_get("/transcript", handle_transcript)
    app.router.add_post("/upload", handle_upload)
    app.router.add_post("/arturo/ptt", handle_arturo_ptt)
    app.router.add_post("/arturo/ptt/stream/audio", handle_arturo_ptt_stream_audio)
    app.router.add_get("/arturo/ptt/stream/events", handle_arturo_ptt_stream_events)
    app.router.add_post("/arturo/ptt/stream/end", handle_arturo_ptt_stream_end)
    app.router.add_get("/arturo/ptt/vendor", handle_arturo_ptt_vendor)
    app.router.add_put("/arturo/ptt/vendor", handle_arturo_ptt_vendor)
    app.router.add_get("/arturo/ptt/voices", handle_arturo_ptt_voice)
    app.router.add_get("/arturo/ptt/voice", handle_arturo_ptt_voice)
    app.router.add_put("/arturo/ptt/voice", handle_arturo_ptt_voice)
    app.router.add_get("/voice-call", handle_voice_call)
    app.router.add_get("/active-voice-call", handle_active_voice_call)
    app.router.add_get("/voice-calls", handle_voice_calls)
    app.router.add_post("/voice-call-ended", handle_voice_call_ended)
    app.router.add_get("/questionnaires", handle_questionnaires)
    app.router.add_get("/questionnaires/{id}", handle_questionnaire_detail)
    app.router.add_put("/questionnaires/{id}/draft", handle_questionnaire_draft)
    app.router.add_post("/questionnaires/{id}/submit", handle_questionnaire_submit)
    app.router.add_post("/questionnaires/{id}/discard", handle_questionnaire_discard)
    app.router.add_get("/events", handle_events)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/surface", handle_surface_post)
    app.router.add_get("/live", handle_gemini_live)
    app.router.add_post("/telemetry", handle_telemetry)
    app.router.add_get("/telemetry", handle_telemetry_index)
    app.router.add_get("/telemetry/{id}", handle_telemetry_view)
    return app



async def handle_telemetry(request):
    from aiohttp import web
    import hmac
    token = request.query.get("token") or ""
    expected = gateway_token()
    if not token or not expected or not hmac.compare_digest(token, expected):
        return web.Response(status=401, text="unauthorized")

    attempt_id = request.query.get("attempt_id")
    if not attempt_id:
        return web.Response(status=400, text="missing attempt_id")

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="invalid json")

    import json
    import os
    telemetry_dir = os.path.join(os.environ.get("ORCHESTRA_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "telemetry")
    os.makedirs(telemetry_dir, exist_ok=True)
    out_path = os.path.join(telemetry_dir, f"{attempt_id}_client.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

    return web.Response(status=200, text="ok")

def get_telemetry_attempts():
    import os, glob, datetime
    tdir = os.path.join(os.environ.get("ORCHESTRA_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "telemetry")
    os.makedirs(tdir, exist_ok=True)
    files = glob.glob(os.path.join(tdir, "*.json*"))
    attempt_times = {}
    for f in files:
        base = os.path.basename(f)
        if "_" in base:
            aid = base.split("_")[0]
            mtime = os.path.pathsep
            try:
                mtime = os.path.getmtime(f)
            except Exception:
                mtime = 0
            if aid not in attempt_times or mtime > attempt_times[aid]:
                attempt_times[aid] = mtime
    
    sorted_attempts = sorted(attempt_times.items(), key=lambda x: x[1], reverse=True)
    out = []
    for aid, mtime in sorted_attempts:
        dt = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        out.append((aid, dt))
    return out

def load_telemetry_events(attempt_id):
    import os, json
    tdir = os.path.join(os.environ.get("ORCHESTRA_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "telemetry")
    events = []
    
    client_file = os.path.join(tdir, f"{attempt_id}_client.json")
    if os.path.exists(client_file):
        try:
            with open(client_file) as f:
                c_events = json.load(f)
                for e in c_events:
                    e["source"] = "client"
                    events.append(e)
        except Exception:
            pass

    server_file = os.path.join(tdir, f"{attempt_id}_server.jsonl")
    if os.path.exists(server_file):
        try:
            with open(server_file) as f:
                for line in f:
                    e = json.loads(line)
                    e["source"] = "server"
                    events.append(e)
        except Exception:
            pass
            
    events.sort(key=lambda x: x.get("timestamp", 0))
    return events

async def handle_telemetry_index(request):
    from aiohttp import web
    import asyncio
    loop = asyncio.get_running_loop()
    attempts = await loop.run_in_executor(None, get_telemetry_attempts)
    html = "<html><head><style>body{font-family:sans-serif;margin:40px;} li{margin-bottom:10px;}</style></head><body>"
    html += "<h1>Telemetry Dashboard</h1><ul>"
    for a, dt in attempts:
        html += f'<li><a href="/telemetry/{a}">{a}</a> <span style="color:#888;font-size:0.9em;margin-left:10px;">({dt})</span></li>'
    html += "</ul></body></html>"
    return web.Response(text=html, content_type="text/html")

async def handle_telemetry_view(request):
    from aiohttp import web
    import json
    import asyncio
    attempt_id = request.match_info["id"]
    loop = asyncio.get_running_loop()
    events = await loop.run_in_executor(None, load_telemetry_events, attempt_id)
    
    events_json = json.dumps(events, indent=2).replace("</", "<\\/")
    
    style = """
    body{font-family:sans-serif;margin:40px;} 
    table{border-collapse:collapse;width:100%;} 
    th,td{border:1px solid #ddd;padding:8px;text-align:left;} 
    .client{background-color:#e8f4f8;} 
    .server{background-color:#f8e8f8;}
    .header-container { display: flex; justify-content: space-between; align-items: center; }
    #copy-btn { padding: 8px 16px; cursor: pointer; background: #007bff; color: white; border: none; border-radius: 4px; font-size: 14px; }
    #copy-btn:hover { background: #0056b3; }
    #toast { visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 4px; padding: 16px; position: fixed; z-index: 1; right: 40px; top: 40px; font-size: 14px; transition: visibility 0s, opacity 0.5s linear; opacity: 0; }
    #toast.show { visibility: visible; opacity: 1; }
    """
    
    script = """
    <script>
    function copyLogs() {
        const data = document.getElementById("events-data").textContent;
        navigator.clipboard.writeText(data).then(function() {
            const toast = document.getElementById("toast");
            toast.className = "show";
            setTimeout(function(){ toast.className = toast.className.replace("show", ""); }, 2000);
        });
    }
    </script>
    """
    
    html = f"<html><head><style>{style}</style>{script}</head><body>"
    html += f"<script id='events-data' type='application/json'>{events_json}</script>"
    html += f"<div class='header-container'><h1>Attempt: {attempt_id}</h1><button id='copy-btn' onclick='copyLogs()'>Copy All Logs</button></div>"
    html += "<div id='toast'>Logs copied to clipboard!</div>"
    html += '<table><tr><th>Timestamp</th><th>Source</th><th>Event</th><th>Details</th></tr>'
    
    for e in events:
        ts = e.get("timestamp", 0)
        src = e.get("source", "")
        evt = e.get("event", "")
        dtl = json.dumps(e.get("details", {}))
        
        cls = "client" if src == "client" else "server"
        html += f'<tr class="{cls}"><td>{ts}</td><td>{src}</td><td>{evt}</td><td>{dtl}</td></tr>'
        
    html += "</table><br><a href='/telemetry'>Back</a></body></html>"
    return web.Response(text=html, content_type="text/html")


def make_token() -> str:
    """Generate + persist a gateway token (chmod 600). Idempotent-safe: prints
    the existing token if one is already present."""
    existing = gateway_token()
    if existing:
        print(existing); return existing
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok)
    os.chmod(TOKEN_FILE, 0o600)
    print(tok)
    return tok


def main():
    if "--make-token" in sys.argv:
        make_token(); return
    if not gateway_token():
        print("[watch_gateway] refusing to start: no token "
              "(run: python3 watch_gateway.py --make-token)", file=sys.stderr)
        sys.exit(2)
    from aiohttp import web
    app = build_app()
    print(f"[watch_gateway] listening on http://{GATEWAY_HOST}:{GATEWAY_PORT}", file=sys.stderr)
    web.run_app(app, host=GATEWAY_HOST, port=GATEWAY_PORT, print=None)


if __name__ == "__main__":
    main()
