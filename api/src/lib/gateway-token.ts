// #85: one reader for the gateway bearer. `orchestra init` writes it to <data dir>/state/
// watch-gateway-token and `settings.py` exports WATCH_GATEWAY_TOKEN_FILE to every child; the
// legacy ~/.config/jarvis path is only a fallback for installs that predate that.
import { readFileSync } from 'fs';
import { join } from 'path';
import { loadConfig } from './config.js';

// #85 (fresh install): the order used to be env -> legacy, so an install that never exported
// WATCH_GATEWAY_TOKEN_FILE read a path `orchestra init` does not write and voice was dead out of
// the box. The CONFIGURED location comes first now; the legacy path stays as a last resort for
// installs that predate it, and is only reached if the config cannot be read at all.
export function gatewayTokenFile(): string {
  if (process.env.WATCH_GATEWAY_TOKEN_FILE) return process.env.WATCH_GATEWAY_TOKEN_FILE;
  try {
    return join(loadConfig().dataDir, 'state', 'watch-gateway-token');
  } catch {
    return `${process.env.HOME}/.config/jarvis/watch-gateway-token`;
  }
}

export function readGatewayToken(): string {
  try { return readFileSync(gatewayTokenFile(), 'utf-8').trim(); } catch { return ''; }
}
