// dashboard-ws-origin.js: who may open a WebSocket on the dashboard (the web terminal).
//
// The web terminal attaches a browser to a seat's tmux pane, and seats run their CLI with
// permission prompts skipped. So a WebSocket here is, in effect, a shell. Browsers do NOT
// apply the same-origin policy to WebSockets: any page the operator has open can try
// `new WebSocket("ws://127.0.0.1:8891/ws/terminal?session=gm")`, and before this check
// the server accepted it (measured 2026-10-08: Origin https://evil.example -> 101).
//
// Two rules, both required for a browser:
//  1. SAME ORIGIN: the Origin's authority (host:port) equals the address the request was
//     sent to. Exact match; a suffix test would let evil-<host> pass for <host>.
//  2. A HOST WE SERVE: that address is one the dashboard is meant to be reached at. Rule 1
//     alone falls to DNS rebinding: an attacker's name that resolves to 127.0.0.1 makes
//     Origin and Host agree. So the host must be loopback (on our port), the configured
//     [dashboard] host, a Tailscale MagicDNS name (*.ts.net; tailnet names cannot be made
//     to resolve to the victim's loopback), or listed in ORCHESTRA_DASHBOARD_ALLOWED_HOSTS.
//
// No Origin at all is allowed: browsers always send one on a WebSocket handshake, so its
// absence means a non-browser client (curl, a script on the box), which this check is not
// meant to stop. X-Forwarded-Host is trusted only from a loopback peer (tailscale serve,
// a local reverse proxy); any client can send the header.

"use strict";

function authorityOf(value) {
  try {
    const u = new URL(value);
    return u.host ? u.host.toLowerCase() : null;
  } catch (e) {
    return null;
  }
}

function hostOnly(authority) {
  // "[::1]:8891" -> "[::1]", "name.ts.net:8445" -> "name.ts.net", "127.0.0.1" -> "127.0.0.1"
  if (authority.startsWith("[")) return authority.slice(0, authority.indexOf("]") + 1);
  const i = authority.lastIndexOf(":");
  return i === -1 ? authority : authority.slice(0, i);
}

function allowedHosts(port, dashboardHost, extra) {
  const set = new Set();
  for (const h of ["127.0.0.1", "localhost", "[::1]"]) set.add(`${h}:${port}`);
  if (dashboardHost && dashboardHost !== "0.0.0.0" && dashboardHost !== "::") {
    set.add(`${dashboardHost.toLowerCase()}:${port}`);
  }
  for (const h of (extra || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)) set.add(h);
  return set;
}

function isLoopbackPeer(addr) {
  return addr === "127.0.0.1" || addr === "::1" || addr === "::ffff:127.0.0.1";
}

/**
 * Decide one handshake. Pure: the caller reads the socket and env.
 * @returns {{allow: boolean, reason: string}}
 */
function wsOriginDecision({ origin, hostHeader, forwardedHost, peerAddress, port, dashboardHost, extraHosts }) {
  if (!origin) return { allow: true, reason: "no-origin" };
  const originAuthority = authorityOf(origin);
  if (!originAuthority) return { allow: false, reason: "malformed-origin" };

  const fwd = isLoopbackPeer(peerAddress || "") ? (forwardedHost || "").split(",")[0].trim() : "";
  const addressedAs = (fwd || hostHeader || "").trim().toLowerCase();
  if (!addressedAs || addressedAs !== originAuthority) return { allow: false, reason: "cross-origin" };

  const hosts = allowedHosts(port, dashboardHost, extraHosts);
  if (hosts.has(addressedAs) || hosts.has(hostOnly(addressedAs))) return { allow: true, reason: "same-origin" };
  if (hostOnly(addressedAs).endsWith(".ts.net")) return { allow: true, reason: "same-origin-tailnet" };
  return { allow: false, reason: "host-not-served" };
}

module.exports = { wsOriginDecision };
