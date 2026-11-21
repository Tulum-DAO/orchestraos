"""state/tailscale-users: a fresh install lets its own Tailscale login into the web terminal and the voice call.

Found on the first real client install: no install issues an orchestra_session cookie and nothing wrote
tailscale-users, so dashboard-proxy.js refused everyone ("[ws] refused /ws/terminal: authenticated session
required"). `orchestra init` (and install-client.sh step 9, through `orchestra tailscale-users --seed`) now
write this machine's own login as admin when the file is ABSENT, and doctor warns when nobody is let in.
Every `tailscale` here is a fake on PATH printing a fixture (conftest shadows the real one).
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from orchestra_cli import doctor as D
from orchestra_cli import init_cmd as I
from orchestra_cli import tailscale_users as TU
from orchestra_cli.tests.test_init import Runner, _repo

ROOT = Path(__file__).resolve().parents[2]
LOGIN = "owner@example.com"
STATUS = {"BackendState": "Running",
          "Self": {"DNSName": "box.example.ts.net.", "UserID": 7},
          "User": {"3": {"ID": 3, "LoginName": "someone-else@example.com"},
                   "7": {"ID": 7, "LoginName": "Owner@Example.com", "DisplayName": "Owner"}}}


def fake_tailscale(tmp_path, monkeypatch, body: str):
    """Put a `tailscale` that prints `body` for `status --json` first on PATH."""
    d = tmp_path / "fake-ts"
    d.mkdir(exist_ok=True)
    (d / "status.json").write_text(body)
    exe = d / "tailscale"
    exe.write_text(f'#!/bin/sh\n[ "$1 $2" = "status --json" ] && exec cat "{d / "status.json"}"\nexit 1\n')
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")


def _init(tmp_path):
    root = tmp_path / "repo" if (tmp_path / "repo").exists() else _repo(tmp_path)
    data = tmp_path / "data"
    report = I.run_init(root, data_dir=data, run=Runner(), skip_npm=True, skip_venv=True, skip_build=True)
    return data, {r.step: r for r in report}, report


def _doctor_row(data: Path, env=None):
    probes = D.DoctorProbes(
        which=lambda n: None, run_cmd=lambda argv: json.dumps(STATUS), port_owner=lambda p: None,
        supervisor_state=lambda d: None, import_ok=lambda m: True, read_file=lambda p: Path(p).read_text(),
        now_ms=lambda: 0, python_version=(3, 12, 3), git_hooks_path=lambda r: ".git-hooks",
        env_get=lambda k: (env or {}).get(k))
    return D.tailscale_users_check(probes, data)


# --- absent -> written -------------------------------------------------------------------------------------

def test_absent_file_is_written_with_this_machines_login_as_admin(tmp_path, monkeypatch):
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    data, steps, report = _init(tmp_path)
    path = data / "state" / "tailscale-users"
    assert steps["tailscale-users"].did, steps["tailscale-users"]
    assert LOGIN in steps["tailscale-users"].detail.lower()
    text = path.read_text()
    assert text.splitlines()[-1] == "Owner@Example.com admin"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "tailscale-users" not in [s.step for s in I.failed_steps(report)]
    assert _doctor_row(data).status == D.OK


def test_a_second_init_keeps_what_the_first_wrote(tmp_path, monkeypatch):
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    data, _, _ = _init(tmp_path)
    before = (data / "state" / "tailscale-users").read_bytes()
    fake_tailscale(tmp_path, monkeypatch, json.dumps({**STATUS, "User": {"7": {"LoginName": "new@example.com"}}}))
    _, steps, _ = _init(tmp_path)
    assert not steps["tailscale-users"].did and "present" in steps["tailscale-users"].detail
    assert (data / "state" / "tailscale-users").read_bytes() == before


# --- present -> untouched ----------------------------------------------------------------------------------

@pytest.mark.parametrize("content", [b"alice@example.org member\n# bob left\n", b""], ids=["listed", "empty"])
def test_a_present_file_is_never_touched(tmp_path, monkeypatch, content):
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    path = tmp_path / "data" / "state" / "tailscale-users"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    os.chmod(path, 0o644)
    mtime = path.stat().st_mtime_ns
    _, steps, _ = _init(tmp_path)
    assert not steps["tailscale-users"].did and "present" in steps["tailscale-users"].detail
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == mtime and stat.S_IMODE(path.stat().st_mode) == 0o644


def test_a_present_file_is_kept_without_even_asking_tailscale(tmp_path):
    path = TU.users_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("")
    res = TU.seed(tmp_path, status=lambda: (_ for _ in ()).throw(AssertionError("tailscale was asked")))
    assert not res.did and res.detail.startswith("present")


def test_a_present_empty_file_makes_doctor_warn_with_the_hand_fix(tmp_path, monkeypatch):
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    path = tmp_path / "data" / "state" / "tailscale-users"
    path.parent.mkdir(parents=True)
    path.write_text("# nobody yet\nadmin\n")
    data, _, _ = _init(tmp_path)
    row = _doctor_row(data)
    assert row.status == D.WARN and not row.required
    assert "lists no login" in row.detail
    assert row.remedy == f"echo 'Owner@Example.com admin' >> {path}"


def test_doctor_warns_when_the_file_is_absent_and_names_the_seed_command(tmp_path):
    (tmp_path / "state").mkdir()
    row = _doctor_row(tmp_path)
    assert row.status == D.WARN and "missing" in row.detail
    assert row.remedy.startswith("orchestra tailscale-users --seed")


def test_doctor_is_ok_when_the_env_allowlist_names_a_login(tmp_path):
    row = _doctor_row(tmp_path, env={"ORCHESTRA_TAILSCALE_USERS": "a@example.com, b@example.com member"})
    assert row.status == D.OK and "2 login" in row.detail


def test_the_doctor_row_is_in_the_full_run_and_share_names_it(tmp_path):
    from orchestra_cli import settings as S
    from orchestra_cli.tests.test_doctor import _probes, _repo as _doctor_repo
    root = _doctor_repo(tmp_path)
    st = S.load_settings(root, root / "orchestra.toml")
    rows = {c.name: c for c in D.run_doctor(st, _probes())}
    row = rows["terminal:tailscale-users"]
    assert row.status == D.WARN and row.remedy.startswith("orchestra tailscale-users --seed")
    assert "terminal:tailscale-users WARN" in D.render_share(list(rows.values()), {})


# --- no login -> nothing written + a hint ------------------------------------------------------------------

@pytest.mark.parametrize("body,why", [
    ("not json", "could not be read"),
    (json.dumps({"BackendState": "NeedsLogin", "Self": {"UserID": 0}}), "not signed in"),
    (json.dumps({"BackendState": "Running", "Self": {"UserID": 7}, "User": {}}), "did not say which login"),
    (json.dumps({"BackendState": "Running", "Self": {"UserID": 9}, "User": {"9": {"LoginName": "tagged-devices"}}}),
     "not a person's login"),
], ids=["bad-json", "logged-out", "no-user-entry", "tagged"])
def test_no_login_writes_nothing_and_says_how_to_add_it(tmp_path, monkeypatch, body, why):
    fake_tailscale(tmp_path, monkeypatch, body)
    data, steps, report = _init(tmp_path)
    detail = steps["tailscale-users"].detail
    assert not (data / "state" / "tailscale-users").exists()
    assert why in detail and f"echo 'you@example.com admin' >> {data / 'state' / 'tailscale-users'}" in detail
    assert "\n" not in detail                       # one line
    assert "tailscale-users" not in [s.step for s in I.failed_steps(report)]             # a hint, never an init failure


def test_tailscale_not_installed_writes_nothing_and_says_how_to_add_it(tmp_path, monkeypatch):
    monkeypatch.setattr(TU.shutil, "which", lambda name: None)
    data, steps, report = _init(tmp_path)
    assert not (data / "state" / "tailscale-users").exists()
    assert "tailscale is not installed" in steps["tailscale-users"].detail
    assert "echo 'you@example.com admin' >>" in steps["tailscale-users"].detail
    assert "tailscale-users" not in [s.step for s in I.failed_steps(report)]


def test_tailscale_that_does_not_answer_writes_nothing(tmp_path):
    # conftest's sandbox `tailscale` exits 1 with no output, as a stopped daemon does
    data, steps, _ = _init(tmp_path)
    assert not (data / "state" / "tailscale-users").exists()
    assert "did not answer" in steps["tailscale-users"].detail


# --- round trip: the written file, read by the real readers ------------------------------------------------

def test_the_written_file_is_read_by_the_real_readers(tmp_path, monkeypatch):
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    data, _, _ = _init(tmp_path)
    path = data / "state" / "tailscale-users"
    assert TU.allowed_logins(path.read_text()) == [LOGIN]
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the JS reader cannot run")
    js = subprocess.run([node, "-e", "const fs=require('fs');const {allowedLogins}=require(process.argv[1]);"
                         "process.stdout.write(JSON.stringify(allowedLogins(fs.readFileSync(process.argv[2],'utf8'))))",
                         str(ROOT / "dashboard-tailscale-users.js"), str(path)],
                        capture_output=True, text=True, timeout=30)
    assert js.returncode == 0, js.stderr
    assert json.loads(js.stdout) == [LOGIN]                    # dashboard-proxy.js lets this login in


def test_the_written_file_gives_admin_in_the_api_parser(tmp_path, monkeypatch):
    tsx = ROOT / "api" / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        pytest.skip("api/node_modules is not installed: the TS parser cannot run (CI runs it in api-typecheck)")
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    data, _, _ = _init(tmp_path)
    script = tmp_path / "read.ts"
    script.write_text(
        f"import {{ readFileSync }} from 'fs';\n"
        f"import {{ parseTailscaleUsers, tailscaleRole }} from {json.dumps(str(ROOT / 'api/src/lib/tailscale-identity.ts'))};\n"
        f"const users = parseTailscaleUsers(readFileSync(process.argv[2], 'utf8'));\n"
        f"console.log(JSON.stringify({{ users, role: tailscaleRole({json.dumps(LOGIN)}, users) }}));\n")
    env = dict(os.environ, ORCHESTRA_CONFIG=str(ROOT / "orchestra.example.toml"))
    r = subprocess.run([str(tsx), str(script), str(data / "state" / "tailscale-users")], capture_output=True,
                       text=True, timeout=60, cwd=str(ROOT / "api"), env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out == {"users": [{"login": LOGIN, "role": "admin"}], "role": "admin"}


# --- the command the installer runs ------------------------------------------------------------------------

def test_the_seed_command_writes_once_then_keeps(tmp_path, monkeypatch, capsys):
    from orchestra_cli.__main__ import main
    fake_tailscale(tmp_path, monkeypatch, json.dumps(STATUS))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "no-config.toml"))
    assert main(["tailscale-users", "--seed"]) == 0
    assert "wrote" in capsys.readouterr().out
    assert (tmp_path / "data" / "state" / "tailscale-users").read_text().endswith("Owner@Example.com admin\n")
    assert main(["tailscale-users", "--seed"]) == 0
    assert "present, kept as is" in capsys.readouterr().out
    assert main(["tailscale-users"]) == 0
    assert LOGIN in capsys.readouterr().out
