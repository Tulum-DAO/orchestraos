#!/usr/bin/env python3
"""One-off GEMINI fire-driver for demo-gemini-pred (gm-authorized forced-swap mechanics
proof; the operator: Gemini-only, Codex deferred). Wires the REAL arm-time hooks (finding #1
sid-register + #5 WAL-capture-with-backfill) + green_wake (#3/#4) + real succession seams,
and drives a FORCED swap end-to-end (OQ-3), then verify_bg_fire --verify.

STAGES (each verified BY EFFECT; run incrementally):
  1-3 arm  : register_sid + start_capture + conformance-gated arm (arm_lineage hooks)
  4   fire : forced BgArm beats SOLO->PREWARMING->READY(wake+ingest)->SWAP->promote->reap
  5   verify: verify_bg_fire.py --verify

NO hand-staging: the sid-map + WAL must appear via the hooks (arming), not by hand.
"""
import argparse
import os
import sys

SEAT = "demo-gemini-pred"
ORCH = os.environ.get("ORCHESTRA_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BOTH paths: repo-root (so internal `from scripts.X` imports resolve — identity_writer)
# AND scripts/ (so bare `from lineage_daemon.X` / `from identity_store.X` resolve).
sys.path.insert(0, os.path.join(ORCH, "scripts"))
sys.path.insert(0, ORCH)
WAL = os.path.join(ORCH, "state", "wal")
# (iii) check-7 LOSSLESS probe: a real checklist the green ADVANCES (same shape the Claude
# drill used) so verify_bg_fire measures a lossless resume by effect, never waived.
CHECKLIST = os.path.join(WAL, f"{SEAT}.drill-checklist.md")
_INGEST_TEXT = ("ingest your hydrate digest, then prove lossless resume by advancing the "
                f"checklist at {CHECKLIST} — change the first '[ ] Step N' line to '[x] "
                "Step N' (append 'DONE step N' on its own line) — then hold.")


def log(m):
    print(f"[fire-gemini] {m}", flush=True)


def _seat_lineage():
    """The seat's declared runtime + model from the canonical lineage (read_canonical_blue's
    #15 runtime join) — the SINGLE source of the seat's runtime, so the driver never hardwires
    or guesses one. Fail-soft: () -> (None, None)."""
    try:
        from lineage_daemon.wal.bg_beat import read_canonical_blue
        b = read_canonical_blue(ORCH, SEAT)
        return b.get("runtime"), b.get("model")
    except Exception as e:  # noqa: BLE001
        log(f"_seat_lineage note: {e}")
        return None, None


def register_sid(seat):
    """FINDING #1 (provider-agnostic): DELEGATES to the shared PRODUCTION hook
    (arm_hooks.make_register_sid_fn) so the demo driver and the operator arm-path run ONE
    converged implementation and can never drift (B2). Runtime/model come from the lineage,
    never a literal — the SAME hook arms a claude/gemini/codex seat unchanged."""
    from lineage_daemon.wal.arm_hooks import make_register_sid_fn
    runtime, _model = _seat_lineage()
    cid = make_register_sid_fn(ORCH, WAL)(seat)
    log(f"#1 register_sid: {seat} (runtime={runtime}) -> LIVE sid={cid}")
    return cid


def start_capture(seat):
    """FINDING #5 (provider-agnostic): DELEGATES to the shared PRODUCTION hook
    (arm_hooks.make_start_capture_fn) — WAL capture WITH BACKFILL via the runtime-dispatched
    adapter (make_adapter(runtime) + source_path_for(runtime,cid)). ONE converged impl shared
    with the operator arm-path (B2); no provider hardwiring in the driver."""
    from lineage_daemon.wal.arm_hooks import make_start_capture_fn
    runtime, _model = _seat_lineage()
    n = make_start_capture_fn(ORCH, WAL)(seat)
    log(f"#5 start_capture (runtime={runtime}): backfilled {n} WAL events")
    return n


def stage_arm():
    """STAGES 1-3: arm_lineage runs the #1/#5 hooks BEFORE the conformance gate, so the
    unstaged seat becomes readable+hydratable by ARMING alone, then arms if conformant."""
    from lineage_daemon.wal.bg_state import arm_lineage, is_armed
    from lineage_daemon.wal.ctx_adapters import read_ctx
    runtime, _model = _seat_lineage()
    log(f"BEFORE: runtime={runtime} read_ctx={read_ctx(runtime, SEAT)}")
    path = arm_lineage(WAL, SEAT, runtime, seat=SEAT,
                       register_sid_fn=register_sid,
                       start_capture_fn=start_capture)
    log(f"ARMED: flag={path}")
    # verify by effect — #1 hook error breadcrumb + the sid the way build_obs reads it
    # (agent-sessions.json, the document-store projection). project so the flat file is
    # fresh, then read it (this is exactly the live_sid_fn path the beat uses).
    from lineage_daemon.wal.bg_state import BgStateStore
    err = BgStateStore(WAL, SEAT).read_meta("arm_sid_register_error")
    import json
    try:
        from identity_store import identity_writer
        identity_writer.project_now(ORCH)
    except Exception as e:  # noqa: BLE001
        log(f"project_now note: {e}")
    db_sid = (json.load(open(os.path.join(ORCH, "state", "agent-sessions.json"))
                        ).get(SEAT) or {}).get("session_id")
    from lineage_daemon.wal.store import WalStore
    ws = WalStore(os.path.join(WAL, f"{SEAT}.db")); nwal = len(ws.events(SEAT)); ws.close()
    log(f"VERIFY by effect: DB sid-map={db_sid} | arm_sid_register_error={err} | "
        f"WAL events={nwal} | is_armed={is_armed(WAL, SEAT)} | read_ctx={read_ctx(runtime, SEAT)}")
    assert not err, f"#1 register hook raised: {err}"
    assert db_sid, "sid NOT mapped in DB (#1 failed)"
    assert nwal > 0, "WAL empty (#5 failed)"
    assert is_armed(WAL, SEAT), "not armed"
    log("STAGES 1-3 PASS by effect (#1 sid-map + #5 WAL both appeared via ARMING, no hand-staging)")


def _green_wake_fn_factory():
    """Real GEMINI green waker (#4 over the gemini adapter): inject 'ingest…' into the
    green's tmux pane + confirm the turn STARTED by effect (the green's brain declares
    'You are <green_alias>' once it boots), with a bounded Enter-resend (#3)."""
    import subprocess
    from lineage_daemon.wal.green_wake import wake_green_to_ingest
    from lineage_daemon.wal.ctx_adapters import resolve_cid_any

    from lineage_daemon.wal.bg_state import BgStateStore

    def wake(green_alias):
        st = BgStateStore(WAL, SEAT)

        def inject(text):
            subprocess.run(["tmux", "send-keys", "-t", green_alias, "-l", text],
                           capture_output=True)
            subprocess.run(["tmux", "send-keys", "-t", green_alias, "Enter"],
                           capture_output=True)

        def probe_started():
            # by effect (provider-agnostic): the green declared 'You are <green_alias>' in
            # its own runtime store once it took its first turn — resolvable via resolve_cid_any.
            return resolve_cid_any(green_alias) is not None

        def reenter():
            subprocess.run(["tmux", "send-keys", "-t", green_alias, "Enter"],
                           capture_output=True)

        def probe_idle():
            # (c) green-IDLE by effect: the pane shows no running turn / no queued composer
            # text. Prevents queuing a duplicate ingest behind the green's boot turn.
            out = subprocess.run(["tmux", "capture-pane", "-t", green_alias, "-p"],
                                 capture_output=True, text=True)
            tail = (out.stdout or "")[-1200:].lower()
            busy = ("running" in tail or "esc to interrupt" in tail
                    or "queued message" in tail)
            return not busy

        # (c) idempotent across beats: send the ingest text ONCE (persisted in bg_state),
        # then resend (Enter nudge) only when the green is IDLE — never a queued duplicate.
        started = wake_green_to_ingest(
            inject, probe_started, reenter, ingest_text=_INGEST_TEXT, max_retries=3,
            sleep_fn=lambda: __import__("time").sleep(3),
            probe_idle_fn=probe_idle,
            already_sent_fn=lambda: bool(st.read_meta("ingest_wake_sent")),
            mark_sent_fn=lambda: st.write_meta("ingest_wake_sent", True))
        log(f"#4 wake_green({green_alias}): started_by_effect={started}")
        return started
    return wake


def _fleet_beat_disabled_path():
    """The AUTHORITATIVE e-brake (fleet.py kill_switch_engaged): ~/runtime/FLEET_BEAT_DISABLED
    (or $ORCH_RUNTIME_DIR). Halts the cron beat instantly; the in-tree state/ path is
    ADVISORY ONLY (a synced file a stale Mac copy could toggle). NOT BG_DISABLED — fleet
    law: BG_DISABLED is absent by design, FLEET_BEAT_DISABLED is the only abort."""
    rt = os.environ.get("ORCH_RUNTIME_DIR", os.path.expanduser("~/runtime"))
    return os.path.join(rt, "FLEET_BEAT_DISABLED")


def _guard_set():
    p = _fleet_beat_disabled_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    log(f"GUARD set: {p} present -> cron beat braked (my forced driver bypasses it)")
    return p


def _guard_clear():
    p = _fleet_beat_disabled_path()
    existed = os.path.exists(p)
    if existed:
        os.remove(p)
    ok = not os.path.exists(p)
    log(f"GUARD cleared: {p} absent={ok} (fleet beat restored)")
    return ok


def _checklist_advanced():
    """(iii) True once the green has advanced the drill checklist past Step 1 (its lossless-
    resume proof by effect) — i.e. Step 2 is marked done. Read-only poll."""
    try:
        txt = open(CHECKLIST).read()
        import re
        return bool(re.search(r"\[x\]\s*Step\s*2", txt, re.I) or re.search(r"DONE step 2", txt, re.I))
    except OSError:
        return False


def _mk_obs():
    """CONVERGENCE (gm ruling A): build obs via the PRODUCTION bg_beat.build_obs — so the
    green's session_id is threaded from its .sid (canonical attributed at promote) + the
    live blue sid + all production fields, exactly like the fleet beat. The ONLY drill
    override is ctx_pct=0.85 (the forced swap trigger — the real 0.31 read already proved
    the trigger separately); everything else is production code on the production path."""
    from lineage_daemon.wal.bg_beat import build_obs, read_canonical_blue, _resolve_live_blue_sid
    blue = read_canonical_blue(ORCH, SEAT)
    agent = {"agent_id": SEAT, "runtime": blue.get("runtime"),
             "ctx": {"status_bar_pct": 85},
             "death": {"state": "idle", "state_age_s": 0}}
    obs = build_obs(agent, blue, wal_dir=WAL,
                    live_sid_fn=lambda r: _resolve_live_blue_sid(ORCH, r))
    obs["ctx_pct"], obs["ctx_source"], obs["ctx_unknown"] = 0.85, "adapter", False
    obs["ceiling_calibrated"] = True
    return obs, blue


def stage_fire(max_beats=8):
    """STAGE 4: drive a FORCED swap end-to-end, then the production COMPLETION phase
    (bg_complete) so the reap runs -> DRAINED. Beat-by-beat, by effect. CONVERGENCE: no
    hand-rolled obs, no disabled blue-pid resolver — production functions on both paths."""
    import subprocess
    from lineage_daemon.wal.bg_arm import BgArm
    from lineage_daemon.wal.bg_beat import real_seams_for, _green_ingested_seq_reader
    from lineage_daemon.wal.bg_state import BgStateStore
    from lineage_daemon.wal.ctx_adapters import resolve_cid_any
    from lineage_daemon.wal import bg_complete

    cid = resolve_cid_any(SEAT)
    obs0, blue0 = _mk_obs()
    blue_generation_id = blue0["blue_generation_id"]
    green_gen = blue0["generation"] + 1
    green_alias = f"{SEAT}-g{green_gen}"
    seams = real_seams_for(SEAT, ORCH,
                           {"generation": blue0["generation"], "model": blue0.get("model"),
                            "blue_generation_id": blue_generation_id, "session_id": cid,
                            # #15: thread the seat runtime (from the lineage) so the promoted
                            # green gets a per-runtime resume_command via resume_command_for.
                            "runtime": blue0.get("runtime")},
                           wal_dir=WAL)
    # (i) NO blue_pane_pid_fn override — BgArm uses _default_blue_pane_pid_fn (production),
    # which records the LIVE bare canonical pane pid at prewarm so complete_swap can reap.
    arm = BgArm(WAL, SEAT, seams=seams, cutover_active=lambda: True, forced=True,
                green_wake_fn=_green_wake_fn_factory(),
                green_ingested_seq_fn=_green_ingested_seq_reader(ORCH))
    # Inter-beat pacing: a REAL antigravity green needs wall-clock to boot + read the ingest
    # instruction + advance the checklist (its check-7 lossless proof). Production beats are
    # seconds-to-minutes apart, not back-to-back; FIRE_BEAT_INTERVAL_S gives a slow real green
    # that time. Default 0 preserves the historical demo-gemini-pred behavior 1:1.
    _beat_interval = int(os.environ.get("FIRE_BEAT_INTERVAL_S", "0"))
    log(f"STAGE 4 forced fire: green_alias={green_alias}, blue_gen_id={blue_generation_id}, "
        f"obs ctx gated on checklist advance (prewarm until the green proves lossless resume); "
        f"inter-beat interval={_beat_interval}s")
    for beat in range(1, max_beats + 1):
        st_before = BgStateStore(WAL, SEAT).read()["state"]
        obs, _ = _mk_obs()   # rebuild each beat (production pattern; green sid appears once booted)
        # (iii) LOSSLESS gate: hold ctx at PREWARM (below SWAP) until the green has ACTUALLY
        # advanced the checklist (its real resume work) — only THEN raise ctx to trigger the
        # swap. So the forced promote never races ahead of the green's lossless proof; the
        # idle-resend (#c) submits the wake once the green finishes booting. Mirrors a real
        # rotation (promote only a green that PROVED it resumed).
        obs["ctx_pct"] = 0.85 if _checklist_advanced() else 0.72
        try:
            arm.beat(obs)
        except Exception as e:  # noqa: BLE001 — surface the exact OQ-3 gap by effect
            import traceback
            log(f"beat {beat}: state {st_before} -> RAISED {type(e).__name__}: {e}")
            traceback.print_exc()
            from lineage_daemon.wal.bg_beat import prune_failed_green
            prune_failed_green(ORCH, WAL, SEAT, green_gen, green_alias)
            log(f"(d) prune-on-fail: pruned provisional {green_alias} gen={green_gen} "
                f"+ reaped pane + reset SOLO")
            break
        st = BgStateStore(WAL, SEAT).read()["state"]
        gcid = resolve_cid_any(green_alias)
        has_pane = subprocess.run(["tmux", "has-session", "-t", green_alias],
                                  capture_output=True).returncode == 0
        log(f"beat {beat}: {st_before} -> {st} | green_pane={has_pane} green_cid={gcid}")
        if st == "DEGRADED":
            # PRODUCTION COMPLETION (bg bar #5): the async swap left DEGRADED
            # (effects-incomplete); drive the reap -> DRAINED exactly as bg_supervise_fleet
            # does after arm.beat. Reap uses the blue_pane_pid recorded at prewarm.
            comp = bg_complete.complete_swap(
                SEAT, wal_dir=WAL, seams=seams, blue_generation_id=blue_generation_id,
                orchestra_dir=ORCH, expected_green_generation=green_gen)
            st2 = BgStateStore(WAL, SEAT).read()["state"]
            log(f"  completion: {comp} -> state {st2}")
            break
        if st in ("DRAINED", "RETIRE_PENDING"):
            log(f"STAGE 4 terminal state: {st}")
            break
        # Non-terminal: give a real green wall-clock time before the next beat re-probes
        # ingestion + checklist advance (idempotent wake resends only when the green is idle).
        if _beat_interval and beat < max_beats:
            log(f"  inter-beat wait {_beat_interval}s (green booting / advancing checklist; "
                f"checklist_advanced={_checklist_advanced()})")
            __import__("time").sleep(_beat_interval)


def _setup_checklist_and_snapshot():
    """(iii) Seed the drill checklist (Step 1 done, Step 2 pending = the NEXT the green must
    advance) and record the BEFORE snapshot keyed on it, so verify_bg_fire check-7 measures a
    real lossless resume (the green advances Step 2 during the fire)."""
    import subprocess
    with open(CHECKLIST, "w") as fh:
        fh.write("# demo-gemini-pred lossless-resume drill checklist\n"
                 "- [x] Step 1\n- [ ] Step 2\n- [ ] Step 3\n")
    r = subprocess.run(["python3", os.path.join("scripts", "verify_bg_fire.py"),
                        "--root", SEAT, "--snapshot", "--progress", CHECKLIST],
                       capture_output=True, text=True)
    log(f"BEFORE snapshot (progress={CHECKLIST}): rc={r.returncode} {r.stdout.strip()[-120:]}")


def _verify_bar():
    import subprocess
    r = subprocess.run(["python3", os.path.join("scripts", "verify_bg_fire.py"),
                        "--root", SEAT, "--verify", "--progress", CHECKLIST],
                       capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr)
    return r.stdout


def main():
    # Declared global at the top (before the default=SEAT read below) so --seat can
    # retarget the three seat-derived module globals the stage functions all key off.
    global SEAT, CHECKLIST, _INGEST_TEXT
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["arm", "fire", "verify", "all"], default="arm")
    ap.add_argument("--max-beats", type=int, default=8)
    ap.add_argument("--seat", default=SEAT,
                    help="the gemini blue seat to fire (default demo-gemini-pred). Point at a "
                         "FRESH provisioned seat (e.g. demo-gemini-pred2) so the drained "
                         "demo-gemini-pred proof artifact is never touched. Reassigns the three "
                         "seat-derived module globals (SEAT/CHECKLIST/_INGEST_TEXT) so every "
                         "stage function keys off the chosen seat.")
    args = ap.parse_args()
    # Parameterize the seat: the stage functions all read the module-level SEAT (and the
    # CHECKLIST/_INGEST_TEXT derived from it). Reassign all three so --seat retargets the
    # whole driver; omitting --seat preserves the historical demo-gemini-pred behavior 1:1.
    if args.seat != SEAT:
        SEAT = args.seat
        CHECKLIST = os.path.join(WAL, f"{SEAT}.drill-checklist.md")
        _INGEST_TEXT = ("ingest your hydrate digest, then prove lossless resume by advancing "
                        f"the checklist at {CHECKLIST} — change the first '[ ] Step N' line to "
                        "'[x] Step N' (append 'DONE step N' on its own line) — then hold.")
        log(f"seat retargeted -> {SEAT} (CHECKLIST={CHECKLIST})")
    if args.stage in ("arm", "all"):
        stage_arm()
    if args.stage in ("fire", "all"):
        _setup_checklist_and_snapshot()   # (iii) BEFORE snapshot for check-7
        # Race guard: FLEET_BEAT_DISABLED (the authoritative e-brake) halts the cron beat
        # while the supervised forced drive runs; ALWAYS cleared in finally (fleet law:
        # absent by design). NOT BG_DISABLED.
        _guard_set()
        try:
            stage_fire(max_beats=args.max_beats)
        finally:
            _guard_clear()
    if args.stage in ("verify", "all"):
        _verify_bar()                     # verify_bg_fire 11/11 bar
    return 0


if __name__ == "__main__":
    sys.exit(main())
