// Test harness: a D1 stand-in on node:sqlite, a synthetic App Attest chain built with openssl
// (same structure as Apple's: P-384 root and intermediate, P-256 leaf with the nonce
// extension), a minimal CBOR encoder, and request helpers.

import { execFileSync } from "node:child_process";
import { createHash, createHmac, sign as nodeSign } from "node:crypto";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

const HERE = new URL(".", import.meta.url).pathname;

// ------------------------------------------------------------------ D1 stand-in

class Stmt {
  constructor(db, sql, args = []) {
    this.db = db;
    this.sql = sql;
    this.args = args;
  }
  bind(...args) {
    return new Stmt(this.db, this.sql, args);
  }
  async first() {
    return this.db.prepare(this.sql).get(...this.args) ?? null;
  }
  async all() {
    return { results: this.db.prepare(this.sql).all(...this.args) };
  }
  async run() {
    const r = this.db.prepare(this.sql).run(...this.args);
    return { meta: { changes: Number(r.changes) } };
  }
}

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(join(HERE, "..", "schema.sql"), "utf8"));
  return {
    raw: db,
    prepare: (sql) => new Stmt(db, sql),
    batch: async (stmts) => Promise.all(stmts.map((s) => s.run())),
  };
}

// ------------------------------------------------------------------ CBOR (encode)

function head(major, n) {
  if (n < 24) return Buffer.from([(major << 5) | n]);
  if (n < 256) return Buffer.from([(major << 5) | 24, n]);
  if (n < 65536) return Buffer.from([(major << 5) | 25, n >> 8, n & 255]);
  const b = Buffer.alloc(5);
  b[0] = (major << 5) | 26;
  b.writeUInt32BE(n, 1);
  return b;
}

export function cbor(v) {
  if (typeof v === "number") return v >= 0 ? head(0, v) : head(1, -1 - v);
  if (typeof v === "string") {
    const b = Buffer.from(v, "utf8");
    return Buffer.concat([head(3, b.length), b]);
  }
  if (v instanceof Uint8Array) return Buffer.concat([head(2, v.length), Buffer.from(v)]);
  if (Array.isArray(v)) return Buffer.concat([head(4, v.length), ...v.map(cbor)]);
  const entries = v instanceof Map ? [...v.entries()] : Object.entries(v);
  return Buffer.concat([head(5, entries.length), ...entries.flatMap(([k, x]) => [cbor(k), cbor(x)])]);
}

// ------------------------------------------------------------------ synthetic App Attest

const sha256 = (b) => createHash("sha256").update(b).digest();

function openssl(dir, ...args) {
  execFileSync("openssl", args, { cwd: dir, stdio: ["ignore", "ignore", "pipe"] });
}

function der(dir, pem) {
  openssl(dir, "x509", "-in", pem, "-outform", "DER", "-out", pem + ".der");
  return readFileSync(join(dir, pem + ".der"));
}

/** A CA pair (root + intermediate) that tests can trust in place of Apple's. */
export function makeCa() {
  const dir = mkdtempSync(join(tmpdir(), "relay-ca-"));
  writeFileSync(join(dir, "ca.ext"), "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign\n");
  openssl(dir, "ecparam", "-name", "secp384r1", "-genkey", "-noout", "-out", "root.key");
  openssl(dir, "req", "-x509", "-new", "-key", "root.key", "-subj", "/CN=Test App Attestation Root CA", "-days", "3650",
    "-sha384", "-addext", "basicConstraints=critical,CA:TRUE", "-out", "root.pem");
  openssl(dir, "ecparam", "-name", "secp384r1", "-genkey", "-noout", "-out", "int.key");
  openssl(dir, "req", "-new", "-key", "int.key", "-subj", "/CN=Test App Attestation CA 1", "-out", "int.csr");
  openssl(dir, "x509", "-req", "-in", "int.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial",
    "-days", "3650", "-sha384", "-extfile", "ca.ext", "-out", "int.pem");
  return { dir, rootDer: der(dir, "root.pem"), intDer: der(dir, "int.pem") };
}

/**
 * Attest a fresh P-256 key, as DCAppAttestService.attestKey would. Overrides let a test break
 * exactly one property.
 */
export function attest(ca, { appId, env = "sandbox", challenge, counter = 0, nonceFor, keyIdOverride, aaguid } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "relay-leaf-"));
  openssl(dir, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "leaf.key");
  openssl(dir, "pkcs8", "-topk8", "-nocrypt", "-in", "leaf.key", "-out", "leaf.p8");
  openssl(dir, "ec", "-in", "leaf.key", "-pubout", "-outform", "DER", "-out", "leaf.spki");
  const spki = readFileSync(join(dir, "leaf.spki"));
  const point = spki.subarray(spki.length - 65);
  const keyId = keyIdOverride || sha256(point);

  const ag = aaguid || (env === "sandbox" ? Buffer.from("appattestdevelop") : Buffer.concat([Buffer.from("appattest"), Buffer.alloc(7)]));
  const ctr = Buffer.alloc(4);
  ctr.writeUInt32BE(counter);
  const authData = Buffer.concat([sha256(Buffer.from(appId)), Buffer.from([0x40]), ctr, ag,
    Buffer.from([0, keyId.length]), keyId, Buffer.from([0xa0])]);
  const nonce = sha256(Buffer.concat([authData, sha256(nonceFor || challenge)]));
  const ext = Buffer.concat([Buffer.from([0x30, 0x24, 0xa1, 0x22, 0x04, 0x20]), nonce]).toString("hex");
  writeFileSync(join(dir, "leaf.ext"), `1.2.840.113635.100.8.2=DER:${ext}\n`);
  openssl(dir, "req", "-new", "-key", "leaf.key", "-subj", "/CN=leaf", "-out", "leaf.csr");
  writeFileSync(join(dir, "int.pem"), readFileSync(join(ca.dir, "int.pem")));
  writeFileSync(join(dir, "int.key"), readFileSync(join(ca.dir, "int.key")));
  openssl(dir, "x509", "-req", "-in", "leaf.csr", "-CA", "int.pem", "-CAkey", "int.key", "-CAcreateserial",
    "-days", "30", "-sha256", "-extfile", "leaf.ext", "-out", "leaf.pem");
  const leafDer = der(dir, "leaf.pem");

  const attestation = cbor(new Map([
    ["fmt", "apple-appattest"],
    ["attStmt", new Map([["x5c", [leafDer, ca.intDer]], ["receipt", new Uint8Array(4)]])],
    ["authData", authData],
  ]));
  const leafKeyPem = readFileSync(join(dir, "leaf.p8"), "utf8");
  return {
    keyIdB64: keyId.toString("base64"),
    attestationB64: attestation.toString("base64"),
    leafKeyPem,
    appId,
    counter: 0,
  };
}

/** generateAssertion(keyId, SHA256(body)) for an attested key. Bumps the key's counter. */
export function assert(key, bodyBytes, { counter, appId } = {}) {
  key.counter = counter ?? key.counter + 1;
  const ctr = Buffer.alloc(4);
  ctr.writeUInt32BE(key.counter);
  const authData = Buffer.concat([sha256(Buffer.from(appId || key.appId)), Buffer.from([0x40]), ctr]);
  const nonce = sha256(Buffer.concat([authData, sha256(Buffer.from(bodyBytes))]));
  const signature = nodeSign("sha256", nonce, { key: key.leafKeyPem, dsaEncoding: "der" });
  return cbor(new Map([["signature", signature], ["authenticatorData", authData]])).toString("base64");
}

// ------------------------------------------------------------------ APNs key + requests

export function apnsKeyPem() {
  const dir = mkdtempSync(join(tmpdir(), "relay-apns-"));
  openssl(dir, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "k.key");
  openssl(dir, "pkcs8", "-topk8", "-nocrypt", "-in", "k.key", "-out", "k.p8");
  openssl(dir, "ec", "-in", "k.key", "-pubout", "-out", "k.pub");
  return { pem: readFileSync(join(dir, "k.p8"), "utf8"), pub: readFileSync(join(dir, "k.pub"), "utf8") };
}

export function req(method, path, { body, headers = {}, ip = "198.51.100.7" } = {}) {
  const raw = body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body);
  return new Request(`https://relay.test${path}`, {
    method,
    body: raw,
    headers: { "content-type": "application/json", "CF-Connecting-IP": ip, ...headers },
  });
}

export function signPush(secret, ts, rawBody) {
  return createHmac("sha256", secret).update(`${ts}.${rawBody}`).digest("hex");
}
