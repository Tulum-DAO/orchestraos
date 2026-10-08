/**
 * ws-origin.ts: who may open a WebSocket on the API (its /ws/terminal is a shell into a
 * seat's tmux pane, and seats run with permission prompts skipped).
 *
 * Browsers do NOT apply the same-origin policy to WebSockets, so any page the operator
 * has open could connect. Measured 2026-10-08: Origin https://evil.example -> 101 on
 * :8888/ws/terminal. This is the API twin of dashboard-ws-origin.js at the repo root.
 * BOTH run every case in contract/ws-origin-cases.json, so the two cannot drift.
 *
 * Rules, both required for a browser:
 *  1. SAME ORIGIN: the Origin's host:port exactly equals the address the request was sent
 *     to. X-Forwarded-Host counts only from a loopback peer.
 *  2. A HOST WE SERVE: loopback (any port), the configured dashboard host on its port, a
 *     Tailscale MagicDNS name (*.ts.net), or ORCHESTRA_DASHBOARD_ALLOWED_HOSTS. Rule 1 alone
 *     falls to DNS rebinding (an attacker name resolving to 127.0.0.1).
 * No Origin is allowed: browsers always send one on a WebSocket handshake.
 */

export interface WsOriginInputs {
  origin?: string;
  hostHeader?: string;
  forwardedHost?: string;
  peerAddress?: string;
  port: number;
  dashboardHost?: string;
  extraHosts?: string;
}

export interface WsOriginDecision {
  allow: boolean;
  reason: string;
}

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]']);

function authorityOf(value: string): string | null {
  try {
    const u = new URL(value);
    return u.host ? u.host.toLowerCase() : null;
  } catch {
    return null;
  }
}

function hostOnly(authority: string): string {
  if (authority.startsWith('[')) return authority.slice(0, authority.indexOf(']') + 1);
  const i = authority.lastIndexOf(':');
  return i === -1 ? authority : authority.slice(0, i);
}

function isLoopbackPeer(addr: string): boolean {
  return addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1';
}

export function wsOriginDecision(i: WsOriginInputs): WsOriginDecision {
  if (!i.origin) return { allow: true, reason: 'no-origin' };
  const originAuthority = authorityOf(i.origin);
  if (!originAuthority) return { allow: false, reason: 'malformed-origin' };

  const fwd = isLoopbackPeer(i.peerAddress || '') ? (i.forwardedHost || '').split(',')[0].trim() : '';
  const addressedAs = (fwd || i.hostHeader || '').trim().toLowerCase();
  if (!addressedAs || addressedAs !== originAuthority) return { allow: false, reason: 'cross-origin' };

  const host = hostOnly(addressedAs);
  if (LOOPBACK.has(host)) return { allow: true, reason: 'same-origin-loopback' };
  const hosts = new Set<string>();
  const dh = (i.dashboardHost || '').toLowerCase();
  if (dh && dh !== '0.0.0.0' && dh !== '::') hosts.add(`${dh}:${i.port}`);
  for (const h of (i.extraHosts || '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean)) hosts.add(h);
  if (hosts.has(addressedAs) || hosts.has(host)) return { allow: true, reason: 'same-origin' };
  if (host.endsWith('.ts.net')) return { allow: true, reason: 'same-origin-tailnet' };
  return { allow: false, reason: 'host-not-served' };
}
