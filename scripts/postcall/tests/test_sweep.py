"""The cron sweeper (design §1 "Mechanism" + "Concurrency contract", §7 live_start_ts).

One run: take the lock, reconcile open effects, then process every settled, ingested Pocket call
created at or after the ledger's start_ts. Every test gets its own state dir and Pocket root."""
import datetime as dt
import fcntl
import json
import os
import shutil

import pytest

from postcall import effects, extract, ledger, sweep
from postcall.effects import Effect

START = dt.datetime(2026, 11, 25, 0, 0, tzinfo=dt.timezone.utc).timestamp()
NOW = START + 24 * 3600          # a day after activation
OLD_MTIME = NOW - 600            # well past the 120 s settle window


def _iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Private state dir + Pocket root, test mode."""
    state, root = tmp_path / "state", tmp_path / "pocket"
    root.mkdir()
    monkeypatch.setenv("POSTCALL_STATE_DIR", str(state))
    monkeypatch.setenv("POCKET_ROOT", str(root))
    monkeypatch.setenv("POSTCALL_MODE", "test")
    return {"state": state, "root": root}


@pytest.fixture
def seeded(env):
    """A test ledger whose start_ts is START (activation happened before any of our calls)."""
    assert sweep.ensure_start_ts(effects.ledger_path("test"), START) == _iso(START)
    return env


def _add_call(fixture_dir, root, name, *, created=START + 3600, mtime=OLD_MTIME, recording_id=None):
    """Copy the two-party fixture into the Pocket root as `name`, retimed and (optionally) re-id'd."""
    dst = root / name
    shutil.copytree(os.path.join(fixture_dir, "pocket", "two-party-call"), dst)
    meta = json.loads((dst / "meta.json").read_text())
    meta["created_at"] = _iso(created)
    meta["title"] = name
    if recording_id:
        meta["recording_id"] = recording_id
    (dst / "meta.json").write_text(json.dumps(meta))
    for f in ("meta.json", "transcript.md"):
        os.utime(dst / f, (mtime, mtime))
    return dst


@pytest.fixture
def ok_extractor(fixture_dir):
    with open(os.path.join(fixture_dir, "llm", "two-party-ok.json")) as f:
        raw = json.load(f)
    calls = []

    def extractor(call):
        calls.append(call.call_id)
        return extract.Extraction(ok=True, raw=raw)

    extractor.calls = calls
    return extractor


# --- lock ----------------------------------------------------------------------------------------

def test_lock_held_elsewhere_means_busy_and_nothing_processed(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "call-a")
    lock = seeded["state"] / "sweep.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    other = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(other, fcntl.LOCK_EX)           # another sweeper (or its surviving child)
    try:
        world = effects.Recorder()
        out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=world)
    finally:
        os.close(other)
    assert out == {"status": "busy"}
    assert ok_extractor.calls == [] and world.sent == []


def test_lock_fd_is_inheritable_and_exclusive(tmp_path):
    path = str(tmp_path / "sub" / "sweep.lock")
    fd = sweep.acquire_lock(path)
    try:
        assert fd is not None
        assert os.get_inheritable(fd)               # CLI children keep holding it (§1)
        assert sweep.acquire_lock(path) is None     # a second open is refused, not blocked
    finally:
        os.close(fd)
    again = sweep.acquire_lock(path)
    assert again is not None
    os.close(again)


def test_lock_is_held_while_calls_are_processed(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "call-a")
    seen = []

    def extractor(call):
        fd = sweep.acquire_lock(str(seeded["state"] / "sweep.lock"))
        seen.append(fd)
        if fd is not None:
            os.close(fd)
        return ok_extractor(call)

    sweep.run_once(now=NOW, extractor=extractor, adapter=effects.Recorder())
    assert seen == [None]


# --- start_ts --------------------------------------------------------------------------------------

def test_first_run_sets_start_ts_and_later_runs_never_move_it(env, ok_extractor):
    path = effects.ledger_path("test")
    assert sweep.live_start_ts(path) is None
    sweep.run_once(now=NOW, extractor=ok_extractor, adapter=effects.Recorder())
    assert sweep.live_start_ts(path) == _iso(NOW)
    sweep.run_once(now=NOW + 3600, extractor=ok_extractor, adapter=effects.Recorder())
    assert sweep.live_start_ts(path) == _iso(NOW)
    assert sweep.ensure_start_ts(path, NOW + 7200) == _iso(NOW)
    assert sweep.live_start_ts(ledger.Ledger(path)) == _iso(NOW)   # a Ledger works as well as a path


def test_call_created_before_start_ts_is_skipped(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "historical", created=START - 3600)
    out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=effects.Recorder())
    assert out["before_start"] == 1 and out["processed"] == 0
    assert ok_extractor.calls == []


# --- settling ----------------------------------------------------------------------------------------

def test_recently_written_call_settles_then_is_processed(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "call-a", mtime=NOW - 30)
    world = effects.Recorder()
    first = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=world)
    assert first["settling"] == 1 and first["processed"] == 0
    assert ok_extractor.calls == []
    later = sweep.run_once(now=NOW + 200, extractor=ok_extractor, adapter=world)
    assert later["settling"] == 0 and later["processed"] == 1
    assert later["statuses"] == {"carded": 1}


def test_candidates_need_ingested_meta_and_transcript(seeded, fixture_dir):
    root = seeded["root"]
    _add_call(fixture_dir, root, "ready")
    no_tx = _add_call(fixture_dir, root, "no-transcript", recording_id="r-2")
    os.unlink(no_tx / "transcript.md")
    not_ingested = _add_call(fixture_dir, root, "not-ingested", recording_id="r-3")
    meta = json.loads((not_ingested / "meta.json").read_text())
    meta["ingested"] = False
    (not_ingested / "meta.json").write_text(json.dumps(meta))
    os.utime(not_ingested / "meta.json", (OLD_MTIME, OLD_MTIME))
    found = [os.path.basename(d) for d in sweep.candidates(str(root), _iso(START), NOW)]
    assert found == ["ready"]


def test_candidates_are_sorted_by_created_at(seeded, fixture_dir):
    root = seeded["root"]
    _add_call(fixture_dir, root, "b-later", created=START + 7200, recording_id="r-b")
    _add_call(fixture_dir, root, "a-earlier-name-later-call", created=START + 9000, recording_id="r-a")
    _add_call(fixture_dir, root, "c-first", created=START + 60, recording_id="r-c")
    found = [os.path.basename(d) for d in sweep.candidates(str(root), _iso(START), NOW)]
    assert found == ["c-first", "b-later", "a-earlier-name-later-call"]


# --- processing --------------------------------------------------------------------------------------

def test_ready_two_party_call_files_one_card(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "call-a")
    world = effects.Recorder()
    out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=world)
    assert out["status"] == "ok" and out["mode"] == "test"
    assert out["processed"] == 1 and out["statuses"] == {"carded": 1} and out["errors"] == []
    assert [e.kind for e in world.sent] == ["card"]


def test_running_the_sweep_twice_files_one_card(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "call-a")
    world = effects.Recorder()
    sweep.run_once(now=NOW, extractor=ok_extractor, adapter=world)
    second = sweep.run_once(now=NOW + 300, extractor=ok_extractor, adapter=world)
    assert second["statuses"] == {"already_processed": 1}
    assert [e.kind for e in world.sent] == ["card"]
    assert ok_extractor.calls == ["pocket:11111111-2222-4333-8444-555555555555"]


def test_one_bad_call_does_not_stop_the_sweep(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "bad", created=START + 60, recording_id="bad-id")
    _add_call(fixture_dir, seeded["root"], "good", created=START + 120, recording_id="good-id")

    def extractor(call):
        if call.call_id == "pocket:bad-id":
            raise ValueError("extractor exploded")
        return ok_extractor(call)

    world = effects.Recorder()
    out = sweep.run_once(now=NOW, extractor=extractor, adapter=world)
    assert out["statuses"] == {"carded": 1}
    assert len(out["errors"]) == 1
    assert out["errors"][0]["call"] == "bad" and "extractor exploded" in out["errors"][0]["error"]
    assert [e.effect_id for e in world.sent] == ["card:pocket:good-id:r0:p1"]


# --- recovery ----------------------------------------------------------------------------------------

def _plan_orphan():
    """An intent committed by a sweep that died before sending it."""
    led = ledger.Ledger(effects.ledger_path("test"))
    led.plan(Effect("card:pocket:orphan:r0:p1", "card", {"title": "orphan"}), call_id="pocket:orphan")
    return led


def test_recovery_sends_an_orphaned_effect_with_no_new_calls(seeded, ok_extractor):
    led = _plan_orphan()
    world = effects.Recorder()
    out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=world)
    assert out["processed"] == 0
    assert [e.effect_id for e in world.sent] == ["card:pocket:orphan:r0:p1"]
    assert led.effects()[0]["state"] == "confirmed"


def test_recovery_runs_before_any_new_call_is_extracted(seeded, fixture_dir, ok_extractor):
    _plan_orphan()
    _add_call(fixture_dir, seeded["root"], "call-a")
    world = effects.Recorder()
    sent_when_extracting = []

    def extractor(call):
        sent_when_extracting.append([e.effect_id for e in world.sent])
        return ok_extractor(call)

    sweep.run_once(now=NOW, extractor=extractor, adapter=world)
    assert sent_when_extracting == [["card:pocket:orphan:r0:p1"]]


# --- modes -------------------------------------------------------------------------------------------

def test_shadow_mode_uses_the_shadow_ledger_not_live(env, fixture_dir, ok_extractor):
    out = sweep.run_once(mode="shadow", now=NOW, extractor=ok_extractor, adapter=effects.Recorder())
    shadow_db = os.path.join(str(env["state"]), "shadow", "ledger.db")
    live_db = os.path.join(str(env["state"]), "live", "ledger.db")
    assert out["mode"] == "shadow" and out["ledger"] == shadow_db
    assert os.path.exists(shadow_db) and not os.path.exists(live_db)
    assert sweep.live_start_ts(shadow_db) == _iso(NOW)


def test_mode_defaults_to_effects_mode(env, ok_extractor, monkeypatch):
    monkeypatch.delenv("POSTCALL_MODE")          # no MODE file either -> shadow
    out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=effects.Recorder())
    assert out["mode"] == "shadow"
    assert not os.path.exists(os.path.join(str(env["state"]), "live", "ledger.db"))


# --- roots + CLI ----------------------------------------------------------------------------------

def test_pocket_root_env_or_home(monkeypatch):
    monkeypatch.setenv("POCKET_ROOT", "/x/y")
    assert sweep.pocket_root() == "/x/y"
    monkeypatch.delenv("POCKET_ROOT")
    assert sweep.pocket_root() == os.path.expanduser("~/pocket-recordings")


def test_main_prints_one_json_line_and_exits_zero_even_when_busy(monkeypatch, capsys):
    monkeypatch.setattr(sweep, "run_once", lambda **kw: {"status": "busy"})
    assert sweep.main(["--once"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line) for line in lines] == [{"status": "busy"}]


class _AnsweringWorld(effects.Recorder):
    def __init__(self):
        super().__init__()
        self.answers = {}

    def card_answers(self, external_id):
        return self.answers.get(external_id, ("pending", {}))


def test_a_later_sweep_applies_submitted_answers_and_acks(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "call-a")
    world = _AnsweringWorld()
    sweep.run_once(now=NOW, extractor=ok_extractor, adapter=world)
    card_ext = "ext-1"
    world.answers[card_ext] = ("submitted", {1: {"option_n": 3, "final": True}})  # drop all
    out = sweep.run_once(now=NOW + 60, extractor=ok_extractor, adapter=world)
    assert out["status"] == "ok"
    assert [e.kind for e in world.sent] == ["card", "qack"]


# --- the lock reaches CLI children (design §1 "Concurrency contract": pass_fds) ----------------------

def test_live_cli_children_spawned_during_a_sweep_get_the_lock_fd(seeded, monkeypatch, ok_extractor):
    led = ledger.Ledger(effects.ledger_path("test"))
    led.plan(Effect("card:pocket:orphan:r0:p1", "card", {"title": "orphan", "summary": "s", "questions": []}),
             call_id="pocket:orphan")
    lock_path = str(seeded["state"] / "sweep.lock")
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "qnr_child1\n", ""

    def fake_run(argv, *a, **kw):
        fds = tuple(kw.get("pass_fds") or ())
        seen.append([fd for fd in fds if os.fstat(fd).st_ino == os.stat(lock_path).st_ino])
        return Done()

    monkeypatch.setattr(effects._subprocess, "run", fake_run)
    out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=effects.Live())
    assert out["status"] == "ok"
    assert len(seen) == 1 and len(seen[0]) == 1        # the card's approval.py child held the lock
    assert led.effects()[0]["state"] == "confirmed"
    assert effects.lock_fd() is None                    # cleared once the sweep lets go


# --- failed_retry is bounded (design §1 states: failed_retry(n) -> failed_final) -------------------

@pytest.mark.parametrize("failure", ["not_ok", "raises"])
def test_a_call_that_keeps_failing_goes_failed_final_after_three_attempts(seeded, fixture_dir, failure):
    _add_call(fixture_dir, seeded["root"], "call-a")
    tried = []

    def extractor(call):
        tried.append(call.call_id)
        if failure == "raises":
            raise ValueError("extractor exploded")
        return extract.Extraction(ok=False, raw=None, error="llm timeout")

    world = effects.Recorder()
    for n in range(3):
        out = sweep.run_once(now=NOW + 300 * n, extractor=extractor, adapter=world)
    assert len(tried) == 3
    led = ledger.Ledger(effects.ledger_path("test"))
    cid = "pocket:11111111-2222-4333-8444-555555555555"
    assert led.call_status(cid) == "failed_final"
    assert out["failed_final"] == [cid]
    out = sweep.run_once(now=NOW + 3000, extractor=extractor, adapter=world)
    assert len(tried) == 3                               # skipped by the sweep from now on
    assert out["failed_final"] == [cid] and world.sent == []


# --- the call END decides eligibility (design §1 "No backfill", §7 live_start_ts) ------------------

def test_a_call_in_progress_at_activation_is_processed(seeded, fixture_dir, ok_extractor):
    _add_call(fixture_dir, seeded["root"], "straddles", created=START - 300)     # 600 s call: ends START + 300
    _add_call(fixture_dir, seeded["root"], "ended-before", created=START - 601, recording_id="r-old")
    out = sweep.run_once(now=NOW, extractor=ok_extractor, adapter=effects.Recorder())
    assert out["before_start"] == 1 and out["processed"] == 1
    assert ok_extractor.calls == ["pocket:11111111-2222-4333-8444-555555555555"]
