import assert from "node:assert/strict";
import { createHash, createPublicKey, verify as nodeVerify } from "node:crypto";
import { before, beforeEach, describe, test } from "node:test";

import { APPLE_APP_ATTESTATION_ROOT_PEM } from "../src/apple-root.js";
import { resetProviderTokenCache } from "../src/apns.js";
import { decodeCbor } from "../src/cbor.js";
import { createRelay, LIMITS } from "../src/index.js";
import { pemToDer } from "../src/x509.js";
import { apnsKeyPem, assert as assertion, attest, cbor, fakeD1, makeCa, req, signPush } from "./helpers.mjs";

const TEAM = "TEAMID1234";
const BUNDLE = "co.example.App";
const WATCH = "co.example.App.watchkitapp";
const APP_ID = `${TEAM}.${BUNDLE}`;
const TOKEN = "ab".repeat(32);

let ca, otherCa, apnsKey;
before(() => {
  ca = makeCa();
  otherCa = makeCa();
  apnsKey = apnsKeyPem();
});

let env, relay, sent, apnsStatus, clock;
beforeEach(() => {
  resetProviderTokenCache();
  sent = [];
  apnsStatus = { status: 200, reason: "" };
  clock = { ms: Date.now() };
  env = { DB: fakeD1(), TEAM_ID: TEAM, BUNDLE_IDS: `${BUNDLE},${WATCH}`, APNS_KEY: apnsKey.pem, APNS_KEY_ID: "KEYID12345" };
  relay = createRelay({
    rootDer: ca.rootDer,
    nowMs: () => clock.ms,
    fetchImpl: async (url, init) => {
      sent.push({ url, init, body: JSON.parse(init.body) });
      return new Response(apnsStatus.status === 200 ? "" : JSON.stringify({ reason: apnsStatus.reason }), { status: apnsStatus.status });
    },
  });
});

const call = (r) => relay.fetch(r, env);
const body = async (r) => r.json();

async function challenge() {
  const r = await call(req("GET", "/v1/challenge"));
  assert.equal(r.status, 200);
  return (await body(r)).challenge;
}

async function register(over = {}, attestOver = {}) {
  const ch = await challenge();
  const key = attest(ca, { appId: APP_ID, challenge: Buffer.from(ch, "base64"), ...attestOver });
  const r = await call(req("POST", "/v1/devices", {
    body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
      env: "sandbox", bundle_id: BUNDLE, platform: "iphone", ...over },
  }));
  return { r, key, ch };
}

async function registered() {
  const { r, key } = await register();
  assert.equal(r.status, 200, JSON.stringify(await r.clone().json()));
  return { handle: (await body(r)).handle, key };
}

async function putDevice(handle, key, payload, opts) {
  const raw = JSON.stringify(payload);
  return call(req("PUT", `/v1/devices/${handle}`, { body: raw, headers: { "X-Assertion": assertion(key, raw, opts) } }));
}

async function install() {
  return body(await call(req("POST", "/v1/installs")));
}

async function bound() {
  const { handle, key } = await registered();
  const inst = await install();
  const r = await putDevice(handle, key, { install_id: inst.install_id });
  assert.equal(r.status, 200);
  return { handle, key, inst };
}

async function pushReq(inst, payload, { ts = Math.floor(clock.ms / 1000), secret = inst.install_secret, sig } = {}) {
  const raw = JSON.stringify(payload);
  return call(req("POST", "/v1/push", {
    body: raw,
    headers: { "X-Install-Timestamp": String(ts), "X-Install-Signature": sig ?? signPush(secret, ts, raw) },
  }));
}

const PUSH = (inst, handle, over = {}) => ({
  install_id: inst.install_id, handle, kind: "approval", count: 1, card_id: "card_123",
  category: "approval.single", collapse: "card:card_123", ...over,
});

// ------------------------------------------------------------------ pinned root

test("the embedded Apple root is the published one (fingerprint pinned)", () => {
  const fp = createHash("sha256").update(pemToDer(APPLE_APP_ATTESTATION_ROOT_PEM)).digest("hex").toUpperCase();
  // Public: the SHA-256 fingerprint Apple publishes for its App Attestation root.
  assert.equal(fp, "1CB9823BA28BA6AD2D33A006941DE2AE4F513EF1D4E831B9F7E0FA7B6242C932"); // pragma: allowlist secret
});

test("the default relay trusts ONLY Apple's root: a synthetic chain is refused", async () => {
  const prod = createRelay({ nowMs: () => clock.ms, fetchImpl: async () => new Response("") });
  const ch = (await (await prod.fetch(req("GET", "/v1/challenge"), env)).json()).challenge;
  const key = attest(ca, { appId: APP_ID, challenge: Buffer.from(ch, "base64") });
  const r = await prod.fetch(req("POST", "/v1/devices", {
    body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
      env: "sandbox", bundle_id: BUNDLE, platform: "iphone" },
  }), { ...env, TEST_ROOT_DER: ca.rootDer });
  assert.equal(r.status, 400);
  assert.match((await r.json()).error, /certificate chain/);
});

// ------------------------------------------------------------------ attestation

describe("registration (App Attest)", () => {
  test("a genuine attestation gets an opaque handle, never the APNs token", async () => {
    const { r } = await register();
    assert.equal(r.status, 200);
    const { handle } = await r.json();
    assert.match(handle, /^[A-Za-z0-9_-]{43}$/);
    assert.ok(!handle.includes(TOKEN));
    const row = env.DB.raw.prepare("SELECT * FROM devices").get();
    assert.equal(row.apns_token, TOKEN);
    assert.equal(row.counter, 0);
  });

  test("the watch attests its own key under its own bundle", async () => {
    const ch = await challenge();
    const key = attest(ca, { appId: `${TEAM}.${WATCH}`, challenge: Buffer.from(ch, "base64") });
    const r = await call(req("POST", "/v1/devices", {
      body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
        env: "sandbox", bundle_id: WATCH, platform: "watch" },
    }));
    assert.equal(r.status, 200);
  });

  test("a challenge is single use", async () => {
    const { r, ch } = await register();
    assert.equal(r.status, 200);
    const key = attest(ca, { appId: APP_ID, challenge: Buffer.from(ch, "base64") });
    const again = await call(req("POST", "/v1/devices", {
      body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
        env: "sandbox", bundle_id: BUNDLE, platform: "iphone" },
    }));
    assert.equal(again.status, 400);
    assert.match((await again.json()).error, /challenge/);
  });

  test("an expired challenge is refused", async () => {
    const ch = await challenge();
    clock.ms += 301 * 1000;
    const key = attest(ca, { appId: APP_ID, challenge: Buffer.from(ch, "base64") });
    const r = await call(req("POST", "/v1/devices", {
      body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
        env: "sandbox", bundle_id: BUNDLE, platform: "iphone" },
    }));
    assert.equal(r.status, 400);
  });

  for (const [name, over, attestOver, why] of [
    ["a chain to another root", {}, { ca: "other" }, /certificate chain/],
    ["an attestation of a different challenge", {}, { nonceFor: Buffer.from("not the challenge") }, /nonce/],
    ["an attestation for another app", { bundle_id: WATCH }, {}, /another app/],
    ["a sandbox key claiming production", { env: "production" }, {}, /aaguid/],
    ["a key id that is not the attested key", {}, { keyIdOverride: Buffer.alloc(32, 7) }, /key id/],
    ["a non-zero counter", {}, { counter: 1 }, /counter/],
  ]) {
    test(`refused: ${name}`, async () => {
      const ch = await challenge();
      const useCa = attestOver.ca === "other" ? otherCa : ca;
      const { ca: _drop, ...rest } = attestOver;
      const key = attest(useCa, { appId: APP_ID, challenge: Buffer.from(ch, "base64"), ...rest });
      const r = await call(req("POST", "/v1/devices", {
        body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
          env: "sandbox", bundle_id: BUNDLE, platform: "iphone", ...over },
      }));
      assert.equal(r.status, 400);
      assert.match((await r.json()).error, why);
      assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM devices").get().n, 0);
    });
  }

  test("garbage attestation is a 400, not a crash", async () => {
    const ch = await challenge();
    const r = await call(req("POST", "/v1/devices", {
      body: { key_id: "AAAA", attestation: Buffer.from("not cbor").toString("base64"), challenge: ch,
        apns_token: TOKEN, env: "sandbox", bundle_id: BUNDLE, platform: "iphone" },
    }));
    assert.equal(r.status, 400);
  });

  test("a bundle id this relay does not serve is refused before any work", async () => {
    const { r } = await register({ bundle_id: "com.someone.else" });
    assert.equal(r.status, 400);
  });

  test("the same key cannot register twice", async () => {
    const { key } = await registered();
    const ch = await challenge();
    const r = await call(req("POST", "/v1/devices", {
      body: { key_id: key.keyIdB64, attestation: key.attestationB64, challenge: ch, apns_token: TOKEN,
        env: "sandbox", bundle_id: BUNDLE, platform: "iphone" },
    }));
    assert.ok([400, 409].includes(r.status));
  });
});

// ------------------------------------------------------------------ assertions

describe("updates and forget (assertion)", () => {
  test("binding to an install needs a valid assertion over the exact body", async () => {
    const { handle, key } = await registered();
    const inst = await install();
    const raw = JSON.stringify({ install_id: inst.install_id });
    const forged = assertion(key, JSON.stringify({ install_id: "someone-else" }));
    const bad = await call(req("PUT", `/v1/devices/${handle}`, { body: raw, headers: { "X-Assertion": forged } }));
    assert.equal(bad.status, 400);
    const ok = await putDevice(handle, key, { install_id: inst.install_id });
    assert.equal(ok.status, 200);
    assert.deepEqual(await ok.json(), { ok: true, bound: true });
  });

  test("a replayed assertion is refused", async () => {
    const { handle, key } = await registered();
    const inst = await install();
    const raw = JSON.stringify({ install_id: inst.install_id });
    const a = assertion(key, raw);
    assert.equal((await call(req("PUT", `/v1/devices/${handle}`, { body: raw, headers: { "X-Assertion": a } }))).status, 200);
    const again = await call(req("PUT", `/v1/devices/${handle}`, { body: raw, headers: { "X-Assertion": a } }));
    assert.equal(again.status, 400);
    assert.match((await again.json()).error, /counter/);
  });

  test("an assertion from another key is refused", async () => {
    const { handle } = await registered();
    const { key: other } = await registered();
    const r = await putDevice(handle, other, { apns_token: "cd".repeat(32) });
    assert.equal(r.status, 400);
  });

  test("no assertion is a 401; an unknown install is a 404", async () => {
    const { handle, key } = await registered();
    assert.equal((await call(req("PUT", `/v1/devices/${handle}`, { body: "{}" }))).status, 401);
    assert.equal((await putDevice(handle, key, { install_id: "nope" })).status, 404);
  });

  test("the token can be refreshed and the device can forget itself", async () => {
    const { handle, key } = await registered();
    assert.equal((await putDevice(handle, key, { apns_token: "CD".repeat(32) })).status, 200);
    assert.equal(env.DB.raw.prepare("SELECT apns_token FROM devices").get().apns_token, "cd".repeat(32));
    const raw = JSON.stringify({ forget: true, at: clock.ms });
    const r = await call(req("DELETE", `/v1/devices/${handle}`, { body: raw, headers: { "X-Assertion": assertion(key, raw) } }));
    assert.deepEqual(await r.json(), { ok: true, forgotten: true });
    assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM devices").get().n, 0);
  });
});

// ------------------------------------------------------------------ push

describe("push (gateway, HMAC-signed)", () => {
  test("a signed push to a device bound to this install reaches APNs with RELAY-written text", async () => {
    const { handle, inst } = await bound();
    const r = await pushReq(inst, PUSH(inst, handle, { count: 3, text: "SECRET CARD TEXT", title: "x" }));
    assert.deepEqual(await r.json(), { ok: true });
    assert.equal(sent.length, 1);
    const { url, init, body: payload } = sent[0];
    assert.equal(url, `https://api.sandbox.push.apple.com/3/device/${TOKEN}`);
    assert.equal(init.headers["apns-topic"], BUNDLE);
    assert.equal(init.headers["apns-collapse-id"], "card:card_123");
    assert.deepEqual(payload, {
      aps: { alert: { title: "OrchestraOS", body: "3 approvals waiting" }, badge: 3, sound: "default",
        category: "approval.single", "thread-id": "approvals" },
      kind: "approval", card_id: "card_123",
    });
    assert.ok(!JSON.stringify(payload).includes("SECRET"));
  });

  test("the provider token is an ES256 JWT for the team and key", async () => {
    const { handle, inst } = await bound();
    await pushReq(inst, PUSH(inst, handle));
    const jwt = sent[0].init.headers.authorization.replace(/^bearer /, "");
    const [h, p, s] = jwt.split(".");
    assert.deepEqual(JSON.parse(Buffer.from(h, "base64url")), { alg: "ES256", kid: "KEYID12345" });
    assert.equal(JSON.parse(Buffer.from(p, "base64url")).iss, TEAM);
    const ok = nodeVerify("sha256", Buffer.from(`${h}.${p}`), { key: createPublicKey(apnsKey.pub), dsaEncoding: "ieee-p1363" },
      Buffer.from(s, "base64url"));
    assert.ok(ok);
  });

  test("unsigned, badly signed, wrong-secret and skewed pushes are refused", async () => {
    const { handle, inst } = await bound();
    const p = PUSH(inst, handle);
    assert.equal((await pushReq(inst, p, { sig: "" })).status, 401);
    assert.equal((await pushReq(inst, p, { sig: "00".repeat(32) })).status, 401);
    const wrongKey = "a-different-install-key";
    assert.equal((await pushReq(inst, p, { secret: wrongKey })).status, 401);
    assert.equal((await pushReq(inst, p, { ts: Math.floor(clock.ms / 1000) - 301 })).status, 401);
    assert.equal(sent.length, 0);
  });

  test("an install cannot push to a device that did not bind itself to it", async () => {
    const { handle } = await bound();
    const stranger = await install();
    const r = await pushReq(stranger, PUSH(stranger, handle));
    assert.equal(r.status, 403);
    assert.equal(sent.length, 0);
  });

  test("an unbound device receives nothing", async () => {
    const { handle } = await registered();
    const inst = await install();
    assert.equal((await pushReq(inst, PUSH(inst, handle))).status, 403);
  });

  for (const [field, value] of [["category", "approval.anything"], ["kind", "chat"], ["count", 0], ["count", 1000],
    ["card_id", "has spaces"], ["collapse", "arbitrary"]]) {
    test(`refused: ${field}=${JSON.stringify(value)}`, async () => {
      const { handle, inst } = await bound();
      assert.equal((await pushReq(inst, PUSH(inst, handle, { [field]: value }))).status, 400);
      assert.equal(sent.length, 0);
    });
  }

  test("APNs 410 deletes the device and tells the gateway it is gone", async () => {
    const { handle, inst } = await bound();
    apnsStatus = { status: 410, reason: "Unregistered" };
    assert.deepEqual(await (await pushReq(inst, PUSH(inst, handle))).json(), { ok: false, gone: true });
    assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM devices").get().n, 0);
  });

  test("other APNs errors are passed back, the device is kept", async () => {
    const { handle, inst } = await bound();
    apnsStatus = { status: 403, reason: "InvalidProviderToken" };
    const r = await pushReq(inst, PUSH(inst, handle));
    assert.equal(r.status, 502);
    assert.deepEqual(await r.json(), { ok: false, apns_status: 403, apns_reason: "InvalidProviderToken" });
    assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM devices").get().n, 1);
  });

  test(`at most ${LIMITS.pushHandle.n} pushes per device per hour`, async () => {
    const { handle, inst } = await bound();
    for (let i = 0; i < LIMITS.pushHandle.n; i++) assert.equal((await pushReq(inst, PUSH(inst, handle))).status, 200);
    assert.equal((await pushReq(inst, PUSH(inst, handle))).status, 429);
    clock.ms += 3600 * 1000;
    assert.equal((await pushReq(inst, PUSH(inst, handle))).status, 200);
  });
});

// ------------------------------------------------------------------ abuse limits + misc

test(`installs are limited to ${LIMITS.install.n} per IP per day`, async () => {
  for (let i = 0; i < LIMITS.install.n; i++) assert.equal((await call(req("POST", "/v1/installs"))).status, 200);
  assert.equal((await call(req("POST", "/v1/installs"))).status, 429);
  assert.equal((await call(req("POST", "/v1/installs", { ip: "203.0.113.9" }))).status, 200);
});

test("an oversized body is refused", async () => {
  const r = await call(req("POST", "/v1/devices", { body: "x".repeat(17 * 1024) }));
  assert.equal(r.status, 413);
});

test("unknown routes are 404 and health is open", async () => {
  assert.equal((await call(req("GET", "/v1/nope"))).status, 404);
  for (const path of ["/health", "/v1/health"]) {
    const r = await call(req("GET", path));
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { ok: true, db: true, config: true, apns_key: true });
  }
});

test("health is 503 and names the part when the relay could not push", async () => {
  const check = async (over, part) => {
    const r = await relay.fetch(req("GET", "/health"), { ...env, ...over });
    assert.equal(r.status, 503);
    const b = await r.json();
    assert.equal(b.ok, false);
    assert.equal(b[part], false);
  };
  await check({ APNS_KEY: undefined }, "apns_key");
  await check({ APNS_KEY: "not-a-pem-key" }, "apns_key");
  await check({ TEAM_ID: "" }, "config");
  await check({ BUNDLE_IDS: " , " }, "config");
  await check({ DB: { prepare: () => ({ first: async () => { throw new Error("d1 down"); } }) } }, "db");
});

test("health answers booleans only (no ids, counts or key material)", async () => {
  const b = await (await call(req("GET", "/health"))).json();
  assert.deepEqual(Object.keys(b).sort(), ["apns_key", "config", "db", "ok"]);
  assert.ok(Object.values(b).every((v) => typeof v === "boolean"));
});

test("the strict CBOR decoder refuses trailing bytes, tags and floats", () => {
  assert.throws(() => decodeCbor(Buffer.concat([cbor(1), Buffer.from([0])])), /trailing/);
  assert.throws(() => decodeCbor(Buffer.from([0xc0, 0x01])), /unsupported/);
  assert.throws(() => decodeCbor(Buffer.from([0xf9, 0x00, 0x00])), /unsupported/);
  assert.deepEqual(decodeCbor(cbor(new Map([["a", 1]]))), new Map([["a", 1]]));
});

test("an expired leaf is refused; a leaf a few minutes in the future is accepted", async () => {
  clock.ms += 31 * 24 * 3600 * 1000; // test leaves are valid 30 days
  const { r } = await register();
  assert.equal(r.status, 400);
  assert.match((await r.json()).error, /not valid now/);
  clock.ms = Date.now() - 60 * 1000;
  assert.equal((await register()).r.status, 200);
});
