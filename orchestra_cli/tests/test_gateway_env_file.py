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


# --- the four properties the public PR promises (added on top of orchestra-builder's branch) ---------

class _FakeProc:
    pid = 4242

    def poll(self):
        return None


def _supervise_logged(st, seen, log):
    def spawn(entry, env, log_path):
        seen[entry.name] = env
        return _FakeProc()
    table = [e for e in PT.build_process_table(st) if e.name == "gateway"]
    return SV.Supervisor(table, data_dir=Path(st.data_dir), base_env={"PATH": "/usr/bin"}, spawn=spawn, log=log)

def test_the_env_file_is_read_and_reaches_the_gateway(tmp_path):
    """An env file is read: every KEY=VALUE line is in the spawned gateway's environment."""
    st = _settings(tmp_path)
    _env_file(st, "MENU_SUBMIT_ARMED=1\nEXPIRE_PENDING=0\n")
    seen = {}
    _supervise(st, seen).tick()
    assert seen["gateway"]["MENU_SUBMIT_ARMED"] == "1"
    assert seen["gateway"]["EXPIRE_PENDING"] == "0"


def test_a_missing_env_file_is_fine_and_warns_about_nothing(tmp_path):
    """No file: the gateway starts with the supervisor's environment and nothing is logged about the file."""
    st = _settings(tmp_path)
    assert not (Path(st.data_dir) / "config" / "watch-gateway.env").exists()
    lines = []
    seen = {}
    _supervise_logged(st, seen, lines.append).tick()
    assert "gateway" in seen
    assert not any("watch-gateway.env" in l for l in lines)


def test_a_malformed_line_is_skipped_with_a_warning_that_names_the_line_not_its_text(tmp_path):
    """A line that is not KEY=VALUE is skipped; the warning says which line, never what it said."""
    p = tmp_path / "x.env"
    p.write_text("MENU_SUBMIT_ARMED=1\nthis is not a pair sk-SECRET-TEXT\n9BAD=hunter2\nEXPIRE_PENDING=0\n")
    warnings = []
    status, env = PT.load_env_file(p, warn=warnings.append)
    assert status == "ok"
    assert env == {"MENU_SUBMIT_ARMED": "1", "EXPIRE_PENDING": "0"}
    assert len(warnings) == 2, warnings
    assert any("line 2" in w for w in warnings) and any("line 3" in w for w in warnings)
    for w in warnings:
        assert "sk-SECRET-TEXT" not in w and "hunter2" not in w and "9BAD" not in w


def test_the_supervisor_logs_the_malformed_line_warning_once_per_start(tmp_path):
    st = _settings(tmp_path)
    _env_file(st, "MENU_SUBMIT_ARMED=1\nnot a pair\n")
    lines = []
    seen = {}
    _supervise_logged(st, seen, lines.append).tick()
    assert seen["gateway"]["MENU_SUBMIT_ARMED"] == "1"
    warned = [l for l in lines if "watch-gateway.env" in l and "line 2" in l]
    assert len(warned) == 1, lines


def test_no_value_from_the_env_file_is_ever_logged(tmp_path):
    """The supervisor log (and the spawn-fallback line) carries key names at most, never a value."""
    st = _settings(tmp_path)
    marker = "value-that-must-never-be-logged-4242"
    _env_file(st, f"MENU_SUBMIT_ARMED=1\nPRIVATE_FLAG={marker}\nbroken line {marker}\n")
    lines = []

    def spawn(entry, env, log_path):
        if "PRIVATE_FLAG" in env:
            raise OSError(7, "Argument list too long")
        return _FakeProc()
    sup = SV.Supervisor(PT.build_process_table(st), data_dir=Path(st.data_dir),
                        base_env={"PATH": "/usr/bin"}, spawn=spawn, log=lines.append)
    sup.tick()
    assert any("made the spawn fail" in l for l in lines)
    assert lines, "the supervisor logged nothing"
    assert not any(marker in l for l in lines), lines


# --- review follow-ups (#599) -------------------------------------------------------------------

def test_a_value_with_a_unicode_or_control_line_separator_is_kept_whole_and_numbers_stay_true(tmp_path):
    """str.splitlines also splits on \x1c-\x1e, \x85, \u2028, \u2029: a value holding one was truncated and the
    remainder reported as a malformed "line 2" that is not line 2 of the file. Lines are split on \n only."""
    p = tmp_path / "x.env"
    p.write_text("A=x\x1cy\nB=p\u2028q\nC=m\x85n\nnot a pair\nD=4\n", encoding="utf-8")
    warnings = []
    status, env = PT.load_env_file(p, warn=warnings.append)
    assert status == "ok"
    assert env == {"A": "x\x1cy", "B": "p\u2028q", "C": "m\x85n", "D": "4"}
    assert len(warnings) == 1 and "line 4" in warnings[0], warnings


def test_doctor_warns_when_the_env_file_is_readable_by_group_or_other(tmp_path):
    st = _settings(tmp_path)
    p = _env_file(st)
    p.chmod(0o644)
    c = D.gateway_env_file_mode_check(st)
    assert c.status == D.WARN and "group/other" in c.detail and "0644" in c.detail
    assert "chmod 0600" in c.remedy
    p.chmod(0o640)
    assert D.gateway_env_file_mode_check(st).status == D.WARN
    p.chmod(0o600)
    assert D.gateway_env_file_mode_check(st).status == D.OK


def test_a_world_readable_env_file_is_still_loaded_by_the_supervisor(tmp_path):
    """The mode row warns; it never refuses the file (a refusal would recreate the dry-run outage)."""
    st = _settings(tmp_path)
    _env_file(st).chmod(0o644)
    seen = {}
    _supervise(st, seen).tick()
    assert seen["gateway"]["MENU_SUBMIT_ARMED"] == "1"


def test_doctor_has_no_mode_row_without_a_file_and_one_with(tmp_path):
    st = _settings(tmp_path)
    assert D.gateway_env_file_mode_check(st) is None
    assert "gateway:arms-file-mode" not in [c.name for c in D.run_doctor(st, _probes(st, environ={}))]
    _env_file(st).chmod(0o600)
    rows = {c.name: c for c in D.run_doctor(st, _probes(st, environ={}))}
    assert rows["gateway:arms-file-mode"].status == D.OK
