"""OrchestraOS config loader.

Reads orchestra.toml (path from ORCHESTRA_CONFIG env, default ./orchestra.toml
next to the repo root) plus a handful of secret env vars it never reads from
the TOML file itself. Fails loud: a required key that is missing or blank
raises ConfigError at import/first-access time, not a silent fallback to a
guessed path.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only hit on py<3.11
    import tomli as tomllib


class ConfigError(RuntimeError):
    pass


def _config_path() -> Path:
    env_path = os.environ.get("ORCHESTRA_CONFIG")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent.parent / "orchestra.toml"


def _require(table: dict, *keys: str, section: str) -> object:
    cur = table
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            raise ConfigError(
                f"orchestra.toml missing required key '{'.'.join(keys)}' "
                f"in [{section}] — copy orchestra.example.toml to orchestra.toml "
                f"and fill it in, or set ORCHESTRA_CONFIG to point at your copy."
            )
        cur = cur[k]
    if cur == "":
        raise ConfigError(
            f"orchestra.toml key '{'.'.join(keys)}' in [{section}] is set but empty."
        )
    return cur


@dataclass(frozen=True)
class OrchestraConfig:
    data_dir: Path
    gateway_host: str
    gateway_port: int
    dashboard_host: str
    dashboard_port: int
    public_host: str
    notify_channel: str
    telegram_bot_token: str | None
    telegram_chat_id: str
    whatsapp_token: str | None
    whatsapp_phone_id: str
    mac_tailscale_ip: str
    mac_ssh_user: str
    vps_tailscale_ip: str
    remote_auth_host: str
    operator_id: str
    runtimes_enabled: list[str]
    rotation_beat_enabled: bool
    rotation_bus_beat_interval_seconds: int
    rotation_cron_beat_interval_seconds: int
    rotation_boundary_delivery_armed: bool
    rotation_ctx_ceiling_pct: float
    rotation_quota_headroom_pct: float
    rotation_readiness_timeout_seconds: int
    rotation_reap_after_generations: int


@lru_cache(maxsize=1)
def load() -> OrchestraConfig:
    path = _config_path()
    if not path.exists():
        raise ConfigError(
            f"No config found at {path}. Copy orchestra.example.toml to "
            f"orchestra.toml (or set ORCHESTRA_CONFIG) before starting any "
            f"OrchestraOS service."
        )
    with path.open("rb") as f:
        raw = tomllib.load(f)

    notify_channel = str(_require(raw, "notify", "channel", section="notify"))

    telegram_bot_token = None
    telegram_chat_id = ""
    if notify_channel == "telegram":
        token_env = str(
            _require(raw, "notify", "telegram", "bot_token_env", section="notify.telegram")
        )
        telegram_bot_token = os.environ.get(token_env)
        if not telegram_bot_token:
            raise ConfigError(
                f"notify.channel is 'telegram' but env var {token_env} is unset."
            )
        telegram_chat_id = str(
            _require(raw, "notify", "telegram", "chat_id", section="notify.telegram")
        )

    whatsapp_token = None
    whatsapp_phone_id = ""
    if notify_channel == "whatsapp":
        token_env = str(
            _require(raw, "notify", "whatsapp", "token_env", section="notify.whatsapp")
        )
        whatsapp_token = os.environ.get(token_env)
        if not whatsapp_token:
            raise ConfigError(
                f"notify.channel is 'whatsapp' but env var {token_env} is unset."
            )
        whatsapp_phone_id = str(
            _require(raw, "notify", "whatsapp", "phone_id", section="notify.whatsapp")
        )

    rotation = raw.get("rotation", {})

    return OrchestraConfig(
        data_dir=Path(str(_require(raw, "data", "dir", section="data"))).expanduser(),
        gateway_host=str(_require(raw, "gateway", "host", section="gateway")),
        gateway_port=int(_require(raw, "gateway", "port", section="gateway")),
        dashboard_host=str(_require(raw, "dashboard", "host", section="dashboard")),
        dashboard_port=int(_require(raw, "dashboard", "port", section="dashboard")),
        public_host=str(raw.get("public", {}).get("host", "")),
        notify_channel=notify_channel,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        whatsapp_token=whatsapp_token,
        whatsapp_phone_id=whatsapp_phone_id,
        mac_tailscale_ip=str(raw.get("machines", {}).get("mac_tailscale_ip", "")),
        mac_ssh_user=str(raw.get("machines", {}).get("mac_ssh_user", "")),
        vps_tailscale_ip=str(raw.get("machines", {}).get("vps_tailscale_ip", "")),
        remote_auth_host=str(raw.get("machines", {}).get("remote_auth_host", "")),
        operator_id=str(raw.get("operator", {}).get("id", "operator")),
        runtimes_enabled=list(_require(raw, "runtimes", "enabled", section="runtimes")),
        rotation_beat_enabled=bool(rotation.get("beat_enabled", True)),
        rotation_bus_beat_interval_seconds=int(
            rotation.get("bus_beat_interval_seconds", 60)
        ),
        rotation_cron_beat_interval_seconds=int(
            rotation.get("cron_beat_interval_seconds", 900)
        ),
        rotation_boundary_delivery_armed=bool(
            rotation.get("boundary_delivery_armed", True)
        ),
        rotation_ctx_ceiling_pct=float(rotation.get("ctx_ceiling_pct", 0.80)),
        rotation_quota_headroom_pct=float(rotation.get("quota_headroom_pct", 0.15)),
        rotation_readiness_timeout_seconds=int(
            rotation.get("readiness_timeout_seconds", 120)
        ),
        rotation_reap_after_generations=int(
            rotation.get("reap_after_generations", 2)
        ),
    )
