// APNs provider API over HTTP/2, token-based auth (ES256 JWT from the team's .p8 key).
// A deployed Worker's fetch negotiates HTTP/2 with APNs; local wrangler cannot (workerd #4841),
// so tests inject `fetchImpl`.

import { toB64url, utf8 } from "./bytes.js";
import { pemToDer } from "./x509.js";

const HOSTS = { sandbox: "api.sandbox.push.apple.com", production: "api.push.apple.com" };
// Apple: refresh the provider token no more than every 20 minutes, no less than every 60.
const JWT_TTL_MS = 40 * 60 * 1000;

let cached = null; // { kid, iss, jwt, at }

async function providerToken(keyPem, keyId, teamId, now) {
  if (cached && cached.kid === keyId && cached.iss === teamId && now - cached.at < JWT_TTL_MS) {
    return cached.jwt;
  }
  const key = await crypto.subtle.importKey("pkcs8", pemToDer(keyPem), { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const head = toB64url(utf8(JSON.stringify({ alg: "ES256", kid: keyId })));
  const body = toB64url(utf8(JSON.stringify({ iss: teamId, iat: Math.floor(now / 1000) })));
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, utf8(`${head}.${body}`)));
  cached = { kid: keyId, iss: teamId, jwt: `${head}.${body}.${toB64url(sig)}`, at: now };
  return cached.jwt;
}

export function resetProviderTokenCache() {
  cached = null;
}

/**
 * Send one alert. Returns {status, reason}: status 200 = accepted by APNs; 410 = the token is
 * no longer valid for this topic (drop it); anything else is APNs' own reason string.
 */
export async function sendAlert({ env, token, topic, payload, collapseId, keyPem, keyId, teamId, now = Date.now(), fetchImpl = fetch }) {
  const host = HOSTS[env];
  if (!host) throw new Error("unknown env");
  const jwt = await providerToken(keyPem, keyId, teamId, now);
  const headers = {
    authorization: `bearer ${jwt}`,
    "apns-topic": topic,
    "apns-push-type": "alert",
    "apns-priority": "10",
    "apns-expiration": String(Math.floor(now / 1000) + 24 * 3600),
    "content-type": "application/json",
  };
  if (collapseId) headers["apns-collapse-id"] = collapseId;
  const resp = await fetchImpl(`https://${host}/3/device/${token}`, { method: "POST", headers, body: JSON.stringify(payload) });
  let reason = "";
  if (resp.status !== 200) {
    try {
      reason = (await resp.json()).reason || "";
    } catch {
      reason = "";
    }
  }
  return { status: resp.status, reason };
}
