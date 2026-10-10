/**
 * Data-dir sweep S5 (gm): with no orchestra.toml (and no ORCHESTRA_DIR), the API must fail with ONE
 * plain line naming the missing file and the fix, not a stack trace. Route modules read the data dir
 * at import, so the throw happens while ES modules are still evaluating; lib/configExit.ts (server.ts's
 * first import) is what turns it into one line. Starts the REAL entry point in a child process.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('no orchestra.toml: the API exits 1 with one plain line, no stack trace', () => {
  const missing = join(mkdtempSync(join(tmpdir(), 'noconf-')), 'orchestra.toml');
  const env: NodeJS.ProcessEnv = { ...process.env, ORCHESTRA_CONFIG: missing };
  delete env.ORCHESTRA_DIR;
  delete env.ORCH_DIR;
  const server = new URL('../server.ts', import.meta.url).pathname;
  const r = spawnSync(process.execPath, ['--import', 'tsx', server], { env, encoding: 'utf-8', timeout: 60_000 });
  assert.equal(r.status, 1, `exit ${r.status}; stderr: ${r.stderr}`);
  const lines = r.stderr.split('\n').filter((l) => l.trim());
  assert.equal(lines.length, 1, `expected one line, got:\n${r.stderr}`);
  assert.ok(lines[0].includes(missing), lines[0]);
  assert.ok(lines[0].includes('orchestra init'), lines[0]);
  assert.ok(!/^\s+at /m.test(r.stderr), 'a stack trace leaked');
});

test('dataDir(): $ORCHESTRA_DIR first, else orchestra.toml [data] dir with ~ expanded (no hard-coded path)', async () => {
  const { readFileSync, writeFileSync } = await import('node:fs');
  const { homedir } = await import('node:os');
  const example = new URL('../../../orchestra.example.toml', import.meta.url).pathname;
  const cfg = join(mkdtempSync(join(tmpdir(), 'cfg-')), 'orchestra.toml');
  writeFileSync(cfg, readFileSync(example, 'utf-8').replace(/^dir = .*$/m, 'dir = "~/od-test"'));
  const saved = { c: process.env.ORCHESTRA_CONFIG, d: process.env.ORCHESTRA_DIR };
  try {
    process.env.ORCHESTRA_CONFIG = cfg;
    delete process.env.ORCHESTRA_DIR;
    const fresh = await import(`./config.js?t=${Date.now()}`);
    assert.equal(fresh.dataDir(), join(homedir(), 'od-test'));
    process.env.ORCHESTRA_DIR = '/srv/explicit';
    assert.equal(fresh.dataDir(), '/srv/explicit');
  } finally {
    if (saved.c === undefined) delete process.env.ORCHESTRA_CONFIG; else process.env.ORCHESTRA_CONFIG = saved.c;
    if (saved.d === undefined) delete process.env.ORCHESTRA_DIR; else process.env.ORCHESTRA_DIR = saved.d;
  }
});
