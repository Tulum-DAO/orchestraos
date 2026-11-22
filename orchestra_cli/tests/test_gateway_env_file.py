"""The gateway's arm flags come from <data dir>/config/watch-gateway.env (gm msg_6d923726, 2026-11-22).

The 11-14 cutover relaunched the gateway from the supervisor with no env file: MENU_SUBMIT_ARMED,
MENU_MULTIPART_SUBMIT_ARMED, MENU_MULTIPART_TEXT_ARMED and EXPIRE_PENDING were gone, so single-option
card answers ran dry (the card said answered, the pane kept its menu). The supervisor now hands the
gateway that file's KEY=VALUE lines, and `orchestra doctor` warns when the running gateway lacks them.
"""
import os
from pathlib import Path

import pytest

from orchestra_cli import doctor as D
from orchestra_cli import process_table as PT
from orchestra_cli import settings as S
from orchestra_cli import supervisor as SV

ARMS = ("MENU_SUBMIT_ARMED=1\nMENU_MULTIPART_SUBMIT_ARMED=1\nMENU_MULTIPART_TEXT_ARMED=0\nEXPIRE_PENDING=0\n")


def _settings(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    data = tmp_path / "data"
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{data}"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n')
    return S.load_settings(repo_root=root)


def _env_file(st, text=ARMS):
    p = Path(st.data_dir) / "config" / "watch-gateway.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _gateway(st):
    return {e.name: e for e in PT.build_process_table(st)}["gateway"]


def test_the_gateway_entry_names_the_env_file(tmp_path):
    st = _settings(tmp_path)
    assert _gateway(st).env_file == str(Path(st.data_dir) / "config" / "watch-gateway.env")


def _supervise(st, seen):
    class _P:
        pid = 4242

        def poll(self):
            return None

    def spawn(entry, env, log_path):
        seen[entry.name] = env
        return _P()

    table = [e for e in PT.build_process_table(st) if e.name == "gateway"]
    return SV.Supervisor(table, data_dir=Path(st.data_dir), base_env={"PATH": "/usr/bin"}, spawn=spawn,
                         log=lambda *_a: None)


def test_an_edit_to_the_file_reaches_the_next_start_without_a_new_table(tmp_path):
    """The supervisor builds its table once (`orchestra up`); the file is read at each start, so editing it
    and restarting the gateway is enough."""
    st = _settings(tmp_path)
    seen = {}
    sup = _supervise(st, seen)
    sup.tick()
    assert "MENU_SUBMIT_ARMED" not in seen["gateway"]               # no file yet: code defaults
    _env_file(st)
    child = sup.children[0]
    sup._start(child, 0.0)                                          # the gateway's next start, same table
    assert seen["gateway"]["MENU_SUBMIT_ARMED"] == "1"


def test_the_spawned_gateway_gets_the_arms(tmp_path):
    """By effect through the supervisor: the env the gateway process is started with."""
    st = _settings(tmp_path)
    _env_file(st)
    seen = {}

    class _P:
        pid = 4242

        def poll(self):
            return None

    def spawn(entry, env, log_path):
        seen[entry.name] = env
        return _P()

    table = [e for e in PT.build_process_table(st) if e.name == "gateway"]
    sup = SV.Supervisor(table, data_dir=Path(st.data_dir), base_env={"PATH": "/usr/bin"}, spawn=spawn,
                        log=lambda *_a: None)
    sup.tick()
    assert seen["gateway"]["MENU_SUBMIT_ARMED"] == "1" and seen["gateway"]["EXPIRE_PENDING"] == "0"
    assert seen["gateway"]["PATH"] == "/usr/bin"


def test_no_env_file_leaves_the_code_defaults(tmp_path):
    st = _settings(tmp_path)
    seen = {}
    _supervise(st, seen).tick()
    assert not any(k in seen["gateway"] for k in ("MENU_SUBMIT_ARMED", "EXPIRE_PENDING"))


def test_the_env_file_parser(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("# comment\n\nexport A=1\nB = 'two'\nC=\"3\"\nnot a line\n9BAD=x\nD=\n  E=five  \n")
    assert PT.read_env_file(p) == {"A": "1", "B": "two", "C": "3", "D": "", "E": "five"}
    assert PT.read_env_file(tmp_path / "missing.env") == {}


def test_a_nul_byte_line_is_dropped_and_a_real_spawn_still_starts(tmp_path):
    """codex review: an environment cannot carry NUL; a value with one must never stop the gateway starting."""
    p = tmp_path / "x.env"
    p.write_text("MENU_SUBMIT_ARMED=1\x00\nEXPIRE_PENDING=0\n")
    env = PT.read_env_file(p)
    assert env == {"EXPIRE_PENDING": "0"}
    entry = PT.ProcEntry("probe", "service", ["python3", "-c", "import os; print(os.environ['EXPIRE_PENDING'])"],
                         str(tmp_path), env_file=str(p))
    sup = SV.Supervisor([entry], data_dir=tmp_path, base_env={"PATH": "/usr/bin:/bin"}, log=lambda *_a: None)
    sup.tick()
    proc = sup.children[0].proc
    assert proc.wait(timeout=30) == 0
    assert (tmp_path / "logs" / "probe.log").read_text().strip() == "0"


def test_a_value_too_long_for_the_kernel_still_starts_the_service_without_the_file(tmp_path):
    """codex review round 2: Linux refuses one env string over 128 KiB (E2BIG). The real spawn must fall back to
    the environment without the file, so the service still starts, and say so in the supervisor log."""
    p = tmp_path / "x.env"
    p.write_text("HUGE=" + "x" * 300_000 + "\n")
    entry = PT.ProcEntry("probe", "service", ["python3", "-c", "import os; print('HUGE' in os.environ)"],
                         str(tmp_path), env_file=str(p))
    lines = []
    sup = SV.Supervisor([entry], data_dir=tmp_path, base_env={"PATH": "/usr/bin:/bin"}, log=lines.append)
    sup.tick()
    assert sup.children[0].proc.wait(timeout=30) == 0
    assert (tmp_path / "logs" / "probe.log").read_text().strip() == "False"
    assert any("made the spawn fail" in l for l in lines)


def test_a_spawn_failure_with_no_file_still_raises(tmp_path):
    entry = PT.ProcEntry("probe", "service", ["/nonexistent/binary"], str(tmp_path))

    def spawn(entry, env, log_path):
        raise OSError(2, "No such file")
    sup = SV.Supervisor([entry], data_dir=tmp_path, base_env={}, spawn=spawn, log=lambda *_a: None)
    with pytest.raises(OSError):
        sup._start(sup.children[0], 0.0)


def test_load_env_file_says_why_it_is_empty(tmp_path):
    assert PT.load_env_file(tmp_path / "nope.env")[0] == "absent"
    (tmp_path / "c.env").write_text("# only a comment\nnot a pair\n")
    assert PT.load_env_file(tmp_path / "c.env") == ("empty", {})
    (tmp_path / "b.env").write_bytes(b"\xff\xfe\x00bad")
    assert PT.load_env_file(tmp_path / "b.env")[0] == "unreadable"


# --- doctor -----------------------------------------------------------------------------------

def _probes(st, environ=None, owner=4242):
    files = {}
    if environ is not None:
        files[f"/proc/{owner}/environ"] = "\0".join(f"{k}={v}" for k, v in environ.items()) + "\0"

    def read_file(p):
        p = str(p)
        if p in files:
            return files[p]
        return Path(p).read_text()
    return D.DoctorProbes(which=lambda _c: None, run_cmd=lambda _a: "", port_owner=lambda _p: owner,
                          supervisor_state=lambda _d: None, import_ok=lambda _m: True, read_file=read_file,
                          now_ms=lambda: 0, python_version=(3, 12, 0), git_hooks_path=lambda _r: "")


def test_doctor_warns_when_the_running_gateway_lacks_the_arms(tmp_path):
    st = _settings(tmp_path)
    _env_file(st)
    c = D.gateway_arms_check(st, _probes(st, environ={"PATH": "/usr/bin", "EXPIRE_PENDING": "0"}))
    assert c.status == D.WARN and "MENU_SUBMIT_ARMED" in c.detail and "4242" in c.detail
    assert "restart" in c.remedy


def test_doctor_is_ok_when_the_running_gateway_matches(tmp_path):
    st = _settings(tmp_path)
    _env_file(st)
    env = {"MENU_SUBMIT_ARMED": "1", "MENU_MULTIPART_SUBMIT_ARMED": "1", "MENU_MULTIPART_TEXT_ARMED": "0",
           "EXPIRE_PENDING": "0", "PATH": "/usr/bin"}
    assert D.gateway_arms_check(st, _probes(st, environ=env)).status == D.OK


def test_doctor_warns_on_a_different_value(tmp_path):
    st = _settings(tmp_path)
    _env_file(st)
    env = {"MENU_SUBMIT_ARMED": "1", "MENU_MULTIPART_SUBMIT_ARMED": "1", "MENU_MULTIPART_TEXT_ARMED": "1",
           "EXPIRE_PENDING": "0"}
    c = D.gateway_arms_check(st, _probes(st, environ=env))
    assert c.status == D.WARN and "MENU_MULTIPART_TEXT_ARMED (different value)" in c.detail


def test_doctor_never_prints_values(tmp_path):
    st = _settings(tmp_path)
    _env_file(st, "SOME_TOKEN=s3cr3t-in-file\n")
    c = D.gateway_arms_check(st, _probes(st, environ={"SOME_TOKEN": "s3cr3t-running"}))
    assert c.status == D.WARN and "SOME_TOKEN" in c.detail
    assert "s3cr3t" not in c.detail + c.remedy


def test_doctor_warns_on_an_empty_or_unreadable_file(tmp_path):
    st = _settings(tmp_path)
    _env_file(st, "# nothing\n")
    assert D.gateway_arms_check(st, _probes(st, environ={})).status == D.WARN
    p = _env_file(st)
    p.chmod(0)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("running as a user that reads mode-0 files")
        c = D.gateway_arms_check(st, _probes(st, environ={}))
        assert c.status == D.WARN and "cannot be read" in c.detail
    finally:
        p.chmod(0o600)


def test_doctor_without_the_file_says_code_defaults(tmp_path):
    st = _settings(tmp_path)
    c = D.gateway_arms_check(st, _probes(st, environ={}))
    assert c.status == D.INFO and "watch-gateway.env" in c.detail


def test_doctor_with_no_running_gateway_says_it_cannot_verify(tmp_path):
    st = _settings(tmp_path)
    _env_file(st)
    c = D.gateway_arms_check(st, _probes(st, owner=None))
    assert c.status == D.INFO and "no running gateway to verify" in c.detail


def test_run_doctor_includes_the_row(tmp_path):
    st = _settings(tmp_path)
    _env_file(st)
    names = [c.name for c in D.run_doctor(st, _probes(st, environ={}))]
    assert "gateway:arms" in names
