"""The agent profiles (config/agent-profiles/) restate the tables the fleet runs on, exactly, and the loader
finds each agent's saved conversation where its profile says."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import agent_profiles as AP  # noqa: E402
from scripts import agent_model, providers, runtime_signatures  # noqa: E402
from scripts.lineage_daemon import collect  # noqa: E402

BUILTIN = ("claude", "codex", "gemini", "grok")


def test_every_profile_file_is_valid_and_named_after_its_id():
    files = sorted(AP.PROFILES_DIR.glob("*.json"))
    assert files
    for f in files:
        p = json.loads(f.read_text())
        assert AP.problems(p) == [], (f.name, AP.problems(p))
        assert f.stem == p["id"]
    assert set(AP.load_all()) == {f.stem for f in files}


def test_the_four_runtimes_and_a_self_reporting_agent_are_profiled():
    profiles = AP.load_all()
    assert set(BUILTIN) <= set(profiles)
    assert set(BUILTIN) <= set(runtime_signatures.VALID_RUNTIMES)
    reporters = [p for p in profiles.values() if p["state"]["source"] == "report"]
    assert [p["id"] for p in reporters] == ["pi"]
    assert reporters[0]["prompt_char"] is None


def test_process_names_match_the_liveness_table():
    profiles = AP.load_all()
    for rt in BUILTIN:
        names = set(profiles[rt]["process"]["basenames"])
        expected = {providers.process_basename(rt)} | {a for a, r in providers._BASENAME_ALIASES.items() if r == rt}
        assert names == expected, rt


def test_prompt_glyphs_match_the_router():
    profiles = AP.load_all()
    for rt in BUILTIN:
        assert profiles[rt]["prompt_char"] == runtime_signatures.PROMPT_SIGNATURES[rt]["prompt_char"], rt


def test_model_switching_matches_agent_model():
    profiles = AP.load_all()
    for rt in BUILTIN:
        want = "none" if rt in agent_model.NO_SWITCH else ("live" if rt in agent_model.LIVE_RUNTIMES else "next-rotation")
        assert profiles[rt]["model_switch"] == want, rt


def test_rotation_matches_what_this_repository_allows():
    profiles = AP.load_all()
    for rt in BUILTIN:
        p = profiles[rt]
        if rt in runtime_signatures.PHASE2_UNSUPPORTED_RUNTIMES:
            assert not AP.works(p, "rotation"), rt
        elif rt in collect._EXPERIMENTAL_RUNTIMES:
            assert p["rotation"] == "opt-in", rt
        else:
            assert p["rotation"] == "supported", rt


def test_a_feature_that_works_only_privately_does_not_work_here():
    grok = AP.get("grok")
    assert grok["rotation"] == grok["tokens"] == AP.UPSTREAM
    assert not AP.works(grok, "rotation") and not AP.works(grok, "tokens")
    assert AP.get("gemini")["context"] == AP.UPSTREAM


def test_a_row_without_a_runtime_is_claude():
    assert AP.get(None)["id"] == "claude"
    assert AP.get("service") is None


def test_problems_are_named():
    bad = {"format": 1, "id": "x", "process": {}, "state": {"source": "telepathy"}, "prompt_char": None,
           "session_log": {"format": "?", "find": ["nowhere"]}, "launch": {"resume": "x --resume"},
           "context": "none", "tokens": "none", "model_switch": "none", "menus": "none", "rotation": "maybe"}
    out = " | ".join(AP.problems(bad))
    for bit in ("process", "state.source", "report its own state", "session_log.format", "'nowhere'",
                "launch.resume", "rotation"):
        assert bit in out, bit


def test_an_invalid_file_is_skipped(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps({**AP.get("pi"), "id": "good"}))
    (tmp_path / "bad.json").write_text(json.dumps({"format": 1, "id": "bad"}))
    (tmp_path / "torn.json").write_text("{")
    (tmp_path / "misnamed.json").write_text(json.dumps({**AP.get("pi"), "id": "other"}))
    assert set(AP.load_all(tmp_path)) == {"good"}


def test_argv_names_the_agent_including_one_started_through_node():
    assert AP.runtime_for_argv(["/usr/local/bin/claude", "--resume", "x"]) == "claude"
    assert AP.runtime_for_argv(["agy"]) == "gemini"
    assert AP.runtime_for_argv(["/home/u/.grok/downloads/grok-linux-x86_64"]) == "grok"
    assert AP.runtime_for_argv(["pi"]) == "pi"
    assert AP.runtime_for_argv(["node", "/usr/lib/node_modules/@mariozechner/pi-coding-agent/dist/cli.js"]) == "pi"
    assert AP.runtime_for_argv(["node", "/srv/app/server.js"]) is None
    assert AP.runtime_for_argv(["bash"]) is None
    assert AP.runtime_for_argv([]) is None


# ---- finding the saved conversation ---------------------------------------------------------------

SID = "11111111-2222-4333-8444-555555555555"


def test_claude_codex_and_grok_logs_are_found_by_session_id(tmp_path):
    claude = tmp_path / ".claude" / "projects" / "-srv-example"
    claude.mkdir(parents=True)
    (claude / f"{SID}.jsonl").write_text("{}\n")
    assert AP.find_session_log(AP.get("claude"), {"session_id": SID}, home=tmp_path) == claude / f"{SID}.jsonl"

    codex = tmp_path / ".codex" / "sessions" / "2026" / "10" / "29"
    codex.mkdir(parents=True)
    codex_sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    (codex / f"rollout-2026-10-29T10-00-00-{codex_sid}.jsonl").write_text("{}\n")
    hit = AP.find_session_log(AP.get("codex"), {"session_id": codex_sid}, home=tmp_path)
    assert hit and hit.name == f"rollout-2026-10-29T10-00-00-{codex_sid}.jsonl"

    grok = tmp_path / ".grok" / "sessions" / "-srv-example" / SID
    grok.mkdir(parents=True)
    (grok / "updates.jsonl").write_text("{}\n")
    assert AP.find_session_log(AP.get("grok"), {"session_id": SID}, home=tmp_path) == grok / "updates.jsonl"


def test_no_session_id_and_no_report_finds_nothing(tmp_path):
    assert AP.find_session_log(AP.get("codex"), {}, home=tmp_path) is None


def _fake_tmux(pane):
    return lambda argv, **kw: SimpleNamespace(stdout=pane + "\n", returncode=0)


def test_pi_is_found_from_its_own_report_first(tmp_path):
    session = tmp_path / "pi-session.jsonl"
    session.write_text("{}\n")
    panes = tmp_path / "data" / "state" / "agent-events" / "panes"
    panes.mkdir(parents=True)
    (panes / "7.json").write_text(json.dumps({"state": "idle", "transcript_path": str(session)}))
    hit = AP.find_session_log(AP.get("pi"), {"tmux_session": "dev-demo"}, orchestra_dir=tmp_path / "data",
                              home=tmp_path, run=_fake_tmux("%7"))
    assert hit == session


def test_pi_falls_back_to_its_sessions_folder(tmp_path):
    folder = tmp_path / ".pi" / "agent" / "sessions" / "--srv-example--"
    folder.mkdir(parents=True)
    (folder / f"2026-10-29T10-00-00-000Z_{SID}.jsonl").write_text("{}\n")
    hit = AP.find_session_log(AP.get("pi"), {"session_id": SID, "tmux_session": "dev-demo"},
                              orchestra_dir=tmp_path / "data", home=tmp_path, run=_fake_tmux("%7"))
    assert hit and hit.name.endswith(f"_{SID}.jsonl")


def test_a_report_naming_a_missing_file_is_ignored(tmp_path):
    panes = tmp_path / "data" / "state" / "agent-events" / "panes"
    panes.mkdir(parents=True)
    (panes / "7.json").write_text(json.dumps({"state": "idle", "transcript_path": str(tmp_path / "gone.jsonl")}))
    assert AP.reported_transcript("dev-demo", tmp_path / "data", run=_fake_tmux("%7")) is None
    assert AP.reported_transcript("dev-demo", tmp_path / "data", run=_fake_tmux("no server")) is None
