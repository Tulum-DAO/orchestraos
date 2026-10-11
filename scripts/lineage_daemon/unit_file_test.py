"""The telemetry daemon's systemd unit (issue G3), tested against the SUBSTITUTED file.

deploy/systemd/orchestra-telemetryd.service.template carries {{PLACEHOLDERS}} for the install
user, the checkout, the data dir, the python and the PATH; `orchestra install-unit --unit
telemetryd` fills them in. These tests render it the way install-unit does and check the result:
it runs as the install user (never root: root only installs it), Type=simple +
Restart=on-failure (event-driven long-runner), invokes telemetryd as a package module with the
checkout and scripts/ on PYTHONPATH, and carries nothing that enables or starts itself.
"""
import configparser
import os
import shutil
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from orchestra_cli import install_unit as IU  # noqa: E402

_TEMPLATE = os.path.join(_ROOT, "deploy", "systemd", "orchestra-telemetryd.service.template")
_USER = "orchestra-operator"
_CHECKOUT = "/opt/orchestraos"
_DATA = "/var/lib/orchestra-data"
_HOME = "/home/orchestra-operator"
_VALUES = {
    "ORCHESTRA_USER": _USER,
    "ORCHESTRA_ROOT": _CHECKOUT,
    "ORCHESTRA_DIR": _DATA,
    "ORCHESTRA_CONFIG": _CHECKOUT + "/orchestra.toml",
    "PYTHON": _CHECKOUT + "/.venv/bin/python",
    "PATH": IU.build_path("/usr/bin", _HOME, exists=lambda d: False),
}


def _text():
    with open(_TEMPLATE) as fh:
        return IU.render(fh.read(), _VALUES)


def _cfg():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    # systemd allows repeated keys and has no interpolation; disable both.
    cp.optionxform = str
    cp.read_string(_text())
    return cp


def _env():
    out = {}
    for line in _text().splitlines():
        if line.startswith("Environment="):
            k, v = line[len("Environment="):].strip('"').split("=", 1)
            out[k] = v
    return out


def test_template_exists_and_is_templated():
    assert os.path.exists(_TEMPLATE), f"missing unit template {_TEMPLATE}"
    raw = open(_TEMPLATE).read()
    for name in ("ORCHESTRA_USER", "ORCHESTRA_DIR", "ORCHESTRA_ROOT", "PYTHON", "PATH"):
        assert "{{%s}}" % name in raw
    assert "/home/" not in raw                   # no install-specific path in the shipped file


def test_substituted_file_has_no_placeholder_left():
    assert "{{" not in _text()


def test_runs_as_the_install_user_not_root():
    svc = _cfg()["Service"]
    assert svc["User"] == _USER and svc["User"] != "root"   # NEVER root (root only installs)


def test_type_simple_and_restart_on_failure():
    svc = _cfg()["Service"]
    assert svc["Type"] == "simple"
    assert svc["Restart"] == "on-failure"


def test_execstart_runs_telemetryd_module():
    exe = _cfg()["Service"]["ExecStart"]
    assert "-m lineage_daemon.telemetryd" in exe
    assert "python" in exe and _VALUES["PYTHON"] in exe


def test_env_and_workingdir_set():
    env = _env()
    assert env["ORCHESTRA_DIR"] == _DATA                    # the daemon's data root
    assert env["ORCHESTRA_REALTIME_DIR"] == _DATA + "/realtime"
    assert env["PYTHONPATH"].split(":") == [_CHECKOUT, _CHECKOUT + "/scripts"]  # the package resolves
    assert _cfg()["Service"]["WorkingDirectory"] == _CHECKOUT


def test_path_includes_the_users_local_bin():
    path = _env()["PATH"].split(":")
    assert _HOME + "/.local/bin" in path
    assert path[-3:] == ["/usr/local/bin", "/usr/bin", "/bin"]


def test_inert_no_autostart_hack():
    # The unit must not smuggle an enable/start into itself (no ExecStartPre that self-enables,
    # no Alias trickery). Activation is the operator's root install; rollback is `systemctl disable`.
    raw = _text()
    assert "systemctl enable" not in raw
    assert "systemctl start" not in raw
    assert "Alias=" not in raw


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze not installed")
def test_systemd_analyze_accepts_the_rendered_unit(tmp_path):
    """systemd's own parser, on a unit rendered for THIS host (real user, python, checkout)."""
    import pwd
    vals = dict(_VALUES, ORCHESTRA_USER=pwd.getpwuid(os.getuid()).pw_name, ORCHESTRA_ROOT=_ROOT,
                ORCHESTRA_DIR=str(tmp_path / "data"), PYTHON=sys.executable)
    unit = tmp_path / "orchestra-telemetryd.service"
    unit.write_text(IU.render(open(_TEMPLATE).read(), vals))
    r = subprocess.run(["systemd-analyze", "verify", "--man=no", str(unit)],
                       capture_output=True, text=True, timeout=60)
    errors = [l for l in (r.stdout + r.stderr).splitlines()
              if str(unit) in l or "orchestra-telemetryd" in l]
    assert r.returncode == 0 and not errors, r.stdout + r.stderr
