"""RED-first: config loading for the orchestra CLI (hermetic, tmp_path only)."""
import os
from pathlib import Path

from orchestra_cli import settings as S


def _example(root: Path) -> Path:
    ex = root / "orchestra.example.toml"
    ex.write_text(
        '[data]\ndir = "/var/lib/orchestraos"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n'
        '[runtimes]\nenabled = ["claude", "gemini", "codex"]\n'
        '[rotation]\nbeat_enabled = true\nbus_beat_interval_seconds = 60\n'
        'cron_beat_interval_seconds = 900\nboundary_delivery_armed = true\n'
    )
    return ex


def test_no_config_file_gives_tolerant_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ORCHESTRA_CONFIG", raising=False)
    st = S.load_settings(repo_root=tmp_path)
    assert st.config_exists is False
    assert st.data_dir == tmp_path / ".orchestra"
    assert st.gateway_port == 8890
    assert st.api_port == 8888
    assert st.dashboard_port == 8891
    assert st.arturo_port == 5071
    assert st.bus_beat_interval == 60
    assert st.cron_beat_interval == 900
    assert st.boundary_delivery_armed is True
    assert st.beat_enabled is True


def test_missing_required_keys_are_listed(tmp_path):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[gateway]\nhost = "127.0.0.1"\n[notify]\nchannel = "none"\n')
    st = S.load_settings(repo_root=tmp_path, config_path=cfg)
    missing = S.missing_required(st.raw)
    assert "data.dir" in missing
    assert "gateway.port" in missing
    assert "dashboard.host" in missing
    assert "runtimes.enabled" in missing
    assert "gateway.host" not in missing
    assert "notify.channel" not in missing


def test_config_values_are_read(tmp_path):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(
        '[data]\ndir = "~/orch-data"\n'
        '[gateway]\nhost = "0.0.0.0"\nport = 9001\n'
        '[api]\nhost = "127.0.0.1"\nport = 9002\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 9003\n'
        '[arturo]\nenabled = false\nport = 9004\nbrain = "runtime"\nruntime_model = "claude-haiku-4-5-20251001"\n'
        '[notify]\nchannel = "none"\n'
        '[runtimes]\nenabled = ["claude"]\n'
        '[rotation]\nbeat_enabled = false\nbus_beat_interval_seconds = 30\n'
        'cron_beat_interval_seconds = 300\nboundary_delivery_armed = false\n'
        '[router]\nenabled = false\ninterval_seconds = 120\n'
    )
    st = S.load_settings(repo_root=tmp_path, config_path=cfg)
    assert st.config_exists is True
    assert st.data_dir == Path(os.path.expanduser("~/orch-data"))
    assert st.gateway_host == "0.0.0.0" and st.gateway_port == 9001
    assert st.api_port == 9002
    assert st.dashboard_port == 9003
    assert st.arturo_enabled is False and st.arturo_port == 9004
    assert st.arturo_brain == "runtime" and st.arturo_runtime_model == "claude-haiku-4-5-20251001"
    assert st.runtimes_enabled == ["claude"]
    assert st.beat_enabled is False
    assert st.bus_beat_interval == 30 and st.cron_beat_interval == 300
    assert st.boundary_delivery_armed is False
    assert st.router_enabled is False and st.router_interval == 120
    assert S.missing_required(st.raw) == []


def test_child_env_points_every_process_at_config_and_data_dir(tmp_path):
    _example(tmp_path)
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text((tmp_path / "orchestra.example.toml").read_text().replace(
        "/var/lib/orchestraos", str(tmp_path / "data")))
    st = S.load_settings(repo_root=tmp_path, config_path=cfg)
    env = S.child_env(st, base={"PATH": "/usr/bin"})
    assert env["ORCHESTRA_CONFIG"] == str(cfg)
    assert env["ORCHESTRA_DIR"] == str(tmp_path / "data")
    assert env["ORCHESTRA_ROOT"] == str(tmp_path)
    assert env["ORCHESTRA_SCRIPTS_DIR"] == str(tmp_path / "scripts")
    assert env["PYTHONPATH"].split(os.pathsep)[:2] == [str(tmp_path), str(tmp_path / "scripts")]
    assert env["WATCH_GATEWAY_PORT"] == "8890"
    assert env["WATCH_GATEWAY_URL"] == "http://127.0.0.1:8890"
    # #84: the Node voice proxy dials WATCH_GATEWAY_WS_URL; nothing exported it, so a default
    # install dialled ws://127.0.0.1:9091 while the gateway listened on 8890.
    assert env["WATCH_GATEWAY_WS_URL"] == "ws://127.0.0.1:8890"
    assert env["WATCH_GATEWAY_TOKEN_FILE"] == str(tmp_path / "data" / "state" / "watch-gateway-token")
    assert env["PORT"] == "8888" and env["ORCHESTRA_API_PORT"] == "8888"
    assert env["ORCHESTRA_DASHBOARD_PORT"] == "8891"
    assert env["ORCHESTRA_ARTURO_PORT"] == "5071"
    assert env["ORCHESTRA_ARTURO_BRAIN"] == "auto"          # T2: default brain selection
    assert env["ORCHESTRA_RUNTIMES_ENABLED"] == "claude,gemini,codex"
    assert env["BOUNDARY_DELIVER_ARMED"] == "1"
    assert env["ORCH_EVENT_STREAM_DIR"] == str(tmp_path / "data" / "state" / "event-stream")
    assert env["PATH"].startswith("/usr/bin") or ".venv" in env["PATH"]
    # secrets never come from the toml
    assert "ORCHESTRA_TELEGRAM_BOT_TOKEN" not in env


def test_child_env_exports_orch_dir_for_the_gateway_and_agent_status(tmp_path):
    """watch_gateway.py and agent-status.py read the DATA dir from ORCH_DIR (older name),
    not ORCHESTRA_DIR. Without this export, `orchestra up` with [data] dir != checkout
    served an empty/foreign registry to the dashboard (B1: the spawned seat never showed)."""
    st = S.Settings(repo_root=tmp_path / "repo", config_path=tmp_path / "orchestra.toml",
                    config_exists=True, raw={}, data_dir=tmp_path / "data")
    env = S.child_env(st, base={"PATH": "/usr/bin"})
    assert env["ORCH_DIR"] == str(tmp_path / "data")
    assert env["ORCH_DIR"] == env["ORCHESTRA_DIR"]


def test_child_env_scopes_the_realtime_snapshot_dir_to_the_data_dir(tmp_path):
    """telemetryd + the api read/write status.json under <data>/realtime, never a shared home dir."""
    root = tmp_path / "repo"; root.mkdir()
    (root / "orchestra.toml").write_text(f'[data]\ndir = "{tmp_path / "data"}"\n')
    st = S.load_settings(repo_root=root, config_path=root / "orchestra.toml")
    env = S.child_env(st, base={})
    assert env["ORCHESTRA_REALTIME_DIR"] == str(tmp_path / "data" / "realtime")


def test_load_settings_honors_ORCHESTRA_DIR_over_config_data_dir(tmp_path, monkeypatch):
    """rab msg_2ea45964: init/doctor/up must agree on the data dir when the env names one."""
    root = tmp_path / "repo"; root.mkdir()
    (root / "orchestra.toml").write_text(f'[data]\ndir = "{tmp_path / "from-config"}"\n')
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "from-env"))
    st = S.load_settings(root)
    assert st.data_dir == tmp_path / "from-env"
    monkeypatch.delenv("ORCHESTRA_DIR")
    assert S.load_settings(root).data_dir == tmp_path / "from-config"


def test_settings_read_plugins_telegram_enabled(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    (root / "orchestra.toml").write_text('[data]\ndir = "x"\n[plugins.telegram]\nenabled = true\nallowed_chat_ids = [1, 2]\n')
    st = S.load_settings(root)
    assert st.telegram_enabled is True
    assert S.load_settings(root).raw["plugins"]["telegram"]["allowed_chat_ids"] == [1, 2]


def test_rotation_experimental_runtimes_default_empty_and_exported(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    (root / "orchestra.toml").write_text('[data]\ndir = "x"\n')
    st = S.load_settings(root)
    assert st.rotation_experimental_runtimes == []
    assert S.child_env(st, {})["ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES"] == ""
    (root / "orchestra.toml").write_text('[data]\ndir = "x"\n[rotation]\nexperimental_runtimes = ["gemini", "Codex"]\n')
    st = S.load_settings(root)
    assert S.child_env(st, {})["ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES"] == "gemini,codex"


def test_child_env_points_the_gateway_at_the_configured_api_port(tmp_path):
    """A changed [api] port (the docs advise it when 8888 is taken) was ignored by the gateway:
    watch_gateway.py reads ORCH_API_URL, which child_env never set, so /upload and red-alert
    forwarding went to whatever other app owned 8888 (pm doc test, 2026-10-08)."""
    st = S.Settings(repo_root=tmp_path / "repo", config_path=tmp_path / "orchestra.toml",
                    config_exists=True, raw={}, data_dir=tmp_path / "data", api_port=18888)
    env = S.child_env(st, base={"PATH": "/usr/bin"})
    assert env["ORCH_API_URL"] == "http://127.0.0.1:18888"


def test_orch_api_url_is_connectable_when_the_api_listens_on_all_interfaces(tmp_path):
    for bind in ("0.0.0.0", "::", ""):
        st = S.Settings(repo_root=tmp_path / "repo", config_path=tmp_path / "orchestra.toml",
                        config_exists=True, raw={}, data_dir=tmp_path / "data",
                        api_host=bind, api_port=18888)
        assert S.child_env(st, base={})["ORCH_API_URL"] == "http://127.0.0.1:18888", bind


def test_orch_api_url_brackets_an_ipv6_api_host(tmp_path):
    st = S.Settings(repo_root=tmp_path / "repo", config_path=tmp_path / "orchestra.toml",
                    config_exists=True, raw={}, data_dir=tmp_path / "data",
                    api_host="::1", api_port=18888)
    assert S.child_env(st, base={})["ORCH_API_URL"] == "http://[::1]:18888"


def test_child_env_drops_the_installer_sessions_identity_and_keeps_config(tmp_path):
    """An AI agent that runs `orchestra up` (the install playbook) exports its own Claude Code
    session: CLAUDECODE, the session id, a messaging socket + TOKEN. The supervisor must not pass
    them on: if it starts the tmux server, every seat's `claude` thinks it is nested and carries
    that token. Operator configuration with a CLAUDE_/ANTHROPIC_ name must survive."""
    st = S.Settings(repo_root=tmp_path / "repo", config_path=tmp_path / "orchestra.toml",
                    config_exists=True, raw={}, data_dir=tmp_path / "data")
    base = {k: "x" for k in S.INSTALLER_SESSION_ENV}
    keep = {"CLAUDE_CONFIG_DIR": "/c", "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1", "PATH": "/usr/bin"}
    env = S.child_env(st, base={**base, **keep})
    assert not [k for k in S.INSTALLER_SESSION_ENV if k in env]
    for k, v in keep.items():
        assert env[k] == v, k


def test_spawn_agent_unsets_the_same_list():
    """spawn-agent.sh can be run directly (by a seat, or by the installer agent). Its `unset` line
    must name exactly INSTALLER_SESSION_ENV, so the two can never drift."""
    import re
    src = (Path(__file__).resolve().parents[2] / "spawn-agent.sh").read_text()
    lines = [l for l in src.splitlines() if l.startswith("unset CLAUDECODE ")]
    assert len(lines) == 1, "one unset line near the top of spawn-agent.sh"
    assert tuple(lines[0].split()[1:]) == S.INSTALLER_SESSION_ENV
    assert src.index(lines[0]) < src.index("SCRIPT_DIR="), "before anything else runs"


def test_spawn_agent_really_unsets_them(tmp_path):
    """By effect: source only the head of spawn-agent.sh (through the unset) in a shell that has
    the installer's session, then print what is left."""
    import subprocess
    src = (Path(__file__).resolve().parents[2] / "spawn-agent.sh").read_text().splitlines()
    head = "\n".join(src[: next(i for i, l in enumerate(src) if l.startswith("unset CLAUDECODE ")) + 1])
    script = tmp_path / "head.sh"
    script.write_text(head + "\nenv\n")
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_CONFIG_DIR": "/c", **{k: "x" for k in S.INSTALLER_SESSION_ENV}}
    out = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=10).stdout
    names = {l.split("=", 1)[0] for l in out.splitlines() if "=" in l}
    assert not names & set(S.INSTALLER_SESSION_ENV)
    assert "CLAUDE_CONFIG_DIR" in names
