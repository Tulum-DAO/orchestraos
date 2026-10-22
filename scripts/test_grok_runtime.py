"""Grok Build seat runtime — see / spawn / message / resume; WAL capture, rotation and approval
cards are explicitly unsupported (refused by name, never defaulted to claude).

Fixtures are SCRUBBED copies of a real Grok session store (scripts/fixtures/grok/README.md). Nothing here launches grok, touches tmux, or reads a
real ~/.grok: every reader gets an injected session root / cmdline / pane pid.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import grok_context as gc  # noqa: E402
import runtime_signatures as rs  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_this_tree(monkeypatch):
    # rotate_agent.py puts $ORCHESTRA_DIR at sys.path[0] on import; pin it to this checkout.
    monkeypatch.setenv("ORCHESTRA_DIR", str(ROOT))
    yield


FIX = ROOT / "scripts" / "fixtures" / "grok"
SID = "0199aaaa-0000-7000-8000-000000000001"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _session(tmp_path, events="events_turn_ended.jsonl", sid=SID, cwd_enc="%2Ftmp"):
    """A fake $GROK_HOME/sessions/<enc-cwd>/<sid>/ built from the scrubbed fixtures."""
    root = tmp_path / "sessions"
    d = root / cwd_enc / sid
    d.mkdir(parents=True)
    if events:
        shutil.copy(FIX / events, d / "events.jsonl")
    shutil.copy(FIX / "signals.json", d / "signals.json")
    shutil.copy(FIX / "summary.json", d / "summary.json")
    return str(root), str(d)


def _argv(*a):
    return ["/home/op/.local/bin/grok", *a]


# =============================================================================
# 1. runtime tables
# =============================================================================

def test_grok_is_a_valid_declared_runtime():
    assert "grok" in rs.VALID_RUNTIMES
    assert rs.resolve_runtime({"runtime": "grok"}, agent_id="g") == "grok"
    assert rs.validate_runtime_for_registration({"runtime": "grok", "model": "grok-4.7"}) == "grok"


def test_grok_is_declaration_only_for_seat_verbs():
    """Like codex: a grok-* model with no runtime field is NOT a seat-verb signal."""
    with pytest.raises(rs.RuntimeResolutionError):
        rs.resolve_runtime({"model": "grok-4.7"}, agent_id="g")




def test_grok_prompt_signature_is_an_unverified_never_matching_sentinel():
    sig = rs.PROMPT_SIGNATURES["grok"]
    assert sig["verified"] is False
    assert sig["prompt_char"].startswith("\x00")     # can never occur in a tmux capture
    assert "grok-composer-unverified" in sig["prompt_char"]
    assert rs.signature_verified("grok") is False
    for rt in ("claude", "gemini", "codex"):
        assert rs.signature_verified(rt) is True      # unchanged runtimes stay verified


def test_router_resolver_maps_grok_rows_to_grok_never_claude(tmp_path):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"agents": {
        "g-decl": {"runtime": "grok"},
        "g-model": {"model": "grok-4.7"},
        "c": {"model": "claude-opus-5"},
    }}))
    assert rs.agent_runtime("g-decl", registry_path=reg) == "grok"
    assert rs.agent_runtime("g-model", registry_path=reg) == "grok"
    assert rs.agent_runtime("c", registry_path=reg) == "claude"
    assert rs.agent_runtime("absent", registry_path=reg) == "claude"   # fail-safe unchanged


def test_phase2_refusal_helper():
    with pytest.raises(rs.Phase2Unsupported, match="grok.*WAL"):
        rs.refuse_if_phase2_unsupported("GROK", "WAL")
    for rt in ("claude", "gemini", "codex", None, ""):
        rs.refuse_if_phase2_unsupported(rt, "WAL")   # no-op
    assert issubclass(rs.Phase2Unsupported, RuntimeError)




def test_process_truth_recognises_grok():
    import providers
    assert providers.__file__.startswith(str(ROOT)), providers.__file__
    assert providers.runtime_for_command("/home/op/.local/bin/grok") == "grok"
    assert providers.runtime_for_command("grok") == "grok"
    assert providers.runtime_for_command("/home/op/.grok/downloads/grok-linux-x86_64") == "grok"
    assert providers.runtime_for_command("grokker") is None
    assert providers.process_basename("grok") == "grok"
    assert providers.claude_resume_is_safe("grok") is False      # never claude --resume




# =============================================================================
# 2. grok_context: process truth -> session -> state
# =============================================================================

@pytest.mark.parametrize("argv,want", [
    (_argv("--always-approve", "--no-alt-screen", "-s", SID, "You are g."), SID),
    (_argv("--session-id", SID), SID),
    (_argv(f"--session-id={SID}"), SID),
    (_argv("-r", SID, "-m", "grok-4.7"), SID),
    (_argv(f"--resume={SID}"), SID),
    (_argv("-r", "my title"), None),                       # a title is not an identity
    (_argv("-r"), None),                                   # bare resume: grok picks
    (_argv("-c"), None),
    (_argv("You are g. -s is not a flag here"), None),
    (_argv("-s", "not-a-uuid"), None),
    (["/usr/bin/claude", "--resume", SID], None),          # not grok
    ([], None),
])
def test_sid_from_argv(argv, want):
    assert gc.sid_from_argv(argv) == want


def test_fork_session_id_wins_over_resume():
    other = "0199aaaa-0000-7000-8000-0000000000ff"
    assert gc.sid_from_argv(_argv("-r", SID, "--fork-session", "-s", other)) == other


def test_session_dir_is_found_by_sid_alone_and_fails_closed(tmp_path):
    root, d = _session(tmp_path)
    assert gc.session_dir(SID, root) == d
    assert gc.session_dir("0199aaaa-0000-7000-8000-000000000009", root) is None
    assert gc.session_dir("../etc", root) is None
    (Path(root) / "%2Fother" / SID).mkdir(parents=True)          # ambiguous
    assert gc.session_dir(SID, root) is None


def test_turn_state_idle_from_real_turn_ended_stream(tmp_path):
    _, d = _session(tmp_path, "events_turn_ended.jsonl")
    st = gc.turn_state(d)
    assert st["state"] == "idle"
    assert st["outcome"] == "completed"
    assert st["turn_number"] == 0
    assert st["model_id"] == "grok-4.7"


def test_turn_state_working_mid_turn(tmp_path):
    _, d = _session(tmp_path, "events_turn_running.jsonl")
    st = gc.turn_state(d)
    assert st["state"] == "working"
    assert st["phase"] == "streaming_text"


def test_turn_state_unknown_before_first_turn_and_without_events(tmp_path):
    _, d = _session(tmp_path, "events_booting.jsonl")
    assert gc.turn_state(d)["state"] == "unknown"
    _, d2 = _session(tmp_path / "b", events=None)
    assert gc.turn_state(d2)["state"] == "unknown"
    assert gc.turn_state(None)["state"] == "unknown"


def test_turn_state_second_turn_started_is_working(tmp_path):
    _, d = _session(tmp_path, "events_turn_ended.jsonl")
    with open(os.path.join(d, "events.jsonl"), "a") as fh:
        fh.write(json.dumps({"ts": "2026-10-09T16:10:00.000Z", "type": "turn_started",
                             "turn_number": 1, "model_id": "grok-4.7"}) + "\n")
        fh.write("{not json\n")
    st = gc.turn_state(d)
    assert st["state"] == "working" and st["turn_number"] == 1


def test_turn_state_tail_drops_partial_first_line(tmp_path):
    _, d = _session(tmp_path, "events_turn_ended.jsonl")
    assert gc.turn_state(d, tail_bytes=120)["state"] == "idle"


def test_context_from_real_signals():
    sig = json.loads((FIX / "signals.json").read_text())
    pct, tokens, window = gc.context_from_signals(sig)
    assert (tokens, window) == (1361, 256000)
    assert round(pct, 2) == 0.53
    assert int(pct) == sig["contextWindowUsage"]       # grok's own integer percent agrees


@pytest.mark.parametrize("sig", [None, {}, {"contextTokensUsed": 5, "contextWindowTokens": 0},
                                 {"contextTokensUsed": "5", "contextWindowTokens": 10}])
def test_context_fails_closed(sig):
    assert gc.context_from_signals(sig) is None


def _fake_proc(pane=100, grok_pid=200, argv=None):
    argv = argv or _argv("--always-approve", "--no-alt-screen", "-s", SID, "You are g.")
    cmd = {pane: ["-bash"], grok_pid: argv}
    kids = {pane: [grok_pid], grok_pid: []}
    return dict(pane_pid_fn=lambda s: pane if s == "zz-grok" else None,
                cmdline_fn=lambda p: cmd.get(p, []),
                children_fn=lambda p: kids.get(p, []))


def test_resolve_session_positive_chain(tmp_path):
    root, d = _session(tmp_path)
    s = gc.resolve_session("zz-grok", root=root, **_fake_proc())
    assert s == {"pid": 200, "sid": SID, "dir": d}
    assert gc.resolve_session("other-seat", root=root, **_fake_proc()) is None


def test_resolve_session_fails_closed_on_two_grok_processes(tmp_path):
    root, _ = _session(tmp_path)
    argv = _argv("-s", SID)
    kw = dict(pane_pid_fn=lambda s: 1, cmdline_fn=lambda p: argv if p in (2, 3) else ["bash"],
              children_fn=lambda p: [2, 3] if p == 1 else [])
    assert gc.resolve_session("x", root=root, **kw) is None


def test_resolve_session_unpinned_grok_is_unreadable(tmp_path):
    root, _ = _session(tmp_path)
    assert gc.resolve_session("zz-grok", root=root,
                              **_fake_proc(argv=_argv("--always-approve"))) is None


def test_transcript_path_is_updates_jsonl_from_the_live_process(tmp_path):
    root, d = _session(tmp_path)
    updates = os.path.join(d, "updates.jsonl")
    with open(updates, "w") as fh:
        fh.write("{}\n")
    # The compacted model context must not be offered as the conversation.
    with open(os.path.join(d, "chat_history.jsonl"), "w") as fh:
        fh.write("{}\n")
    hit = gc.transcript_path("zz-grok", root=root, **_fake_proc())
    assert hit == {"path": updates, "sid": SID}


def test_transcript_path_fails_closed_when_the_live_session_has_no_updates_file(tmp_path):
    root, _ = _session(tmp_path)
    other = "0199aaaa-0000-7000-8000-0000000000aa"
    other_dir = Path(root) / "%2Fother" / other
    other_dir.mkdir(parents=True)
    (other_dir / "updates.jsonl").write_text("{}\n")
    # A live process bound to SID must not fall through onto `other`.
    assert gc.transcript_path("zz-grok", other, root=root, **_fake_proc()) is None


def test_transcript_path_sid_fallback_when_the_pane_has_no_grok_process(tmp_path):
    root, d = _session(tmp_path)
    updates = os.path.join(d, "updates.jsonl")
    with open(updates, "w") as fh:
        fh.write("{}\n")
    dead = dict(pane_pid_fn=lambda s: None, cmdline_fn=lambda p: [], children_fn=lambda p: [])
    assert gc.transcript_path("zz-grok", SID, root=root, **dead) == {"path": updates, "sid": SID}
    assert gc.transcript_path("zz-grok", "not-a-uuid", root=root, **dead) is None


def test_get_grok_session_context_and_seat_state(tmp_path):
    root, _ = _session(tmp_path, "events_turn_running.jsonl")
    pct, tokens, window = gc.get_grok_session_context("zz-grok", root=root, **_fake_proc())
    assert (tokens, window) == (1361, 256000)
    st = gc.seat_turn_state("zz-grok", root=root, **_fake_proc())
    assert st["state"] == "working" and st["sid"] == SID


def test_status_for_pid(tmp_path):
    root, _ = _session(tmp_path, "events_turn_ended.jsonl")
    argv = _argv("-s", SID)
    row = gc.status_for_pid(200, cmdline_fn=lambda p: argv, root=root)
    assert row["state"] == "idle"
    assert row["model"] == "grok-4.7"
    assert row["context_pct_of_window"] == 1
    assert row["sid"] == SID
    assert row["confidence"] == "grok-events"
    unpinned = gc.status_for_pid(200, cmdline_fn=lambda p: _argv(), root=root)
    assert unpinned["state"] == "unknown" and unpinned["sid"] is None


def test_router_idle_holds_unless_armed_verified_and_turn_ended(tmp_path, monkeypatch):
    root, _ = _session(tmp_path, "events_turn_ended.jsonl")
    kw = dict(root=root, **_fake_proc())
    monkeypatch.delenv(gc.DELIVERY_ARM_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))                 # no sentinel here
    assert gc.router_idle("zz-grok", signature_verified=True, **kw) == (
        False, "grok-delivery-unarmed")
    monkeypatch.setenv(gc.DELIVERY_ARM_ENV, "1")
    assert gc.router_idle("zz-grok", signature_verified=False, **kw) == (
        False, "grok-composer-signature-unverified")
    assert gc.router_idle("zz-grok", signature_verified=True, **kw) == (
        True, "grok-turn-ended")
    root2, _ = _session(tmp_path / "w", "events_turn_running.jsonl")
    assert gc.router_idle("zz-grok", signature_verified=True, root=root2,
                          **_fake_proc()) == (False, "grok-turn-working")


def test_delivery_arm_sentinel(tmp_path, monkeypatch):
    monkeypatch.delenv(gc.DELIVERY_ARM_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gc.delivery_armed() is False
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "ROUTER_GROK_DELIVERY_ARMED").write_text("")
    assert gc.delivery_armed() is True


def test_wait_turn_started_by_effect(tmp_path):
    root, _ = _session(tmp_path, "events_booting.jsonl")
    ticks = iter(range(100))
    assert gc.wait_turn_started(SID, timeout_s=3, root=root, sleep_fn=lambda s: None,
                                clock=lambda: next(ticks)) is False
    root2, _ = _session(tmp_path / "b", "events_turn_running.jsonl")
    assert gc.wait_turn_started(SID, timeout_s=3, root=root2, sleep_fn=lambda s: None) is True


def test_new_session_id_is_a_uuid():
    assert gc.is_uuid(gc.new_session_id())


def test_grok_context_never_reads_auth_json():
    src = (ROOT / "scripts" / "grok_context.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "auth.json" not in code.split('"""', 2)[-1]


# =============================================================================
# 3. identity: sid resolver + resume command
# =============================================================================

def test_cid_resolver_registered_for_identity_only():
    from lineage_daemon.wal import ctx_adapters as ca
    assert ca.CID_RESOLVER_REGISTRY["grok"] is ca.resolve_grok_cid
    for reg in (ca.CTX_ADAPTER_REGISTRY, ca.GREEN_PROGRESS_REGISTRY, ca.SOURCE_PATH_REGISTRY):
        assert "grok" not in reg                      # rotation ctx / green / WAL: not implemented
    assert ca.read_ctx("grok", "zz-grok") == (None, False)
    assert ca.source_path_for("grok", SID) is None
    assert ca.green_progress("grok", SID) is None


def test_resolve_grok_cid(tmp_path):
    from lineage_daemon.wal import ctx_adapters as ca
    hit = lambda seat: {"pid": 2, "sid": SID, "dir": "/x"}
    assert ca.resolve_grok_cid("zz-grok", resolve_fn=hit, retired_sids_fn=lambda s: set()) == SID
    assert ca.resolve_grok_cid("zz-grok", resolve_fn=hit,
                               retired_sids_fn=lambda s: {SID}) is None
    assert ca.resolve_grok_cid("zz-grok", resolve_fn=lambda s: None,
                               retired_sids_fn=lambda s: set()) is None




def test_grok_resolver_uses_the_memoised_global_session_list(monkeypatch):
    from lineage_daemon.wal import ctx_adapters as ca
    calls = []

    class R:
        returncode = 0
        stdout = "gm\t11\nzz-grok\t22\nzz-grok-g2\t33\n"
    monkeypatch.setattr(ca, "_tmux_global", lambda args: calls.append(args) or R())
    assert ca._pane_pid_from_global_list("zz-grok") == 22        # exact, never the -g2 prefix
    assert ca._pane_pid_from_global_list("nope") is None
    assert all(a[:2] == ["tmux", "list-sessions"] for a in calls)


def test_resume_command_for_grok():
    import promote_successor as ps
    assert ps.resume_command_for("grok", SID) == (
        f"grok --always-approve --minimal --no-alt-screen -r {SID}")
    assert ps.resume_command_for("grok", SID, "grok-4.7").endswith(" -m grok-4.7")
    assert "-m" not in ps.resume_command_for("grok", SID, "claude-opus-5[1m]").split()
    assert "-m" not in ps.resume_command_for("grok", SID, "unknown").split()
    with pytest.raises(ps.RuntimeRefused):
        ps.resume_command_for("grok", "my title")
    # codex: UUID-checked too (fix/codex-resume-pinned); the shape itself is unchanged
    assert ps.resume_command_for("codex", SID) == f"codex --yolo resume {SID}"
    with pytest.raises(ps.RuntimeRefused):
        ps.resume_command_for("codex", "s1")


# =============================================================================
# 4. unimplemented surfaces refuse grok by name
# =============================================================================

def test_wal_capture_refuses_grok():
    from lineage_daemon.wal import multiplexer as mx
    with pytest.raises(rs.Phase2Unsupported, match="WAL"):
        mx.make_adapter("grok", None, "zz-grok", 1)




def test_rotation_beat_skips_grok():
    from lineage_daemon import collect
    assert collect.beat_skip_reason({"runtime": "grok"}) == "unsupported-runtime"
    assert collect._resolve_runtime({}, "grok-4.7") == "grok"
    assert collect._resolve_runtime({}, "something-else") == "claude"   # fail-safe unchanged




def test_bg_beat_never_arms_grok_even_forced(tmp_path, capsys):
    from lineage_daemon.wal import bg_beat
    assert bg_beat._PHASE2_UNSUPPORTED_RUNTIMES == rs.PHASE2_UNSUPPORTED_RUNTIMES
    agents = [{"agent_id": "zz-grok", "runtime": "grok"},
              {"agent_id": "c1", "runtime": "claude"}]
    assert bg_beat.armed_roots(agents, str(tmp_path),
                               force_roots={"zz-grok", "c1"}) == ["c1"]
    assert "runtime-phase2-unsupported: zz-grok" in capsys.readouterr().out


def test_approval_cards_refuse_grok():
    import approval_adapters as aa
    with pytest.raises(rs.Phase2Unsupported, match="approval-card"):
        aa.grok_create_approval("zz-grok", "ok?")


def test_manual_rotation_refuses_grok_before_minting(monkeypatch):
    # rotate_agent inserts $ORCHESTRA_DIR at sys.path[0] on import: pin it to THIS tree and
    # give the import a private sys.path (both restored by monkeypatch at teardown).
    monkeypatch.setenv("ORCHESTRA_DIR", str(ROOT))
    monkeypatch.setattr(sys, "path", list(sys.path))
    import rotate_agent as ra
    assert ra.__file__.startswith(str(ROOT)), ra.__file__
    monkeypatch.setattr(ra, "get_agent_registry", lambda s: {"runtime": "grok"})

    def boom(*a, **k):
        raise AssertionError("generation resolution reached for a grok seat")
    monkeypatch.setattr(ra, "resolve_pred_generation_or_refuse", boom)
    with pytest.raises(rs.Phase2Unsupported, match="rotation"):
        ra.execute_rotation("zz-grok", dry_run=True)


# =============================================================================
# 5. see: agent-status row + tmux widget
# =============================================================================

def test_agent_status_grok_row_comes_from_session_store(tmp_path, monkeypatch):
    ags = _load("agent_status_grok", "scripts/agent-status.py")
    root, _ = _session(tmp_path, "events_turn_running.jsonl")
    proc = {"running": True, "runtime": "grok", "pid": 200, "cpu": 1.0, "rss_mb": 190,
            "elapsed": "01:00", "children": 0}
    monkeypatch.setattr(ags, "check_claude_process", lambda s: proc)
    monkeypatch.setattr(ags, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(gc, "_cmdline", lambda p: _argv("-s", SID))
    monkeypatch.setattr(gc, "sessions_root", lambda home=None: root)

    def no_screen(*a, **k):
        raise AssertionError("grok status must not scrape the pane / consult the deriver")
    monkeypatch.setattr(ags, "get_tmux_output", no_screen)
    monkeypatch.setattr(ags, "_deriver_lookup", no_screen)
    out = ags.get_agent_status("zz-grok")
    assert out["state"] == "working"
    assert out["runtime"] == "grok" and out["session_id"] == SID
    assert out["model"] == "grok-4.7"
    assert out["context_pct"] == "1%" and out["context_pct_of_window"] == 1
    assert out["process"]["runtime"] == "grok"
    assert "pending_menu" not in out


def test_agent_status_grok_reader_fault_is_unknown(tmp_path, monkeypatch):
    ags = _load("agent_status_grok2", "scripts/agent-status.py")
    proc = {"running": True, "runtime": "grok", "pid": 200, "cpu": 0.0, "rss_mb": 1,
            "elapsed": "00:01", "children": 0}
    monkeypatch.setattr(ags, "check_claude_process", lambda s: proc)
    monkeypatch.setattr(ags, "STATE_DIR", str(tmp_path / "state"))

    def broken(pid):
        raise OSError("boom")
    monkeypatch.setattr(gc, "status_for_pid", broken)
    out = ags.get_agent_status("zz-grok")
    assert out["state"] == "unknown"




# =============================================================================
# 6. message: router holds by default
# =============================================================================

@pytest.fixture
def router():
    return _load("message_router_grok", "scripts/message-router.py")


def test_router_grok_idle_never_reads_claude_hook_file(router, monkeypatch):
    def no_hook(s):
        raise AssertionError("a pane's claude-era hook file must not decide grok idleness")
    monkeypatch.setattr(router, "hook_state", no_hook)
    monkeypatch.setattr(gc, "router_idle", lambda s, signature_verified: (False, "x"))
    assert router.agent_is_idle("zz-grok", "grok") is False


def test_router_grok_unverified_signature_holds_even_when_armed(router, monkeypatch):
    monkeypatch.setenv(gc.DELIVERY_ARM_ENV, "1")
    monkeypatch.setattr(gc, "seat_turn_state", lambda s, **k: {"state": "idle", "sid": SID})

    def no_capture(*a):
        raise AssertionError("no pane capture before the file/signature gate passes")
    monkeypatch.setattr(router, "tmux", no_capture)
    assert router.agent_is_idle("zz-grok", "grok") is False


def test_router_grok_verified_path_checks_composer(router, monkeypatch):
    sig = dict(rs.PROMPT_SIGNATURES["grok"], prompt_char="\u276f", verified=True)
    monkeypatch.setitem(router.PROMPT_SIGNATURES, "grok", sig)
    monkeypatch.setattr(gc, "router_idle", lambda s, signature_verified: (True, "ok"))

    class Cap:
        returncode = 0
        stdout = "history\n\u276f \n"
    monkeypatch.setattr(router, "tmux", lambda *a: Cap())
    assert router.agent_is_idle("zz-grok", "grok") is True
    Cap.stdout = "history\n\u276f hello i am typing\n"
    assert router.agent_is_idle("zz-grok", "grok") is False


def test_router_inject_busy_holds_a_grok_turn(router, monkeypatch):
    monkeypatch.setattr(router, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(router, "exit_copy_mode", lambda s: None)
    monkeypatch.setattr(gc, "seat_turn_state", lambda s: {"state": "working"})

    def no_paste(*a, **k):
        raise AssertionError("must not reach the paste stages")
    monkeypatch.setattr(router, "pane_split", no_paste)
    assert router.inject("zz-grok", "hello", "grok") == "held"      # nothing typed: re-queued, never "delivered"


# =============================================================================
# 7. spawn: dispatch + launch line (sourced, never run)
# =============================================================================

def _bash(script, tmp_path, registry=None):
    tree = tmp_path / "tree"
    (tree / "state").mkdir(parents=True, exist_ok=True)
    reg = tree / "registry.json"
    reg.write_text(json.dumps(registry or {"agents": {}}))
    env = dict(os.environ, ORCHESTRA_DIR=str(tree), REGISTRY=str(reg),
               NTFY_TOKEN_FILE="/nonexistent")
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    # $0 is NOT the script path, so spawn-agent.sh's main guard stays off when sourced;
    # SCRIPT_DIR then resolves to the cwd (ROOT).
    return subprocess.run(["bash", "-c", f'source "$1"; {script}', "spawn-test",
                           str(ROOT / "spawn-agent.sh")],
                          env=env, capture_output=True, text=True, timeout=60, cwd=str(ROOT))


def test_spawn_dispatch_accepts_grok_without_a_ready_signature(tmp_path):
    r = _bash("runtime_dispatch zz-grok", tmp_path,
              {"agents": {"zz-grok": {"runtime": "grok", "model": "grok-4.7"}}})
    assert r.returncode == 0, r.stderr
    assert r.stdout == "grok\t\n"
    r2 = _bash("runtime_dispatch c1", tmp_path,
               {"agents": {"c1": {"runtime": "claude"}}})
    assert r2.stdout == "claude\t\u276f\n"            # unchanged


def test_spawn_grok_launch_line(tmp_path):
    r = _bash(f'build_grok_launch_cmd /bin/grok new {SID} grok-4.7 "You are g. Read /tmp/a b.md"',
              tmp_path)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert line.startswith(f"/bin/grok --always-approve --minimal --no-alt-screen -s {SID} -m grok-4.7 ")
    assert "You\\ are\\ g." in line                    # boot line is one %q-quoted argv
    r = _bash(f"build_grok_launch_cmd grok resume {SID} claude-opus-5 ''", tmp_path)
    assert r.stdout.strip() == f"grok --always-approve --minimal --no-alt-screen -r {SID}"
    r = _bash("build_grok_launch_cmd grok resume my-title || echo rc=$?", tmp_path)
    assert r.stdout.strip() == "rc=2"
    r = _bash(f"build_grok_launch_cmd grok sideways {SID} || echo rc=$?", tmp_path)
    assert r.stdout.strip() == "rc=2"


def test_spawn_grok_path_types_nothing_into_the_pane():
    """Static guard: the grok branch skips wait_for_tui and inject_prompt."""
    src = (ROOT / "spawn-agent.sh").read_text()
    assert 'if [[ "$runtime" != "grok" ]] && ! wait_for_tui' in src
    assert ":   # the boot line already rode grok's argv" in src
    assert 'grok_context.py" wait-turn "$grok_sid"' in src


def test_spawn_writes_no_grok_config():
    """A grok seat installs no hooks/statusline: nothing in spawn writes under ~/.grok. The one
    allowed mention is READING the installer's binary: the GROK_BIN pick's `[[ -x ... ]]` test (#443)."""
    lines = [l for l in (ROOT / "spawn-agent.sh").read_text().splitlines() if not l.lstrip().startswith("#")]
    grok_dir = [l for l in lines if ".grok/" in l]
    read_only = ('[[ -n "$GROK_BIN" ]] || { [[ -x "$HOME/.grok/bin/grok" ]] '
                 '&& GROK_BIN="$HOME/.grok/bin/grok" || GROK_BIN="grok"; }')
    assert grok_dir in ([], [read_only]), grok_dir      # exactly that read, nothing else under ~/.grok
    code = "\n".join(lines)
    assert "config.toml" not in code and "requirements.toml" not in code


# =============================================================================
# 8. safety gates: router resolution, profiles, kill, deny-by-default WAL
# =============================================================================

# --- the router ALWAYS resolves grok -----------

def test_router_unarmed_still_resolves_grok_and_holds(router, monkeypatch):
    """With ROUTER_GEMINI_IDLE_ARMED absent a grok row is still judged by grok's gate (HELD),
    never by claude's ❯. Restore the old `if gemini_idle_routing_armed(): ... else
    DEFAULT_RUNTIME` gate and routing_runtime returns 'claude' -> this test goes RED."""
    monkeypatch.setattr(router, "gemini_idle_routing_armed", lambda: False)
    monkeypatch.setattr(router, "agent_runtime", lambda a, meta=None: "grok")
    rt = router.routing_runtime("zz-grok", {})
    assert rt == "grok"

    def no_hook(s):
        raise AssertionError("a grok seat must not be judged by the claude hook/glyph path")
    monkeypatch.setattr(router, "hook_state", no_hook)
    monkeypatch.setattr(gc, "router_idle", lambda s, signature_verified: (False, "held"))
    assert router.agent_is_idle("zz-grok", rt) is False


@pytest.mark.parametrize("resolved,armed,want", [
    ("claude", False, "claude"), ("codex", False, "claude"), ("gemini", False, "claude"),
    ("claude", True, "claude"), ("codex", True, "codex"), ("gemini", True, "gemini"),
    ("grok", False, "grok"), ("grok", True, "grok"),
])
def test_router_routing_runtime_is_byte_identical_for_every_non_grok_seat(
        router, monkeypatch, resolved, armed, want):
    monkeypatch.setattr(router, "gemini_idle_routing_armed", lambda: armed)
    monkeypatch.setattr(router, "agent_runtime", lambda a, meta=None: resolved)
    assert router.routing_runtime("x", {}) == want


def test_router_both_call_sites_use_routing_runtime():
    """Delivery AND the self-cleanup pass resolve through routing_runtime; neither keeps the
    old armed-only branch (which forced claude on a grok pane)."""
    src = (ROOT / "scripts" / "message-router.py").read_text()
    assert "_runtime = routing_runtime(_target_agent, meta)" in src
    assert "_crt = routing_runtime(agent_for_session(sess, meta) or sess, meta)" in src
    assert "_runtime = DEFAULT_RUNTIME\n" not in src
    assert "_crt = DEFAULT_RUNTIME\n" not in src


# --- an explicit grok provider profile ------------------------

def test_grok_has_its_own_unmeasured_provider_profile():
    from lineage_daemon.realtime import provider_profiles as pp
    p = pp.profile_for("grok")
    assert p.provider == "grok"                       # never the claude fallback
    assert p.cpu_axis_reliable is False and p.idle_baseline_measured is False
    assert p.chrome == {} and p.hook_recency_s == 0.0
    assert p.prompt_char == rs.PROMPT_SIGNATURES["grok"]["prompt_char"]
    assert pp.profile_for("GROK ").provider == "grok"
    assert pp.profile_for("wat").provider == "claude"   # undeclared: unchanged fallback
    assert set(pp.PROFILES) >= {"claude", "codex", "gemini", "grok"}


def test_grok_profile_drives_the_deriver_without_claude_constants():
    from lineage_daemon.realtime import provider_profiles as pp
    from lineage_daemon.realtime.status_deriver import derive_status
    prof = pp.profile_for("grok")
    quiet = {"bytes_flowing": False, "cpu_core": 50.0, "io_delta": 0, "live_pids": 2,
             "pane_gone": False, "chrome": {}, "turn_complete": False}
    # CPU is declared unreliable for grok: a hot-but-silent render loop is not 'computing'
    assert derive_status(quiet, profile=prof) != "computing"
    assert derive_status(dict(quiet, pane_gone=True), profile=prof) == "offline_crashed"





class _Seat:
    def __init__(self, root, runtime):
        self.root, self.runtime = root, runtime








# --- --kill on a grok seat no-ops the handoff and does not crash --------

def _kill(tmp_path, agent, row):
    tree = tmp_path / "tree"
    log = tmp_path / "tmux.log"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "tmux").write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        'case "$1" in capture-pane) printf "\\u276f hi\\n";; esac\n'
        "exit 0\n")
    (fakebin / "tmux").chmod(0o755)
    env_path = f"{fakebin}:{os.environ.get('PATH', '')}"
    r = _bash(f'export PATH="{env_path}"; unset IDENTITY_STORE_CUTOVER; '
              f'sleep() {{ :; }}; kill_agent {agent}; echo "rc=$?"',
              tmp_path, {"agents": {agent: dict(row, tmux_session=agent)}})
    return r, tree, (log.read_text() if log.exists() else "")


def test_kill_on_a_grok_seat_noops_the_handoff_and_does_not_crash(tmp_path):
    r, tree, tmux_log = _kill(tmp_path, "zz-grok", {"runtime": "grok", "model": "grok-4.7"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "rc=0" in r.stdout, out
    assert "Handoff: no-op for grok seat zz-grok" in out
    assert "Killed agent: zz-grok" in out
    assert not (tree / "state" / "agent-handoffs" / "zz-grok.json").exists()
    assert json.loads((tree / "state" / "zz-grok.json").read_text())["status"] == "killed"
    assert "kill-session -t zz-grok" in tmux_log


def test_kill_on_a_grok_model_row_without_runtime_also_noops(tmp_path):
    r, tree, _ = _kill(tmp_path, "g2", {"model": "grok-4.7"})
    assert "Handoff: no-op for grok seat g2" in r.stdout + r.stderr
    assert "rc=0" in r.stdout


def test_kill_on_a_claude_seat_still_saves_the_handoff(tmp_path):
    """Control: the no-op is grok-only; a claude seat's autosnap is unchanged."""
    r, tree, _ = _kill(tmp_path, "c1", {"runtime": "claude", "model": "claude-opus-5"})
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert "Saving handoff for c1" in r.stdout + r.stderr
    assert "Handoff: no-op" not in r.stdout + r.stderr


# --- make_adapter is deny-by-default ---------------------------------------

def test_make_adapter_is_deny_by_default(tmp_path):
    from lineage_daemon.wal import multiplexer as mx
    from lineage_daemon.wal.store import WalStore
    st = WalStore(str(tmp_path / "w.db"))
    assert set(mx.WAL_ADAPTER_RUNTIMES) == {"claude", "codex", "gemini"}
    for rt in ("claude", "codex", "gemini"):
        assert mx.make_adapter(rt, st, "r", 0) is not None
    with pytest.raises(rs.Phase2Unsupported):
        mx.make_adapter("grok", st, "r", 0)                      # refused BY NAME
    for rt in ("wat", "", None, "service", "agy", "Claude"):
        with pytest.raises(mx.UnsupportedWalRuntime, match="deny-by-default"):
            mx.make_adapter(rt, st, "r", 0)


# =============================================================================
# 9. identity-plane coverage (orphan scan, model gate)
# =============================================================================

GROK_CMD = f"/home/op/.local/bin/grok --always-approve --minimal --no-alt-screen -s {SID}"


@pytest.fixture
def idb(tmp_path):
    """A REAL identity store (orchestra_db.init_db), the orphan-scan suite's fixture rule."""
    from scripts.identity_store import orchestra_db
    dbp = tmp_path / "state" / "orchestra-registry.db"
    dbp.parent.mkdir(parents=True)
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        orchestra_db.adopt_identity(c, {"root": "gm", "generation": 1, "model": "m",
                                        "tier": "T0", "runtime": "claude"})
        orchestra_db.adopt_identity(c, {"root": "zz-grok", "generation": 1,
                                        "model": "grok-4.7", "tier": "T0",
                                        "runtime": "grok"})
    finally:
        c.close()
    return str(dbp)


# --- fix (b): orphan_pane_scan sees grok panes -------------------------------------------

@pytest.mark.parametrize("cmd", [GROK_CMD, "grok -r " + SID,
                                 "/home/op/.grok/downloads/grok-linux-x86_64 -s " + SID])
def test_orphan_scan_flags_an_unregistered_grok_pane(idb, cmd):
    from scripts.identity_store import orphan_pane_scan as ops
    out = ops.find_orphans([{"session": "rogue-grok", "child_cmdline": cmd}], idb)
    assert [o["session"] for o in out] == ["rogue-grok"]


def test_orphan_scan_registered_grok_seat_is_not_an_orphan(idb):
    from scripts.identity_store import orphan_pane_scan as ops
    panes = [{"session": "zz-grok", "child_cmdline": GROK_CMD},
             {"session": "zz-grok-g2", "child_cmdline": GROK_CMD}]
    assert ops.find_orphans(panes, idb) == []


def test_orphan_scan_flags_a_resurrected_grok_seat(idb):
    """The 09-08 resurrection shape on a grok seat: canonical dropped, pane raw-respawned."""
    from scripts.identity_store import orchestra_db, orphan_pane_scan as ops
    c = orchestra_db.get_connection(idb)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM canonical WHERE root='zz-grok'")
        c.execute("COMMIT")
    finally:
        c.close()
    out = ops.find_resurrections([{"session": "zz-grok", "child_cmdline": GROK_CMD}], idb)
    assert [(o["session"], o["reason"]) for o in out] == [("zz-grok", "no-canonical")]


def test_orphan_scan_ignores_grok_lookalikes(idb):
    from scripts.identity_store import orphan_pane_scan as ops
    panes = [{"session": "x1", "child_cmdline": "grokker --serve"},
             {"session": "x2", "child_cmdline": "tail -f grok.log"},
             {"session": "x3", "child_cmdline": "python3 grok_context.py state zz-grok"}]
    assert ops.find_orphans(panes, idb) == []




# --- fix (c): shims._LLM_RUNTIMES guards a grok row's model ------------------------------

def _model_seat(tmp_path, runtime):
    from scripts.identity_store import orchestra_db
    (tmp_path / "state").mkdir(exist_ok=True)
    dbp = str(tmp_path / "state" / "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root,tier,runtime) VALUES ('seat','T0',?)", (runtime,))
    gid = c.execute("INSERT INTO generations (root,generation,session_id,model) "
                    "VALUES ('seat',1,'s1','grok-4.7')").lastrowid
    c.execute("INSERT INTO canonical (root,generation_id,tmux_session,status) "
              "VALUES ('seat',?,'seat','online')", (gid,))
    c.execute("INSERT INTO source_records (file,kind,key,ordinal,payload_json) "
              "VALUES ('agent-sessions.json','session','seat',0,?)",
              (json.dumps({"session_id": "s1", "generation": 1, "status": "online"}),))
    c.commit()
    return c, gid


@pytest.mark.parametrize("bad", ["", "unknown", None])
def test_sessions_update_refuses_an_unknown_model_for_a_grok_seat(tmp_path, bad):
    from scripts.identity_store import shims
    c, gid = _model_seat(tmp_path, "grok")
    try:
        with pytest.raises(shims.InvalidModel):
            shims.sessions_update(c, "seat", {"model": bad})
        assert c.execute("SELECT model FROM generations WHERE id=?",
                         (gid,)).fetchone()["model"] == "grok-4.7"
    finally:
        c.close()


def test_sessions_update_persists_a_real_grok_model(tmp_path):
    from scripts.identity_store import shims
    c, gid = _model_seat(tmp_path, "grok")
    try:
        shims.sessions_update(c, "seat", {"model": "grok-4.7"})
        assert c.execute("SELECT model FROM generations WHERE id=?",
                         (gid,)).fetchone()["model"] == "grok-4.7"
    finally:
        c.close()


def test_llm_runtimes_are_every_declared_runtime_but_service():
    from scripts.identity_store import shims
    assert set(shims._LLM_RUNTIMES) == set(rs.VALID_RUNTIMES) - {"service"}


def test_spawn_refuses_agent_runtime_that_disagrees_with_the_seat(tmp_path):
    """A stale row runtime=claude plus AGENT_RUNTIME=grok used to silently launch CLAUDE on a
    Grok session id. A disagreeing override now refuses (rc 3)."""
    r = _bash("AGENT_RUNTIME=grok runtime_override_check zz-grok claude || echo rc=$?", tmp_path)
    assert r.stdout.strip() == "rc=3"
    assert "AGENT_RUNTIME=grok" in r.stderr and "runtime=claude" in r.stderr
    r = _bash("AGENT_RUNTIME=grok runtime_override_check zz-grok grok && echo ok", tmp_path)
    assert r.stdout.strip() == "ok"
    r = _bash("unset AGENT_RUNTIME; runtime_override_check c1 claude && echo ok", tmp_path)
    assert r.stdout.strip() == "ok"
    src = (ROOT / "spawn-agent.sh").read_text()
    assert 'runtime_override_check "$agent_id" "$runtime" || exit 3' in src


# =============================================================================
# 10. stock-install surfaces: recovery, provider registry, auth probe, mail watch
# =============================================================================

def test_recovery_admits_grok_unconditionally():
    rr = _load("recovery_runtime_grok", "scripts/recovery_runtime.py")
    assert "grok" in rr.UNCONDITIONAL_RUNTIMES


def test_providers_json_declares_grok_with_a_text_auth_probe():
    d = json.loads((ROOT / "config" / "providers.json").read_text())
    g = [p for p in d["providers"] if p["id"] == "grok"]
    assert len(g) == 1
    g = g[0]
    assert g["cli"] == "grok"
    assert g["auth_probe"]["kind"] == "cli-text"
    assert g["model_catalog"]["probe"]["kind"] == "line-regex"
    assert g["logo_svg"].startswith("<svg")
    assert all(m["id"].startswith("grok-") for m in g["model_catalog"]["static"])


@pytest.mark.parametrize("stdout,want", [
    ("You are logged in with grok.com\n\n  * grok-4.7 (default)\n  - grok-4.6\n", True),
    ("You are not authenticated.\nRun `grok login` to sign in.\n", False),
    ("some unexpected banner\n", "unverified"),
])
def test_cli_text_auth_verdict(stdout, want):
    from orchestra_cli import runtime_probe as rp
    g = [p for p in json.loads((ROOT / "config" / "providers.json").read_text())["providers"]
         if p["id"] == "grok"][0]
    assert rp.cli_text_verdict(stdout, g["auth_probe"])["authed"] == want


def test_mail_watch_requires_an_agent_and_defaults_the_tenant(monkeypatch):
    mw = _load("grok_mail_watch_t", "scripts/grok_mail_watch.py")
    with pytest.raises(SystemExit):
        mw.main([])                                   # --agent is required
    src = (ROOT / "scripts" / "grok_mail_watch.py").read_text()
    assert "shaw" not in src.lower()
    assert "ORCHESTRA_TENANT" in src


def test_children_reads_proc_without_a_helper_module():
    """grok_context._children used to import an unpublished `proc_children` module, so on a
    public install seat_turn_state() was always reader-error and the mail watcher never nudged."""
    import subprocess as _sp
    child = _sp.Popen(["sleep", "30"])
    try:
        assert child.pid in gc._children(os.getpid())
    finally:
        child.kill()
        child.wait()
