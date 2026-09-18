"""RED-first tests — P1b Voice Layer Dispatcher (DEC-1788772980798256, SPEC v2 D1-D5).

Matrix from spec §5 (6 tests) + orchestra-builder residual (tier-2→tier-3 degrade spoken
line) + the fold-in (journal `surface` stamping from active-surface at call start).
All subprocess tests use injected fake commands — no real claude/agy spawns.
"""
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import pytest

from services.arturo import dispatcher as dp


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_dp", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    dp._reset_for_tests()
    monkeypatch.setenv(dp.FLAG, "1")
    yield
    dp._reset_for_tests()


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------- 1. bounded subprocess lifecycle ----------

def test_deep_query_bounded_subprocess_lifecycle(tmp_path):
    """Spawns in a NEW session (own PGID), runs, exits cleanly, output returned capped."""
    pidfile = tmp_path / "pid"
    cmd = ["bash", "-c", f"echo $$ > {pidfile}; ps -o pgid= -p $$ >> {pidfile}; echo ANALYSIS-OK"]
    ok, text = dp.run_analyst("why did the build fail", cmd=cmd, wall_s=10)
    assert ok and "ANALYSIS-OK" in text
    pid, pgid = pidfile.read_text().split()
    assert int(pid) == int(pgid), "start_new_session must make the child its own process-group leader"
    assert len(text) <= dp.OUTPUT_CAP


def test_deep_query_output_capped_3000():
    cmd = ["bash", "-c", "yes x | head -c 20000"]
    ok, text = dp.run_analyst("q", cmd=cmd, wall_s=10)
    assert ok
    assert len(text) <= dp.OUTPUT_CAP == 3000


# ---------- 2. timeout kills the whole process group ----------

def test_deep_query_timeout_kills_process_group(tmp_path):
    """A slow probe WITH a grandchild: on wall timeout the PGID gets SIGKILL — no leaked children."""
    pidfile = tmp_path / "pids"
    cmd = ["bash", "-c", f"sleep 15 & echo $! $$ > {pidfile}; wait"]
    t0 = time.time()
    ok, text = dp.run_analyst("slow question", cmd=cmd, wall_s=1.0)
    took = time.time() - t0
    assert not ok and text == ""            # timeout degrades to empty (caller degrades to tier 3)
    assert took < 5, "wall must cut the wait, not ride the 15s sleep"
    time.sleep(0.3)
    for pid in pidfile.read_text().split():
        assert not _pid_alive(int(pid)), f"leaked process {pid} after killpg"


def test_deep_query_default_wall_is_12s():
    assert dp.DEEP_QUERY_WALL_S == 12.0


# ---------- 3. memory-floor degradation ----------

def test_deep_query_memory_floor_degradation(tmp_path, monkeypatch):
    """MemAvailable < 500MB → no spawn at all, (False,'') so the caller degrades to async."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 32000000 kB\nMemAvailable: 200000 kB\n")  # ~195MB
    monkeypatch.setattr(dp, "MEMINFO_PATH", str(meminfo))
    spawned = tmp_path / "spawned"
    cmd = ["bash", "-c", f"touch {spawned}; echo hi"]
    ok, text = dp.run_analyst("q", cmd=cmd, wall_s=5)
    assert not ok and text == ""
    assert not spawned.exists(), "must not spawn under the memory floor"


def test_mem_available_parses_proc(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 32000000 kB\nMemAvailable: 8192000 kB\n")
    monkeypatch.setattr(dp, "MEMINFO_PATH", str(meminfo))
    assert dp.mem_available_mb() == 8000
    monkeypatch.setattr(dp, "MEMINFO_PATH", str(tmp_path / "missing"))
    assert dp.mem_available_mb() is None       # unknown -> caller treats as OK (fail-open on read)


# ---------- 4. ask_gm reuses the async_task pipeline ----------

def test_ask_gm_reuses_async_task_pipeline(monkeypatch):
    mod = _load_proxy()
    calls = []
    real_execute = mod.execute_tool
    def spy(name, args, user_turns=None):
        calls.append((name, args))
        if name == "async_task":
            return "Task queued: x. I'll text you the result on Telegram when it's done."
        return real_execute(name, args, user_turns)
    monkeypatch.setattr(mod, "execute_tool", spy)
    out = mod.execute_tool("ask_gm", {"request": "deploy the arturo bundle"})
    async_calls = [c for c in calls if c[0] == "async_task"]
    assert async_calls, "ask_gm must route through async_task (inherits TG outbox + verified delivery)"
    inner = async_calls[0][1]
    assert inner.get("tool_name") == "gm_command", "the async payload must be gm_command (role-inversion+provenance inherited)"
    assert "deploy the arturo bundle" in json.dumps(inner)
    # D4: spoken return is first-person, no third-person GM attribution
    assert "GM" not in out and "gm" not in out.split("Telegram")[0].lower()
    assert "I'll text you" in out


# ---------- 5. persona: never third-person GM ----------

def test_arturo_persona_no_third_person_gm():
    for line in (dp.SPOKEN_DEEP_FALLBACK, dp.SPOKEN_ASK_GM_ACK):
        low = line.lower()
        assert "gm" not in low and "general manager" not in low and "queued for" not in low
        assert "i'll" in low or "let me" in low or "i " in low, "must speak in the first person"


def test_tier2_degrade_to_tier3_spoken_line(monkeypatch, tmp_path):
    """Residual (orchestra-builder vote): when deep_query times out / degrades, the spoken text
    is the first-person D4 template and async_task actually fired."""
    mod = _load_proxy()
    fired = []
    def spy(name, args, user_turns=None):
        if name == "async_task":
            fired.append(args)
            return "Task queued: x. I'll text you the result on Telegram when it's done."
        return mod_orig(name, args, user_turns)
    mod_orig = mod.execute_tool
    monkeypatch.setattr(mod, "execute_tool", spy)
    monkeypatch.setattr(dp, "run_analyst", lambda *a, **k: (False, ""))  # forced tier-2 failure
    out = mod.execute_tool("deep_query", {"question": "what is blocking northwind"})
    assert fired, "degrade must fall through to the async_task path"
    assert fired[0].get("tool_name") == "gm_command"
    assert out == dp.SPOKEN_DEEP_FALLBACK
    assert "gm" not in out.lower()


# ---------- 6. concurrency: exactly one child ----------

def test_concurrency_semaphore_limit(tmp_path):
    """3 concurrent deep_query calls → exactly 1 spawns; the others degrade instantly."""
    marker_dir = tmp_path / "spawns"; marker_dir.mkdir()
    cmd = ["bash", "-c", f"touch {marker_dir}/$$; sleep 1; echo done"]
    results = []
    def one():
        results.append(dp.run_analyst("q", cmd=cmd, wall_s=5))
    threads = [threading.Thread(target=one) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(list(marker_dir.iterdir())) == 1, "BoundedSemaphore(1) must cap children at exactly 1"
    assert sum(1 for ok, _ in results if ok) == 1
    assert sum(1 for ok, _ in results if not ok) == 2


# ---------- flag gating / tool surface ----------

def test_flag_off_tools_unchanged(monkeypatch):
    monkeypatch.delenv(dp.FLAG, raising=False)
    mod = _load_proxy()
    names = [t["function"]["name"] for t in mod.TOOLS]
    assert "deep_query" not in names and "ask_gm" not in names
    assert "gm_command" in names          # legacy surface untouched with flag off


def test_flag_on_retires_sync_gm_command_and_adds_dispatcher_tools(monkeypatch):
    monkeypatch.setenv(dp.FLAG, "1")
    mod = _load_proxy()
    names = [t["function"]["name"] for t in mod.TOOLS]
    assert "deep_query" in names and "ask_gm" in names
    assert "gm_command" not in names, "D3: the model-facing sync gm_command path is retired under the flag"
    # the internal branch survives for async_task(gm_command) reuse
    assert mod.execute_tool("gm_command", {"prompt": ""}).startswith("ERROR")


def test_transform_tools_pure():
    tools = [{"type": "function", "function": {"name": "gm_command"}},
             {"type": "function", "function": {"name": "knowledge"}}]
    out = dp.transform_tools(tools)
    names = [t["function"]["name"] for t in out]
    assert names.count("knowledge") == 1 and "gm_command" not in names
    assert "deep_query" in names and "ask_gm" in names
    assert [t["function"]["name"] for t in tools] == ["gm_command", "knowledge"], "input not mutated"


# ---------- fold-in: journal `surface` stamping ----------

def _surface_doc(device="ios", route="/calls", age_s=5):
    now = time.time()
    return {"current": {"device": device, "route": route, "updated_at": now - age_s,
                        "stack": [], "focused": None}}


def test_journal_stamped_with_surface_at_creation(tmp_path, monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    sp = tmp_path / "active-surface.json"
    sp.write_text(json.dumps(_surface_doc(device="ios", route="/agents/x")))
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path)
    non_system = [{"role": "user", "content": "hey arturo"},
                  {"role": "assistant", "content": "hey boss"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="funnel", conv_id="cv1")
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert d["origin"] == "funnel"
    surf = d.get("surface")
    # canonical vocabulary (cross-lane msg_7549a899): the /surface path says device 'ios'/'web'
    # ('watch' coming); the journal stamp normalizes to phone/watch/web so the brain never
    # sees a split vocabulary. Raw value preserved alongside.
    assert surf and surf["device"] == "phone" and surf["device_raw"] == "ios"
    assert surf["route"] == "/agents/x"
    assert isinstance(surf.get("age_s"), (int, float))


@pytest.mark.parametrize("raw,canon", [("ios", "phone"), ("iphone", "phone"),
                                       ("web", "web"),
                                       ("something-new", "something-new")])
def test_call_surface_canonicalizes_device(tmp_path, raw, canon):
    from services.arturo import surface as sf
    p = tmp_path / "active-surface.json"
    p.write_text(json.dumps(_surface_doc(device=raw)))
    out = sf.call_surface(p)
    assert out["device"] == canon and out["device_raw"] == raw


def test_call_surface_watch_omits_stamp(tmp_path):
    """Watch presence can be the freshest surface (live since ios-watch-dev msg_9e1a4d2c) but
    NO voice path exists on the watch — a voice journal stamped device=watch would be a false
    attribution. Watch at call start => omit (absence=unknown contract). second-brain-dev-g5
    msg_ea2b04bd."""
    from services.arturo import surface as sf
    p = tmp_path / "active-surface.json"
    p.write_text(json.dumps(_surface_doc(device="watch")))
    assert sf.call_surface(p) is None


def test_journal_no_stamp_when_watch_freshest(tmp_path, monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    (tmp_path / "active-surface.json").write_text(json.dumps(_surface_doc(device="watch")))
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path)
    non_system = [{"role": "user", "content": "hey arturo"},
                  {"role": "assistant", "content": "hey boss"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="funnel", conv_id="cv3")
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert "surface" not in d


def test_journal_surface_absent_file_no_stamp_no_crash(tmp_path, monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path / "nosuch")
    non_system = [{"role": "user", "content": "hey arturo"},
                  {"role": "assistant", "content": "hey boss"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="local", conv_id="cv2")
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert "surface" not in d              # missing/malformed surface -> omit, never garbage


def test_call_surface_helper_malformed(tmp_path):
    from services.arturo import surface as sf
    p = tmp_path / "active-surface.json"
    p.write_text("{not json")
    assert sf.call_surface(p) is None
    p.write_text(json.dumps({"current": {}}))
    out = sf.call_surface(p)
    assert out is None or out.get("device")   # empty current -> no useless stamp
