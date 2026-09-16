#!/usr/bin/env python3
"""Stage C — the Stop-hook completion-check ENTRY (PRIMARY trigger, scope §39).

A Stop hook fires at the PREDECESSOR's OWN turn boundary — structurally immune to
the frozen-dead-pane false-fire class the daemon beat suffers (a dead pane has no
turn boundary -> never fires). This is the PRIMARY graduation-completion trigger;
the lineage-daemon beat is a BACKSTOP only.

Thin by construction: read the hook JSON -> resolve the predecessor agent-id from
its session_id -> dispatch DRY. It computes the plan (keep / would-card /
would-execute) but takes NO live action — arming the card live is a SEPARATE the operator
go. Registering this hook in settings.json is itself the arming step (a live hook
is a MOVED SURFACE); this build ships the entry UNREGISTERED.

HARD CONTRACT (mirrors rotation-self-trigger.js / state-event-hook / gm-queue-drain):
ALWAYS exit 0, never wedge a turn; every error is swallowed.
"""
import json
import sys

from scripts.lineage_daemon import graduation_dispatch as GD
from scripts.lineage_daemon import graduation_retire as GR

# The six real executor seams dispatch_graduation / execute_graduation_retire
# consume. Threaded in here so an ARMED dispatch has REAL {kill_gate1_fn,
# promote_fn, retire_fn, create_fn, notify_fn, skip_notify_fn} — before this the
# hook passed NONE, so an armed proceed_no_card would call None(seat) and crash
# (silently skipping the retire). Wiring them changes NO default: dispatch still
# runs DRY (armed=False), and arming remains `armed=True` + settings.json
# registration (neither done here).
_EXECUTOR_KEYS = ("kill_gate1_fn", "promote_fn", "retire_fn",
                  "create_fn", "notify_fn", "skip_notify_fn")


def _live_executors():
    """The real executor dict, built lazily (its adapters pull live IO). Module
    indirection so tests stub it without importing live state."""
    from scripts.lineage_daemon import graduation_executors as GE
    return GE.live_dispatch_executors()


def run_stop_hook(data, *, resolve_pred, readers_for,
                  disabled_path=GR._DEFAULT_DISABLED_PATH, armed=False,
                  executors=None, **dispatch_kw):
    """Pure over injected seams. Returns a trace dict (never raises).
      resolve_pred(session_id) -> predecessor agent-id, or None if this session is
        not a lineage predecessor with a graded successor (the common case -> noop).
      readers_for(pred) -> the live-state readers dict graduation_dispatch consumes.
      executors -> the six real dispatch executor seams. None (the production
        default) => build the live executors; an explicit dict (tests) is used
        verbatim and the live build is NEVER touched. An explicit dispatch_kw seam
        (e.g. a test's kill_gate1_fn) always WINS over the executor dict.
    """
    try:
        sid = (data or {}).get("session_id")
        if not sid:
            return {"status": "noop:no-session"}
        pred = resolve_pred(sid)
        if not pred:
            return {"status": "noop:not-a-lineage-predecessor"}
        readers = readers_for(pred)
        # Resolve the executor seams. Build the live set only when none injected;
        # a build failure is swallowed to an empty set (dispatch then runs DRY-safe)
        # to preserve the HARD CONTRACT (never wedge a turn on an executor issue).
        if executors is None:
            try:
                executors = _live_executors()
            except Exception:  # noqa: BLE001 -- degrade to no-executor DRY dispatch
                executors = {}
        # Explicit dispatch_kw seams win over the executor dict (test injection).
        seams = {k: executors[k] for k in _EXECUTOR_KEYS
                 if k in executors and k not in dispatch_kw}
        seams.update(dispatch_kw)
        disp = GD.dispatch_graduation(pred, readers, disabled_path=disabled_path,
                                      armed=armed, **seams)
        return {"status": "dispatched", "pred": pred, "dispatch": disp}
    except Exception as e:  # noqa: BLE001 -- HARD CONTRACT: never wedge a turn
        return {"status": f"error:{type(e).__name__}", "error": str(e)}


def main(argv=None, *, stdin_text=None, resolve_pred=None, readers_for=None) -> int:
    """CLI/hook entry. Reads the hook JSON from stdin, runs the check, ALWAYS
    exits 0. resolve_pred/readers_for are injected in tests; production wires the
    real live resolvers from graduation_resolvers.live_resolvers() below, and
    run_stop_hook binds the real executor seams (graduation_executors) by default.

    NOTE: wiring the live resolvers + executors here does NOT arm anything —
    dispatch still runs DRY (armed=False by default), so the real promote/retire/
    card seams are BUILT but never CALLED. Registering this hook in settings.json
    remains the separate the operator-gated arming step. Until registered the entry is never
    invoked; wired-but-unregistered = inert. Resolver acquisition is itself
    fail-safe: any import/setup error falls back to the historical noop:unwired
    trace."""
    try:
        raw = stdin_text if stdin_text is not None else sys.stdin.read()
        data = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        data = {}
    if resolve_pred is None or readers_for is None:
        # Production path: bind the real live resolvers (read-only over the live
        # 3-store world). Fail-safe: any acquisition error keeps the pre-wiring
        # inert behaviour rather than wedging the turn.
        try:
            from scripts.lineage_daemon import graduation_resolvers as GRS
            live_rp, live_rf = GRS.live_resolvers()
            resolve_pred = resolve_pred or live_rp
            readers_for = readers_for or live_rf
        except Exception:  # noqa: BLE001 -- HARD CONTRACT: never wedge a turn
            print(json.dumps({"status": "noop:unwired"}))
            return 0
    out = run_stop_hook(data, resolve_pred=resolve_pred, readers_for=readers_for)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
