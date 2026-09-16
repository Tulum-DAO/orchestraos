"""RED-first tests for Build B's api_bridge CLI — the thin wrapper the TS
telemetry API shells to REUSE B1's court-scrub disposition seam (it does NOT
re-implement the fail-closed flag check; it calls LineageFlagStore /
token_extractor.disposition_for_stream directly).

The safety contract mirrors B1's token_extractor_test, now proven THROUGH the
CLI boundary the API crosses:
  * clean lineage      -> flag-status readable+not-flagged; disposition streams
  * flagged lineage    -> flag-status flagged; disposition => block, text=""
  * absent/corrupt store-> fail-closed (readable=False) => disposition block
  * RED vs all 3 A0 court REDs at the CLI boundary: zero verbatim leak.
"""
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(__file__)
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))          # .../scripts
_ROOT = os.path.abspath(os.path.join(_SCRIPTS, ".."))               # repo root
_FIX = os.path.join(_ROOT, "contract", "transcript", "fixtures")
BRIDGE = os.path.join(_HERE, "api_bridge.py")


def _run(*args, env=None):
    e = dict(os.environ)
    e["PYTHONPATH"] = _SCRIPTS + os.pathsep + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, BRIDGE, *args],
                       capture_output=True, text=True, env=e)
    return p


def _flags_file(tmp, flagged):
    p = os.path.join(tmp, "lineage_flags.json")
    with open(p, "w") as fh:
        json.dump({"schema": "lineage-flags/v1",
                   "flagged": {r: {"reason": "court"} for r in flagged}}, fh)
    return p


def _court_body(provider):
    with open(os.path.join(_FIX, provider, "a0-court-red.json")) as fh:
        red = json.load(fh)
    inp = red["input"]
    return (inp.get("assistant_text_block") or inp.get("response_item_text")
            or inp.get("step_payload_decoded_text"))


# --- flag-status: reuse LineageFlagStore, fail-closed ------------------------

def test_flag_status_clean_lineage_readable_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        fp = _flags_file(tmp, flagged=["dirty"])
        p = _run("flag-status", "--lineage-root", "clean-one", "--flags-path", fp)
        assert p.returncode == 0, p.stderr
        out = json.loads(p.stdout)
        assert out == {"flagged": False, "readable": True}


def test_flag_status_flagged_lineage():
    with tempfile.TemporaryDirectory() as tmp:
        fp = _flags_file(tmp, flagged=["dirty"])
        out = json.loads(_run("flag-status", "--lineage-root", "dirty",
                              "--flags-path", fp).stdout)
        assert out == {"flagged": True, "readable": True}


def test_flag_status_absent_store_fail_closed():
    out = json.loads(_run("flag-status", "--lineage-root", "x",
                          "--flags-path", "/nope/flags.json").stdout)
    assert out == {"flagged": False, "readable": False}   # caller MUST block


# --- BLOCKING-1 (claude COUNTER): unresolved lineage_root fails CLOSED --------

def test_flag_status_empty_lineage_root_fail_closed():
    # The real leak: LineageFlagStore.status("") would return (False, True) =
    # clean+readable = STREAM. The bridge MUST fail-closed on an empty root
    # BEFORE consulting the store.
    with tempfile.TemporaryDirectory() as tmp:
        fp = _flags_file(tmp, flagged=[])
        out = json.loads(_run("flag-status", "--lineage-root", "",
                              "--flags-path", fp).stdout)
        assert out == {"flagged": False, "readable": False}   # => block


def test_disposition_empty_lineage_root_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        fp = _flags_file(tmp, flagged=[])
        raw = os.path.join(tmp, "raw"); open(raw, "w").write("hello world")
        out = json.loads(_run("disposition", "--runtime", "claude",
                              "--lineage-root", "", "--flags-path", fp,
                              "--raw-file", raw).stdout)
        assert out["stream_mode"] == "block"
        assert out["text"] == ""


# --- disposition: reuse token_extractor.disposition_for_stream ---------------

def test_disposition_clean_streams():
    with tempfile.TemporaryDirectory() as tmp:
        fp = _flags_file(tmp, flagged=[])
        raw = os.path.join(tmp, "raw"); open(raw, "w").write("hello world")
        out = json.loads(_run("disposition", "--runtime", "claude",
                              "--lineage-root", "clean", "--flags-path", fp,
                              "--raw-file", raw).stdout)
        assert out["stream_mode"] == "clean"
        assert out["streamed"] is True
        assert out["text"] == "hello world"


def test_disposition_all_three_court_reds_block_zero_leak():
    with tempfile.TemporaryDirectory() as tmp:
        fp = _flags_file(tmp, flagged=["dirty-root"])
        for provider in ("claude", "codex", "gemini"):
            body = _court_body(provider)
            raw = os.path.join(tmp, f"raw-{provider}")
            open(raw, "w").write(body)
            out_txt = _run("disposition", "--runtime", provider,
                           "--lineage-root", "dirty-root", "--flags-path", fp,
                           "--raw-file", raw).stdout
            out = json.loads(out_txt)
            assert out["stream_mode"] == "block", provider
            assert out["streamed"] is False, provider
            assert out["text"] == "", provider
            assert body[:24] not in out_txt, provider    # zero verbatim leak


def test_disposition_fail_closed_on_absent_store():
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "raw"); open(raw, "w").write("anything")
        out = json.loads(_run("disposition", "--runtime", "claude",
                              "--lineage-root", "x", "--flags-path",
                              "/nope/flags.json", "--raw-file", raw).stdout)
        assert out["stream_mode"] == "block"
