"""TDD for the durable BG-observation fix: run_capture must re-resolve the seat's
canonical sid each tick and rebuild the tailer when it changes (else a rotation
re-blinds capture). DEC-1789348596101795."""
from types import SimpleNamespace

from . import run_capture as rc


def _fakes():
    holder = {"sid": "sidA"}
    builds = []
    ticks = []

    def fake_build(seat, home=None, orchestra_dir=None):
        spec = SimpleNamespace(sid=holder["sid"], lineage_root=seat)
        tailer = SimpleNamespace(tick=lambda: ticks.append(holder["sid"]))
        builds.append(holder["sid"])
        return spec, None, tailer, "/wal"

    return holder, builds, ticks, fake_build


def test_rebuilds_tailer_when_sid_changes():
    holder, builds, ticks, fake_build = _fakes()
    resolve_returns = ["sidA", "sidB"]  # tick1: same; tick2: rotated -> rebuild

    def fake_resolve(seat):
        v = resolve_returns.pop(0)
        holder["sid"] = v
        return v

    stops = iter([False, False, True])
    rc.run_capture_loop("ios-watch-dev", interval=5, build_fn=fake_build,
                        resolve_sid_fn=fake_resolve,
                        should_stop=lambda: next(stops), sleep=lambda s: None)
    assert builds == ["sidA", "sidB"]      # initial + one rebuild on the flip
    assert ticks == ["sidA", "sidB"]       # two ticks, second on the new sid


def test_no_rebuild_when_sid_stable():
    holder, builds, ticks, fake_build = _fakes()

    def fake_resolve(seat):
        return "sidA"

    stops = iter([False, False, False, True])
    rc.run_capture_loop("ios-watch-dev", interval=5, build_fn=fake_build,
                        resolve_sid_fn=fake_resolve,
                        should_stop=lambda: next(stops), sleep=lambda s: None)
    assert builds == ["sidA"]              # built once, never rebuilt
    assert len(ticks) == 3


def test_resolver_error_keeps_current_tailer_and_does_not_crash():
    holder, builds, ticks, fake_build = _fakes()

    def boom(seat):
        raise RuntimeError("registry read failed")

    stops = iter([False, False, True])
    rc.run_capture_loop("ios-watch-dev", interval=5, build_fn=fake_build,
                        resolve_sid_fn=boom,
                        should_stop=lambda: next(stops), sleep=lambda s: None)
    assert builds == ["sidA"]              # no rebuild on resolver error
    assert len(ticks) == 2                 # kept ticking, never crashed
