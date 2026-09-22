"""RED-first: doctor checks run against fake probes (no network, no tmux, no CLIs)."""
import json
from pathlib import Path

from orchestra_cli import doctor as D
from orchestra_cli import settings as S


def _repo(tmp_path: Path, *, config=True, data=True, built=True, registry=None):
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "providers.json").write_text(json.dumps({"providers": [
        {"id": "claude", "label": "Claude", "cli": "claude", "aliases": [],
         "detect": {"cmd": "which claude"},
         "auth_probe": {"kind": "cli-json", "cmd": "claude auth status", "success_key": "loggedIn"}},
        {"id": "codex", "label": "Codex", "cli": "codex", "aliases": [],
         "detect": {"cmd": "which codex"},
         "auth_probe": {"kind": "file-json-key", "path": "~/.codex/auth.json", "key": "tokens"}},
    ]}))
    (root / "scripts").mkdir()
    (root / "scripts" / "watch_gateway.py").write_text("")
    (root / "requirements.txt").write_text("aiohttp\n")
    if built:
        (root / "api" / "dist").mkdir(parents=True)
        (root / "api" / "dist" / "server.js").write_text("")
        (root / "api" / "node_modules").mkdir()
        (root / "dashboard" / "dist").mkdir(parents=True)
        (root / "dashboard" / "dist" / "index.html").write_text("")
        (root / "dashboard" / "node_modules").mkdir()
        (root / "node_modules").mkdir()
    (root / "api").mkdir(exist_ok=True)
    (root / "api" / "package.json").write_text("{}")
    (root / "dashboard").mkdir(exist_ok=True)
    (root / "dashboard" / "package.json").write_text("{}")
    (root / "package.json").write_text("{}")
    data_dir = tmp_path / "data"
    if config:
        (root / "orchestra.toml").write_text(
            f'[data]\ndir = "{data_dir}"\n'
            '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
            '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
            '[notify]\nchannel = "none"\n'
            '[runtimes]\nenabled = ["claude", "codex"]\n'
        )
    if data:
        (data_dir / "state").mkdir(parents=True)
        (data_dir / "logs").mkdir()
        (data_dir / "state" / "watch-gateway-token").write_text("tok")
        (data_dir / "registry.json").write_text(json.dumps(registry or {"agents": {}}))
    return root


def _probes(*, which=("tmux", "node", "npm", "claude"), ports_in_use=None, cmd_out='{"loggedIn": true}',
            supervisor=None, py_modules=("aiohttp",), runtime_files=None):
    ports_in_use = ports_in_use or {}
    runtime_files = runtime_files or {}

    def port_owner(port):
        # returns None (free) or a pid int
        return ports_in_use.get(port)

    return D.DoctorProbes(
        which=lambda n: f"/usr/bin/{n}" if n in which else None,
        run_cmd=lambda argv: cmd_out,
        port_owner=port_owner,
        supervisor_state=lambda data_dir: supervisor,
        import_ok=lambda mod: mod in py_modules,
        read_file=lambda p: runtime_files[str(p)] if str(p) in runtime_files else (_ for _ in ()).throw(FileNotFoundError(p)),
        now_ms=lambda: 0,
        python_version=(3, 12, 3),
        git_hooks_path=lambda root: ".git-hooks",
    )


def _by_name(checks):
    return {c.name: c for c in checks}


def test_all_green_when_everything_present(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = D.run_doctor(st, _probes())
    names = _by_name(checks)
    assert D.exit_code(checks) == 0
    assert names["tmux"].status == "OK"
    assert names["config"].status == "OK"
    assert names["runtime:claude"].status == "OK"
    assert names["port:gateway"].status == "OK"
    assert names["python:aiohttp"].status == "OK"
    assert names["api:build"].status == "OK"
    assert names["dashboard:build"].status == "OK"
    assert names["gateway:token"].status == "OK"
    assert all(c.remedy for c in checks if c.status == "MISSING")


def test_missing_config_lists_keys_and_fails(tmp_path):
    root = _repo(tmp_path, config=False)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = D.run_doctor(st, _probes())
    c = _by_name(checks)["config"]
    assert c.status == "MISSING" and "orchestra init" in c.remedy
    assert D.exit_code(checks) != 0


def test_partial_config_names_missing_keys(tmp_path):
    root = _repo(tmp_path, config=False)
    (root / "orchestra.toml").write_text('[gateway]\nhost = "127.0.0.1"\n[notify]\nchannel = "none"\n')
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    c = _by_name(D.run_doctor(st, _probes()))["config"]
    assert c.status == "MISSING"
    assert "data.dir" in c.detail and "runtimes.enabled" in c.detail


def test_no_authed_runtime_is_missing_with_remedy(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = D.run_doctor(st, _probes(which=("tmux", "node", "npm"), cmd_out=""))
    names = _by_name(checks)
    assert names["runtime:claude"].status == "MISSING"
    assert "install" in names["runtime:claude"].remedy.lower()
    assert names["runtime:any"].status == "MISSING"
    assert D.exit_code(checks) != 0


def test_one_authed_runtime_is_enough_others_are_warn(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = D.run_doctor(st, _probes(which=("tmux", "node", "npm", "claude")))
    names = _by_name(checks)
    assert names["runtime:claude"].status == "OK"
    assert names["runtime:codex"].status == "WARN"      # enabled but absent: optional
    assert names["runtime:any"].status == "OK"
    assert D.exit_code(checks) == 0


def test_installed_but_not_authed_shows_auth_remedy(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = D.run_doctor(st, _probes(cmd_out='{"loggedIn": false}'))
    c = _by_name(checks)["runtime:claude"]
    assert c.status == "MISSING" and "loggedIn=false" in c.detail
    assert "login" in c.remedy.lower() or "auth" in c.remedy.lower()


def test_port_in_use_by_stranger_is_missing_but_own_child_is_ok(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    sup = {"pid": 10, "children": {"gateway": {"pid": 4242, "port": 8890}}}
    checks = D.run_doctor(st, _probes(ports_in_use={8890: 4242, 8891: 999}, supervisor=sup))
    names = _by_name(checks)
    assert names["port:gateway"].status == "OK" and "gateway" in names["port:gateway"].detail
    assert names["port:dashboard"].status == "MISSING" and "999" in names["port:dashboard"].detail
    assert D.exit_code(checks) != 0


def test_tmux_and_node_missing(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    names = _by_name(D.run_doctor(st, _probes(which=("claude",))))
    assert names["tmux"].status == "MISSING" and "apt" in names["tmux"].remedy
    assert names["node"].status == "MISSING"
    assert names["npm"].status == "MISSING"


def test_unbuilt_api_and_dashboard_are_missing(tmp_path):
    root = _repo(tmp_path, built=False)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    names = _by_name(D.run_doctor(st, _probes()))
    assert names["api:build"].status == "MISSING" and "orchestra init" in names["api:build"].remedy
    assert names["dashboard:build"].status == "MISSING"
    assert names["api:node_modules"].status == "MISSING"


def test_python_dep_missing_is_missing_when_required(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    names = _by_name(D.run_doctor(st, _probes(py_modules=())))
    assert names["python:aiohttp"].status == "MISSING" and "requirements.txt" in names["python:aiohttp"].remedy
    assert names["python:flask"].status == "WARN"   # arturo is optional for the minimum path


def test_rotation_beat_report(tmp_path):
    registry = {"agents": {
        "pm-a": {"tier": "T2", "runtime": "claude", "status": "active"},
        "svc": {"tier": "T2", "runtime": "service"},
        "gem": {"tier": "T2", "runtime": "gemini"},
        "old": {"tier": "T2", "runtime": "claude", "status": "retired"},
        "gm": {"tier": "T0", "runtime": "claude"},
    }}
    root = _repo(tmp_path, registry=registry)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    armed = {str(st.runtime_dir / "self_retire_armed"): "pm-a\n# comment\n"}
    names = _by_name(D.run_doctor(st, _probes(runtime_files=armed)))
    beat = names["rotation:beat"]
    assert beat.status == "OK" and "armed" in beat.detail
    seats = names["rotation:seats"]
    assert seats.status == "INFO"
    assert "pm-a" in seats.detail and "old" not in seats.detail and "svc" not in seats.detail
    assert "gem" not in seats.detail


def test_rotation_kill_switch_present_is_warn(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    files = {str(st.runtime_dir / "FLEET_BEAT_DISABLED"): ""}
    names = _by_name(D.run_doctor(st, _probes(runtime_files=files)))
    assert names["rotation:beat"].status == "WARN" and "FLEET_BEAT_DISABLED" in names["rotation:beat"].detail


def test_rotation_beat_disabled_in_config_is_warn(tmp_path):
    root = _repo(tmp_path)
    (root / "orchestra.toml").write_text((root / "orchestra.toml").read_text() + "[rotation]\nbeat_enabled = false\n")
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    names = _by_name(D.run_doctor(st, _probes()))
    assert names["rotation:beat"].status == "WARN"


def test_git_hooks_status_is_warn_only(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    probes = _probes()
    probes.git_hooks_path = lambda root: ""
    names = _by_name(D.run_doctor(st, probes))
    assert names["git:hooks"].status == "WARN" and "core.hooksPath" in names["git:hooks"].remedy
    assert D.exit_code(D.run_doctor(st, probes)) == 0


def test_render_table_and_json(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = D.run_doctor(st, _probes())
    text = D.render_table(checks)
    assert "OK" in text and "runtime:claude" in text
    payload = json.loads(D.render_json(checks))
    assert payload["ok"] is True and any(c["name"] == "tmux" for c in payload["checks"])


def test_doctor_flags_a_missing_better_sqlite3_native_binding(tmp_path):
    """api/dist/server.js opens tasks.db through better-sqlite3, a native addon. An
    `npm ci` that skipped the prebuild leaves node_modules present but no
    better_sqlite3.node; the api then crash-loops under the supervisor with 'Could not
    locate the bindings file' and its recovery path misreads that as a corrupt DB.
    doctor must catch it before `up`."""
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")

    probes = _probes()

    def run_cmd(argv):
        if argv[0] == "node" and "better-sqlite3" in " ".join(argv):
            raise RuntimeError("Could not locate the bindings file")
        return '{"loggedIn": true}'

    probes.run_cmd = run_cmd
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    assert checks["api:better-sqlite3"].status == D.MISSING
    assert "npm rebuild better-sqlite3" in checks["api:better-sqlite3"].remedy
    assert D.exit_code(list(checks.values())) == 1


def test_doctor_probes_api_health_when_the_supervisor_owns_the_api_port(tmp_path):
    """GET /api/health (process up, DB open, bindings loaded) is the by-effect proof that
    the api child is serving, not merely bound. Only probed while our supervisor owns the port."""
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    sup = {"pid": 1, "children": {"api": {"pid": 4242, "status": "running", "port": 8888}}}
    probes = _probes(ports_in_use={8888: 4242}, supervisor=sup)
    probes.http_get = lambda url: '{"status":"ok","db":{"open":true},"bindings":true}' if url.endswith("/api/health") else "{}"
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    assert checks["api:health"].status == D.OK

    def broken(url):
        raise RuntimeError("connection refused")
    probes.http_get = broken
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    assert checks["api:health"].status == D.MISSING


def test_doctor_health_check_absent_when_api_not_running(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = {c.name: c for c in D.run_doctor(st, _probes())}
    assert "api:health" not in checks


def test_doctor_warns_about_tmux_sessions_that_are_not_registered_seats(tmp_path):
    """tmux is host-global; the beat and the dashboard ignore foreign sessions (registry-scoped
    since msg_9f04c5f0), and doctor says which ones it sees so an operator is not surprised."""
    root = _repo(tmp_path, registry={"agents": {"gm": {"tier": "T0"}, "ob": {"tmux_session": "ob-gen44"}}})
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    probes = _probes()
    probes.tmux_sessions = lambda: ["gm", "ob-gen44", "someone-elses-shell", "another"]
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    c = checks["tmux:foreign-sessions"]
    assert c.status == D.WARN and c.required is False
    assert "someone-elses-shell" in c.detail and "another" in c.detail and "gm" not in c.detail

    probes.tmux_sessions = lambda: ["gm", "ob-gen44"]
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    assert checks["tmux:foreign-sessions"].status == D.OK


# --- arturo:brain (track T2) ------------------------------------------------------------

def test_arturo_brain_row_runtime_when_no_key_and_one_authed_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root)
    checks = D.run_doctor(st, _probes(which=("tmux", "node", "npm", "claude")))
    c = _by_name(checks)["arturo:brain"]
    assert c.status == "OK" and "runtime" in c.detail and "claude" in c.detail


def test_arturo_brain_row_api_when_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root)
    checks = D.run_doctor(st, _probes(which=("tmux", "node", "npm", "claude")))
    c = _by_name(checks)["arturo:brain"]
    assert c.status == "OK" and c.detail.startswith("api")


def test_arturo_brain_row_none_is_warn_with_remedy(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root)
    checks = D.run_doctor(st, _probes(which=("tmux", "node", "npm"), cmd_out=""))
    c = _by_name(checks)["arturo:brain"]
    assert c.status == "WARN" and "none" in c.detail and c.remedy


def test_arturo_brain_row_absent_when_arturo_disabled(tmp_path):
    root = _repo(tmp_path)
    (root / "orchestra.toml").write_text((root / "orchestra.toml").read_text() + '\n[arturo]\nenabled = false\n')
    st = S.load_settings(repo_root=root)
    checks = D.run_doctor(st, _probes(which=("tmux", "node", "npm", "claude")))
    assert "arturo:brain" not in _by_name(checks)


def test_plugin_telegram_row_is_info_when_disabled_and_missing_without_token(tmp_path, monkeypatch):
    """Tier 0 item 6: a disabled channel plugin is a clean INFO row, never a failure; enabled
    without its secret is MISSING with the BotFather remedy; enabled + token is OK."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    root = _repo(tmp_path)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))   # the fixture's data dir (no chat-id file)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    names = _by_name(D.run_doctor(st, _probes()))
    assert names["plugin:telegram"].status == "INFO" and D.exit_code(D.run_doctor(st, _probes())) == 0
    st.raw.setdefault("plugins", {})["telegram"] = {"enabled": True}
    names = _by_name(D.run_doctor(st, _probes()))
    assert names["plugin:telegram"].status == "MISSING" and "TELEGRAM_BOT_TOKEN" in names["plugin:telegram"].remedy
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    names = _by_name(D.run_doctor(st, _probes()))
    assert names["plugin:telegram"].status == "OK"


def test_doctor_flags_an_unbuilt_node_pty(tmp_path):
    """The web terminal (dashboard-proxy.js /ws/terminal and api /ws/terminal) needs node-pty,
    a native addon. On a host without the build toolchain the npm install completes with
    node-pty skipped or unbuilt, the app boots green, and every terminal pane answers
    'Web terminal unavailable: node-pty is not installed' (second-install DX report,
    2026-09-20). doctor must catch it as a RED, required row before `up`."""
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")

    probes = _probes()

    def run_cmd(argv):
        if argv[0] == "node" and "node-pty" in " ".join(argv):
            raise RuntimeError("Cannot find module 'node-pty'")
        return '{"loggedIn": true}'

    probes.run_cmd = run_cmd
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    assert checks["terminal:node-pty"].status == D.MISSING
    assert checks["terminal:node-pty"].required is True
    assert "npm rebuild node-pty" in checks["terminal:node-pty"].remedy
    assert "build-essential" in checks["terminal:node-pty"].remedy
    assert D.exit_code(list(checks.values())) == 1


def test_doctor_node_pty_ok_when_the_native_addon_loads(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    checks = {c.name: c for c in D.run_doctor(st, _probes())}
    assert checks["terminal:node-pty"].status == D.OK


def test_doctor_version_row_warns_when_behind_the_latest_tag(tmp_path):
    """Issue #104: a `orchestra:version` row — installed (git describe) vs the newest tag on
    origin; WARN with the `orchestra upgrade` remedy when behind, never REQUIRED."""
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    probes = _probes()

    def run_cmd(argv):
        if "describe" in argv:
            return "v0.1.0\n"
        if "ls-remote" in argv:
            return "abc\trefs/tags/v0.1.0\ndef\trefs/tags/v0.2.0\n"
        return '{"loggedIn": true}'

    probes.run_cmd = run_cmd
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    row = checks["orchestra:version"]
    assert row.status == D.WARN and row.required is False
    assert "v0.1.0" in row.detail and "v0.2.0" in row.detail and "orchestra upgrade" in row.remedy
    assert D.exit_code(list(checks.values())) == 0        # behind is a nudge, not a failure


def test_doctor_version_row_is_info_when_the_remote_cannot_be_asked(tmp_path):
    root = _repo(tmp_path)
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    probes = _probes()

    def run_cmd(argv):
        if "describe" in argv:
            return "v0.1.0\n"
        if "ls-remote" in argv:
            raise RuntimeError("unable to access origin")
        return '{"loggedIn": true}'

    probes.run_cmd = run_cmd
    checks = {c.name: c for c in D.run_doctor(st, probes)}
    assert checks["orchestra:version"].status == D.INFO
