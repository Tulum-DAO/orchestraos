/**
 * The ONE place the API works out where the watch gateway listens (#84 class).
 *
 * Order: WATCH_GATEWAY_URL (WATCH_GATEWAY_WS_URL for the websocket), then orchestra.toml's
 * [gateway] host + port, then the gateway's own built-in default (scripts/watch_gateway.py
 * GATEWAY_PORT, 9091) only when the config cannot be read at all. Route files used to carry their
 * own literal fallbacks, :9091 in some and :8890 in others, so on a fresh install (the template ships
 * [gateway] port = 8890) half of them dialled a port nothing listens on, and any custom port broke
 * the other half. Read per call, so a test or a late env change is honoured.
 */
import { loadConfig } from './config.js';

type Cfg = () => { gatewayHost: string; gatewayPort: number };

function fromConfig(scheme: 'http' | 'ws', cfg: Cfg): string {
  try {
    const c = cfg();
    return `${scheme}://${c.gatewayHost}:${c.gatewayPort}`;
  } catch {
    return `${scheme}://127.0.0.1:9091`;
  }
}

export function gatewayUrl(env: NodeJS.ProcessEnv = process.env, cfg: Cfg = loadConfig): string {
  return env.WATCH_GATEWAY_URL || fromConfig('http', cfg);
}

export function gatewayWsUrl(env: NodeJS.ProcessEnv = process.env, cfg: Cfg = loadConfig): string {
  return env.WATCH_GATEWAY_WS_URL || fromConfig('ws', cfg);
}
