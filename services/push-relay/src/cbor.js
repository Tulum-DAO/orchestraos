// A strict CBOR decoder for exactly what App Attest sends: maps, arrays, byte and text strings,
// unsigned and negative integers. Anything else (floats, tags, indefinite lengths) is refused,
// and so is trailing data, because an attestation is parsed from untrusted input.

const MAX_DEPTH = 8;

export function decodeCbor(bytes) {
  const r = { b: bytes, o: 0 };
  const v = item(r, 0);
  if (r.o !== bytes.length) throw new Error("cbor: trailing bytes");
  return v;
}

function need(r, n) {
  if (r.o + n > r.b.length) throw new Error("cbor: truncated");
}

function arg(r, info) {
  if (info < 24) return info;
  const n = { 24: 1, 25: 2, 26: 4, 27: 8 }[info];
  if (!n) throw new Error("cbor: unsupported length encoding");
  need(r, n);
  let v = 0;
  for (let i = 0; i < n; i++) v = v * 256 + r.b[r.o + i];
  r.o += n;
  if (!Number.isSafeInteger(v)) throw new Error("cbor: integer too large");
  return v;
}

function item(r, depth) {
  if (depth > MAX_DEPTH) throw new Error("cbor: too deep");
  need(r, 1);
  const head = r.b[r.o++];
  const major = head >> 5;
  const info = head & 31;
  switch (major) {
    case 0:
      return arg(r, info);
    case 1:
      return -1 - arg(r, info);
    case 2: {
      const n = arg(r, info);
      need(r, n);
      const out = r.b.slice(r.o, r.o + n);
      r.o += n;
      return out;
    }
    case 3: {
      const n = arg(r, info);
      need(r, n);
      const out = new TextDecoder("utf-8", { fatal: true }).decode(r.b.subarray(r.o, r.o + n));
      r.o += n;
      return out;
    }
    case 4: {
      const n = arg(r, info);
      if (n > 16) throw new Error("cbor: array too long");
      const out = [];
      for (let i = 0; i < n; i++) out.push(item(r, depth + 1));
      return out;
    }
    case 5: {
      const n = arg(r, info);
      if (n > 16) throw new Error("cbor: map too long");
      const out = new Map();
      for (let i = 0; i < n; i++) {
        const k = item(r, depth + 1);
        if (typeof k !== "string" && typeof k !== "number") throw new Error("cbor: bad map key");
        if (out.has(k)) throw new Error("cbor: duplicate map key");
        out.set(k, item(r, depth + 1));
      }
      return out;
    }
    default:
      throw new Error("cbor: unsupported type");
  }
}
