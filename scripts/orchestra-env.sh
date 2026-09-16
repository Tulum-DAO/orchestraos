#!/usr/bin/env bash
# orchestra-env.sh — export ORCHESTRA_* env vars from orchestra.toml.
#
# Source this (don't execute it) before launching any OrchestraOS process —
# spawn-agent.sh, dashboard-proxy.js, the rotation beat cron entries — so
# standalone scripts that read env vars directly (rather than importing
# services/config.py or api/src/lib/config.ts) still get their config.
#
#   source scripts/orchestra-env.sh
#
# Reads $ORCHESTRA_CONFIG if set, else <repo-root>/orchestra.toml. Silently
# does nothing if that file doesn't exist (the config-loader modules give the
# real "copy orchestra.example.toml" error when a process actually needs a
# missing required key; this exporter is best-effort plumbing for the
# scripts that don't go through those loaders).

_orchestra_env_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_orchestra_env_config="${ORCHESTRA_CONFIG:-$_orchestra_env_root/orchestra.toml}"

[ -f "$_orchestra_env_config" ] || return 0 2>/dev/null || exit 0

_orchestra_env_exports="$(python3 - "$_orchestra_env_config" "$_orchestra_env_root" << 'PYEOF'
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib

path, root = sys.argv[1], sys.argv[2]
with open(path, "rb") as f:
    cfg = tomllib.load(f)

data = cfg.get("data", {})
gateway = cfg.get("gateway", {})
dashboard = cfg.get("dashboard", {})
machines = cfg.get("machines", {})
api = cfg.get("api", {})
arturo = cfg.get("arturo", {})
rotation = cfg.get("rotation", {})
notify = cfg.get("notify", {})

pairs = {
    "ORCHESTRA_DIR": data.get("dir", root),
    "ORCH_DIR": data.get("dir", root),  # older name read by watch_gateway.py / agent-status.py
    "ORCHESTRA_GATEWAY_HOST": gateway.get("host", ""),
    "ORCHESTRA_GATEWAY_PORT": str(gateway.get("port", "")),
    "ORCHESTRA_DASHBOARD_HOST": dashboard.get("host", ""),
    "ORCHESTRA_DASHBOARD_PORT": str(dashboard.get("port", "")),
    "ORCHESTRA_MAC_TAILSCALE_IP": machines.get("mac_tailscale_ip", ""),
    "ORCHESTRA_MAC_SSH_USER": machines.get("mac_ssh_user", ""),
    "ORCHESTRA_VPS_TAILSCALE_IP": machines.get("vps_tailscale_ip", ""),
    "ORCHESTRA_REMOTE_AUTH_HOST": machines.get("remote_auth_host", ""),
    "ORCHESTRA_VPS_HOSTNAME": machines.get("vps_hostname", ""),
    "ORCHESTRA_NOTIFY_CHANNEL": notify.get("channel", "none"),
    "ORCHESTRA_ROOT": root,
    "ORCHESTRA_SCRIPTS_DIR": root + "/scripts",
    "ORCHESTRA_API_HOST": api.get("host", ""),
    "ORCHESTRA_API_PORT": str(api.get("port", "")),
    "ORCHESTRA_ARTURO_PORT": str(arturo.get("port", "")),
    "WATCH_GATEWAY_HOST": gateway.get("host", ""),
    "WATCH_GATEWAY_PORT": str(gateway.get("port", "")),
    "WATCH_GATEWAY_URL": ("http://%s:%s" % (gateway.get("host"), gateway.get("port"))) if gateway.get("port") else "",
    "WATCH_GATEWAY_TOKEN_FILE": (data.get("dir", root) + "/state/watch-gateway-token"),
    "ORCH_RUNTIME_DIR": rotation.get("runtime_dir", ""),
}
if notify.get("channel") == "telegram":
    tg = notify.get("telegram", {})
    pairs["ORCHESTRA_TELEGRAM_CHAT_ID"] = tg.get("chat_id", "")
    # the bot token itself is read from whatever env var bot_token_env names
    # (set that separately, e.g. in a systemd EnvironmentFile) — never sourced
    # from orchestra.toml, which holds no secrets.

for k, v in pairs.items():
    if v == "" or v is None:
        continue
    v = str(v).replace("'", "'\\''")
    print(f"export {k}='{v}'")
PYEOF
)"

if [ -n "$_orchestra_env_exports" ]; then
  eval "$_orchestra_env_exports"
fi
unset _orchestra_env_root _orchestra_env_config _orchestra_env_exports
