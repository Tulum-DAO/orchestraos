"""Behavioral tests for the rotation-self-trigger.js hook (gm commission).

Shells out to the real node hook with piped stdin + a scratch metrics/config/mark
setup (tempfile-isolated). Verifies: silent above band, fires+escalates through the
level ladder once-per-level, writes a seam mark matching build_mark's shape, warn_only
strips the autonomous act, env opt-out, and fail-open on garbage. Skips if node absent.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

import pytest

from scripts.focus_registry.rotation_signal import build_mark

# The REPO copy is the version-controlled single source of truth (gm ruling); the
# arm-action copies/symlinks it to ~/.claude/hooks/. Test the tracked file.
_HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "scripts", "hooks", "rotation-self-trigger.js")
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(
    not (_HAS_NODE and os.path.exists(_HOOK)), reason="node or hook missing")


class _Env:
    """Isolated scratch: a fake metrics file + config pointing marks at a temp dir."""
    def __init__(self):
        self.d = tempfile.mkdtemp(prefix="rothook-")
        self.session = "sess-" + os.path.basename(self.d)
        self.metrics = os.path.join(self.d, f"claude-ctx-{self.session}.json")
        self.sentinel = os.path.join(self.d, f"rotation-{self.session}.json")
        self.mark_dir = os.path.join(self.d, "marks")
        self.config = os.path.join(self.d, "config.json")
        self._write_config({})

    def _write_config(self, extra):
        cfg = {"mark_dir": self.mark_dir}
        cfg.update(extra)
        with open(self.config, "w") as fh:
            json.dump(cfg, fh)

    def set_remaining(self, remaining, used=None):
        with open(self.metrics, "w") as fh:
            json.dump({"remaining_percentage": remaining,
                       "used_pct": used if used is not None else 100 - remaining,
                       "timestamp": int(time.time())}, fh)

    def run(self, *, env_extra=None, config_extra=None, stdin=None):
        if config_extra is not None:
            self._write_config(config_extra)
        env = dict(os.environ)
        # Redirect the hook's HOME-derived paths into the scratch dir by overriding
        # the metrics/sentinel via a tmpdir + config-driven mark_dir. The hook reads
        # metrics from os.tmpdir()/claude-ctx-<sid>; point TMPDIR at our scratch.
        env["TMPDIR"] = self.d
        env["HOME"] = self.d  # so CONFIG_PATH resolves under scratch .claude
        # ISOLATE: the fleet-default ORCH_SELF_ROTATE (set at arming) must NOT leak
        # into config-driven tests — strip ambient control vars; tests set them via
        # env_extra when they mean to exercise env precedence.
        env.pop("ORCH_SELF_ROTATE", None)
        env.pop("ORCH_SELF_ROTATE_MODE", None)
        os.makedirs(os.path.join(self.d, ".claude", "hooks"), exist_ok=True)
        shutil.copy(_HOOK, os.path.join(self.d, ".claude", "hooks", "rotation-self-trigger.js"))
        shutil.copy(self.config, os.path.join(self.d, ".claude", "rotation-self-trigger.json"))
        if env_extra:
            env.update(env_extra)
        payload = stdin if stdin is not None else json.dumps(
            {"session_id": self.session, "cwd": "/x"})
        proc = subprocess.run(
            ["node", os.path.join(self.d, ".claude", "hooks", "rotation-self-trigger.js")],
            input=payload, text=True, capture_output=True, env=env, timeout=10)
        return proc

    def mark(self):
        p = os.path.join(self.d, "scripts", "agent-orchestra", "state",
                         "rotation-self-triggers")  # default mark_dir under HOME
        # config sets mark_dir to self.mark_dir under scratch HOME? config mark_dir is
        # absolute -> honored directly.
        mp = os.path.join(self.mark_dir, self.session + ".json")
        return json.load(open(mp)) if os.path.exists(mp) else None

    def cleanup(self):
        shutil.rmtree(self.d, ignore_errors=True)


@pytest.fixture
def envs():
    e = _Env()
    yield e
    e.cleanup()


def _ctx(proc):
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_silent_above_rotation_band(envs):
    envs.set_remaining(35)
    proc = envs.run()
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_fires_author_at_12_and_writes_mark(envs):
    envs.set_remaining(12, used=88)
    proc = envs.run()
    assert proc.returncode == 0
    msg = _ctx(proc)
    assert msg and "author" in msg.lower()
    m = envs.mark()
    assert m is not None
    assert m["kind"] == "rotation_self_trigger"
    assert m["level"] == "author"
    assert set(m.keys()) == set(build_mark("x", "%1", "/x", 9.0, "author", 1.0).keys())


def test_debounces_same_level(envs):
    envs.set_remaining(12)
    envs.run()                      # first author fire
    proc = envs.run()               # same band again
    assert proc.stdout.strip() == ""  # debounced


def test_escalates_to_rotate(envs):
    envs.set_remaining(12)
    envs.run()                      # author
    envs.set_remaining(9, used=91)
    proc = envs.run()               # escalate
    msg = _ctx(proc)
    assert msg is not None and "rotate" in msg.lower()


def test_immediate_level_says_before_auto_compact(envs):
    envs.set_remaining(7, used=93)
    msg = _ctx(envs.run())
    assert msg and ("immediate" in msg.lower() or "auto-compact" in msg.lower())


def test_full_mode_includes_autonomous_spawn_retire(envs):
    envs.set_remaining(9)
    msg = _ctx(envs.run(config_extra={"mode": "full"}))
    assert "spawn" in msg.lower() and "self-retire" in msg.lower()


def test_warn_only_mode_strips_autonomous_act(envs):
    envs.set_remaining(9)
    msg = _ctx(envs.run(config_extra={"mode": "warn_only"}))
    assert "do not auto-retire" in msg.lower() or "supervised" in msg.lower()
    assert "self-retire (park-idle" not in msg.lower()


def test_env_optout_suppresses_fire(envs):
    envs.set_remaining(7)
    proc = envs.run(env_extra={"ORCH_SELF_ROTATE": "off"})
    assert proc.stdout.strip() == ""


def test_env_warn_only_single_var_selects_supervised_mode(envs):
    # gm baking: ORCH_SELF_ROTATE=warn_only (single var) -> warn_only for T0/T1.
    envs.set_remaining(9)
    msg = _ctx(envs.run(env_extra={"ORCH_SELF_ROTATE": "warn_only"}))
    assert msg is not None
    assert "do not auto-retire" in msg.lower() or "supervised" in msg.lower()
    assert "self-retire (park-idle" not in msg.lower()


def test_env_on_single_var_selects_full_autonomous(envs):
    # ORCH_SELF_ROTATE=on -> full autonomous for T2.
    envs.set_remaining(9)
    msg = _ctx(envs.run(env_extra={"ORCH_SELF_ROTATE": "on"}))
    assert msg is not None
    assert "spawn" in msg.lower() and "self-retire" in msg.lower()


def test_fail_open_on_garbage_stdin(envs):
    proc = envs.run(stdin="not json at all")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_repo_copy_matches_live_copy_if_armed():
    # Drift guard: once armed, ~/.claude/hooks/ must equal the tracked repo copy
    # (the arm-action copies the repo copy). If the live copy is absent (not armed),
    # skip — staging is a valid state. If present, it MUST match the source of truth.
    live = os.path.expanduser("~/.claude/hooks/rotation-self-trigger.js")
    if not os.path.exists(live):
        pytest.skip("hook not armed to ~/.claude (staging) — repo copy is the source of truth")
    with open(_HOOK) as a, open(live) as b:
        assert a.read() == b.read(), "live hook drifted from the repo source of truth"
