"""Turn a pending approval row into a self-hosted ntfy push with 3 id-bound action buttons.
Primary trigger: called inline by approval.py request (instant). Backstop: cron re-notify."""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_config import (NTFY_BASE, NTFY_APPROVALS_TOPIC, NTFY_ANSWERS_TOPIC,
                             ntfy_token, ESCALATE_REPEAT_MINUTES,
                             PHONE_TAILNET_HOST, PHONE_OFFTAILNET_THRESHOLD_S,
                             TAILSCALE_STATUS_TIMEOUT_S, ESCALATION_DIGEST_MAX_SINGLES)
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore

def _http_post(url, headers, data):
    req = urllib.request.Request(url, data=data.encode() if isinstance(data, str) else data,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status

def build_payload(row, token):
    """Build the JSON publish payload for ntfy's root endpoint.
    Action bodies are JSON strings embedded inside the JSON document — comma-safe,
    unlike the X-Actions header form which splits on commas and truncates JSON bodies."""
    answers_url = f"{NTFY_BASE}/{NTFY_ANSWERS_TOPIC}"
    base = {
        "topic": NTFY_APPROVALS_TOPIC,
        "title": f"Approval {row['id']} — {row['from_agent']}",
        "message": row["question"],
        "priority": 4,
    }
    # kind=menu: NO quick-action buttons (orchestra-builder ruling msg_0318716b,
    # mirrors the watch rule at ApprovalPoller.swift:223). The options ARE the
    # actions and answer only through the §1a option contract; a label posted
    # as a legacy {id, answer} is rejected by the gateway (400) and would be
    # recorded as a mis-answer by approval_listener. Tap opens the app.
    if row.get("kind") == "menu":
        return base
    opts = json.loads(row["options"])
    labels = {"approve": "Approve", "deny": "Deny", "hold": "Hold"}
    actions = []
    for opt in opts[:3]:  # ntfy caps at 3 action buttons
        actions.append({
            "action": "http",
            "label": labels.get(opt, opt),
            "url": answers_url,
            "method": "POST",
            "headers": {"Authorization": f"Bearer {token}"},
            "body": json.dumps({"id": row["id"], "answer": opt}),  # JSON string, comma-safe in JSON publish form
            "clear": True,
        })
    return {**base, "actions": actions}

def notify(rid, store=None):
    store = store or ApprovalStore()
    row = store.get(rid)
    if not row or row["status"] != "pending":
        return
    token = ntfy_token()
    if not token:                                   # never silently push without auth
        raise RuntimeError("ntfy token missing (~/.config/jarvis/ntfy-token) — refusing to push unauthenticated")
    payload = build_payload(row, token)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    _http_post(NTFY_BASE, headers, json.dumps(payload))   # POST to ROOT, topic is in the body
    store.set_notified(rid)

def build_qnr_payload(row):
    """Questionnaire push (spec §5): title + 'N questions', NO per-question
    actions — the container answers via the app's paged flow, so the default
    tap (open the app) is the only affordance."""
    return {
        "topic": NTFY_APPROVALS_TOPIC,
        "title": f"Questionnaire — {row['from_agent']}",
        "message": f"{row['title']} ({row['question_count']} questions)",
        "priority": 4,
    }

def notify_questionnaire(qid, store=None):
    store = store or QuestionnaireStore()
    row = store.get(qid)
    if not row or row["status"] != "pending":
        return
    token = ntfy_token()
    if not token:                                   # never silently push without auth
        raise RuntimeError("ntfy token missing (~/.config/jarvis/ntfy-token) — refusing to push unauthenticated")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    _http_post(NTFY_BASE, headers, json.dumps(build_qnr_payload(row)))
    store.set_notified(qid)

# ---------------------------------------------------------------------------
# S3 item 2 — Telegram notify channel (Universal Decision-Surface Pipeline
# spec §2.4). the operator: "sends me a telegram (for now)." Every newly-surfaced row
# fires a Telegram ping ALONGSIDE the ntfy app push. PER-CHANNEL dedup (build
# guard §3.1-c): a failed channel retries WITHOUT re-sending the succeeded one.
# ntfy's success is the row's notified_at (unchanged); Telegram's success lives
# in this independent sidecar, so neither channel re-spams on the other's
# failure. One Telegram per row (approval id / qnr id), pruned when the row
# leaves pending. Uses tg-notify.sh — NEVER raw curl.
# ---------------------------------------------------------------------------
TG_NOTIFY_STATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state", "notify-telegram-state.json")


def _load_tg_state(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_tg_state(path, state):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)   # atomic — never a torn read on a concurrent beat


def _tg_send(text):
    """One Telegram ping via the operator's verified tg-notify.sh (NEVER raw curl).
    True only on ok exit(0). Never raises — caller decides retry."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run(["bash", os.path.join(here, "tg-notify.sh"), text],
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def _tg_text_for_approval(row):
    return (f"APPROVAL — {row['from_agent']}: {row['question']} "
            f"— open OrchestraOS to answer.")


def _tg_text_for_qnr(row):
    return (f"QUESTIONNAIRE — {row['from_agent']}: {row['title']} "
            f"({row['question_count']} questions) — open OrchestraOS.")


def notify_telegram(key, text, state=None, state_path=None, send=None):
    """Send EXACTLY ONE Telegram ping per key, sidecar-deduped. No-op if already
    sent. Returns True if sent-or-already-sent, False if the send failed (state
    NOT stamped → retried next beat). When `state` is passed in, mutates it in
    place and does NOT persist (caller batches the save); else loads+saves the
    sidecar itself. send/state injectable for tests."""
    state_path = state_path or TG_NOTIFY_STATE
    send = send or _tg_send
    owns_state = state is None
    state = _load_tg_state(state_path) if owns_state else state
    if state.get(key, {}).get("sent_at"):
        return True                                   # per-channel dedup
    ok = False
    try:
        ok = bool(send(text))
    except Exception as e:  # noqa: BLE001 — a failed channel is retriable, never fatal
        print(f"[approval_notify] telegram {key} failed: {e}", file=sys.stderr)
    if ok:
        import time as _t
        state[key] = {"sent_at": _t.time()}
    if owns_state:
        _save_tg_state(state_path, state)
    return ok


# ---------------------------------------------------------------------------
# Off-tailnet escalation (gm commission msg_c5757395 — fix (a) of the
# apr_321ad47b delivery gap). ntfy (:9080) is served tailnet-only: when the operator's
# phone is OFF Tailscale the push publishes fine and dies silently. Three parts:
#   (1) phone_tailnet_status — `tailscale status --json` peer for the phone;
#       off-tailnet = Online false AND last-seen older than the threshold.
#       Timeout / parse error / peer missing => FAIL-OPEN as ON (never spam).
#   (2) escalate_offtailnet — while off, EVERY pending card gets a FULL Telegram
#       card (question + options + reply instructions), sidecar-deduped under
#       an `escalate:` key, stamped only on a verified ok send (S3 discipline).
#   (3) refire_on_return — the off->on transition re-fires the ntfy push for
#       still-pending cards that were pushed BEFORE the return, once per return.
# Every decision is logged (the operator standing rule: never silent).
# ---------------------------------------------------------------------------
TAILNET_NOTIFY_STATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state", "notify-tailnet-state.json")
_ESCALATION_LOG_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "logs", "notify-escalation.log")


def _tailnet_state_path():
    """TAILNET_NOTIFY_STATE_PATH overrides the prod sidecar (tests -> tmp via
    conftest); resolved at CALL time so a cron_backstop() call without the
    kwarg can never write state/ from a test run (gm msg_083b5273 (2))."""
    return os.environ.get("TAILNET_NOTIFY_STATE_PATH") or TAILNET_NOTIFY_STATE


def _escalation_log_path():
    """NOTIFY_ESCALATION_LOG_PATH overrides the prod log (tests -> tmp via conftest)."""
    return os.environ.get("NOTIFY_ESCALATION_LOG_PATH") or _ESCALATION_LOG_DEFAULT


def _escalation_log(msg):
    """Durable + stdout (the cron log). Best-effort; never raises in the beat."""
    import time as _t
    line = f"{_t.strftime('%Y-%m-%dT%H:%M:%S%z')} [notify-escalation] {msg}"
    print(line)
    try:
        path = _escalation_log_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _parse_iso_epoch(v):
    """ISO-8601 (Z or offset) or epoch -> float epoch; None when unparsable."""
    from datetime import datetime, timezone
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        sv = str(v).strip()
        if sv.endswith("Z"):
            sv = sv[:-1] + "+00:00"
        # tailscale emits fractional seconds of arbitrary width; fromisoformat
        # (3.11+) accepts them. Zero-date = "never seen" -> None.
        d = datetime.fromisoformat(sv)
        if d.year <= 1:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except (ValueError, TypeError):
        return None


def _run_tailscale_status(cmd, timeout):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def phone_tailnet_status(now=None, run=None, host=None, threshold_s=None, timeout_s=None):
    """{"online", "off_tailnet", "last_seen", "reason"} for the operator's phone.
    off_tailnet is True ONLY on positive evidence (peer found, Online false,
    last-seen older than threshold). Anything unknown fails OPEN (off=False,
    reason 'unknown:<why>') and is logged — an unknown must never spam."""
    import time as _t
    now = _t.time() if now is None else now
    host = host or PHONE_TAILNET_HOST
    threshold_s = PHONE_OFFTAILNET_THRESHOLD_S if threshold_s is None else threshold_s
    timeout_s = TAILSCALE_STATUS_TIMEOUT_S if timeout_s is None else timeout_s
    run = run or _run_tailscale_status
    out = {"online": True, "off_tailnet": False, "last_seen": None, "reason": "unknown"}
    try:
        r = run(["tailscale", "status", "--json"], timeout=timeout_s)
        doc = json.loads(r.stdout)
        peer = None
        for p in (doc.get("Peer") or {}).values():
            dns = (p.get("DNSName") or "").lower()
            if dns.startswith(host.lower() + ".") or (p.get("HostName") or "").lower() == host.lower():
                peer = p
                break
        if peer is None:
            out["reason"] = f"unknown:peer_missing:{host}"
        elif peer.get("Online"):
            out.update(online=True, off_tailnet=False, reason="online")
        else:
            seen = _parse_iso_epoch(peer.get("LastSeen"))
            out["online"] = False
            out["last_seen"] = seen
            if seen is None:
                out["reason"] = "unknown:no_last_seen"
            elif (now - seen) > threshold_s:
                out.update(off_tailnet=True, reason=f"offline_for:{int(now - seen)}")
            else:
                out["reason"] = f"offline_recent:{int(now - seen)}"
    except Exception as e:  # noqa: BLE001 — timeout / no binary / bad json: fail OPEN
        out["reason"] = f"unknown:{type(e).__name__}"
    if out["reason"].startswith("unknown:"):
        _escalation_log(f"tailnet status {out['reason']} -> fail-open ON-tailnet (no escalation)")
    return out


def _tg_full_card_text(row):
    """The FULL card for Telegram: what the app would show, plus how to answer
    without the app. Not the ~159-char stub the operator missed on apr_321ad47b."""
    labels = _card_option_labels(row)
    lines = [
        f"ESCALATION — your phone has been off Tailscale, so the OrchestraOS push could not reach you.",
        f"Card {row['id']} from {row['from_agent']}:",
        "",
        str(row.get("question") or ""),
        "",
        "Options:",
        *labels,
        "",
        "To answer: reconnect Tailscale and open OrchestraOS (the card is still pending), "
        f"or reply to this message with '{row['id']}: <option>' and the GM will relay it.",
    ]
    return "\n".join(lines)


def _tg_full_card_text_qnr(q):
    return ("ESCALATION — your phone has been off Tailscale, so the OrchestraOS push could not reach you.\n"
            f"Questionnaire {q['id']} from {q['from_agent']}: {q['title']} "
            f"({q['question_count']} questions).\n\n"
            "To answer: reconnect Tailscale and open OrchestraOS (it is still pending).")


def _card_option_labels(row):
    labels = []
    menu = row.get("menu")
    try:
        m = json.loads(menu) if isinstance(menu, str) else (menu or {})
        for i, o in enumerate(m.get("options") or [], 1):
            n = o.get("n") or str(i)
            labels.append(f"{n}. {o.get('label', '')}".rstrip())
    except (ValueError, AttributeError, TypeError):
        labels = []
    if not labels:
        try:
            opts = json.loads(row.get("options") or "[]")
        except (ValueError, TypeError):
            opts = []
        labels = [f"{i}. {o}" for i, o in enumerate(opts, 1)]
    return labels


def _digest_entry(kind, row):
    """One card's block inside the digest."""
    if kind == "qnr":
        return (f"- Questionnaire {row['id']} from {row['from_agent']}: {row['title']} "
                f"({row['question_count']} questions)")
    labels = _card_option_labels(row)
    opts = "; ".join(labels) if labels else "(no options)"
    return (f"- {row['id']} from {row['from_agent']}: {row.get('question') or ''}\n"
            f"    Options: {opts}")


def _tg_digest_text(items):
    """ONE Telegram for many qualifying cards (gm msg_083b5273 (1))."""
    lines = [
        f"ESCALATION DIGEST — your phone has been off Tailscale, so the OrchestraOS pushes "
        f"could not reach you. {len(items)} pending decision(s):",
        "",
    ]
    for kind, row in items:
        lines.append(_digest_entry(kind, row))
        lines.append("")
    lines.append("To answer: reconnect Tailscale and open OrchestraOS (all are still pending), "
                 "or reply to this message with '<card id>: <option>' per card and the GM will relay it.")
    return "\n".join(lines)


def escalate_offtailnet(store, qstore, status, tg_state, send=None, max_singles=None):
    """While the phone is off-tailnet, escalate every pending row that has NOT
    yet been escalated (sidecar key `escalate:<key>`, stamped only on ok):
    <= max_singles qualifying rows => one FULL card each; more => ONE digest
    listing all of them (each card's key stamped on the digest's ok). Returns
    the escalated keys."""
    done = []
    if not status.get("off_tailnet"):
        return done
    max_singles = ESCALATION_DIGEST_MAX_SINGLES if max_singles is None else max_singles
    send = send or _tg_send
    pending = [("approval", r, f"escalate:approval:{r['id']}") for r in store.pending_to_notify()]
    pending += [("qnr", q, f"escalate:qnr:{q['id']}") for q in qstore.list_pending()]
    todo = [(k, r, key) for k, r, key in pending if not tg_state.get(key, {}).get("sent_at")]
    if not todo:
        return done
    reason = status.get("reason")
    if len(todo) > max_singles:
        text = _tg_digest_text([(k, r) for k, r, _ in todo])
        ok = False
        try:
            ok = bool(send(text))
        except Exception as e:  # noqa: BLE001
            print(f"[approval_notify] telegram digest failed: {e}", file=sys.stderr)
        ids = ", ".join(r["id"] for _, r, _ in todo)
        _escalation_log(f"off_tailnet ({reason}) -> ONE digest telegram for {len(todo)} card(s) "
                        f"[{ids}]: {'escalated' if ok else 'SEND FAILED, will retry'}")
        if ok:
            import time as _t
            now = _t.time()
            for _, _, key in todo:
                tg_state[key] = {"sent_at": now, "digest": True}
                done.append(key)
        return done
    for kind, row, key in todo:
        text = _tg_full_card_text(row) if kind == "approval" else _tg_full_card_text_qnr(row)
        ok = notify_telegram(key, text, state=tg_state, send=send)
        label = row["id"] if kind == "approval" else f"qnr {row['id']}"
        _escalation_log(f"off_tailnet ({reason}) -> full-card telegram for {label} "
                        f"from {row['from_agent']}: {'escalated' if ok else 'SEND FAILED, will retry'}")
        if ok:
            done.append(key)
    return done


def _default_ntfy_publish(payload):
    token = ntfy_token()
    if not token:
        raise RuntimeError("ntfy token missing (~/.config/jarvis/ntfy-token) — refusing to push unauthenticated")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    _http_post(NTFY_BASE, headers, json.dumps(payload))
    return True


def _already_pushed_ids(store, qstore):
    """Pending rows whose FIRST ntfy push already happened (notified_at set).
    Captured BEFORE the beat's normal notify loop so 'pushed before the return'
    is a set membership, not a wall-clock comparison against DB stamps."""
    ids = {r["id"] for r in store.pending_to_notify() if r.get("notified_at")}
    ids |= {q["id"] for q in qstore.list_pending() if q.get("notified_at")}
    return ids


def refire_on_return(store, qstore, status, state_path=None, publish=None, now=None,
                     candidates=None):
    """Track the phone's off/on across beats in a sidecar. On the off->on
    transition stamp `return_at` and freeze `targets` = the still-pending rows
    that were pushed BEFORE this beat (candidates); then re-fire the ntfy push
    for each target once per return (retrying a failed publish on later beats).
    A row first pushed on/after the return is the normal loop's push — never
    doubled. Returns re-fired ids."""
    import time as _t
    now = _t.time() if now is None else now
    state_path = state_path or _tailnet_state_path()
    publish = publish or _default_ntfy_publish
    candidates = _already_pushed_ids(store, qstore) if candidates is None else set(candidates)
    st = _load_tg_state(state_path)             # same tolerant loader/shape
    was_off = bool(st.get("off"))
    is_off = bool(status.get("off_tailnet"))
    if is_off and not was_off:
        st["off_since"] = now
        _escalation_log(f"phone OFF tailnet ({status.get('reason')}) — pushes will not reach it; "
                        f"full-card telegram escalation armed")
    if was_off and not is_off:
        st["return_at"] = now
        st["targets"] = sorted(candidates)
        off_for = int(now - float(st.get("off_since") or now))
        _escalation_log(f"phone RETURNED to tailnet after {off_for}s — re-firing ntfy for "
                        f"{len(candidates)} still-pending card(s) pushed while away")
    st["off"] = is_off
    fired = []
    targets = st.get("targets") or []
    if not is_off and targets:
        rows = {r["id"]: ("approval", r) for r in store.pending_to_notify()}
        rows.update({q["id"]: ("qnr", q) for q in qstore.list_pending()})
        remaining = []
        for rid in targets:
            if rid not in rows:
                continue                                  # answered/vanished — drop
            kind, row = rows[rid]
            try:
                payload = build_payload(row, ntfy_token() or "") if kind == "approval" else build_qnr_payload(row)
                ok = bool(publish(payload))
            except Exception as e:  # noqa: BLE001
                ok = False
                _escalation_log(f"return refire {rid} FAILED: {e} (will retry next beat)")
            if ok:
                fired.append(rid)
                _escalation_log(f"return refire ntfy for {rid} from {row['from_agent']}: ok")
            else:
                remaining.append(rid)
        st["targets"] = remaining
    _save_tg_state(state_path, st)
    return fired


def stamp_outrun_skips(store=None):
    """Auditability stamp (v3 msg_1a926229, from the 07:24 field case): a row
    that left 'pending' with notified_at still NULL was OUTRUN (the operator's
    poll-tap beat the minute-cron push) — stamp notify_skipped so a tracer
    never reads the NULL as a silent push failure. Idempotent, read-mostly."""
    store = store or ApprovalStore()
    c = store._conn()
    try:
        from datetime import datetime, timezone
        c.execute("UPDATE approval_requests SET notify_skipped=? "
                  "WHERE status != 'pending' AND notified_at IS NULL "
                  "AND notify_skipped IS NULL AND answered_at IS NOT NULL",
                  [f"answered-before-push@{datetime.now(timezone.utc).isoformat()}"])
        c.commit()
    finally:
        c.close()


def cron_backstop(store=None, qstore=None, tg_send=None, tg_state_path=None,
                  tailnet_status=None, tailnet_state_path=None, ntfy_publish=None, now=None):
    """Notify NEW pending rows once; backstop covers MISSED pushes only.
    R8 (F2): the expiry sweep is RETIRED — pending rows never auto-expire; they
    end answered/resumed or discarded (the operator), and the queue is priority-sorted.
    the operator ruling 2026-08-14: EXACTLY ONE ntfy per approval request. Skip rows that
    already have notified_at (notify() stamps it on a successful push), so a
    pending row that sits unanswered is NOT re-pushed every cron beat. A failed
    first push leaves notified_at NULL → retried next beat until delivered.

    S3 item 2: a SECOND, per-channel-deduped Telegram pass fires one ping per
    pending row independent of ntfy's notified_at, so a row whose ntfy fired
    inline still gets its Telegram (and neither channel re-spams on the other's
    failure). store/qstore/tg_send/tg_state_path injectable for tests."""
    store = store or ApprovalStore()
    qstore = qstore or QuestionnaireStore()
    # Off-tailnet (msg_c5757395): status + "already pushed before this beat"
    # are captured FIRST so the return re-fire never doubles a push the normal
    # loop below makes on the same beat.
    status = tailnet_status if tailnet_status is not None else phone_tailnet_status(now=now)
    pre_pushed = _already_pushed_ids(store, qstore)
    for row in store.pending_to_notify():
        if row.get("notified_at"):
            continue                                  # already pushed once — never re-spam
        try:
            notify(row["id"], store=store)
        except Exception as e:  # noqa: BLE001 — never let one bad row kill the sweep
            print(f"[approval_notify] {row['id']} failed: {e}", file=sys.stderr)
    # Questionnaires (spec §5): backstop covers MISSED pushes only (notified_at
    # unstamped) — the container card re-surfaces via the priority-sorted queue,
    # so re-pushing every cron run would just be spam.
    for q in qstore.list_pending():
        if q.get("notified_at"):
            continue
        try:
            notify_questionnaire(q["id"], store=qstore)
        except Exception as e:  # noqa: BLE001
            print(f"[approval_notify] qnr {q['id']} failed: {e}", file=sys.stderr)

    # Telegram channel (S3 item 2) — one ping per pending row, per-channel dedup,
    # pruned when a row leaves pending so the sidecar stays bounded.
    tg_state_path = tg_state_path or TG_NOTIFY_STATE
    tg_state = _load_tg_state(tg_state_path)
    live_keys = set()
    for row in store.pending_to_notify():
        key = f"approval:{row['id']}"
        live_keys.add(key)
        notify_telegram(key, _tg_text_for_approval(row), state=tg_state, send=tg_send)
    for q in qstore.list_pending():
        key = f"qnr:{q['id']}"
        live_keys.add(key)
        notify_telegram(key, _tg_text_for_qnr(q), state=tg_state, send=tg_send)
    # Off-tailnet (msg_c5757395), pass A: log the off/on transition and, on a
    # return, re-fire ntfy for the cards pushed while the phone was away.
    try:
        refire_on_return(store, qstore, status, state_path=tailnet_state_path,
                         publish=ntfy_publish, now=now, candidates=pre_pushed)
    except Exception as e:  # noqa: BLE001 — never let it kill the beat
        _escalation_log(f"return-refire pass FAILED: {e}")
    # Pass B: while off, the FULL card over Telegram; same sidecar, `escalate:`
    # keys, same ok-only stamping as the stub channel.
    try:
        escalate_offtailnet(store, qstore, status, tg_state, send=tg_send)
    except Exception as e:  # noqa: BLE001
        _escalation_log(f"escalation pass FAILED: {e}")
    for k in list(tg_state.keys()):
        base = k[len("escalate:"):] if k.startswith("escalate:") else k
        if base not in live_keys:
            del tg_state[k]                           # prune answered/vanished rows
    _save_tg_state(tg_state_path, tg_state)

# ---------------------------------------------------------------------------
# D2 — permission-prompt notify backstop (DEC-1786758700 + the operator 2-push amendment)
# ---------------------------------------------------------------------------
# Native permission prompts are NOT ledger rows (they surface as read-time
# pseudo-rows on /pending-approvals, answered via /agent-key). So they have no
# `notified_at` column — this backstop keeps its OWN durable per-op_key state so
# an unwatched agent stuck on a permission prompt is still discoverable, WITHOUT
# creating a durable answerable row.
#
# HARD CAP 2 pushes/op_key (the operator ruling 2026-08-15):
#   #1 — prompt persists > PERM_PUSH1_DELAY_S (60s), no push yet.
#   #2 — SAME op_key still pending at the next 08:00 America/New_York, AND it was
#        already stuck before that 08:00 (overnight-persisted). One re-surface so
#        an overnight blocker is the first thing the operator sees.
#   then SILENCE — never a third.
# Dedup RESETS when the prompt clears: an op_key absent from the live scan is
# dropped from state, so a fresh same-shaped prompt (same op_key = hash of
# session+question) is push-eligible again instead of being permanently silenced
# by a stale mark (the op_key-collision fix flagged pre-build).

PERM_PUSH1_DELAY_S = 60
_PERM_STATE_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state", "perm-notify-state.json")


def _load_perm_state(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_perm_state(path, state):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)   # atomic — never a torn read on a concurrent cron beat


def _today_0800_et(now_epoch):
    """Epoch of 08:00 America/New_York on the ET calendar day containing now."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now_et = datetime.fromtimestamp(now_epoch, et)
    return now_et.replace(hour=8, minute=0, second=0, microsecond=0).timestamp()


def _read_instance_n(ledger, sess, digest):
    """READ instance_n for (sess,digest) from the gateway-owned instance ledger.
    The gateway is the sole WRITER (DEC-1786771513 §4.5); the D2 cron only reads,
    so prompt #2 (a distinct instance) gets its own op_key + 2-push budget. Falls
    back to 1 when absent (fail-open; a stale/missing read at worst merges #1/#2's
    budget = the bounded gap the operator ACCEPTED as D-A)."""
    e = ledger.get(f"{sess}|{digest}")
    try:
        return int(e.get("instance_n", 1)) if isinstance(e, dict) else 1
    except (TypeError, ValueError):
        return 1


def _scan_permission_prompts():
    """Live fleet scan -> {op_key: {"agent","question"}} for every session at a
    native permission prompt. FAIL-OPEN: any error -> {} (never crash the beat).
    op_key = menu:<sess>:<digest>:<instance_n> — the instance_n (read from the
    gateway-owned ledger) makes consecutive same-question prompts distinct op_keys
    so each gets its own 2-push budget (DEC-1786771513)."""
    import hashlib, importlib.util, subprocess
    out = {}
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "agent_status", os.path.join(here, "agent-status.py"))
        A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
        ledger = _read_instance_ledger()
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                           capture_output=True, text=True, timeout=5)
        for sess in (s for s in r.stdout.splitlines() if s.strip()):
            try:
                st = A.get_agent_status(sess)
                menu = st.get("pending_menu") if isinstance(st, dict) else None
                if isinstance(menu, dict) and menu.get("kind") == "permission":
                    q = menu.get("question") or ""
                    digest = hashlib.sha256((sess + "|" + q).encode()).hexdigest()[:16]
                    n = _read_instance_n(ledger, sess, digest)
                    out[f"menu:{sess}:{digest}:{n}"] = {"agent": sess, "question": q}
            except Exception as e:  # noqa: BLE001 — one bad session never sinks the scan
                print(f"[approval_notify] perm scan skip {sess}: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — fail open
        print(f"[approval_notify] perm scan failed: {e}", file=sys.stderr)
        return {}
    return out


def _read_instance_ledger():
    """Read the gateway-owned instance ledger (read-only from the D2 cron).
    FAIL-OPEN: missing/corrupt -> {} so the backstop still runs on base identity."""
    import json as _json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "state", "perm-instance-ledger.json")
    try:
        with open(path) as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _send_perm_push(agent, question, kind):
    """Notify-only push (no action buttons — permission prompts answer in the app
    via /agent-key, an ntfy quick-action would 400). Reuses tg-notify.sh (the operator's
    verified Telegram path)."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    if kind == "overnight":
        text = f"PERMISSION NEEDED (still stuck overnight) — {agent}: {question} — open OrchestraOS to answer."
    elif kind == "escalate":
        text = f"PERMISSION STILL NEEDED — {agent}: {question} — still waiting, open OrchestraOS to answer."
    else:
        text = f"PERMISSION NEEDED — {agent}: {question} — open OrchestraOS to answer."
    subprocess.run(["bash", os.path.join(here, "tg-notify.sh"), text],
                   capture_output=True, text=True, timeout=15)


def permission_backstop(now=None, state_path=None, scan=None, send=None):
    """One cron beat of the D2 permission backstop. Pure-ish: now/scan/send/state
    are injectable for tests. Returns the list of (op_key, "push1"|"push2") fired
    this beat. See module header for the 2-push cap + reset semantics."""
    import time as _time
    now = _time.time() if now is None else now
    state_path = state_path or _PERM_STATE_DEFAULT
    scan = scan or _scan_permission_prompts
    send = send or _send_perm_push

    try:
        present = scan()
    except Exception as e:  # noqa: BLE001 — fail open, do not touch state
        print(f"[approval_notify] perm backstop scan failed: {e}", file=sys.stderr)
        return []

    state = _load_perm_state(state_path)
    actions = []

    # RESET: drop op_keys no longer present (prompt answered/vanished) so a fresh
    # same-op_key prompt starts clean and is push-eligible again.
    for k in list(state.keys()):
        if k not in present:
            del state[k]

    for op_key, meta in present.items():
        st = state.setdefault(op_key, {"agent": meta["agent"], "question": meta["question"],
                                       "first_seen": now, "push1_at": None, "push2_at": None})
        # PUSH #1 — persisted past the delay, not yet pushed.
        if st.get("push1_at") is None:
            if (now - st["first_seen"]) >= PERM_PUSH1_DELAY_S:
                try:
                    send(meta["agent"], meta["question"], "stuck")
                    st["push1_at"] = now; actions.append((op_key, "push1"))
                except Exception as e:  # noqa: BLE001
                    print(f"[approval_notify] perm push1 {op_key} failed: {e}", file=sys.stderr)
            continue   # never push1 and push2 in the same beat
        # PUSH #2 — morning re-surface, overnight-persisted only, ceiling 2.
        if st.get("push2_at") is None:
            t0800 = _today_0800_et(now)
            if now >= t0800 and st["push1_at"] < t0800:
                try:
                    send(meta["agent"], meta["question"], "overnight")
                    st["push2_at"] = now; actions.append((op_key, "push2"))
                except Exception as e:  # noqa: BLE001
                    print(f"[approval_notify] perm push2 {op_key} failed: {e}", file=sys.stderr)

    _save_perm_state(state_path, state)
    return actions


# ---------------------------------------------------------------------------
# S3 item 4 — permission durable-resume/escalation (spec §2.5 + guards d/e)
# ---------------------------------------------------------------------------
# The 2-push cap goes SILENT after 2 notifies — an ignored permission prompt then
# dies even though the agent is still parked. This SLA-based durable record keeps
# escalating on the approval cadence (ESCALATE_REPEAT_MINUTES) until the prompt is
# answered or vanishes — "work continues" requires the loop to close even when
# the operator is away. It is SIDECAR-ONLY (guard d: it creates NO ledger row, so it never
# duplicates the read-time permission pseudo-row — the single surface row is that
# pseudo-row). Guard e: an op_key absent from the scan (answered/vanished) clears
# its record IMMEDIATELY, so the escalator never false-fires on a resolved prompt.
# SHIPS DISABLED (perm-durable-config.json enabled:false) → the 2-push
# permission_backstop stays the default until gm flips it.

PERM_DURABLE_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state", "perm-durable-config.json")
PERM_DURABLE_STATE_DEFAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "state", "perm-durable-state.json")
PERM_ESCALATE_REPEAT_S = ESCALATE_REPEAT_MINUTES * 60


def _perm_durable_enabled(path=None):
    """gm-owned flag. Missing/broken -> False (fall back to the 2-push cap)."""
    path = path or PERM_DURABLE_CONFIG
    try:
        with open(path) as f:
            d = json.load(f)
        return bool(d.get("enabled")) if isinstance(d, dict) else False
    except (FileNotFoundError, ValueError, OSError):
        return False


def permission_durable_backstop(now=None, state_path=None, scan=None, send=None,
                                push1_delay_s=PERM_PUSH1_DELAY_S,
                                escalate_repeat_s=None):
    """One beat of the SLA-based permission escalator. Push #1 once the prompt has
    persisted past push1_delay_s, then re-escalate every escalate_repeat_s while
    it is still present (UNCAPPED — the differentiator from the 2-push cap). Guard
    e clears any op_key no longer present. Sidecar-only (guard d): no ApprovalStore,
    no ledger row. now/scan/send/state injectable for tests. Returns the list of
    (op_key, "push1"|"escalate") fired this beat."""
    import time as _time
    now = _time.time() if now is None else now
    state_path = state_path or PERM_DURABLE_STATE_DEFAULT
    scan = scan or _scan_permission_prompts
    send = send or _send_perm_push
    escalate_repeat_s = (PERM_ESCALATE_REPEAT_S if escalate_repeat_s is None
                         else escalate_repeat_s)

    try:
        present = scan()
    except Exception as e:  # noqa: BLE001 — fail open, do not touch state
        print(f"[approval_notify] perm durable scan failed: {e}", file=sys.stderr)
        return []

    state = _load_perm_state(state_path)
    fired = []

    # GUARD e: drop op_keys no longer present (answered/vanished) — immediate clear
    # so the escalator never false-fires on a resolved prompt, and a fresh
    # same-op_key prompt starts clean.
    for k in list(state.keys()):
        if k not in present:
            del state[k]

    for op_key, meta in present.items():
        st = state.setdefault(op_key, {"agent": meta["agent"], "question": meta["question"],
                                       "first_seen": now, "notified_at": None,
                                       "last_escalated_at": None, "escalations": 0})
        # PUSH #1 — persisted past the delay, not yet notified.
        if st.get("notified_at") is None:
            if (now - st["first_seen"]) >= push1_delay_s:
                try:
                    send(meta["agent"], meta["question"], "stuck")
                    st["notified_at"] = now
                    st["last_escalated_at"] = now
                    fired.append((op_key, "push1"))
                except Exception as e:  # noqa: BLE001
                    print(f"[approval_notify] perm durable push1 {op_key} failed: {e}", file=sys.stderr)
            continue                                   # never push1 + escalate same beat
        # RE-ESCALATE — SLA cadence, uncapped, while the prompt is still present.
        last = st.get("last_escalated_at") or st["notified_at"]
        if (now - last) >= escalate_repeat_s:
            try:
                send(meta["agent"], meta["question"], "escalate")
                st["last_escalated_at"] = now
                st["escalations"] = st.get("escalations", 0) + 1
                fired.append((op_key, "escalate"))
            except Exception as e:  # noqa: BLE001
                print(f"[approval_notify] perm durable escalate {op_key} failed: {e}", file=sys.stderr)

    _save_perm_state(state_path, state)
    return fired


if __name__ == "__main__":
    cron_backstop()
    stamp_outrun_skips()
    # S3 item 4: SLA escalation when armed; else the 2-push cap (ship-disabled).
    if _perm_durable_enabled():
        permission_durable_backstop()
    else:
        permission_backstop()
