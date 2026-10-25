/**
 * The ONE place the API works out where the watch gateway listens (#84 class).
 *
 * Order: WATCH_GATEWAY_URL (WATCH_GATEWAY_WS_URL for the websocket), then orchestra.toml's
 * [gateway] host + port, then the gateway's own built-in default (scripts/watch_gateway.py
 * GATEWAY_PORT, 9091) only when there is no config file at all (an invalid one throws). Route files used to carry their
 * own literal fallbacks, :9091 in some and :8890 in others, so on a fresh install (the template ships
 * [gateway] port = 8890) half of them dialled a port nothing listens on, and any custom port broke
 * the other half. Read per call, so a test or a late env change is honoured.
 */
import { existsSync } from 'fs';
import { loadConfig, configPath } from './config.js';

type Cfg = () => { gatewayHost: string; gatewayPort: number };

/** The gateway's default applies ONLY when there is no config file at all (a bare `node`, a test without
 * ORCHESTRA_CONFIG). A config that exists but is invalid throws, as everywhere else in the API: a broken
 * orchestra.toml must surface, not quietly dial a port the operator never chose. */
function fromConfig(scheme: 'http' | 'ws', cfg: Cfg, hasConfig: () => boolean): string {
  try {
    const c = cfg();
    return `${scheme}://${c.gatewayHost}:${c.gatewayPort}`;
  } catch (e) {
    if (!hasConfig()) return `${scheme}://127.0.0.1:9091`;
    throw e;
  }
}

const configExists = () => existsSync(configPath());

export function gatewayUrl(env: NodeJS.ProcessEnv = process.env, cfg: Cfg = loadConfig,
                           hasConfig: () => boolean = configExists): string {
  return env.WATCH_GATEWAY_URL || fromConfig('http', cfg, hasConfig);
}

export function gatewayWsUrl(env: NodeJS.ProcessEnv = process.env, cfg: Cfg = loadConfig,
                             hasConfig: () => boolean = configExists): string {
  return env.WATCH_GATEWAY_WS_URL || fromConfig('ws', cfg, hasConfig);
}
