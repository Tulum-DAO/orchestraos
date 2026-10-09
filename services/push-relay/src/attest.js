// App Attest: the relay accepts a device token only from the genuine app.
//
// Attestation (once per app install key), per Apple's "Validating apps that connect to your
// server": the certificate chain ends at the pinned Apple App Attestation Root CA; the leaf's
// nonce extension equals SHA256(authData || clientDataHash); the key id is SHA256 of the leaf's
// public key; authData names this app (rpIdHash), starts at counter 0, carries the aaguid for
// the expected environment, and its credential id is the key id.
//
// Assertion (every later request): the stored public key signed SHA256(authData ||
// clientDataHash), authData names this app, and its counter is above the stored one.

import { decodeCbor } from "./cbor.js";
import { concat, equal, fromB64, sha256, utf8 } from "./bytes.js";
import { children, ecdsaDerToRaw, pemToDer, readTlv, verifyChain } from "./x509.js";

const NONCE_OID = "1.2.840.113635.100.8.2";
const AAGUID = {
  sandbox: utf8("appattestdevelop"),
  production: concat(utf8("appattest"), new Uint8Array(7)),
};

export class AttestError extends Error {}

function fail(msg) {
  throw new AttestError(msg);
}

function parseAuthData(a) {
  if (!(a instanceof Uint8Array) || a.length < 37) fail("authData too short");
  return {
    rpIdHash: a.subarray(0, 32),
    flags: a[32],
    counter: ((a[33] << 24) >>> 0) + (a[34] << 16) + (a[35] << 8) + a[36],
    rest: a.subarray(37),
  };
}

function nonceFromLeaf(leaf) {
  const ext = leaf.extensions.get(NONCE_OID);
  if (!ext) fail("leaf has no nonce extension");
  // SEQUENCE { [1] EXPLICIT OCTET STRING nonce }
  const seq = readTlv(ext);
  if (seq.tag !== 0x30) fail("bad nonce extension");
  const tagged = children(seq).find((c) => c.tag === 0xa1);
  if (!tagged) fail("bad nonce extension");
  const inner = children(tagged)[0];
  if (!inner || inner.tag !== 0x04) fail("bad nonce extension");
  return inner.value;
}

/**
 * @param {object} p
 * @param {string} p.attestationB64  CBOR attestation object from DCAppAttestService.attestKey
 * @param {Uint8Array} p.challenge    the single-use challenge the relay issued
 * @param {string} p.keyIdB64         the app's key id (base64)
 * @param {string} p.appId            "<team id>.<bundle id>"
 * @param {"sandbox"|"production"} p.env
 * @param {Uint8Array} p.rootDer      the pinned Apple App Attestation Root CA
 * @param {number} p.now              ms since epoch
 * @returns {Promise<{publicKeySpki: Uint8Array, counter: number}>}
 */
export async function verifyAttestation({ attestationB64, challenge, keyIdB64, appId, env, rootDer, now }) {
  let att;
  try {
    att = decodeCbor(fromB64(attestationB64));
  } catch (e) {
    fail(`attestation is not CBOR: ${e.message}`);
  }
  if (!(att instanceof Map) || att.get("fmt") !== "apple-appattest") fail("not an App Attest attestation");
  const stmt = att.get("attStmt");
  const authData = att.get("authData");
  if (!(stmt instanceof Map)) fail("missing attStmt");
  const x5c = stmt.get("x5c");
  if (!Array.isArray(x5c) || x5c.length !== 2 || !x5c.every((c) => c instanceof Uint8Array)) {
    fail("x5c must be [leaf, intermediate]");
  }

  let leaf;
  try {
    leaf = await verifyChain(x5c[0], x5c[1], rootDer, now);
  } catch (e) {
    fail(`certificate chain: ${e.message}`);
  }

  const clientDataHash = await sha256(challenge);
  const nonce = await sha256(concat(authData, clientDataHash));
  if (!equal(nonceFromLeaf(leaf), nonce)) fail("nonce does not match the challenge");

  const keyId = fromB64(keyIdB64);
  if (!equal(await sha256(leaf.publicPoint), keyId)) fail("key id is not the attested key");

  const ad = parseAuthData(authData);
  if (!equal(ad.rpIdHash, await sha256(utf8(appId)))) fail("attestation is for another app");
  if (ad.counter !== 0) fail("attestation counter is not 0");
  const expected = AAGUID[env];
  if (!expected) fail("unknown env");
  if (ad.rest.length < 18) fail("authData has no attested credential");
  if (!equal(ad.rest.subarray(0, 16), expected)) fail(`aaguid does not match env ${env}`);
  const credLen = (ad.rest[16] << 8) + ad.rest[17];
  if (!equal(ad.rest.subarray(18, 18 + credLen), keyId)) fail("credential id is not the key id");

  return { publicKeySpki: leaf.spki, counter: 0 };
}

/**
 * @param {object} p
 * @param {string} p.assertionB64     CBOR {signature, authenticatorData} from generateAssertion
 * @param {Uint8Array} p.clientData   the exact bytes the app hashed (the raw request body)
 * @param {Uint8Array} p.publicKeySpki stored at attestation
 * @param {number} p.storedCounter
 * @param {string} p.appId
 * @returns {Promise<{counter: number}>}
 */
export async function verifyAssertion({ assertionB64, clientData, publicKeySpki, storedCounter, appId }) {
  let a;
  try {
    a = decodeCbor(fromB64(assertionB64));
  } catch (e) {
    fail(`assertion is not CBOR: ${e.message}`);
  }
  if (!(a instanceof Map)) fail("assertion is not a map");
  const sig = a.get("signature");
  const authData = a.get("authenticatorData");
  if (!(sig instanceof Uint8Array) || !(authData instanceof Uint8Array)) fail("assertion fields missing");

  const nonce = await sha256(concat(authData, await sha256(clientData)));
  const key = await crypto.subtle.importKey("spki", publicKeySpki, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
  let raw;
  try {
    raw = ecdsaDerToRaw(sig, 32);
  } catch (e) {
    fail(`bad signature encoding: ${e.message}`);
  }
  if (!(await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, raw, nonce))) fail("bad assertion signature");

  const ad = parseAuthData(authData);
  if (!equal(ad.rpIdHash, await sha256(utf8(appId)))) fail("assertion is for another app");
  if (ad.counter <= storedCounter) fail("assertion counter did not increase (replay)");
  return { counter: ad.counter };
}

export function appleRootDer(pem) {
  return pemToDer(pem);
}

