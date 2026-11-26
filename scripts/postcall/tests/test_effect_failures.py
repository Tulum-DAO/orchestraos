"""A failing effect never blocks the others (design §1 "Recovery"): each send is tried on its own; a
failure is recorded on the effect row (error + attempts) and the effect is retried on the next sweep,
once per sweep. After 5 failed attempts it is `failed`, skipped thereafter, and listed in the output."""
import datetime as dt
import os
import shutil
import json

import pytest

from postcall import effects, extract, ledger, sweep
from postcall.effects import Effect

START = dt.datetime(2026, 11, 25, 0, 0, tzinfo=dt.timezone.utc).timestamp()
NOW = START + 24 * 3600


class ResultsDown(effects.Recorder):
    """The message bus refuses every results notice; everything else lands."""

    def __init__(self):
        super().__init__()
        self.tries = 0

    def send(self, effect):
        if effect.kind == "results":
            self.tries += 1
            raise RuntimeError("msg_store send failed rc=1: bus down")
        return super().send(effect)


@pytest.fixture
def env(tmp_path, monkeypatch, fixture_dir):
    root = tmp_path / "pocket"
    root.mkdir()
    monkeypatch.setenv("POSTCALL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("POCKET_ROOT", str(root))
    sweep.ensure_start_ts(effects.ledger_path("test"), START)
    dst = root / "call-a"
    shutil.copytree(os.path.join(fixture_dir, "pocket", "two-party-call"), dst)
    meta = json.loads((dst / "meta.json").read_text())
    meta["created_at"] = "2026-11-25T01:00:00Z"
    (dst / "meta.json").write_text(json.dumps(meta))
    for f in ("meta.json", "transcript.md"):
        os.utime(dst / f, (NOW - 600, NOW - 600))
    with open(os.path.join(fixture_dir, "llm", "two-party-ok.json")) as f:
        raw = json.load(f)
    return lambda call: extract.Extraction(ok=True, raw=raw)


def test_one_failing_effect_kind_never_blocks_the_rest_and_fails_after_five_sweeps(env):
    led = ledger.Ledger(effects.ledger_path("test"))
    led.plan(Effect("results:card:old:r0:p1", "results", {"to": "gm", "subject": "s", "body": "b"}), call_id="old")
    world = ResultsDown()

    out = sweep.run_once(now=NOW, extractor=env, adapter=world)
    assert out["status"] == "ok" and out["statuses"] == {"carded": 1}     # new work still happened
    assert [e.kind for e in world.sent] == ["card"]
    assert world.tries == 1                                                 # once per sweep, not per pass
    row = {e["effect_id"]: e for e in led.effects()}["results:card:old:r0:p1"]
    assert row["attempts"] == 1 and "bus down" in row["last_error"] and row["state"] in ("planned", "sent")
    assert out["failed_effects"] == []

    for n in range(1, 5):
        out = sweep.run_once(now=NOW + 300 * n, extractor=env, adapter=world)
    assert world.tries == 5
    row = {e["effect_id"]: e for e in led.effects()}["results:card:old:r0:p1"]
    assert row["state"] == "failed" and row["attempts"] == 5
    assert [f["effect_id"] for f in out["failed_effects"]] == ["results:card:old:r0:p1"]

    out = sweep.run_once(now=NOW + 3000, extractor=env, adapter=world)
    assert world.tries == 5                                                 # skipped thereafter
    assert [f["effect_id"] for f in out["failed_effects"]] == ["results:card:old:r0:p1"]


def test_run_pending_keeps_going_past_a_failing_effect(tmp_path):
    led = ledger.Ledger(str(tmp_path / "l.db"))
    led.plan(Effect("results:a", "results", {"to": "gm"}), call_id="x")
    led.plan(Effect("card:x:r0:p1", "card", {"title": "t"}), call_id="x")
    world = ResultsDown()
    led.run_pending(world)
    assert [e.effect_id for e in world.sent] == ["card:x:r0:p1"]
