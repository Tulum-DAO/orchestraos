// The smallest X.509 reader that App Attest needs, plus chain verification with WebCrypto.
// It reads only what the checks below use. Anything malformed throws; a throw is a refusal.

import { equal } from "./bytes.js";

const OID = {
  ecPublicKey: "1.2.840.10045.2.1",
  p256: "1.2.840.10045.3.1.7",
  p384: "1.3.132.0.34",
  ecdsaSha256: "1.2.840.10045.4.3.2",
  ecdsaSha384: "1.2.840.10045.4.3.3",
  basicConstraints: "2.5.29.19",
};

const CURVES = {
  [OID.p256]: { name: "P-256", size: 32 },
  [OID.p384]: { name: "P-384", size: 48 },
};
const SIG_HASH = { [OID.ecdsaSha256]: "SHA-256", [OID.ecdsaSha384]: "SHA-384" };

// ---------------------------------------------------------------------------- DER

export function readTlv(b, o = 0) {
  if (o + 2 > b.length) throw new Error("der: truncated header");
  const tag = b[o];
  let len = b[o + 1];
  let hdr = 2;
  if (len & 0x80) {
    const n = len & 0x7f;
    if (n === 0 || n > 3) throw new Error("der: bad length");
    if (o + 2 + n > b.length) throw new Error("der: truncated length");
    len = 0;
    for (let i = 0; i < n; i++) len = len * 256 + b[o + 2 + i];
    hdr += n;
  }
  const start = o + hdr;
  const end = start + len;
  if (end > b.length) throw new Error("der: truncated value");
  return { tag, start, end, raw: b.subarray(o, end), value: b.subarray(start, end) };
}

export function children(tlv) {
  const out = [];
  let o = 0;
  const v = tlv.value;
  while (o < v.length) {
    const c = readTlv(v, o);
    out.push(c);
    o = c.end;
  }
  return out;
}

function expect(tlv, tag, what) {
  if (tlv.tag !== tag) throw new Error(`der: expected ${what}`);
  return tlv;
}

export function oid(tlv) {
  expect(tlv, 0x06, "OID");
  const v = tlv.value;
  if (!v.length) throw new Error("der: empty OID");
  const parts = [Math.floor(v[0] / 40), v[0] % 40];
  let n = 0;
  for (let i = 1; i < v.length; i++) {
    n = n * 128 + (v[i] & 0x7f);
    if (!(v[i] & 0x80)) {
      parts.push(n);
      n = 0;
    }
  }
  return parts.join(".");
}

function time(tlv) {
  const s = new TextDecoder().decode(tlv.value);
  let m;
  if (tlv.tag === 0x17 && (m = /^(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})Z$/.exec(s))) {
    const y = Number(m[1]);
    return Date.UTC(y < 50 ? 2000 + y : 1900 + y, m[2] - 1, m[3], m[4], m[5], m[6]);
  }
  if (tlv.tag === 0x18 && (m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})Z$/.exec(s))) {
    return Date.UTC(Number(m[1]), m[2] - 1, m[3], m[4], m[5], m[6]);
  }
  throw new Error("der: bad time");
}

// ---------------------------------------------------------------------------- certificate

export function parseCert(der) {
  const cert = expect(readTlv(der), 0x30, "Certificate");
  if (cert.end !== der.length) throw new Error("der: trailing bytes after certificate");
  const [tbs, sigAlg, sigVal] = children(cert);
  expect(tbs, 0x30, "TBSCertificate");
  const sigOid = oid(children(expect(sigAlg, 0x30, "AlgorithmIdentifier"))[0]);
  expect(sigVal, 0x03, "signature BIT STRING");
  if (sigVal.value[0] !== 0) throw new Error("der: signature has unused bits");

  const f = children(tbs);
  let i = 0;
  if (f[i].tag === 0xa0) i++; // [0] version
  i++; // serialNumber
  const innerSig = oid(children(expect(f[i++], 0x30, "signature"))[0]);
  if (innerSig !== sigOid) throw new Error("x509: signature algorithm mismatch");
  const issuer = expect(f[i++], 0x30, "issuer").raw;
  const [nb, na] = children(expect(f[i++], 0x30, "validity"));
  const subject = expect(f[i++], 0x30, "subject").raw;
  const spki = expect(f[i++], 0x30, "SubjectPublicKeyInfo");
  const [alg, bits] = children(spki);
  const [algOid, curveOid] = children(alg).map(oid);
  if (algOid !== OID.ecPublicKey || !CURVES[curveOid]) throw new Error("x509: not an EC P-256/P-384 key");
  expect(bits, 0x03, "subjectPublicKey");
  if (bits.value[0] !== 0) throw new Error("der: key has unused bits");

  const extensions = new Map();
  for (; i < f.length; i++) {
    if (f[i].tag !== 0xa3) continue; // [3] extensions
    for (const ext of children(expect(children(f[i])[0], 0x30, "Extensions"))) {
      const parts = children(ext);
      const id = oid(parts[0]);
      const val = expect(parts[parts.length - 1], 0x04, "extnValue");
      if (extensions.has(id)) throw new Error("x509: duplicate extension");
      extensions.set(id, val.value);
    }
  }

  return {
    tbs: tbs.raw,
    sigHash: SIG_HASH[sigOid],
    signature: sigVal.value.subarray(1),
    issuer,
    subject,
    notBefore: time(nb),
    notAfter: time(na),
    spki: spki.raw,
    curve: CURVES[curveOid],
    publicPoint: bits.value.subarray(1),
    extensions,
  };
}

export function isCa(cert) {
  const v = cert.extensions.get(OID.basicConstraints);
  if (!v) return false;
  const seq = children(expect(readTlv(v), 0x30, "BasicConstraints"));
  return seq.length > 0 && seq[0].tag === 0x01 && seq[0].value[0] === 0xff;
}

// DER ECDSA-Sig-Value -> the fixed-width r||s WebCrypto verifies.
export function ecdsaDerToRaw(der, size) {
  const [r, s] = children(expect(readTlv(der), 0x30, "ECDSA signature"));
  const out = new Uint8Array(2 * size);
  for (const [k, n] of [[0, r], [1, s]]) {
    let v = expect(n, 0x02, "INTEGER").value;
    while (v.length > size && v[0] === 0) v = v.subarray(1);
    if (v.length > size) throw new Error("ecdsa: integer too large");
    out.set(v, k * size + (size - v.length));
  }
  return out;
}

export async function importEcKey(cert) {
  return crypto.subtle.importKey("spki", cert.spki, { name: "ECDSA", namedCurve: cert.curve.name }, false, ["verify"]);
}

// Verify that `child` was signed by `parent` and that the names chain.
export async function verifySignedBy(child, parent) {
  if (!equal(child.issuer, parent.subject)) throw new Error("x509: issuer does not match");
  if (!child.sigHash) throw new Error("x509: unsupported signature algorithm");
  const key = await importEcKey(parent);
  const raw = ecdsaDerToRaw(child.signature, parent.curve.size);
  const ok = await crypto.subtle.verify({ name: "ECDSA", hash: child.sigHash }, key, raw, child.tbs);
  if (!ok) throw new Error("x509: bad signature");
}

// A freshly issued App Attest leaf can be stamped a moment ahead of our clock.
export const NOT_BEFORE_SKEW_MS = 5 * 60 * 1000;

// leaf <- intermediate <- root. The root is pinned by the caller (its exact DER bytes), so the
// root's own signature is not what makes it trusted.
export async function verifyChain(leafDer, intermediateDer, rootDer, now) {
  const leaf = parseCert(leafDer);
  const inter = parseCert(intermediateDer);
  const root = parseCert(rootDer);
  for (const c of [leaf, inter, root]) {
    if (now + NOT_BEFORE_SKEW_MS < c.notBefore || now > c.notAfter) throw new Error("x509: certificate not valid now");
  }
  if (!isCa(inter)) throw new Error("x509: intermediate is not a CA");
  await verifySignedBy(inter, root);
  await verifySignedBy(leaf, inter);
  return leaf;
}

export function pemToDer(pem) {
  const b64 = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}
