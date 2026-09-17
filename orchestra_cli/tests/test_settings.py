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
