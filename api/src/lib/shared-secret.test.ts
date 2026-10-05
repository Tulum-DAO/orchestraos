/**
 * Regression suite for the hardcoded-secret-fallback class.
 *
 * The burned literal is deliberately NOT reproduced here. The property that matters is not
 * "that one string is rejected" -- it is that NO string a reader of the source can know is
 * accepted. These tests assert the general property, which also covers the burned value.
 *
 * Run: npx tsx --test src/lib/shared-secret.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, mkdtempSync, rmSync, chmodSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { loadSharedSecret } from './shared-secret.js';

function withEnv<T>(vars: Record<string, string | undefined>, fn: () => T): T {
  const saved: Record<string, string | undefined> = {};
  for (const k of Object.keys(vars)) { saved[k] = process.env[k]; 
    if (vars[k] === undefined) delete process.env[k]; else process.env[k] = vars[k]!; }
  try { return fn(); } finally {
    for (const k of Object.keys(vars)) {
      if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k]!;
    }
  }
}

test('the env var wins when set', () => {
  withEnv({ T1_SECRET: 'from-the-environment' }, () => {  // pragma: allowlist secret
    assert.equal(loadSharedSecret('T1_SECRET', 'unused'), 'from-the-environment');
  });
});

test('a secret FILE is read when the env var is unset', () => {
  const dir = mkdtempSync(join(tmpdir(), 'sharedsecret-'));
  try {
    const f = join(dir, 's');
    writeFileSync(f, '  from-the-file\n');     // trimmed, so a trailing newline is not part of the key
    chmodSync(f, 0o600);
    withEnv({ T2_SECRET: undefined, T2_SECRET_FILE: f }, () => {
      assert.equal(loadSharedSecret('T2_SECRET', 'unused'), 'from-the-file');
    });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('with neither env nor file, the key is random and NOT any value a source reader could know', () => {
  withEnv({ T3_SECRET: undefined, T3_SECRET_FILE: '/nonexistent/never' }, () => {
    const got = loadSharedSecret('T3_SECRET', '/nonexistent/also-never');
    // 32 random bytes as hex. The whole point: an attacker reading this repo learns nothing.
    assert.match(got, /^[0-9a-f]{64}$/);
    assert.ok(got.length === 64);
  });
});

test('the ephemeral key is STABLE within a process, so a token verifies against itself', () => {
  withEnv({ T4_SECRET: undefined, T4_SECRET_FILE: '/nonexistent/never' }, () => {
    const a = loadSharedSecret('T4_SECRET', '/nonexistent/also-never');
    const b = loadSharedSecret('T4_SECRET', '/nonexistent/also-never');
    assert.equal(a, b);
  });
});

test('two different secrets do NOT collapse to the same ephemeral key', () => {
  // A single shared cache would make the session key and the JWT key identical, so a cookie
  // would verify as a bearer token. Keyed per env var precisely to prevent that.
  withEnv({ T5A_SECRET: undefined, T5B_SECRET: undefined,
            T5A_SECRET_FILE: '/nonexistent/never', T5B_SECRET_FILE: '/nonexistent/never' }, () => {
    const a = loadSharedSecret('T5A_SECRET', '/nonexistent/x');
    const b = loadSharedSecret('T5B_SECRET', '/nonexistent/y');
    assert.notEqual(a, b);
  });
});

test('an empty env var does not shadow the file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'sharedsecret-'));
  try {
    const f = join(dir, 's');
    writeFileSync(f, 'real-key-from-file');
    withEnv({ T6_SECRET: '', T6_SECRET_FILE: f }, () => {
      assert.equal(loadSharedSecret('T6_SECRET', 'unused'), 'real-key-from-file');
    });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('an empty or whitespace-only secret file falls through rather than signing with ""', () => {
  const dir = mkdtempSync(join(tmpdir(), 'sharedsecret-'));
  try {
    const f = join(dir, 's');
    writeFileSync(f, '   \n');
    withEnv({ T7_SECRET: undefined, T7_SECRET_FILE: f }, () => {
      const got = loadSharedSecret('T7_SECRET', '/nonexistent/never');
      assert.match(got, /^[0-9a-f]{64}$/, 'an empty file must not become an empty key');
    });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
