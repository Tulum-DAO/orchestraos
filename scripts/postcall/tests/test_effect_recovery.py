"""Effect recovery (design §1): every external write has a durable intent row with a deterministic
id; a run reconciles against the outside world before it re-sends, so a crash on either side of
the external write still yields exactly one card / task / message."""
import os

import pytest

from postcall import effects, extract, ledger, pipeline, sources
from postcall.effects import Effect


class CrashAfterWrite(effects.Recorder):
    """The external write lands, then the process dies before the ledger records it."""

    def __init__(self):
        super().__init__()
        self.crash = True

    def send(self, effect):
        ext = super().send(effect)
        if self.crash:
            self.crash = False
            raise SystemExit("killed after external write")
        return ext


class CrashBeforeWrite(effects.Recorder):
    def __init__(self):
        super().__init__()
        self.crash = True

    def send(self, effect):
        if self.crash:
            self.crash = False
            raise SystemExit("killed before external write")
        return super().send(effect)


@pytest.fixture
def led(tmp_path):
    return ledger.Ledger(str(tmp_path / "ledger.db"))


def _card(n=1):
    return Effect(effect_id=f"card:pocket:x:r0:p{n}", kind="card", payload={"title": "t"})


def test_planning_the_same_effect_twice_keeps_one_intent(led):
    led.plan(_card(), call_id="pocket:x")
    led.plan(_card(), call_id="pocket:x")
    assert [e["effect_id"] for e in led.effects()] == ["card:pocket:x:r0:p1"]
    assert led.effects()[0]["state"] == "planned"


def test_run_sends_once_and_confirms(led):
    world = effects.Recorder()
    led.plan(_card(), call_id="pocket:x")
    led.run_pending(world)
    led.run_pending(world)
    assert [e.effect_id for e in world.sent] == ["card:pocket:x:r0:p1"]
    assert led.effects()[0]["state"] == "confirmed"
    assert led.effects()[0]["external_id"]


def test_crash_after_external_write_is_reconciled_not_resent(led):
    world = CrashAfterWrite()
    led.plan(_card(), call_id="pocket:x")
    with pytest.raises(SystemExit):
        led.run_pending(world)
    assert led.effects()[0]["state"] == "sent"
    led.run_pending(world)
    assert len(world.sent) == 1
    assert led.effects()[0]["state"] == "confirmed"


def test_crash_before_external_write_is_resent_once(led):
    world = CrashBeforeWrite()
    led.plan(_card(), call_id="pocket:x")
    with pytest.raises(SystemExit):
        led.run_pending(world)
    led.run_pending(world)
    assert len(world.sent) == 1
    assert led.effects()[0]["state"] == "confirmed"


def test_processing_the_same_call_twice_files_one_card(fixture_dir, led):
    call = sources.load_pocket(os.path.join(fixture_dir, "pocket", "two-party-call"))
    world = effects.Recorder()
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": [{
        "owner": "shaw", "kind": "commitment", "counterpart": "Dana Reyes", "action_type": "document_send",
        "operation": "send", "deliverable": "pricing deck", "commitment_text": "Send Dana the pricing deck",
        "quote": "I'll send you the pricing deck by Friday."}]}

    def extractor(c):
        return extract.Extraction(ok=True, raw=raw)

    pipeline.process(call, ledger=led, extractor=extractor, effects=world)
    second = pipeline.process(call, ledger=led, extractor=extractor, effects=world)
    assert [e.kind for e in world.sent] == ["card"]
    assert second.status == "already_processed"


def test_replanning_a_confirmed_effect_does_not_reopen_it(led):
    world = effects.Recorder()
    led.plan(_card(), call_id="pocket:x")
    led.run_pending(world)
    led.plan(_card(), call_id="pocket:x")
    assert led.effects()[0]["state"] == "confirmed"
    led.run_pending(effects.Recorder())  # a world that has forgotten it must not see a resend
    assert led.effects()[0]["state"] == "confirmed"
