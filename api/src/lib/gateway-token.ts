// #85: one reader for the gateway bearer. `orchestra init` writes it to <data dir>/state/
// watch-gateway-token and `settings.py` exports WATCH_GATEWAY_TOKEN_FILE to every child; the
// legacy ~/.config/jarvis path is only a fallback for installs that predate that.
import { readFileSync } from 'fs';

export function gatewayTokenFile(): string {
  return process.env.WATCH_GATEWAY_TOKEN_FILE || `${process.env.HOME}/.config/jarvis/watch-gateway-token`;
}

export function readGatewayToken(): string {
  try { return readFileSync(gatewayTokenFile(), 'utf-8').trim(); } catch { return ''; }
}
