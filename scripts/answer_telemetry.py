"""R7b delivery telemetry (SPEC docs/SPEC_r7-answer-path-package.md §R7b, the operator's
ruling 2026-08-25).

Append-only JSONL of the answer lifecycle: submit-tap (`answer_submitted`, at
apply_answer once the row is recorded) -> delivery-confirmation
(`delivery_confirmed` / `delivery_failed`, at each delivery lane). Every delivery
line carries `lane` + `reason` + `latency_ms` (confirm-time minus the row's
`answered_at`, i.e. submit-tap -> menu-disappear / inject-ack).

Purpose: MEASURE, before building the fallback big, whether the operator's hypothesis
holds — in-agent pane menus (`menu_keypress` / `menu_free_text`) only disappear
when the agent actually received the selection, so `menu_gone` failures are
in-agent-menu-lane-only, while approvals/authored decisions
(`pane_inject` / `authored_menu_inject` / `coalesce_inject`) deliver durably via
inject/msg and do not exhibit the drop.

Best-effort ONLY: every emit is wrapped so a telemetry IO / serialization
failure can NEVER raise into the answer path. A missing/broken log degrades to
"no measurement," never to "answers stop delivering." No live-bus / tmux / DB
writes here — this module only appends to its own JSONL file."""
import os
import sys
import json
from datetime import datetime, timezone

LANES = ("menu_keypress", "menu_free_text", "pane_inject",
         "authored_menu_inject", "coalesce_inject", "qnr_digest_inject")


def _path():
    """The durable telemetry log path. ANSWER_TELEMETRY_PATH overrides (tests +
    ops point it elsewhere); default is the gitignored runtime log."""
    env = os.environ.get("ANSWER_TELEMETRY_PATH")
    if env:
        return env
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "answer-telemetry.jsonl")


def _now():
    return datetime.now(timezone.utc)


def _latency_ms(answered_at, now=None):
    """Milliseconds from the submit-tap (`answered_at`) to now. None when the
    row has no answered_at (e.g. a non-applied/unknown row) or the timestamp is
    unparseable — never raises."""
    if not answered_at:
        return None
    try:
        a = datetime.fromisoformat(str(answered_at))
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        n = now or _now()
        return round((n - a).total_seconds() * 1000, 3)
    except Exception:  # noqa: BLE001 — telemetry never raises into the answer path
        return None


def _emit(rec):
    try:
        rec.setdefault("ts", _now().isoformat())
        path = _path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    except Exception as e:  # noqa: BLE001 — best-effort; must never break delivery
        print(f"[answer_telemetry] emit failed: {e}", file=sys.stderr)


def log_submit(row):
    """The submit-tap: an answer was recorded onto a row (apply_answer)."""
    row = row or {}
    _emit({"event": "answer_submitted",
           "id": row.get("id"),
           "from_agent": row.get("from_agent"),
           "kind": row.get("kind"),
           "worker_kind": row.get("worker_kind"),
           "answer": row.get("answer"),
           "option_n": row.get("option_n"),
           "answered_at": row.get("answered_at")})


def log_delivery(row, *, lane, ok, reason=None, via=None, now=None):
    """A delivery attempt outcome for a recorded answer. `lane` names the
    transport (see LANES); `ok` -> delivery_confirmed else delivery_failed;
    `reason` is the transport's failure/success reason. `latency_ms` = submit-tap
    -> this confirmation. `via` marks a distinguishable delivery channel (e.g.
    'fallback_durable' for the R7b menu_gone fallback).

    The record carries `kind` + `provider` (from the row) alongside `lane` so the
    log can be PARTITIONED by lane x kind x runtime — that partition is what
    confirms/refutes the operator's hypothesis (the drop class is in-agent-menu-lane
    only) directly from the data (all-model-parity rider 1)."""
    row = row or {}
    _emit({"event": "delivery_confirmed" if ok else "delivery_failed",
           "id": row.get("id"),
           "from_agent": row.get("from_agent"),
           "lane": lane,
           "kind": row.get("kind"),
           "worker_kind": row.get("worker_kind"),
           "provider": row.get("provider"),
           "reason": reason,
           "delivered_via": via,
           "answered_at": row.get("answered_at"),
           "latency_ms": _latency_ms(row.get("answered_at"), now=now)})
