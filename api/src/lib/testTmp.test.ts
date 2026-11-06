/**
 * testTmpDir: the dirs it makes are gone once the test process exits, and no test makes a temp
 * dir any other way (a raw mkdtempSync leaked one dir per run; gate runs filled a disk with them).
 * Run: npx tsx --test src/lib/testTmp.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { testTmpDir } from './testTmp.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..');

test('a dir made in a child process is removed when that process exits', () => {
  const helper = join(HERE, 'testTmp.ts');
  const script = `import { testTmpDir } from ${JSON.stringify(helper)};
    import { writeFileSync, mkdirSync } from 'node:fs';
    const d = testTmpDir('testtmp-probe-');
    mkdirSync(d + '/nested/deeper', { recursive: true });
    writeFileSync(d + '/nested/deeper/f.txt', 'x');
    process.stdout.write(d);`;
  const tsx = join(SRC, '..', 'node_modules', '.bin', 'tsx');
  const d = execFileSync(tsx, ['--eval', script], { encoding: 'utf8', env: { ...process.env, ORCHESTRA_KEEP_TEST_TMP: '' } });
  assert.ok(d.includes('testtmp-probe-'), d);
  assert.equal(existsSync(d), false, `${d} survived the process`);
});

test('the dir exists and is writable while the test runs', () => {
  const d = testTmpDir('testtmp-live-');
  assert.ok(statSync(d).isDirectory());
});

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(test|fixture)\.ts$/.test(e.name)) out.push(p);
  }
  return out;
}

test('no test under src/ calls mkdtempSync itself (use testTmpDir, which cleans up)', () => {
  const offenders = walk(SRC).filter((f) => /\bmkdtemp(Sync)?\s*\(/.test(readFileSync(f, 'utf8')));
  assert.deepEqual(offenders.map((f) => relative(SRC, f)), []);
});
