/**
 * Codegen drift suite: the committed generated artifacts (TS + Swift) must be
 * EXACTLY what codegen.mjs re-emits from transcript.v2.schema.json, and the
 * generated TS must typecheck + agree with the server's GRAMMAR_VERSION.
 * Edit the schema -> `node codegen.mjs` -> commit; never hand-edit gen/.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');

test('codegen --check: committed gen/ artifacts match a fresh regeneration', () => {
  const r = spawnSync('node', [join(ROOT, 'codegen.mjs'), '--check'], { encoding: 'utf-8' });
  assert.equal(r.status, 0, `codegen --check failed:\n${r.stdout}\n${r.stderr}`);
});

test('generated TS exists and typechecks standalone', () => {
  const genTs = join(ROOT, 'gen', 'transcript.gen.ts');
  assert.ok(existsSync(genTs), 'gen/transcript.gen.ts missing — run codegen.mjs');
  const r = spawnSync('npx', ['tsc', '-p', join(ROOT, 'tsconfig.gen.json')], {
    encoding: 'utf-8',
    cwd: join(ROOT, '..', '..', 'api'),
  });
  assert.equal(r.status, 0, `generated TS does not typecheck:\n${r.stdout}\n${r.stderr}`);
});

test('generated Swift artifact exists (canonical copy lives in contract/transcript/gen)', () => {
  assert.ok(existsSync(join(ROOT, 'gen', 'TranscriptContract.gen.swift')));
});

test('generated GRAMMAR_VERSION matches the server stamp', async () => {
  const gen = await import(join(ROOT, 'gen', 'transcript.gen.ts'));
  const server = await import('../../../api/src/routes/chat-transcript.js');
  assert.equal(gen.GRAMMAR_VERSION, server.GRAMMAR_VERSION);
});
