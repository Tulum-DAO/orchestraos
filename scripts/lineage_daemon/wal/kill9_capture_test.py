"""STAGE-1 PROOF (spec §7 step 1): complete, ordered capture across a FORCED
kill -9 of a Blue mid-tool = zero-in-band-exit demonstrated by effect.

Two proofs:
  * test_deterministic_partial_line_* : a Blue transcript that ends mid-write
    (the exact kill-9 shape) is captured completely up to the last WHOLE event,
    the partial trailing byte-run is never half-captured, and the final in-flight
    tool_call is present with its result correctly ABSENT (risk-3/F5: Green must
    treat the dangling call as UNKNOWN-outcome — capture never fabricates it).
  * test_real_subprocess_sigkill_midtool : spawn a REAL writer process emitting a
    harness-shaped jsonl, SIGKILL it while it holds a half-written tool_result
    line, then tail. The dying process does NOTHING at exit; because capture is
    out-of-band, everything it flushed is in the WAL, in order, no gap.
"""
import json
import os
import signal
import subprocess
import sys
import textwrap
import time

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.adapter_claude import ClaudeWalAdapter


def _line(**o):
    o.setdefault("sessionId", "blue-sid")
    o.setdefault("timestamp", "2026-09-02T03:00:00.000Z")
    return json.dumps(o) + "\n"


def _tool_call(i):
    return _line(type="assistant", message={"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Bash",
         "input": {"command": "echo hi"}}]})


def _tool_result(i):
    return _line(type="user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]})


def test_deterministic_partial_line_complete_ordered_capture(tmp_path):
    src = str(tmp_path / "blue.jsonl")
    prompt = _line(type="user", message={"role": "user",
                   "content": [{"type": "text", "text": "go"}]})
    complete = prompt + _tool_call(0) + _tool_result(0) + _tool_call(1)
    # killed MID-TOOL: the tool_result for t1 is only half-written, no newline
    partial = '{"type": "user", "sessionId": "blue-sid", "message": {"content":[{"type":"tool_re'
    with open(src, "w") as fh:
        fh.write(complete + partial)

    store = WalStore(str(tmp_path / "blue.db"))
    ad = ClaudeWalAdapter(store, "ios-watch-dev", 6)
    ad.tail(src)

    evs = store.events()
    kinds = [r["kind"] for r in evs]
    # every WHOLE event captured, in order; the half-written line is NOT present
    assert kinds == ["prompt", "tool_call", "tool_result", "tool_call"]
    # no gap/unparseable marker — capture is clean, not lossy
    assert all("unparseable" not in (r["summary"] or "") for r in evs)
    # the final in-flight tool_call is captured; its result is ABSENT (not faked)
    assert evs[-1]["kind"] == "tool_call"
    assert "tool_result" not in [k for k in kinds[4:]]
    # all events carry an integrity hash (gap/tamper detection)
    assert all(r["integrity"] for r in evs)


def test_partial_line_resolves_when_writer_completes_it(tmp_path):
    """The partial trailing line is not lost forever: once newline-completed it
    is captured on the next tail (proves we hold, not drop, the boundary)."""
    src = str(tmp_path / "blue.jsonl")
    with open(src, "w") as fh:
        fh.write(_tool_call(0) + '{"type":"user","sessionId":"blue-sid","messa')
    store = WalStore(str(tmp_path / "b.db"))
    ad = ClaudeWalAdapter(store, "ios-watch-dev", 6)
    assert ad.tail(src) == 1
    with open(src, "w") as fh:
        fh.write(_tool_call(0) + _tool_result(0))
    assert ad.tail(src) == 1
    assert [r["kind"] for r in store.events()] == ["tool_call", "tool_result"]


_WRITER = textwrap.dedent('''
    import json, os, sys, time
    p = sys.argv[1]
    sid = "blue-sid"
    def w(obj):
        with open(p, "a") as f:
            f.write(json.dumps(obj) + "\\n"); f.flush(); os.fsync(f.fileno())
    w({"type":"user","sessionId":sid,"timestamp":"2026-09-02T03:00:00.000Z",
       "message":{"role":"user","content":[{"type":"text","text":"go"}]}})
    for i in range(3):
        w({"type":"assistant","sessionId":sid,"timestamp":"2026-09-02T03:00:01.000Z",
           "message":{"role":"assistant","content":[{"type":"tool_use","id":"t%d"%i,
           "name":"Bash","input":{"command":"echo"}}]}})
        w({"type":"user","sessionId":sid,"timestamp":"2026-09-02T03:00:02.000Z",
           "message":{"role":"user","content":[{"type":"tool_result",
           "tool_use_id":"t%d"%i,"content":"ok"}]}})
    # final in-flight tool_call (complete + flushed) = the tool that was RUNNING
    w({"type":"assistant","sessionId":sid,"timestamp":"2026-09-02T03:00:03.000Z",
       "message":{"role":"assistant","content":[{"type":"tool_use","id":"tFINAL",
       "name":"Bash","input":{"command":"sleep"}}]}})
    # begin its tool_result but never finish the line (no newline) -> mid-tool death
    with open(p, "a") as f:
        f.write('{"type": "user", "sessionId": "blue-sid", "message": {"content":[{"type":"tool_re')
        f.flush(); os.fsync(f.fileno())
    time.sleep(3600)  # block until SIGKILL
''')


def test_real_subprocess_sigkill_midtool(tmp_path):
    src = str(tmp_path / "blue.jsonl")
    writer_py = str(tmp_path / "writer.py")
    with open(writer_py, "w") as fh:
        fh.write(_WRITER)

    proc = subprocess.Popen([sys.executable, writer_py, src])
    try:
        # wait until the process has flushed the final tool_call + the partial
        # tool_result start (the mid-tool state), then SIGKILL it.
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.path.exists(src):
                data = open(src).read()
                if "tFINAL" in data and data.endswith('"tool_re'):
                    break
            time.sleep(0.02)
        else:
            proc.kill(); proc.wait()
            raise AssertionError("writer never reached mid-tool state")
        os.kill(proc.pid, signal.SIGKILL)   # FORCED kill -9, mid-tool
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()

    assert proc.returncode == -signal.SIGKILL  # it really was force-killed

    # Out-of-band capture AFTER death: the dead process did nothing to hand off.
    store = WalStore(str(tmp_path / "blue.db"))
    ad = ClaudeWalAdapter(store, "ios-watch-dev", 6)
    ad.tail(src)

    kinds = [r["kind"] for r in store.events()]
    expected = (["prompt"]
                + ["tool_call", "tool_result"] * 3
                + ["tool_call"])  # tFINAL, its result half-written & uncaptured
    assert kinds == expected, kinds
    # completeness: no gap/unparseable markers, every event has integrity
    assert all("unparseable" not in (r["summary"] or "") for r in store.events())
    assert all(r["integrity"] for r in store.events())
    # the in-flight tool call survived death; its outcome is UNKNOWN (absent)
    assert store.events()[-1]["kind"] == "tool_call"
