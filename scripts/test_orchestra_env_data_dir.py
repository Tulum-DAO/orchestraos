"""scripts/orchestra-env.sh (sourced by spawn-agent.sh and the cron beats) must follow the ONE data-dir order
(data-dir sweep S5): a caller's $ORCHESTRA_DIR (or the deprecated $ORCH_DIR) wins, then [data] dir in the toml,
then ~/.orchestra. It used to export the toml's dir UNCONDITIONALLY, falling back to the CHECKOUT ROOT: on an
installed checkout a test that pointed ORCHESTRA_DIR at a temp dir had its seat written into the operator's real
registry (orchestraos-providers, measured in a fresh gate container)."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_SH = ROOT / "scripts" / "orchestra-env.sh"
KEYS = ("ORCHESTRA_DIR", "ORCH_DIR", "WATCH_GATEWAY_TOKEN_FILE")


def _sourced(tmp_path, toml, preset=None):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(toml)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORCH")}
    env.update(ORCHESTRA_CONFIG=str(cfg), HOME=str(tmp_path / "home"), **(preset or {}))
    out = subprocess.run(["bash", "-c", f"source '{ENV_SH}'; " + "; ".join(f'echo "${k}"' for k in KEYS)],
                         env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return dict(zip(KEYS, out.stdout.splitlines()))


def test_a_preset_orchestra_dir_wins_over_the_toml(tmp_path):
    got = _sourced(tmp_path, '[data]\ndir = "/srv/real-install"\n', {"ORCHESTRA_DIR": "/tmp/test-fence"})
    assert got["ORCHESTRA_DIR"] == got["ORCH_DIR"] == "/tmp/test-fence"
    assert got["WATCH_GATEWAY_TOKEN_FILE"] == "/tmp/test-fence/state/watch-gateway-token"


def test_the_deprecated_orch_dir_alias_is_honoured_too(tmp_path):
    got = _sourced(tmp_path, '[data]\ndir = "/srv/real-install"\n', {"ORCH_DIR": "/tmp/old-name"})
    assert got["ORCHESTRA_DIR"] == got["ORCH_DIR"] == "/tmp/old-name"


def test_without_a_preset_the_toml_dir_is_used(tmp_path):
    got = _sourced(tmp_path, '[data]\ndir = "/srv/real-install"\n')
    assert got["ORCHESTRA_DIR"] == got["ORCH_DIR"] == "/srv/real-install"


@pytest.mark.parametrize("toml", ["", '[data]\ndir = ""\n', '[api]\nport = 18888\n'])
def test_no_data_dir_in_the_toml_means_the_default_never_the_checkout(tmp_path, toml):
    got = _sourced(tmp_path, toml)
    assert got["ORCHESTRA_DIR"] == str(tmp_path / "home" / ".orchestra")
    assert got["ORCHESTRA_DIR"] != str(ROOT), "fell back to the checkout root"


def test_BY_EFFECT_a_test_seat_never_lands_in_the_installs_registry(tmp_path):
    """The reported leak, end to end through the REAL spawn path: an installed checkout (a toml naming the
    install's data dir) and test_tier_rule's parentless-helper test, which points ORCHESTRA_DIR at its own temp
    dir. Before the fix that test's seat landed in the install's registry (plus logs/, memory/, state/)."""
    real = tmp_path / "the-install"
    real.mkdir()
    (real / "registry.json").write_text('{"agents": {}}')
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(f'[data]\ndir = "{real}"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORCH")}
    env["ORCHESTRA_CONFIG"] = str(cfg)
    t = "scripts/test_tier_rule.py::test_BY_EFFECT_a_parentless_helper_registers_as_a_T1_worker"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", t],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:]
    assert json.loads((real / "registry.json").read_text())["agents"] == {}, "a test seat reached the install"
    assert sorted(p.name for p in real.iterdir()) == ["registry.json"], "the spawn wrote into the install"
