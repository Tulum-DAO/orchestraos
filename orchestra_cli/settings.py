"""Tolerant config view for the CLI.

services/config.py is the LOUD loader every service uses (raises on a missing
required key). The CLI needs a tolerant view of the same file — `doctor` must be
able to say WHICH keys are missing, and `init` must run before the file exists —
so this module parses the TOML with defaults and exposes `missing_required()`
with the same required-key list services/config.py enforces.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Mirrors the _require() calls in services/config.py and api/src/lib/config.ts.
REQUIRED_KEYS = (
    ("data", "dir"),
    ("gateway", "host"),
    ("gateway", "port"),
    ("dashboard", "host"),
    ("dashboard", "port"),
    ("notify", "channel"),
    ("runtimes", "enabled"),
)

DEFAULT_DATA_DIR = "~/.orchestra"
DEFAULT_RUNTIME_DIR = "~/runtime"   # the non-synced sentinel dir the beat scripts read


@dataclass
class Settings:
    repo_root: Path
    config_path: Path
    config_exists: bool
    raw: dict = field(default_factory=dict)
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8890
    api_host: str = "127.0.0.1"
    api_port: int = 8888
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8891
    arturo_enabled: bool = True
    arturo_port: int = 5071
    notify_channel: str = "none"
    runtimes_enabled: list = field(default_factory=lambda: ["claude", "gemini", "codex"])
    beat_enabled: bool = True
    bus_beat_interval: int = 60
    cron_beat_interval: int = 900
    boundary_delivery_armed: bool = True
    router_enabled: bool = True
    router_interval: int = 60
    runtime_dir: Path = Path(DEFAULT_RUNTIME_DIR)

    @property
    def token_file(self) -> Path:
        return self.data_dir / "state" / "watch-gateway-token"

    @property
    def venv_dir(self) -> Path:
        return self.repo_root / ".venv"

    def python_bin(self) -> str:
        cand = self.venv_dir / "bin" / "python"
        return str(cand) if cand.exists() else sys.executable or "python3"


def repo_root_from_env() -> Path:
    env = os.environ.get("ORCHESTRA_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def config_path_for(repo_root: Path) -> Path:
    env = os.environ.get("ORCHESTRA_CONFIG")
    return Path(env) if env else repo_root / "orchestra.toml"


def read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def missing_required(raw: dict) -> list[str]:
    out = []
    for keys in REQUIRED_KEYS:
        cur = raw
        ok = True
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if not ok or cur == "" or cur == []:
            out.append(".".join(keys))
    return out


def _get(raw: dict, section: str, key: str, default):
    sec = raw.get(section) or {}
    val = sec.get(key, default) if isinstance(sec, dict) else default
    return default if val in ("", None) else val


def load_settings(repo_root: Path | None = None, config_path: Path | None = None) -> Settings:
    repo_root = Path(repo_root or repo_root_from_env()).resolve()
    config_path = Path(config_path or config_path_for(repo_root))
    raw: dict = {}
    exists = config_path.exists()
    if exists:
        try:
            raw = read_toml(config_path)
        except Exception:  # noqa: BLE001 — doctor reports it; never crash the CLI
            raw = {}
    rot = raw.get("rotation") or {}
    st = Settings(
        repo_root=repo_root,
        config_path=config_path,
        config_exists=exists,
        raw=raw,
        data_dir=Path(os.path.expanduser(str(_get(raw, "data", "dir", DEFAULT_DATA_DIR)))),
        gateway_host=str(_get(raw, "gateway", "host", "127.0.0.1")),
        gateway_port=int(_get(raw, "gateway", "port", 8890)),
        api_host=str(_get(raw, "api", "host", "127.0.0.1")),
        api_port=int(_get(raw, "api", "port", 8888)),
        dashboard_host=str(_get(raw, "dashboard", "host", "127.0.0.1")),
        dashboard_port=int(_get(raw, "dashboard", "port", 8891)),
        arturo_enabled=bool(_get(raw, "arturo", "enabled", True)),
        arturo_port=int(_get(raw, "arturo", "port", 5071)),
        notify_channel=str(_get(raw, "notify", "channel", "none")),
        runtimes_enabled=list(_get(raw, "runtimes", "enabled", ["claude", "gemini", "codex"])),
        beat_enabled=bool(rot.get("beat_enabled", True)),
        bus_beat_interval=int(rot.get("bus_beat_interval_seconds", 60)),
        cron_beat_interval=int(rot.get("cron_beat_interval_seconds", 900)),
        boundary_delivery_armed=bool(rot.get("boundary_delivery_armed", True)),
        router_enabled=bool(_get(raw, "router", "enabled", True)),
        router_interval=int(_get(raw, "router", "interval_seconds", 60)),
        runtime_dir=Path(os.path.expanduser(str(rot.get("runtime_dir", DEFAULT_RUNTIME_DIR)))),
    )
    return st


def child_env(st: Settings, base: dict | None = None) -> dict:
    """The environment every supervised process (and every beat) receives.

    Mirrors scripts/orchestra-env.sh, plus the code-vs-data split: ORCHESTRA_DIR is
    the DATA dir (state/, logs/, registry.json), ORCHESTRA_ROOT is the checkout, and
    PYTHONPATH makes the checkout importable regardless of where the data lives.
    Secrets are never read from the toml (bot tokens etc. come from the caller's env).
    """
    env = dict(os.environ if base is None else base)
    data = str(st.data_dir)
    root = str(st.repo_root)
    py_path = [root, str(st.repo_root / "scripts")]
    if env.get("PYTHONPATH"):
        py_path.append(env["PYTHONPATH"])
    gateway_url = f"http://{st.gateway_host}:{st.gateway_port}"
    env.update({
        "ORCHESTRA_CONFIG": str(st.config_path),
        "ORCHESTRA_DIR": data,
        "ORCHESTRA_ROOT": root,
        "ORCHESTRA_SCRIPTS_DIR": str(st.repo_root / "scripts"),
        "PYTHONPATH": os.pathsep.join(py_path),
        "ORCHESTRA_GATEWAY_HOST": st.gateway_host,
        "ORCHESTRA_GATEWAY_PORT": str(st.gateway_port),
        "WATCH_GATEWAY_HOST": st.gateway_host,
        "WATCH_GATEWAY_PORT": str(st.gateway_port),
        "WATCH_GATEWAY_URL": gateway_url,
        "WATCH_GATEWAY_TOKEN_FILE": str(st.token_file),
        "ORCHESTRA_API_HOST": st.api_host,
        "ORCHESTRA_API_PORT": str(st.api_port),
        "PORT": str(st.api_port),
        "ORCHESTRA_DASHBOARD_HOST": st.dashboard_host,
        "ORCHESTRA_DASHBOARD_PORT": str(st.dashboard_port),
        "ORCHESTRA_ARTURO_PORT": str(st.arturo_port),
        "ORCHESTRA_NOTIFY_CHANNEL": st.notify_channel,
        "ORCH_RUNTIME_DIR": str(st.runtime_dir),
        "ORCH_EVENT_STREAM_DIR": str(st.data_dir / "state" / "event-stream"),
        "ORCH_BUS_CURSOR": str(st.data_dir / "state" / "bus-cursor.json"),
        "ORCH_WAKE_COOLDOWN": str(st.data_dir / "state" / "wake-cooldown.json"),
        "BOUNDARY_DELIVER_ARMED": "1" if (st.beat_enabled and st.boundary_delivery_armed) else "0",
    })
    venv_bin = st.venv_dir / "bin"
    if venv_bin.exists():
        env["PATH"] = str(venv_bin) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(st.venv_dir)
    return env
