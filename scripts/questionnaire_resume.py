"""Questionnaire R3 delivery (spec §4) — ONE batched digest per submit, delivered
to the emitter's live head via msg_store (the live router injects it). NEVER N
sequential injects — the per-question fire path does not exist. The digest is
idempotent: a watchdog re-fire is a repeat message, never a repeat state change.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root for msg_store
_CHECKOUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # code, not data (B1 run-2 finding B)
APPROVAL_CLI = os.path.join(_CHECKOUT, "scripts", "approval.py")


def _msg_store():
    from msg_store import MessageStore
    return MessageStore()


def _line_for(question, ans):
    """One human-readable line: 'n. prompt -> chosen label (+ "verbatim text")'."""
    n = question["n"]
    prompt = question.get("prompt", "")
    opt_n = ans.get("option_n") if ans else None
    txt = ans.get("answer_text") if ans else None
    chosen = None
    if opt_n is not None:
        for o in ((question.get("menu") or {}).get("options") or []):
            if o.get("n") == opt_n:
                chosen = o.get("label")
                break
        chosen = chosen or f"option {opt_n}"
    parts = []
    if chosen:
        parts.append(chosen)
    if txt:
        parts.append(f"\u201c{txt}\u201d")
    return f"{n}. {prompt} \u2192 " + (" + ".join(parts) if parts else "(no answer)")


def digest(row):
    """Composed once: a human-readable list + a stable machine header/JSON block."""
    lines = [f"[questionnaire-answers {row['id']}] {row.get('title', '')}", ""]
    machine = {"questionnaire_id": row["id"], "title": row.get("title"), "answers": []}
    for q in row.get("questions", []):
        ans = q.get("answer") or {}
        lines.append(_line_for(q, ans))
        machine["answers"].append({"n": q["n"], "prompt": q.get("prompt"),
                                   "option_n": ans.get("option_n"),
                                   "answer_text": ans.get("answer_text")})
    lines += ["", "```json", json.dumps(machine, ensure_ascii=False), "```",
              f"Ack: python3 {APPROVAL_CLI} qack "
              f"{row['id']} --from {row['from_agent']}"]
    return "\n".join(lines)


def _default_msg_send(row, body, *, type, subject, priority):
    _msg_store().send(from_agent="questionnaire-loop", to_agent=row["from_agent"],
                      type=type, subject=subject, body=body, priority=priority,
                      metadata={"questionnaire_id": row["id"]})


def _deliver_to_live_head(row, body, resolve=None, inject=None):
    """Outcome-classed live-head delivery (SLA spec 2026-08-14):
      'delivered' — verified inject landed;
      'cheap'     — busy / no-live-head (nothing entered the pane; free retry);
      'fail'      — idle-pane inject that failed verify (a REAL failed attempt).
    Never inject blind.

    Returns (outcome, detail): `detail` is the transport's refusal reason plus
    the detector state when the injector reported one (incident qnr_d49f5401:
    attempt #1's refusal to an idle-by-transcript pane was unrecoverable
    because this function discarded the info dict)."""
    from approval_resume import _default_resolve, _default_inject
    resolve = resolve or _default_resolve
    inject = inject or _default_inject
    session, reason = resolve(row["from_agent"])
    if not session:
        return "cheap", f"no-live-head:{reason}" if reason else "no-live-head"
    ok, info = inject(session, body)
    if ok:
        return "delivered", "delivered"
    reason = (info or {}).get("reason") if isinstance(info, dict) else None
    state = (info or {}).get("state") if isinstance(info, dict) else None
    detail = reason or "inject_refused"
    if state:
        detail = f"{detail} state={state}"
    return ("cheap" if reason == "busy" else "fail"), detail


def deliver(row, store, msg_send=None, resolve=None, inject=None):
    """ONE batched delivery: durable msg_store row on the FIRST try only (cheap
    per-beat retries must not spam the inbox), then outcome-classed live-head
    inject. delivered -> real attempt + auto-ack; cheap (busy/no-head) ->
    stamp_retry only (never counts toward escalation); fail -> real attempt,
    watchdog re-fires after the ack window. (SLA spec 2026-08-14.)"""
    # G3 once-only CAS: the claim is atomic with the row still being
    # submitted-unacked. If a manual qack consumed the answers first (tonight's
    # triple-path fixture: store-poll + router-held notification + late inject
    # all carrying the same payload), the late delivery never fires.
    if not store.claim_delivery(row["id"]):
        return
    body = digest(row)
    first = not row.get("resumed_at") and not row.get("last_attempt_at")
    if first:
        (msg_send or (lambda r, b: _default_msg_send(
            r, b, type="questionnaire_submitted",
            subject=f"Questionnaire {row['id']} answered "
                    f"({len(row.get('questions', []))} questions)",
            priority="high")))(row, body)
    outcome, detail = _deliver_to_live_head(row, body, resolve, inject)
    # Attributability (incident qnr_d49f5401, 2026-09-14): every attempt leaves
    # a telemetry line + the refusal reason on the row — the approvals lane has
    # had this since R7b; the questionnaire lane silently dropped the outcome.
    try:
        import answer_telemetry
        answer_telemetry.log_delivery(row, lane="qnr_digest_inject",
                                      ok=(outcome == "delivered"), reason=detail)
    except Exception as e:  # noqa: BLE001 — telemetry never breaks delivery
        print(f"[questionnaire_resume] telemetry emit failed for "
              f"{row.get('id')}: {e}", file=sys.stderr)
    if outcome != "delivered":
        print(f"[questionnaire_resume] deliver {row['id']} -> {row['from_agent']}: "
              f"{outcome} ({detail})", file=sys.stderr)
    if outcome == "delivered":
        store.mark_resumed_fired(row["id"])     # clears any stale refusal note
        store.ack(row["id"])                    # verified inject == delivered
    elif outcome == "cheap":
        store.stamp_retry(row["id"], reason=f"cheap:{detail}")
    else:
        store.mark_resumed_fired(row["id"], error=detail)  # real failed attempt


def deliver_discarded(row, store, msg_send=None, resolve=None, inject=None):
    """R8: one honest 'discarded without answers' notify so a parked agent never
    waits forever on a dismissed ask (spec §2.3)."""
    body = (f"[questionnaire-discarded {row['id']}] '{row.get('title', '')}' was "
            f"DISMISSED without answers. Do not keep waiting on it.")
    (msg_send or (lambda r, b: _default_msg_send(
        r, b, type="questionnaire_discarded",
        subject=f"Questionnaire {row['id']} discarded",
        priority="normal")))(row, body)
    _deliver_to_live_head(row, body, resolve, inject)   # best-effort; row is terminal


def _escalate(row, cheap=False):
    """Alert the operator. Wording distinguishes the classes (DEC-1786690995 caveat):
    cheap=True  -> target busy / no live head for the stuck window (delivery
                   pending, nothing wrong with the agent's ack behavior);
    cheap=False -> delivered N times, agent not acking."""
    import urllib.request
    from approval_config import NTFY_BASE, NTFY_APPROVALS_TOPIC, ntfy_token, ESCALATE_STUCK_MINUTES
    headers = {"Authorization": f"Bearer {ntfy_token()}",
               "X-Title": f"[!] Questionnaire {row['id']} undelivered" if cheap
                          else f"[!] Questionnaire {row['id']} not acking",
               "Priority": "high"}
    if cheap:
        data = (f"Answers for '{row.get('title','')}' undeliverable to "
                f"{row['from_agent']} for {ESCALATE_STUCK_MINUTES}+ min "
                f"(target busy or no live head). Still retrying every beat.").encode()
    else:
        data = (f"Digest to {row['from_agent']} for '{row.get('title','')}' delivered "
                f"{row['resume_attempts']}x with no ack. Check the pane.").encode()
    urllib.request.urlopen(urllib.request.Request(
        f"{NTFY_BASE}/{NTFY_APPROVALS_TOPIC}", data=data, headers=headers,
        method="POST"), timeout=10)


def watchdog(store=None, resolve=None, inject=None):
    """SLA spec 2026-08-14: cheap retries every beat (55s guard band) so an idle
    target gets the answer within a minute of going idle; landed/failed REAL
    attempts respect the 5-min ack window; escalate on 3 real failures OR the
    15-min wall-clock backstop, throttled to one ping per 30 min; cheap retries
    continue after escalation. Idempotent digests; ack only on verified inject."""
    from datetime import datetime, timezone, timedelta
    from approval_config import (WATCHDOG_MINUTES, WATCHDOG_MAX_ATTEMPTS,
                                 WATCHDOG_RETRY_BEAT_SECONDS,
                                 ESCALATE_STUCK_MINUTES, ESCALATE_REPEAT_MINUTES)
    from questionnaire_schema import QuestionnaireStore
    store = store or QuestionnaireStore()
    now = datetime.now(timezone.utc)
    beat_cut = (now - timedelta(seconds=WATCHDOG_RETRY_BEAT_SECONDS)).isoformat()
    ack_cut = (now - timedelta(minutes=WATCHDOG_MINUTES)).isoformat()
    stuck_cut = (now - timedelta(minutes=ESCALATE_STUCK_MINUTES)).isoformat()
    esc_cut = (now - timedelta(minutes=ESCALATE_REPEAT_MINUTES)).isoformat()
    for row in store.submitted_unacked():
        try:
            if row.get("last_attempt_at") and row["last_attempt_at"] > beat_cut:
                continue                          # tried within a beat — wait
            if row.get("resumed_at") and row["resumed_at"] > ack_cut:
                continue                          # real attempt recent — ack window
            recently_escalated = (row.get("escalated_at")
                                  and row["escalated_at"] > esc_cut)
            # G3: the not-acking gate reads REAL attempts. resume_attempts now
            # counts every attempt incl. busy refusals, so gating on it would
            # turn 3 busy minutes into a false "delivered 3x with no ack" page
            # and STOP the retry loop for a target that was merely mid-turn.
            real = row.get("real_attempts")
            if real is None:                      # pre-migration row shape
                real = row["resume_attempts"]
            if real >= WATCHDOG_MAX_ATTEMPTS:
                if not recently_escalated:
                    _escalate(row, cheap=False)
                    store.mark_escalated(row["id"])
                continue                          # real failures maxed — stop firing
            wall_stuck = (row.get("submitted_at")
                          and row["submitted_at"] < stuck_cut)
            if wall_stuck and not recently_escalated:
                _escalate(row, cheap=True)
                store.mark_escalated(row["id"])
                continue                          # retry resumes next beat
            deliver(store.get(row["id"]), store, resolve=resolve, inject=inject)
        except Exception as e:  # noqa: BLE001 — never let one row kill the sweep
            import sys as _sys
            print(f"[questionnaire_resume] watchdog row {row['id']} failed: {e}", file=_sys.stderr)


# --- G3 age escalation (spec 2026-08-18): 10min TG / 30min gm digest ---------
# SHADOW-FIRST: this is a live the operator-facing path, so until RESUME_RETRY_ARMED=1
# (gm's flip) the sweep only prints [resume-retry-shadow] WOULD-lines to the
# cron log. The senders are module-level so tests (and the arm) can see them.

_last_decision = None

# INCIDENT 2026-08-19 03:36Z: a staged calibration row paged the operator because I staged it
# without re-checking that gm had ARMED the path. The "unmistakable title" bind was
# human-readable only; this makes it mechanical. A row that announces itself as test
# residue never escalates — to the operator or to gm.
TEST_TITLE_MARKERS = ("NOT A REAL ASK", "-CALIB", "SCRATCH")

TG_AGE_MINUTES = 10
GM_DIGEST_AGE_MINUTES = 30


def _armed():
    return os.environ.get("RESUME_RETRY_ARMED") == "1"


def _send_tg(text):
    """Telegram via the house helper (never raw curl — tg-notify.sh verifies ok)."""
    import subprocess
    subprocess.run([os.path.join(_CHECKOUT, "scripts", "tg-notify.sh"),
                    text], check=True, timeout=30)


def _send_gm_digest(row, text, decision=None):
    """gm G-5: the escalation carries the STRUCTURED state it acted on, so a wrong
    escalation is diagnosable without parsing prose. Free text is for humans."""
    meta = {"questionnaire_id": row["id"]}
    if decision:
        meta["g3_decision"] = decision
    _msg_store().send(from_agent="questionnaire-loop", to_agent="gm",
                      type="escalation", subject=f"[G3] answer undelivered 30m+: {row['id']}",
                      body=text, priority="high", metadata=meta)


def _resolve_target(agent_id):
    """(session|None, reason) for the addressee. Seam so fixtures can drive both classes."""
    try:
        from approval_resume import _default_resolve
        return _default_resolve(agent_id)
    except Exception as e:                        # noqa: BLE001
        return (None, f"resolve-error:{type(e).__name__}")


def _target_state(agent_id):
    """Best-effort target state for the escalation wording; never raises."""
    session, reason = _resolve_target(agent_id)
    return f"session={session or 'none'} ({reason})"


def age_escalation_sweep(store=None):
    """G3 spec §4: undelivered answers age-escalate — >10 min TG to the operator naming
    the stuck answer + target state; >30 min a HIGH gm digest row. Shadow-first:
    WOULD-lines only until armed. Armed sends throttle via escalated_at
    (ESCALATE_REPEAT_MINUTES); shadow lines print every sweep by design — the
    cron log is the evidence stream gm reviews to arm."""
    from datetime import datetime, timezone
    from approval_config import ESCALATE_REPEAT_MINUTES
    from questionnaire_schema import QuestionnaireStore
    store = store or QuestionnaireStore()
    now = datetime.now(timezone.utc)
    for row in store.submitted_unacked():
        try:
            sub = row.get("submitted_at")
            if not sub:
                continue
            age_min = (now - datetime.fromisoformat(sub)).total_seconds() / 60.0
            if age_min < TG_AGE_MINUTES:
                continue
            title = row.get("title") or ""
            if any(m in title.upper() for m in TEST_TITLE_MARKERS):
                print(f"[resume-retry-shadow] SKIP (test-residue title): {row['id']}")
                continue
            session, reason = _resolve_target(row["from_agent"])
            state = f"session={session or 'none'} ({reason})"
            # gm bind 2026-08-19: a DEAD ADDRESSEE and a STUCK ANSWER are not the same
            # event and must not produce the same escalation. My own calibration fired
            # via "session=none (unknown-address)" — armed, that would have paged the operator
            # about an answer undeliverable to an agent that no longer exists. Only a
            # LIVE-but-not-receiving target is a decision he can act on; an absent one
            # is fleet hygiene and goes to gm.
            target_live = session is not None
            digest_due = age_min >= GM_DIGEST_AGE_MINUTES
            # gm G-5: structured first, prose second. Every field a gate might reason
            # about lives here; the sentences below are rendered FROM this, never
            # parsed back out of it. `detector` is reserved for the G-8 wiring and is
            # deliberately null until agent-status.py stops answering about the CALLER
            # for an unrecognised target (ob's fix) — a null slot is honest, a
            # fabricated one is the defect we are fixing.
            decision = {
                "qnr_id": row["id"],
                "target": row["from_agent"],
                "target_session": session,
                "resolve_reason": reason,
                "target_live": bool(target_live),
                "class": "target-live-not-receiving" if target_live else "target-not-live",
                "age_min": round(age_min, 1),
                "routed_to": "operator" if target_live else "gm",
                "shaw_notified": bool(target_live),
                "armed": _armed(),
                "detector": None,
            }
            globals()["_last_decision"] = decision
            tg_text = (f"[G3] Answer stuck {int(age_min)}m: '{row.get('title','')}' "
                       f"({row['id']}) undelivered to {row['from_agent']} — {state}. "
                       f"Retrying every tick.")
            gm_text = (f"[G3 hygiene] Answers for '{row.get('title','')}' ({row['id']}) have "
                       f"been undeliverable {int(age_min)}m because the ADDRESSEE IS NOT LIVE: "
                       f"{row['from_agent']} — {state}. Not escalated to the operator (nothing he can "
                       f"act on). Dispose, re-home, or park the row.")
            if not _armed():
                if target_live:
                    print(f"[resume-retry-shadow] WOULD TG ({int(age_min)}m): {tg_text}")
                    if digest_due:
                        print(f"[resume-retry-shadow] WOULD gm-digest ({int(age_min)}m): "
                              f"{row['id']} '{row.get('title','')}' -> gm HIGH")
                else:
                    print(f"[resume-retry-shadow] WOULD gm-route ({int(age_min)}m, "
                          f"target-not-live): {gm_text}")
                print("[resume-retry-shadow-json] " + json.dumps(decision, sort_keys=True))
                continue
            recently = row.get("escalated_at") and (
                (now - datetime.fromisoformat(row["escalated_at"])).total_seconds()
                < ESCALATE_REPEAT_MINUTES * 60)
            if recently:
                continue
            if target_live:
                _send_tg(tg_text)                     # (a) the lost-answer class: the operator's call
                if digest_due:
                    _send_gm_digest(row, tg_text, decision=decision)
            else:
                _send_gm_digest(row, gm_text, decision=decision)   # (b) dead addressee: NEVER the operator
            store.mark_escalated(row["id"])
        except Exception as e:                    # noqa: BLE001 — never kill the sweep
            print(f"[questionnaire_resume] age sweep row {row.get('id')} failed: {e}",
                  file=sys.stderr)


if __name__ == "__main__":
    watchdog()
    age_escalation_sweep()
