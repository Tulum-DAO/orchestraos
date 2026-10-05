// One reader for every HMAC secret the API signs or verifies with.
//
// It exists because the old shape was `process.env.X || '<literal>'`, and that literal shipped in
// a PUBLIC repo. A fallback secret in public source is not a default, it is a published key:
// anyone could mint `{u, r:'admin'}` and be trusted. The env var was never set in the live
// process, so the published key WAS the key.
//
// Resolution order, and never a literal:
//   1. the env var itself                       (deployments, tests)
//   2. the <VAR>_FILE path, if set              (lets the live fleet point at the same file the
//                                                session-signing proxy reads, so cookies verify)
//   3. <data dir>/state/<name>                  (what a normal install uses)
//   4. a random 32 bytes for THIS process only  (fail-closed: signatures from anyone else, and
//                                                from our own earlier processes, stop verifying)
//
// Step 4 is deliberately not an exception. A verifier that throws on boot takes the whole API
// down over a cookie path most installs never touch; a verifier with a key nobody else knows
// simply rejects everything it cannot prove, which is the behaviour we actually want. It warns
// once per secret so the operator is told rather than left guessing.
import { readFileSync } from 'fs';
import { join } from 'path';
import { randomBytes } from 'crypto';
import { loadConfig } from './config.js';

const ephemeral = new Map<string, string>();
const warned = new Set<string>();

function stateFile(name: string): string | null {
  try { return join(loadConfig().dataDir, 'state', name); } catch { return null; }
}

/** `envVar` is read fresh on every call, NOT captured at module load: a module-level
 *  `const` is what forced the test suite to depend on the published fallback, because
 *  an import is hoisted above any `process.env` assignment a test makes. */
export function loadSharedSecret(envVar: string, name: string): string {
  const direct = process.env[envVar];
  if (direct) return direct;

  const fromFile = process.env[`${envVar}_FILE`] || stateFile(name);
  if (fromFile) {
    try {
      const v = readFileSync(fromFile, 'utf-8').trim();
      if (v) return v;
    } catch { /* absent or unreadable: fall through to the ephemeral key */ }
  }

  let eph = ephemeral.get(envVar);
  if (!eph) {
    eph = randomBytes(32).toString('hex');
    ephemeral.set(envVar, eph);
  }
  if (!warned.has(envVar)) {
    warned.add(envVar);
    console.warn(
      `[shared-secret] ${envVar} is unset and no secret file was readable — using a random ` +
      `per-process key. Tokens signed elsewhere (or before this restart) will NOT verify. ` +
      `Set ${envVar}, ${envVar}_FILE, or write <data dir>/state/${name} (mode 0600).`,
    );
  }
  return eph;
}

export const sessionSecret = () => loadSharedSecret('SESSION_SECRET', 'session-secret');
export const jwtSecret = () => loadSharedSecret('JWT_SECRET', 'jwt-secret');
