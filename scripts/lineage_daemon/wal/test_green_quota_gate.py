"""RED (gm msg_7b3ffa11 GO, by effect 2026-09-16 01:32Z): leg (ii) promoted a codex green whose
workspace credits were depleted (rollout 01a0a4fe latest token_count.rate_limits: limit_id
premium, credits.has_credits false, rate_limit_reached_type workspace_owner_credits_depleted).
The readiness/swap gates had no vendor-quota check. Contract: GREEN_QUOTA_READERS[runtime]
(data lookup, no runtime literals in control flow) -> {exhausted, kind, limit_id, reset_at} or
None (unknown); consumed at three seams: before the first wake (PREWARMING), the READY write,
and the READY-gate before swap. Positive => HOLD + breadcrumb + one deduped alarm, never
prune/respawn. None/unreadable => proceed as today. Death swaps never gated, breadcrumbed."""
import json
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import green_quota as gq  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "codex-seat"
CREDITS = {"limit_id": "premium", "limit_name": None, "primary": None, "secondary": None,
           "credits": {"has_credits": False, "unlimited": False, "balance": None},
           "individual_limit": None, "spend_control_reached": None, "plan_type": "team",
           "rate_limit_reached_type": "workspace_owner_credits_depleted"}
WINDOW_OK = {"limit_id": "codex", "primary": {"used_percent": 83, "resets_at": 1789523880},
             "secondary": {"used_percent": 36}, "credits": {"has_credits": True,
             "unlimited": False}, "plan_type": "team", "rate_limit_reached_type": None}
WINDOW_HIT = {**WINDOW_OK, "primary": {"used_percent": 100, "resets_at": 1789523880},
              "rate_limit_reached_type": "primary_window"}


def _rollout(tmp_path, records):
    p = tmp_path / "rollout-x.jsonl"
    lines = [json.dumps({"type": "session_meta", "payload": {"id": "x"}})]
    for i, rl in enumerate(records):
        lines.append(json.dumps({"timestamp": f"2026-09-16T01:0{i}:00Z", "type": "event_msg",
                                 "payload": {"type": "token_count", "rate_limits": rl}}))
    p.write_text("\n".join(lines) + "\n")
    return str(p)


# ---- (e) codex reader ---------------------------------------------------------------------

def test_codex_reader_credits_depleted(tmp_path):
    r = gq.codex_quota("sid", rollout_path_fn=lambda sid: _rollout(tmp_path, [WINDOW_OK, CREDITS]))
    assert r["exhausted"] is True and r["kind"] == "credits" and r["limit_id"] == "premium"


def test_codex_reader_window_only_not_exhausted(tmp_path):
    r = gq.codex_quota("sid", rollout_path_fn=lambda sid: _rollout(tmp_path, [CREDITS, WINDOW_OK]))
    assert r["exhausted"] is False               # latest record wins


def test_codex_reader_window_hit_is_window_kind_with_reset(tmp_path):
    r = gq.codex_quota("sid", rollout_path_fn=lambda sid: _rollout(tmp_path, [WINDOW_HIT]))
    assert r["exhausted"] is True and r["kind"] == "window" and r["reset_at"] == 1789523880


def test_codex_reader_no_record_is_unknown(tmp_path):
    assert gq.codex_quota("sid", rollout_path_fn=lambda sid: _rollout(tmp_path, [])) is None
    assert gq.codex_quota("sid", rollout_path_fn=lambda sid: None) is None


# ---- (f) registry dispatch -----------------------------------------------------------------

def test_registry_dispatch_unknown_runtime_is_none():
    assert gq.green_quota_any("gemini", "alias", "sid") is None
    assert gq.green_quota_any("nope", "alias", "sid") is None
    assert set(gq.GREEN_QUOTA_READERS) >= {"codex", "claude"}


def test_pane_text_reader_matches_live_codex_phrase():
    lines = ["■ Usage limit reached. You've reached your usage limit. Increase your limits to "
             "continue using codex.", "› Ask Codex to do anything"]
    r = gq.pane_text_quota(lines)
    assert r["exhausted"] is True
    assert gq.pane_text_quota(["› Ask Codex to do anything"])["exhausted"] is False


# ---- seams in BgArm ------------------------------------------------------------------------

class _Seams:
    def __init__(self):
        self.calls = []

    def spawn(self, root, green_alias):
        self.calls.append(("spawn", green_alias)); return {"alias": green_alias, "pid": 1}

    def register_provisional(self, root, green_alias, generation=None, model=None):
        self.calls.append(("register", green_alias))

    def project_now(self, root):
        self.calls.append(("project", root))

    def verify(self, root, green_alias):
        self.calls.append(("verify", green_alias)); return True

    def hydrate(self, root, green_alias, since_seq):
        self.calls.append(("hydrate", green_alias)); return {"delivered": 5}

    def produce(self, root, green_alias):
        self.calls.append(("produce", green_alias)); return {"seq": 1}

    def swap(self, root, green, blue_generation_id):
        self.calls.append(("swap", green.get("generation")))
        return type("O", (), {"status": "complete", "swap_id": 1, "effects_status": "complete"})()

    def reap(self, root, blue):
        self.calls.append(("reap", blue))

    def kinds(self):
        return [c[0] for c in self.calls]


def _arm(tmp_path, seams, quota, wake=None):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), ROOT, seams=seams, cutover_active=lambda: True,
                 blue_wal_event_count_fn=lambda: 1, blue_pane_pid_fn=lambda r: 4242,
                 green_ingested_seq_fn=lambda alias: 999,
                 green_wake_fn=wake, green_quota_fn=lambda alias: quota)


def _obs(ctx_pct=0.30, death=None, **kw):
    o = {"root": ROOT, "runtime": "codex", "ctx_pct": ctx_pct, "ctx_source": "adapter",
         "ceiling_calibrated": True, "death": death, "state": "idle", "state_age_s": 900,
         "blue_generation_id": 6, "green": {"generation": 2, "model": "gpt-5.6-terra"},
         "blue_turn_complete": True, "blue_pending_cards": 1, "blue_composer_text": "",
         "blue_attached": False}
    o.update(kw); return o


def test_prewarming_holds_before_first_wake_when_credits_depleted(tmp_path):
    seams = _Seams(); woke = []
    st = BgStateStore(str(tmp_path), ROOT); st.write_state("PREWARMING", reason="ctx:prewarm")
    arm = _arm(tmp_path, seams, {"exhausted": True, "kind": "credits", "limit_id": "premium",
                                 "reset_at": None}, wake=lambda alias: woke.append(alias) or True)
    arm.beat(_obs(ctx_pct=0.74))
    assert st.read()["state"] == "PREWARMING"          # never READY
    assert woke == []                                    # first wake never sent
    assert st.read_meta("green_quota_limited")["kind"] == "credits"
    assert st.read_meta("green_quota_alarm") is not None


def test_ready_gate_holds_swap_when_green_quota_limited(tmp_path):
    seams = _Seams()
    st = BgStateStore(str(tmp_path), ROOT); st.write_state("READY", reason="ingest-verified")
    arm = _arm(tmp_path, seams, {"exhausted": True, "kind": "credits", "limit_id": "premium",
                                 "reset_at": None})
    arm.beat(_obs(ctx_pct=0.85))                       # L1 swap decision
    assert "swap" not in seams.kinds()
    assert st.read()["state"] == "READY"               # hold, no prune, no respawn
    assert st.read_meta("green_quota_limited")["seam"] == "swap-gate"


def test_ready_gate_swaps_when_quota_clean_or_unknown(tmp_path):
    for quota in ({"exhausted": False, "kind": None}, None):
        seams = _Seams()
        d = tmp_path / ("q" + str(quota is None)); d.mkdir()
        st = BgStateStore(str(d), ROOT); st.write_state("READY", reason="ingest-verified")
        _arm(d, seams, quota).beat(_obs(ctx_pct=0.85))
        assert "swap" in seams.kinds(), quota


def test_death_swap_never_gated_but_breadcrumbed(tmp_path):
    seams = _Seams()
    st = BgStateStore(str(tmp_path), ROOT); st.write_state("READY", reason="ingest-verified")
    _arm(tmp_path, seams, {"exhausted": True, "kind": "credits", "limit_id": "premium",
                           "reset_at": None}).beat(_obs(death="exhausted"))
    assert "swap" in seams.kinds()
    assert st.read_meta("green_quota_limited")["kind"] == "credits"


def test_quota_alarm_is_deduped_across_beats(tmp_path):
    seams = _Seams()
    st = BgStateStore(str(tmp_path), ROOT); st.write_state("READY", reason="ingest-verified")
    arm = _arm(tmp_path, seams, {"exhausted": True, "kind": "credits", "limit_id": "premium",
                                 "reset_at": None})
    arm.beat(_obs(ctx_pct=0.85)); first = st.read_meta("green_quota_alarm")
    arm.beat(_obs(ctx_pct=0.85)); second = st.read_meta("green_quota_alarm")
    assert first == second                              # same key => not re-raised
