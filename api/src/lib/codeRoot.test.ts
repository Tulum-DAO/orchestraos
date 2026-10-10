/**
 * CODE vs DATA (gm msg_c1a15b1a): under `orchestra up` ORCHESTRA_DIR is the DATA dir (~/.orchestra by
 * default) and the repo lives elsewhere. Code the api runs (spawn-agent.sh, message_bus.py, scripts/*)
 * resolved from ORCHESTRA_DIR does not exist there: the dashboard's Spawn button ran
 * ~/.orchestra/spawn-agent.sh, every transcript stream was blocked because the telemetry bridge was
 * "missing", and so on. These tests run with ORCHESTRA_DIR pointed at an EMPTY temp data dir.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, statSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const API = join(import.meta.dirname, '..', '..');

function codeRootWith(env: Record<string, string | undefined>): string {
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries({ ...process.env, ...env })) if (v !== undefined) clean[k] = v;
  delete clean.ORCHESTRA_ROOT;
  if (env.ORCHESTRA_ROOT) clean.ORCHESTRA_ROOT = env.ORCHESTRA_ROOT;
  return execFileSync('npx', ['tsx', '-e', "import('./src/lib/codeRoot.ts').then(m => process.stdout.write(m.CODE_ROOT))"],
    { cwd: API, env: clean, encoding: 'utf8' });
}

// Every repo file the api launches or reads as code, as the api names it.
const CODE_FILES = [
  'spawn-agent.sh',
  'message_bus.py',
  join('scripts', 'approval.py'),
  join('scripts', 'lineage_daemon', 'realtime', 'api_bridge.py'),
  'prompts',
];

test('with ORCHESTRA_DIR an empty data dir and no ORCHESTRA_ROOT, every code file resolves in the checkout', () => {
  const data = mkdtempSync(join(tmpdir(), 'orch-data-'));
  const root = codeRootWith({ ORCHESTRA_DIR: data });
  assert.notEqual(root, data);
  for (const f of CODE_FILES) assert.ok(existsSync(join(root, f)), `${f} missing under CODE_ROOT=${root}`);
  for (const f of CODE_FILES) assert.ok(!existsSync(join(data, f)), `${f} must not be looked for in the data dir`);
});

test('ORCHESTRA_ROOT, when set, is the code root', () => {
  const data = mkdtempSync(join(tmpdir(), 'orch-data-'));
  assert.equal(codeRootWith({ ORCHESTRA_DIR: data, ORCHESTRA_ROOT: '/some/checkout' }), '/some/checkout');
});

test('no api file builds a known code path from a data-dir variable', () => {
  // A data-dir variable here is anything assigned from ORCHESTRA_DIR / loadConfig().dataDir. The code
  // files below must come from CODE_ROOT (lib/codeRoot.ts) instead.
  const code = /join\(\s*(ORCHESTRA|ORCH|ORCHESTRA_DIR|PROMPT_ORCH\(\))\s*,\s*'(spawn-agent\.sh|message_bus\.py|msg_store\.py|scripts|prompts)'/;
  const offenders: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) { walk(p); continue; }
      if (!p.endsWith('.ts') || p.endsWith('.test.ts')) continue;
      readFileSync(p, 'utf8').split('\n').forEach((line, i) => {
        if (code.test(line) && !/^\s*(\/\/|\*)/.test(line)) offenders.push(`${p.slice(API.length + 1)}:${i + 1}: ${line.trim()}`);
      });
    }
  };
  walk(join(API, 'src'));
  assert.deepEqual(offenders, [], 'resolve code from CODE_ROOT (lib/codeRoot.ts):\n' + offenders.join('\n'));
});
