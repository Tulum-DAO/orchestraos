"""§9.6 P0 boundary delivery — router delivers gateway-HELD messages RAW.

A message written by watch_gateway._hold_for_boundary carries
metadata.deliver_raw and IS a user turn deferred to the target's turn boundary.
The router must inject it as the RAW body (exactly as the sender typed), never
the inter-agent [MSG ...] 'read this file and act on it' envelope. Everything
else keeps the envelope. Metadata may be a dict OR the JSON string sqlite stores.
"""
import importlib.util
import json
import pytest
import os

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "message_router", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def test_stale_banner_late_row(monkeypatch):
    # >5m old -> banner replaces the (sent Xm ago) token (§9.6 addendum)
    b = mr._stale_banner(7, superseded=False, n_min=5)
    assert b is not None and "SENT 7m AGO" in b and "verify current state" in b

def test_stale_banner_fresh_row_none():
    assert mr._stale_banner(2, superseded=False, n_min=5) is None   # fresh -> no banner

def test_stale_banner_superseded_even_if_fresh():
    # a superseding row exists -> banner even at 1m old
    b = mr._stale_banner(1, superseded=True, n_min=5)
    assert b is not None and "SENT 1m AGO" in b

def test_stale_banner_boundary_at_n():
    assert mr._stale_banner(5, superseded=False, n_min=5) is None    # exactly N = not yet late
    assert mr._stale_banner(6, superseded=False, n_min=5) is not None


class _SupStore:
    def __init__(self, rows): self._rows = rows
    def has_superseding_row(self, from_agent, to_agent, created_at):
        return any(r["from_agent"] == from_agent and r["to_agent"] == to_agent
                   and r["created_at"] > created_at for r in self._rows)

def test_superseding_detected_same_pair_later():
    store = _SupStore([{"from_agent": "pm", "to_agent": "gm",
                        "created_at": "2026-08-16T21:00:05"}])
    assert store.has_superseding_row("pm", "gm", "2026-08-16T21:00:01") is True

def test_superseding_ignores_other_pair_or_earlier():
    store = _SupStore([{"from_agent": "pm", "to_agent": "OTHER",
                        "created_at": "2026-08-16T21:00:05"},
                       {"from_agent": "pm", "to_agent": "gm",
                        "created_at": "2026-08-16T20:00:00"}])
    assert store.has_superseding_row("pm", "gm", "2026-08-16T21:00:01") is False


def test_format_injection_late_row_gets_banner(monkeypatch):
    # a >5m [MSG]-enveloped row gets the banner instead of the plain token
    old_created = "2000-01-01T00:00:00+00:00"     # very old -> definitely late
    msg = {"id": "m1", "from_agent": "pm", "to_agent": "gm", "type": "reply",
           "subject": "hi", "body": "x", "priority": "medium", "created_at": old_created,
           "metadata": None, "parent_id": None}
    store = _SupStore([])                          # no superseding
    text = mr.format_injection(msg, store)
    assert "verify current state before acting" in text
    assert "(sent" not in text                     # plain token replaced by banner

def test_deliver_raw_never_gets_banner(monkeypatch):
    # deliver_raw (the operator's chat) stays unwrapped — banner is [MSG]-envelope only
    monkeypatch.setattr(mr, "format_injection", lambda m, s: "ENVELOPE-SHOULD-NOT-RUN")
    msg = {"id": "m2", "from_agent": "operator", "to_agent": "gm", "body": "raw chat",
           "created_at": "2000-01-01T00:00:00+00:00",   # ancient, but raw
           "metadata": {"deliver_raw": True}}
    out = mr.injection_text(msg, _SupStore([]))
    assert out == "raw chat"                        # no banner, no envelope


def test_held_message_delivered_raw_dict_metadata(monkeypatch):
    monkeypatch.setattr(mr, "format_injection", lambda msg, store: "ENVELOPE")
    msg = {"id": "m1", "body": "reindex the crm now", "from_agent": "operator",
           "metadata": {"held_for_boundary": True, "deliver_raw": True}}
    assert mr.injection_text(msg, None) == "reindex the crm now"   # raw, no envelope


def test_held_message_delivered_raw_json_string_metadata(monkeypatch):
    import json
    monkeypatch.setattr(mr, "format_injection", lambda msg, store: "ENVELOPE")
    msg = {"id": "m2", "body": "do X", "from_agent": "operator",
           "metadata": json.dumps({"deliver_raw": True})}    # sqlite stores a string
    assert mr.injection_text(msg, None) == "do X"


def test_normal_message_uses_envelope(monkeypatch):
    monkeypatch.setattr(mr, "format_injection", lambda msg, store: "ENVELOPE")
    msg = {"id": "m3", "body": "hi", "from_agent": "pm-clients", "metadata": None}
    assert mr.injection_text(msg, None) == "ENVELOPE"


def test_non_raw_metadata_uses_envelope(monkeypatch):
    monkeypatch.setattr(mr, "format_injection", lambda msg, store: "ENVELOPE")
    msg = {"id": "m4", "body": "hi", "from_agent": "gm",
           "metadata": {"approval_id": "apr_1"}}             # no deliver_raw
    assert mr.injection_text(msg, None) == "ENVELOPE"


def test_broken_metadata_falls_back_to_envelope(monkeypatch):
    monkeypatch.setattr(mr, "format_injection", lambda msg, store: "ENVELOPE")
    msg = {"id": "m5", "body": "hi", "from_agent": "gm", "metadata": "{not json"}
    assert mr.injection_text(msg, None) == "ENVELOPE"        # fail-safe


def test_held_message_empty_body_is_empty_not_envelope(monkeypatch):
    # a deliver_raw row with no body -> raw empty string (the router's own
    # empty-notification guard handles it upstream); never leaks the envelope.
    monkeypatch.setattr(mr, "format_injection", lambda msg, store: "ENVELOPE")
    msg = {"id": "m6", "body": None, "from_agent": "operator",
           "metadata": {"deliver_raw": True}}
    assert mr.injection_text(msg, None) == ""


# ---- breathe-window exemption for held rows (live-E2E finding) --------------
def test_is_held_row_true_for_held_boundary():
    assert mr._is_held_row({"metadata": {"held_for_boundary": True}}) is True

def test_is_held_row_true_for_json_string_metadata():
    import json
    assert mr._is_held_row({"metadata": json.dumps({"held_for_boundary": True})}) is True

def test_is_held_row_false_for_normal_message():
    assert mr._is_held_row({"metadata": None}) is False
    assert mr._is_held_row({"metadata": {"approval_id": "x"}}) is False

def test_is_held_row_false_on_broken_metadata():
    assert mr._is_held_row({"metadata": "{not json"}) is False   # fail-safe: keep breathe gate

def test_deliver_raw_without_held_flag_is_not_breathe_exempt():
    # deliver_raw alone (hypothetical) does NOT exempt breathe — only the explicit
    # held_for_boundary flag does, so the exemption is tightly scoped to §9.6.
    assert mr._is_held_row({"metadata": {"deliver_raw": True}}) is False


# ---- breathe-exemption extended to high priority (the operator latency push) --------
def test_breathe_exempt_critical():
    assert mr._breathe_exempt({"priority": "critical"}) is True

def test_breathe_exempt_high():
    # NEW: high-priority mail no longer eats the 10-min quiet-wait
    assert mr._breathe_exempt({"priority": "high"}) is True

def test_breathe_exempt_held_row():
    assert mr._breathe_exempt({"priority": "medium",
                               "metadata": {"held_for_boundary": True}}) is True

def test_breathe_not_exempt_medium_and_low():
    assert mr._breathe_exempt({"priority": "medium"}) is False
    assert mr._breathe_exempt({"priority": "low"}) is False
    assert mr._breathe_exempt({"priority": None}) is False
    assert mr._breathe_exempt({}) is False


# ---- R1: ◼ todo-panel false-busy (RCA gm-starvation) -----------------------
class _FakeAgentStatus:
    def __init__(self, state): self._state = state
    def get_agent_status(self, sess): return {"state": self._state}

def _patch_detector(monkeypatch, state):
    import importlib
    monkeypatch.setattr(importlib, "import_module",
                        lambda name: _FakeAgentStatus(state) if name == "agent-status"
                        else importlib.__import__(name))

def test_detector_generating_true_for_working(monkeypatch):
    _patch_detector(monkeypatch, "working")
    assert mr._detector_is_generating("gm") is True

def test_detector_generating_true_for_thinking(monkeypatch):
    _patch_detector(monkeypatch, "thinking")
    assert mr._detector_is_generating("gm") is True

def test_detector_generating_false_for_idle(monkeypatch):
    # THE fix: a persistent ◼ todo panel while the detector says idle -> NOT busy
    _patch_detector(monkeypatch, "idle")
    assert mr._detector_is_generating("gm") is False

def test_detector_generating_failsafe_busy_on_error(monkeypatch):
    import importlib
    def boom(name):
        if name == "agent-status": raise RuntimeError("detector down")
        return importlib.__import__(name)
    monkeypatch.setattr(importlib, "import_module", boom)
    assert mr._detector_is_generating("gm") is True   # fail toward hold, never clobber


# ---- R2: hold observability (throttled log + SLA escalate) -----------------
def test_note_hold_logs_once_then_throttles(monkeypatch):
    logs = []
    monkeypatch.setattr(mr, "log", lambda m: logs.append(m))
    monkeypatch.setattr(mr, "telegram", lambda m: None)
    hs = {}
    msg = {"id": "m1", "subject": "s", "created_at": "2026-08-16T21:00:00+00:00"}
    now = mr.datetime_now_epoch() if hasattr(mr, "datetime_now_epoch") else 1786000000.0
    mr.note_hold(hs, "gm", msg, "not-idle", now)
    mr.note_hold(hs, "gm", msg, "not-idle", now + 10)          # within throttle
    assert len([l for l in logs if "HELD gm" in l]) == 1        # logged once
    mr.note_hold(hs, "gm", msg, "not-idle", now + mr.HOLD_LOG_THROTTLE_S + 1)
    assert len([l for l in logs if "HELD gm" in l]) == 2        # re-logs after window

def test_note_hold_escalates_past_sla(monkeypatch):
    monkeypatch.setattr(mr, "log", lambda m: None)
    tg = []
    monkeypatch.setattr(mr, "telegram", lambda m: tg.append(m))
    hs = {}
    now = 1786000000.0
    old = {"id": "m2", "subject": "old", "created_at": mr._iso_at(now - mr.HOLD_SLA_S - 5)
           if hasattr(mr, "_iso_at") else "2026-08-16T00:00:00+00:00"}
    # force age past SLA via a fixed created_at far in the past
    old["created_at"] = "2000-01-01T00:00:00+00:00"
    mr.note_hold(hs, "gm", old, "not-idle", now)
    assert len(tg) == 1 and "HELD past SLA" in tg[0]

def test_note_hold_never_escalates_internal_bg_hydrate(monkeypatch):
    # BG hydrate is durable plumbing (the green reads it from its db via the boot
    # hook); a "not-idle" inject-hold is EXPECTED and must NEVER telegram-escalate
    # to the operator. Regression for the first-real-BG-arm notification leak (gm-g44).
    monkeypatch.setattr(mr, "log", lambda m: None)
    tg = []
    monkeypatch.setattr(mr, "telegram", lambda m: tg.append(m))
    hs = {}
    now = 1786000000.0
    for msg in (
        {"id": "h1", "subject": "[WAL DELTA] hydrate from X blue",
         "created_at": "2000-01-01T00:00:00+00:00", "source": "bg-hydrate"},
        {"id": "h2", "subject": "[WAL DELTA] hydrate",
         "created_at": "2000-01-01T00:00:00+00:00", "type": "lineage_hydrate"},
        {"id": "h3", "subject": "hydrate", "created_at": "2000-01-01T00:00:00+00:00",
         "from_agent": "lineage-daemon"},
    ):
        mr.note_hold(hs, "second-brain-dev-g5", msg, "not-idle", now)
    assert tg == [], f"internal BG hydrate must never the operator-escalate, got: {tg}"
    # sanity: a NORMAL held row to the same target still escalates
    normal = {"id": "n1", "subject": "real stranded",
              "created_at": "2000-01-01T00:00:00+00:00", "from_agent": "pm-acme"}
    mr.note_hold(hs, "second-brain-dev-g5", normal, "not-idle", now)
    assert any("HELD past SLA" in t for t in tg), "a genuine strand must still escalate"


def test_note_hold_no_escalate_under_sla(monkeypatch):
    monkeypatch.setattr(mr, "log", lambda m: None)
    tg = []
    monkeypatch.setattr(mr, "telegram", lambda m: tg.append(m))
    hs = {}
    now = 1786000000.0
    fresh = {"id": "m3", "subject": "fresh",
             "created_at": mr._iso_now(now) if hasattr(mr, "_iso_now") else None}
    # age 0 -> no escalation
    import datetime
    fresh["created_at"] = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()
    mr.note_hold(hs, "gm", fresh, "not-idle", now)
    assert tg == []


# ---- E7 bug (msg_56c5cdb8): bracketed-paste chip-aware inject verification ---
# Large/multiline payloads render as a collapsed "[Pasted text #N]" chip; the
# literal-text grep never matches -> false paste-fail -> re-paste -> chip
# accumulation wedges the composer AND the boundary lane. Verification must
# treat a NEW chip as landed, a submitted chip as submitted, and NEVER paste
# onto an existing chip (unattributable: ours-from-wedge OR a human draft).

def test_count_paste_chips():
    assert mr.count_paste_chips("\u276f [Pasted text #1] and [Pasted text #2 +9 lines]") == 2
    assert mr.count_paste_chips("\u276f plain typed text") == 0
    assert mr.count_paste_chips("") == 0


def test_paste_receipt_literal_text_still_ok():
    assert mr.paste_receipt_ok("\u276f hello world message body", "hello world message body", 0) is True


def test_paste_receipt_new_chip_counts_as_landed():
    big = "LARGE-" + "x" * 500
    assert mr.paste_receipt_ok("\u276f [Pasted text #3 +58 lines]", big, 0) is True


def test_paste_receipt_preexisting_chip_is_not_our_landing():
    assert mr.paste_receipt_ok("\u276f [Pasted text #2]", "body text here", 1) is False


def test_paste_receipt_empty_line_is_failed():
    assert mr.paste_receipt_ok("\u276f ", "body text here", 0) is False


def test_submit_ok_chip_aware():
    big = "LARGE-" + "x" * 500
    # submitted: input clear, chip marker moved to scrollback
    assert mr.submit_ok(["> [Pasted text #1]", "\u25cf working"], "\u276f ", big) is True
    # NOT submitted: chip still sits in the input line
    assert mr.submit_ok([], "\u276f [Pasted text #1]", big) is False
    # classic literal path preserved both ways
    assert mr.submit_ok(["> hello there friend"], "\u276f ", "hello there friend") is True
    assert mr.submit_ok(["> hello there friend"], "\u276f hello there friend", "hello there friend") is False


def test_stranded_chip_guard():
    assert mr.composer_has_stranded_chip("\u276f [Pasted text #1]") is True
    assert mr.composer_has_stranded_chip("\u276f [Pasted text #1] plus typed") is True
    assert mr.composer_has_stranded_chip("\u276f typed only") is False
    assert mr.composer_has_stranded_chip("") is False


# ---- §9.6-B.4 envelope lint (DEC-1787032722, Piece 2/4): FLAG-ONLY -----------
# Drive-class rows (task_request/directive/request) missing the sender envelope
# (reason + contributes_to in metadata) get logged + stamped envelope_missing —
# NEVER deprioritized/reordered/blocked in v1 (the load-bearing restraint from
# gm's APPROVE). Idempotent: an already-linted row is not re-stamped each tick.

def _lint_msg(type="task_request", md=None):
    import json as _j
    return {"id": "msg_l1", "type": type, "from_agent": "pm-x", "to_agent": "w",
            "metadata": _j.dumps(md) if isinstance(md, dict) else md}

def test_envelope_lint_flags_drive_class_missing_both():
    r = mr.envelope_lint(_lint_msg())
    assert r is not None and "reason" in r and "contributes_to" in r

def test_envelope_lint_flags_missing_one():
    r = mr.envelope_lint(_lint_msg(md={"reason": "x"}))
    assert r is not None and "contributes_to" in r and "reason" not in r.split(",")

def test_envelope_lint_passes_complete_envelope():
    assert mr.envelope_lint(_lint_msg(md={"reason": "x", "contributes_to": "s96/n1"})) is None

def test_envelope_lint_ignores_non_drive_class():
    assert mr.envelope_lint(_lint_msg(type="message")) is None
    assert mr.envelope_lint(_lint_msg(type="reply")) is None
    assert mr.envelope_lint(_lint_msg(type="status")) is None

def test_envelope_lint_idempotent_after_stamp():
    # once envelope_linted_at is stamped, later ticks return None (no re-stamp spam)
    assert mr.envelope_lint(_lint_msg(md={"envelope_linted_at": "2026-08-18T06:30:00"})) is None

def test_envelope_lint_tolerates_string_and_broken_metadata():
    assert mr.envelope_lint(_lint_msg(md=None)) is not None       # missing md = missing envelope
    assert mr.envelope_lint(_lint_msg(md="{not json")) is not None


# ---- #12 observability: hold log carries queue depth (the oldest-only log
# line sent gm's starvation trace down a wrong path — msg_66d1f98d claim 1)

def test_note_hold_logs_queue_depth(monkeypatch):
    logs = []
    monkeypatch.setattr(mr, "log", lambda m: logs.append(m))
    monkeypatch.setattr(mr, "telegram", lambda t: None)
    msg = {"id": "m1", "subject": "s", "created_at": "2026-08-18T08:00:00+00:00"}
    monkeypatch.setattr(mr, "_msg_age_s", lambda m, now: 10)
    mr.note_hold({}, "sess", msg, "not-idle", now=1.787e9, queue_n=7)
    assert any("7 queued" in l for l in logs)

def test_note_hold_queue_depth_optional(monkeypatch):
    logs = []
    monkeypatch.setattr(mr, "log", lambda m: logs.append(m))
    monkeypatch.setattr(mr, "telegram", lambda t: None)
    msg = {"id": "m1", "subject": "s", "created_at": "2026-08-18T08:00:00+00:00"}
    monkeypatch.setattr(mr, "_msg_age_s", lambda m, now: 10)
    mr.note_hold({}, "sess", msg, "not-idle", now=1.787e9)   # legacy call shape
    assert logs


# ---- leg-4 act (2): note_hold escalation TERMINATES (gm msg_c74d3ae3) -------

def _mk_hold_msg():
    return {"id": "m_stuck", "subject": "HIGH BUG", "from_agent": "gm",
            "created_at": "2026-08-17T00:34:57+00:00"}

def test_escalation_terminates_into_park(monkeypatch):
    """gm msg_86bcc168 item 5: the cap still TERMINATES the escalation loop, but by
    PARKING the row (pending, retried on idle), never dead-lettering it."""
    sent, dead, notified = [], [], []
    monkeypatch.setattr(mr, "telegram", lambda t: sent.append(t))
    monkeypatch.setattr(mr, "log", lambda m: None)
    # age of gm's real stranded row (~34h): past SLA AND past the age floor
    monkeypatch.setattr(mr, "_msg_age_s", lambda m, now: 34 * 3600)
    hs = {}
    now = 1.787e9
    batch = {}
    for i in range(mr.HOLD_ESCALATE_MAX + 2):
        mr.note_hold(hs, "arturo-proxy", _mk_hold_msg(), "not-idle",
                     now + i * (mr.HOLD_ESCALATE_REPEAT_S + 1),
                     park_fn=lambda mid, reason: dead.append((mid, reason)) or True,
                     dl_batch=batch)
    assert len(sent) == mr.HOLD_ESCALATE_MAX, f"escalated {len(sent)}x, expected cap"
    assert len(dead) == 1, "row must park exactly once"
    assert "parked" in dead[0][1]
    # batched by (sender, target-class) — one notice, not one per row
    assert list(batch) == [("gm", "service")], batch
    notices = []
    assert mr.flush_dead_letter_notices(batch, send_fn=lambda who, t: notices.append((who, t))) == 1
    assert notices[0][0] == "gm" and "service" in notices[0][1]

def test_escalation_below_cap_still_escalates(monkeypatch):
    sent = []
    monkeypatch.setattr(mr, "telegram", lambda t: sent.append(t))
    monkeypatch.setattr(mr, "log", lambda m: None)
    monkeypatch.setattr(mr, "_msg_age_s", lambda m, now: mr.HOLD_SLA_S + 1)
    hs = {}
    mr.note_hold(hs, "s", _mk_hold_msg(), "not-idle", 1.787e9)
    assert len(sent) == 1

def test_dead_letter_never_fires_below_sla(monkeypatch):
    dead = []
    monkeypatch.setattr(mr, "telegram", lambda t: None)
    monkeypatch.setattr(mr, "log", lambda m: None)
    monkeypatch.setattr(mr, "_msg_age_s", lambda m, now: 10)
    hs = {}
    for i in range(10):
        mr.note_hold(hs, "s", _mk_hold_msg(), "not-idle", 1.787e9 + i * 3600,
                     dead_letter_fn=lambda mid, reason: dead.append(mid) or True)
    assert dead == []


def test_age_floor_blocks_dead_letter_even_past_cap(monkeypatch):
    """gm bind (a): the absolute age floor is INDEPENDENT of the cap — no
    tuning of HOLD_ESCALATE_MAX can reach a young row."""
    dead = []
    monkeypatch.setattr(mr, "telegram", lambda t: None)
    monkeypatch.setattr(mr, "log", lambda m: None)
    # past SLA (so it escalates) but BELOW the dead-letter age floor
    monkeypatch.setattr(mr, "_msg_age_s", lambda m, now: mr.DEAD_LETTER_MIN_AGE_S - 1)
    hs, now = {}, 1.787e9
    for i in range(mr.HOLD_ESCALATE_MAX + 3):
        mr.note_hold(hs, "svc", _mk_hold_msg(), "not-idle",
                     now + i * (mr.HOLD_ESCALATE_REPEAT_S + 1),
                     dead_letter_fn=lambda mid, r: dead.append(mid) or True, dl_batch={})
    assert dead == [], "age floor must veto dead-letter regardless of the cap"
    assert mr.DEAD_LETTER_MIN_AGE_S >= 3600



@pytest.fixture
def _registered_agent(monkeypatch, tmp_path):
    """Hermetic registry: 'orchestra-builder' is a declared agent on this checkout's HOST, not on a
    bare CI runner. The router loads addressability.py BY PATH per call (fresh module), which
    reads ORCHESTRA_DIR at import — so point ORCHESTRA_DIR at a tmp data dir carrying the row and
    the classifier's step 5 (declared identity, offline) yields 'agent' with no live pane/store."""
    (tmp_path / "state").mkdir()
    (tmp_path / "registry.json").write_text(json.dumps(
        {"agents": {"orchestra-builder": {"name": "orchestra-builder", "tier": "T1", "runtime": "claude"},
                    "arturo-proxy": {"name": "arturo-proxy", "kind": "service"},
                    "lineage-daemon": {"name": "lineage-daemon", "kind": "daemon"},
                    "agy": {"name": "agy", "kind": "vote-slot"}}}))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    yield


def test_target_class_maps_known_non_agents(_registered_agent):
    assert mr.target_class("agy") == "vote-slot"
    assert mr.target_class("lineage-daemon") == "daemon"
    assert mr.target_class("arturo-proxy") == "service"
    assert mr.target_class("orchestra-builder") == "agent"

def test_gm_high_row_reads_as_rerouted_not_dropped():
    assert "re-commissioned to orchestra-builder" in \
        mr.DEAD_LETTER_REASON_OVERRIDE["msg_demo0002_0000002"]

def test_flush_batches_one_notice_per_sender_class():
    batch = {("gm", "service"): ["m1"], ("gm", "daemon"): ["m2", "m3"],
             ("v3", "vote-slot"): ["m4", "m5", "m6"]}
    out = []
    assert mr.flush_dead_letter_notices(batch, send_fn=lambda w, t: out.append((w, t))) == 3
    assert sum(1 for w, _ in out if w == "gm") == 2
    assert "3 message(s)" in dict((w, t) for w, t in out)["v3"]


# ---- leg-4 latent defects (DEC-1787052528; found while verifying act-2) ------
# (a) rec['dead_lettered'] latched per (session|reason) BUCKET, so after a bucket
#     terminated ONE row, every later row to that target under that reason could
#     never dead-letter AND — since n >= HOLD_ESCALATE_MAX falls to the `pass`
#     branch — stopped escalating too. It held SILENTLY forever: a noisy starvation
#     traded for a quiet one, which is strictly worse.
# (b) HOLD_FILE lived in /tmp, so a reboot reset every escalation counter and the
#     "escalation always terminates" guarantee silently voided.

def _old_row(mid, age_s=7200):
    m = _mk_hold_msg()
    m["id"] = mid
    return m


def test_second_row_in_the_same_bucket_still_terminates(monkeypatch):
    """The latch is per MESSAGE, not per (session|reason). Termination = PARK."""
    monkeypatch.setattr(mr, "telegram", lambda t: None)
    monkeypatch.setattr(mr, "log", lambda m: None)
    monkeypatch.setattr(mr, "_msg_age_s", lambda msg, now: 40 * 3600)
    hs, dead, batch = {}, [], {}
    dl = lambda mid, reason: dead.append(mid) or True
    for mid in ("msg_first", "msg_second"):
        for i in range(mr.HOLD_ESCALATE_MAX + 2):
            mr.note_hold(hs, "arturo-proxy", _old_row(mid), "not-idle",
                         1_000_000 + i * (mr.HOLD_ESCALATE_REPEAT_S + 1),
                         park_fn=dl, dl_batch=batch)
    assert dead == ["msg_first", "msg_second"], dead


def test_a_terminated_bucket_does_not_silence_a_later_row(monkeypatch):
    """After one row terminates, a NEW row to the same target must still escalate
    (visible) rather than being swallowed by the capped-bucket branch."""
    sent = []
    monkeypatch.setattr(mr, "telegram", lambda t: sent.append(t))
    monkeypatch.setattr(mr, "log", lambda m: None)
    monkeypatch.setattr(mr, "_msg_age_s", lambda msg, now: 40 * 3600)
    hs, batch = {}, {}
    dl = lambda mid, reason: True
    for i in range(mr.HOLD_ESCALATE_MAX + 2):
        mr.note_hold(hs, "arturo-proxy", _old_row("msg_first"), "not-idle",
                     1_000_000 + i * (mr.HOLD_ESCALATE_REPEAT_S + 1),
                     dead_letter_fn=dl, dl_batch=batch)
    before = len(sent)
    mr.note_hold(hs, "arturo-proxy", _old_row("msg_later"), "not-idle",
                 2_000_000, dead_letter_fn=dl, dl_batch=batch)
    assert len(sent) > before, "a later row was silently swallowed by the bucket"


def test_hold_state_is_durable_not_tmp():
    assert "/tmp" not in str(mr.HOLD_FILE), mr.HOLD_FILE
    assert str(mr.HOLD_FILE).endswith("message-router-holds.json")


def test_migration_preserves_in_flight_escalation_counters(tmp_path, monkeypatch):
    """gm bind (b): moving the file must NOT reset counters — a row three
    escalations deep must not restart at zero and become immortal."""
    legacy = tmp_path / "legacy.json"
    durable = tmp_path / "durable.json"
    legacy.write_text(json.dumps({"arturo-proxy|not-idle": {"escalations": 2,
                                                            "escalated_at": 123.0}}))
    monkeypatch.setattr(mr, "HOLD_FILE", durable)
    monkeypatch.setattr(mr, "HOLD_FILE_LEGACY", legacy)
    h = mr.load_holds()
    assert h["arturo-proxy|not-idle"]["escalations"] == 2, h
    mr.save_holds(h)
    assert json.loads(durable.read_text())["arturo-proxy|not-idle"]["escalations"] == 2


def test_migration_prefers_durable_when_both_exist(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.json"
    durable = tmp_path / "durable.json"
    legacy.write_text(json.dumps({"a|b": {"escalations": 1}}))
    durable.write_text(json.dumps({"a|b": {"escalations": 3}}))
    monkeypatch.setattr(mr, "HOLD_FILE", durable)
    monkeypatch.setattr(mr, "HOLD_FILE_LEGACY", legacy)
    assert mr.load_holds()["a|b"]["escalations"] == 3


# ---- leg-4 act 3: the router consumes the SAME classifier (no second dialect) --

def test_target_class_comes_from_the_shared_classifier_not_a_local_map():
    """_TARGET_CLASS is deleted in this change: a second dialect is how this class
    regrows (the P2-4 shared-walk lesson, gm-endorsed)."""
    assert not hasattr(mr, "_TARGET_CLASS"), "the local map must be gone"
    src = open(mr.__file__ if hasattr(mr, "__file__") else
               "scripts/message-router.py").read()
    assert "addressability" in src


def test_target_class_classifies_real_identities(_registered_agent):
    assert mr.target_class("lineage-daemon") == "daemon"
    assert mr.target_class("agy") == "vote-slot"
    assert mr.target_class("arturo-proxy") == "service"
    assert mr.target_class("orchestra-builder") == "agent"


def test_target_class_never_raises_and_defaults_to_agent(monkeypatch):
    """Fail-open on the delivery side too: an unclassifiable target must read as
    an ordinary agent, never crash the sweep."""
    monkeypatch.setattr(mr, "_classify_target", lambda t: (_ for _ in ()).throw(
        RuntimeError("boom")))
    assert mr.target_class("whatever") == "agent"


def test_no_delivery_records_the_addressability_kind(monkeypatch):
    """unknown-address stops being a catch-all: a permanently unaddressable row
    must be distinguishable from a successor that is merely still spawning."""
    lines = []
    monkeypatch.setattr(mr, "log", lines.append)
    monkeypatch.setattr(mr, "telegram", lambda t: None)
    monkeypatch.setattr(mr, "target_class", lambda t: "vote-slot")
    msg = {"id": "msg_x", "created_at": "2026-08-16T00:00:00+00:00", "subject": "s"}
    mr.handle_no_delivery(msg, "agy", "unknown-address", {}, 1_000_000.0)
    assert any("kind=vote-slot" in ln for ln in lines), lines


# ---- a PROVISIONING successor is not a live head (found by gm-gen13 MID-GATE,
# msg_060955a1; root-caused to the supervisor's own Protocol-v2 breadcrumb) ------
# Protocol v2 requires succeeded_by breadcrumbs BEFORE the successor's gate. The
# resolver followed them unconditionally, so during the gm gen-13 rotation two
# gm-bound rows were delivered to the UNGATED successor with '[fwd from gm]' and
# marked acknowledged — while the agent that received them was forbidden to act
# and the lane's actual owner never saw them. Delivered-but-unowned: the
# silent-starvation shape, opened by EVERY supervised rotation.

def _meta(**over):
    m = {"gm": {"tmux_session": "gm", "succeeded_by": "gm-gen13"},
         "gm-gen13": {"tmux_session": "gm-gen13", "status": "provisioning"}}
    m.update(over)
    return m


def test_mail_never_follows_a_breadcrumb_to_a_provisioning_successor():
    """Mid-rotation: predecessor live, successor provisioning -> deliver to the
    PREDECESSOR, who still owns the lane."""
    sess, fwd, why = mr.resolve_delivery_target("gm", {"gm", "gm-gen13"}, _meta())
    assert sess == "gm", (sess, why)
    assert fwd is None, "not a forward — the predecessor IS the addressee"


def test_provisioning_successor_with_dead_predecessor_holds_not_misdelivers():
    """If the predecessor's pane is gone mid-rotation, the row WAITS (retryable)
    rather than being steered at an agent forbidden to act on it."""
    sess, fwd, why = mr.resolve_delivery_target("gm", {"gm-gen13"}, _meta())
    assert sess is None, (sess, why)


def test_promoted_successor_is_followed_normally():
    m = _meta()
    m["gm-gen13"]["status"] = "online"
    sess, fwd, why = mr.resolve_delivery_target("gm", {"gm-gen13"}, m)
    assert sess == "gm-gen13" and fwd == "gm" and why == "succeeded-by-chain"


def test_chain_through_a_retired_member_to_a_live_gated_head_still_works():
    m = {"a": {"tmux_session": "a-old", "succeeded_by": "b"},
         "b": {"tmux_session": "b", "succeeded_by": "c", "status": "retired"},
         "c": {"tmux_session": "c", "status": "online"}}
    sess, fwd, why = mr.resolve_delivery_target("a", {"c"}, m)
    assert sess == "c" and fwd == "a"


# ---- successor-ungated must ESCALATE on age, not wait quietly (gm-gen13's
# residual, msg_e5eccb58) --------------------------------------------------------
# 'A hold that stops escalating is silent forever' — the per-bucket dead-letter
# latch, as a resting state. If a gate never passes (successor fails, rotation
# abandoned, supervisor dies mid-gate), rows waiting behind it were LOG-ONLY after
# 1h and hard-expired at 48h: quiet destruction of the canonical lane's mail at
# exactly the moment nobody is attending.

def test_successor_ungated_telegram_escalates_after_the_grace_hour(monkeypatch):
    sent, logged = [], []
    monkeypatch.setattr(mr, "telegram", lambda t: sent.append(t))
    monkeypatch.setattr(mr, "log", logged.append)
    msg = {"id": "msg_x", "created_at": "2026-08-18T10:00:00+00:00", "subject": "s"}
    mr.handle_no_delivery(msg, "gm", "successor-ungated", {}, 1_900_000_000.0)
    assert sent, "a rotation stuck >1h with queued mail is a human-worthy signal"
    assert any("successor-ungated" in t or "rotation" in t.lower() for t in sent)


def test_successor_ungated_stays_quiet_inside_the_grace_hour(monkeypatch):
    """A normal gate takes ~15min; no noise during a healthy rotation window."""
    import datetime
    sent = []
    monkeypatch.setattr(mr, "telegram", lambda t: sent.append(t))
    monkeypatch.setattr(mr, "log", lambda m: None)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    fresh = (now_dt - datetime.timedelta(minutes=20)).isoformat()
    msg = {"id": "msg_y", "created_at": fresh, "subject": "s"}
    import time as _t
    mr.handle_no_delivery(msg, "gm", "successor-ungated", {}, _t.time())
    assert not sent


# --- Gemini busy detection and deduplication tests --------------------------

def test_gemini_agent_is_idle_detects_esc_to_cancel_as_busy(monkeypatch):
    busy_pane = """
● Bash(npm test)
──────────────────────────────────────────────────
>
──────────────────────────────────────────────────
esc to cancel                                 Gemini 3.1 Pro · high
"""
    class MockRes:
        returncode = 0
        stdout = busy_pane
    monkeypatch.setattr(mr, "tmux", lambda *args, **kwargs: MockRes())
    monkeypatch.setattr(mr.time, "sleep", lambda *a: None)
    assert mr.agent_is_idle("gemini-test", runtime="gemini") is False


def test_gemini_agent_is_idle_detects_spinners_and_generating_as_busy(monkeypatch):
    busy_pane = """
⣽  Generating...
──────────────────────────────────────────────────
>
──────────────────────────────────────────────────
? for shortcuts                               Gemini 3.1 Pro · high
"""
    class MockRes:
        returncode = 0
        stdout = busy_pane
    monkeypatch.setattr(mr, "tmux", lambda *args, **kwargs: MockRes())
    monkeypatch.setattr(mr.time, "sleep", lambda *a: None)
    assert mr.agent_is_idle("gemini-test", runtime="gemini") is False


def test_gemini_agent_is_idle_returns_true_when_settled_with_shortcuts(monkeypatch):
    idle_pane = """
● Finished all work cleanly.
──────────────────────────────────────────────────
> 
──────────────────────────────────────────────────
? for shortcuts                               Gemini 3.1 Pro · high
"""
    class MockRes:
        returncode = 0
        stdout = idle_pane
    monkeypatch.setattr(mr, "tmux", lambda *args, **kwargs: MockRes())
    monkeypatch.setattr(mr.time, "sleep", lambda *a: None)
    assert mr.agent_is_idle("gemini-test", runtime="gemini") is True


def test_inject_skips_repaste_when_probe_already_in_input_line(monkeypatch):
    msg_text = "[MSG from sender | medium] Test probe message"
    probe = msg_text[:60]
    
    pastes = []
    keys = []
    
    def mock_tmux(*args, **kwargs):
        class Res:
            returncode = 0
            stdout = ""
        cmd = args[0]
        if cmd == "paste-buffer":
            pastes.append(args)
        elif cmd == "send-keys":
            keys.append(args)
        elif cmd == "display-message":
            res = Res()
            res.stdout = "0"
            return res
        return Res()
        
    monkeypatch.setattr(mr, "tmux", mock_tmux)
    monkeypatch.setattr(mr.time, "sleep", lambda *a: None)
    monkeypatch.setattr(mr, "pane_split", lambda s, r: (["prior scrollback"], f"> {probe}", True))
    monkeypatch.setattr(mr, "submit_ok", lambda scroll, inp, pr: True)
    
    res = mr.inject("gemini-test", msg_text, runtime="gemini")
    assert res == "submitted"
    assert len(pastes) == 0, "must NOT paste when probe is already present in input line"
    assert any("Enter" in k for k in keys), "must attempt Enter submission"

