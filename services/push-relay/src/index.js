// OrchestraOS push relay: a Cloudflare Worker that lets self-hosted gateways push to the
// published app without holding the app owner's APNs key.
//
//   app     -> relay: GET /v1/challenge, POST /v1/devices (App Attest), PUT/DELETE /v1/devices/:h (assertion)
//   gateway -> relay: POST /v1/installs, POST /v1/push (HMAC-signed)
//   anyone  -> relay: GET /health (or /v1/health): booleans only, 503 when it could not push
//
// The relay writes every alert's text itself from a fixed template. A gateway sends a kind, a
// count and an opaque card id, never words, so the worst a gateway can do is nudge devices that
// chose it. See README.md for the protocol.

import { APPLE_APP_ATTESTATION_ROOT_PEM } from "./apple-root.js";
import { AttestError, verifyAssertion, verifyAttestation } from "./attest.js";
import { sendAlert } from "./apns.js";
import { equal, fromB64, hmacSha256, randomBytes, toB64, toB64url, toHex, utf8 } from "./bytes.js";
import { pemToDer } from "./x509.js";

const MAX_BODY = 16 * 1024;
const CHALLENGE_TTL_S = 300;
const SIGNATURE_SKEW_S = 300;
const PLATFORMS = ["iphone", "ipad", "watch"];
const ENVS = ["sandbox", "production"];
const CATEGORIES = ["approval.single", "approval.multipart", "approval.summary"];
export const LIMITS = {
  challenge: { n: 30, window: 3600 },        // per IP
  register: { n: 20, window: 86400 },        // per IP
  install: { n: 10, window: 86400 },         // per IP
  pushHandle: { n: 30, window: 3600 },       // per device
  pushInstall: { n: 300, window: 3600 },     // per gateway install
};

const APPLE_ROOT_DER = pemToDer(APPLE_APP_ATTESTATION_ROOT_PEM);



class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function readBody(request) {
  const len = Number(request.headers.get("content-length") || 0);
  if (len > MAX_BODY) throw new HttpError(413, "body too large");
  const raw = new Uint8Array(await request.arrayBuffer());
  if (raw.length > MAX_BODY) throw new HttpError(413, "body too large");
  return raw;
}

function parseJson(raw) {
  try {
    const v = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
    if (v && typeof v === "object" && !Array.isArray(v)) return v;
  } catch {
    // fall through
  }
  throw new HttpError(400, "body must be a JSON object");
}

// Request context: the Worker env plus the dependencies only tests replace. They are NOT read
// from env, so no deployed variable can swap the trusted root, the clock or the network.
function context(env, deps) {
  return {
    env,
    DB: env.DB,
    rootDer: deps.rootDer || APPLE_ROOT_DER,
    fetchImpl: deps.fetchImpl || ((...a) => fetch(...a)),
    nowMs: deps.nowMs || (() => Date.now()),
  };
}

function nowS(c) {
  return Math.floor(c.nowMs() / 1000);
}

function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

function bundleIds(env) {
  return String(env.BUNDLE_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
}

async function rateLimit(c, kind, id, now) {
  const { n, window } = LIMITS[kind];
  const start = now - (now % window);
  const k = `${kind}:${id}:${start}`;
  const row = await c.DB.prepare(
    "INSERT INTO counters (k, n, expires_at) VALUES (?1, 1, ?2) " +
      "ON CONFLICT(k) DO UPDATE SET n = n + 1 RETURNING n",
  ).bind(k, start + window).first();
  if (row && row.n > n) throw new HttpError(429, "rate limited");
}

async function sweep(c, now) {
  await c.DB.batch([
    c.DB.prepare("DELETE FROM challenges WHERE expires_at < ?1").bind(now),
    c.DB.prepare("DELETE FROM counters WHERE expires_at < ?1").bind(now),
  ]);
}

// ---------------------------------------------------------------------------- app side

async function issueChallenge(request, c) {
  const now = nowS(c);
  await rateLimit(c, "challenge", clientIp(request), now);
  await sweep(c, now);
  const challenge = toB64(randomBytes(32));
  await c.DB.prepare("INSERT INTO challenges (challenge, expires_at) VALUES (?1, ?2)")
    .bind(challenge, now + CHALLENGE_TTL_S).run();
  return json({ challenge, expires_in: CHALLENGE_TTL_S });
}

async function registerDevice(request, c) {
  const now = nowS(c);
  const body = parseJson(await readBody(request));
  const { key_id, attestation, challenge, apns_token, env: apnsEnv, bundle_id, platform } = body;
  if (typeof key_id !== "string" || typeof attestation !== "string" || typeof challenge !== "string") {
    throw new HttpError(400, "key_id, attestation and challenge are required");
  }
  if (typeof apns_token !== "string" || !/^[0-9a-fA-F]{64,200}$/.test(apns_token)) {
    throw new HttpError(400, "apns_token must be the device token as hex");
  }
  if (!ENVS.includes(apnsEnv)) throw new HttpError(400, "env must be sandbox or production");
  if (!PLATFORMS.includes(platform)) throw new HttpError(400, "platform must be iphone, ipad or watch");
  if (!bundleIds(c.env).includes(bundle_id)) throw new HttpError(400, "bundle_id is not an app this relay serves");
  await rateLimit(c, "register", clientIp(request), now);

  // Single use: the DELETE is the claim.
  const claimed = await c.DB.prepare("DELETE FROM challenges WHERE challenge = ?1 AND expires_at >= ?2 RETURNING challenge")
    .bind(challenge, now).first();
  if (!claimed) throw new HttpError(400, "challenge is unknown, used or expired");

  const { publicKeySpki } = await verifyAttestation({
    attestationB64: attestation,
    challenge: fromB64(challenge),
    keyIdB64: key_id,
    appId: `${c.env.TEAM_ID}.${bundle_id}`,
    env: apnsEnv,
    rootDer: c.rootDer,
    now: now * 1000,
  });

  const handle = toB64url(randomBytes(32));
  try {
    await c.DB.prepare(
      "INSERT INTO devices (handle, key_id, public_key, counter, apns_token, env, bundle_id, platform, created_at, updated_at) " +
        "VALUES (?1, ?2, ?3, 0, ?4, ?5, ?6, ?7, ?8, ?8)",
    ).bind(handle, key_id, toB64(publicKeySpki), apns_token.toLowerCase(), apnsEnv, bundle_id, platform, now).run();
  } catch {
    throw new HttpError(409, "this key is already registered; use PUT with an assertion");
  }
  return json({ handle });
}

async function loadDeviceForAssertion(request, c, handle, raw) {
  const assertion = request.headers.get("X-Assertion");
  if (!assertion) throw new HttpError(401, "X-Assertion is required");
  const dev = await c.DB.prepare("SELECT * FROM devices WHERE handle = ?1").bind(handle).first();
  if (!dev) throw new HttpError(404, "unknown handle");
  const { counter } = await verifyAssertion({
    assertionB64: assertion,
    clientData: raw,
    publicKeySpki: fromB64(dev.public_key),
    storedCounter: dev.counter,
    appId: `${c.env.TEAM_ID}.${dev.bundle_id}`,
  });
  return { dev, counter };
}

async function updateDevice(request, c, handle) {
  const now = nowS(c);
  const raw = await readBody(request);
  const body = parseJson(raw);
  const { dev, counter } = await loadDeviceForAssertion(request, c, handle, raw);
  let token = dev.apns_token;
  let install = dev.install_id;
  if ("apns_token" in body) {
    if (typeof body.apns_token !== "string" || !/^[0-9a-fA-F]{64,200}$/.test(body.apns_token)) {
      throw new HttpError(400, "apns_token must be the device token as hex");
    }
    token = body.apns_token.toLowerCase();
  }
  if ("install_id" in body) {
    if (body.install_id === null) {
      install = null;
    } else {
      const found = await c.DB.prepare("SELECT install_id FROM installs WHERE install_id = ?1").bind(String(body.install_id)).first();
      if (!found) throw new HttpError(404, "unknown install_id");
      install = found.install_id;
    }
  }
  // The counter check and the write are one statement, so two racing requests cannot both win.
  const res = await c.DB.prepare(
    "UPDATE devices SET counter = ?1, apns_token = ?2, install_id = ?3, updated_at = ?4 WHERE handle = ?5 AND counter < ?1",
  ).bind(counter, token, install, now, handle).run();
  if (!res.meta || res.meta.changes !== 1) throw new HttpError(409, "assertion counter did not increase (replay)");
  return json({ ok: true, bound: install !== null });
}

async function forgetDevice(request, c, handle) {
  const raw = await readBody(request);
  const body = parseJson(raw);
  if (body.forget !== true) throw new HttpError(400, 'body must be {"forget": true, "at": <ms>}');
  const { counter } = await loadDeviceForAssertion(request, c, handle, raw);
  const res = await c.DB.prepare("DELETE FROM devices WHERE handle = ?1 AND counter < ?2").bind(handle, counter).run();
  if (!res.meta || res.meta.changes !== 1) throw new HttpError(409, "assertion counter did not increase (replay)");
  return json({ ok: true, forgotten: true });
}

// ---------------------------------------------------------------------------- gateway side

async function createInstall(request, c) {
  const now = nowS(c);
  await rateLimit(c, "install", clientIp(request), now);
  const install_id = toB64url(randomBytes(16));
  const secret = toB64url(randomBytes(32));
  await c.DB.prepare("INSERT INTO installs (install_id, secret, created_at) VALUES (?1, ?2, ?3)")
    .bind(install_id, secret, now).run();
  return json({ install_id, install_secret: secret });
}

export function alertFor(kind, count) {
  const n = Number.isInteger(count) && count > 0 ? count : 1;
  return {
    title: "OrchestraOS",
    body: n === 1 ? "Approval waiting" : `${n} approvals waiting`,
  };
}

async function push(request, c) {
  const now = nowS(c);
  const raw = await readBody(request);
  const ts = Number(request.headers.get("X-Install-Timestamp"));
  const sigHex = request.headers.get("X-Install-Signature") || "";
  if (!Number.isInteger(ts) || Math.abs(now - ts) > SIGNATURE_SKEW_S) throw new HttpError(401, "timestamp missing or skewed");
  const body = parseJson(raw);
  const { install_id, handle, kind, count, card_id, category, collapse } = body;
  if (typeof install_id !== "string" || typeof handle !== "string") throw new HttpError(400, "install_id and handle are required");

  const inst = await c.DB.prepare("SELECT secret FROM installs WHERE install_id = ?1").bind(install_id).first();
  const expected = inst ? await hmacSha256(utf8(inst.secret), concatBody(ts, raw)) : null;
  if (!inst || !equal(utf8(toHex(expected)), utf8(sigHex.toLowerCase()))) throw new HttpError(401, "bad signature");

  if (kind !== "approval") throw new HttpError(400, "kind must be approval");
  if (!CATEGORIES.includes(category)) throw new HttpError(400, "unknown category");
  if (!Number.isInteger(count) || count < 1 || count > 999) throw new HttpError(400, "count must be 1-999");
  if (typeof card_id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(card_id)) throw new HttpError(400, "bad card_id");
  if (collapse !== undefined && (typeof collapse !== "string" || !/^(card:[A-Za-z0-9_-]{1,64}|summary)$/.test(collapse))) {
    throw new HttpError(400, "bad collapse");
  }

  const dev = await c.DB.prepare("SELECT * FROM devices WHERE handle = ?1").bind(handle).first();
  if (!dev || dev.install_id !== install_id) throw new HttpError(403, "this device has not bound itself to this install");
  await rateLimit(c, "pushInstall", install_id, now);
  await rateLimit(c, "pushHandle", handle, now);

  const payload = {
    aps: { alert: alertFor(kind, count), badge: count, sound: "default", category, "thread-id": "approvals" },
    kind,
    card_id,
  };
  const result = await sendAlert({
    env: dev.env,
    token: dev.apns_token,
    topic: dev.bundle_id,
    payload,
    collapseId: collapse,
    keyPem: c.env.APNS_KEY,
    keyId: c.env.APNS_KEY_ID,
    teamId: c.env.TEAM_ID,
    now: now * 1000,
    fetchImpl: c.fetchImpl,
  });
  if (result.status === 200) return json({ ok: true });
  if (result.status === 410 || result.reason === "Unregistered" || result.reason === "BadDeviceToken") {
    await c.DB.prepare("DELETE FROM devices WHERE handle = ?1").bind(handle).run();
    return json({ ok: false, gone: true });
  }
  return json({ ok: false, apns_status: result.status, apns_reason: result.reason }, 502);
}

function concatBody(ts, raw) {
  const head = utf8(`${ts}.`);
  const out = new Uint8Array(head.length + raw.length);
  out.set(head, 0);
  out.set(raw, head.length);
  return out;
}

// ---------------------------------------------------------------------------- health

// Open to anyone, so it answers booleans only: no ids, counts, versions or key material.
// 200 when the relay could actually serve a push: D1 answers, the config is present and the
// APNs key imports as P-256. 503 otherwise, so an uptime check fails on a broken deploy rather
// than on a crash.
async function health(c) {
  let db = false;
  try {
    db = (await c.DB.prepare("SELECT 1 AS one").first())?.one === 1;
  } catch {
    db = false;
  }
  const e = c.env;
  const config = Boolean(e.TEAM_ID && e.APNS_KEY_ID && bundleIds(e).length);
  let apns_key = false;
  try {
    await crypto.subtle.importKey("pkcs8", pemToDer(String(e.APNS_KEY || "")),
      { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
    apns_key = true;
  } catch {
    apns_key = false;
  }
  const ok = db && config && apns_key;
  return json({ ok, db, config, apns_key }, ok ? 200 : 503);
}

// ---------------------------------------------------------------------------- router

export function createRelay(deps = {}) {
  return {
    async fetch(request, env) {
      const c = context(env, deps);
      const path = new URL(request.url).pathname;
      try {
        if (request.method === "GET" && (path === "/health" || path === "/v1/health")) return await health(c);
        if (request.method === "GET" && path === "/v1/challenge") return await issueChallenge(request, c);
        if (request.method === "POST" && path === "/v1/devices") return await registerDevice(request, c);
        const m = /^\/v1\/devices\/([A-Za-z0-9_-]{16,64})$/.exec(path);
        if (m && request.method === "PUT") return await updateDevice(request, c, m[1]);
        if (m && request.method === "DELETE") return await forgetDevice(request, c, m[1]);
        if (request.method === "POST" && path === "/v1/installs") return await createInstall(request, c);
        if (request.method === "POST" && path === "/v1/push") return await push(request, c);
        return json({ error: "not found" }, 404);
      } catch (e) {
        if (e instanceof HttpError) return json({ error: e.message }, e.status);
        if (e instanceof AttestError) return json({ error: `attestation refused: ${e.message}` }, 400);
        return json({ error: "internal error" }, 500);
      }
    },
  };
}

export default createRelay();
