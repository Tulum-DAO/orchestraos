/**
 * POST /api/questionnaires/:id/submit (gm msg_e46c0dd7). Drives the REAL submitQuestionnaire against a
 * real temp directory; only msg_store is captured. Express decodes %2F in route params, so the ids
 * below are what the handler actually receives for /api/questionnaires/..%2F..%2Fx/submit.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { submitQuestionnaire } from './questionnaireSubmit.js';

const SENDER = 'operator-under-test';

function setup() {
  const root = mkdtempSync(join(tmpdir(), 'qsub-'));
  const qdir = join(root, 'orch', 'state', 'questionnaires');
  mkdirSync(qdir, { recursive: true });
  writeFileSync(join(qdir, 'index.json'), JSON.stringify([
    { id: 'qnr_ok_1', title: 'Real one', created_by: 'gm', status: 'pending' },
  ]));
  const sent: string[][] = [];
  const deps = {
    orchestraDir: join(root, 'orch'),
    sender: SENDER,
    readJson: (p: string) => { try { return JSON.parse(readFileSync(p, 'utf-8')); } catch { return null; } },
    writeFile: (p: string, d: string) => writeFileSync(p, d),
    ensureDir: (d: string) => { if (!existsSync(d)) mkdirSync(d, { recursive: true }); },
    send: (argv: string[]) => { sent.push(argv); },
    now: () => 1791350000000,
  };
  return { root, deps, sent };
}

function filesUnder(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) out.push(...filesUnder(p)); else out.push(p);
  }
  return out;
}

test('a TRAVERSAL id is refused with 400 and NOTHING is written anywhere', () => {
  const { root, deps, sent } = setup();
  const before = filesUnder(root).sort();
  for (const id of ['../../x', '../../../tmp/x', 'a/b', '..', 'x\u0000y', '']) {
    const r = submitQuestionnaire(deps, id, { answers: { q: 1 } });
    assert.equal(r.status, 400, JSON.stringify(id));
  }
  assert.deepEqual(filesUnder(root).sort(), before, 'no file may be created by a refused id');
  assert.equal(sent.length, 0);
});

test('an UNKNOWN (but well-formed) id is refused BEFORE any write', () => {
  const { root, deps } = setup();
  const before = filesUnder(root).sort();
  const r = submitQuestionnaire(deps, 'qnr_not_in_index', { answers: { q: 1 } });
  assert.equal(r.status, 404);
  assert.deepEqual(filesUnder(root).sort(), before);
});

test('POSITIVE CONTROL: a legit submit lands INSIDE state/feedback and completes the index', () => {
  const { deps } = setup();
  const r = submitQuestionnaire(deps, 'qnr_ok_1', { answers: { q: 'yes' } });
  assert.equal(r.status, 200);
  const fb = join(deps.orchestraDir, 'state', 'feedback');
  const files = readdirSync(fb);
  assert.deepEqual(files, ['qnr_ok_1_1791350000000.json']);
  const idx = JSON.parse(readFileSync(join(deps.orchestraDir, 'state', 'questionnaires', 'index.json'), 'utf-8'));
  assert.equal(idx[0].status, 'completed');
  assert.equal(idx[0].response_file, 'state/feedback/qnr_ok_1_1791350000000.json');
});

test('the SENDER on the bus is always the configured operator, whatever submitted_by says; the claim is kept as unverified', () => {
  const { deps, sent } = setup();
  submitQuestionnaire(deps, 'qnr_ok_1', { answers: { q: 1 }, submitted_by: 'gm' });
  assert.equal(sent.length, 1);
  const argv = sent[0];
  assert.equal(argv[argv.indexOf('--from') + 1], SENDER);
  assert.equal(argv[argv.indexOf('--to') + 1], 'gm');
  const stored = JSON.parse(readFileSync(join(deps.orchestraDir, 'state', 'feedback', 'qnr_ok_1_1791350000000.json'), 'utf-8'));
  assert.equal(stored.submitted_by, SENDER);
  assert.equal(stored.unverified_submitted_by, 'gm');
});

// ---- THE WIRING: the real router, the real Express param decoding -----------------------------
// Without this, reverting the route to its old inline body would leave every test above green.
test('ROUTE: POST /api/questionnaires/..%2F..%2Fpwn/submit is a 400 and writes nothing', async () => {
  const { root } = setup();
  const orch = join(root, 'orch');
  process.env.ORCHESTRA_DIR = orch;
  // The route reads the operator id from orchestra.toml; give it the shipped example, as a fresh
  // checkout would, so the test does not depend on a local config existing.
  if (!process.env.ORCHESTRA_CONFIG) {
    process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
  }
  const express = (await import('express')).default;
  const router = (await import(`./questionnaires.js?t=${Date.now()}`)).default;
  const app = express();
  app.use(express.json());
  app.use('/api/questionnaires', router);
  const server = app.listen(0);
  try {
    const port = (server.address() as any).port;
    const before = filesUnder(root).sort();
    const bad = await fetch(`http://127.0.0.1:${port}/api/questionnaires/..%2F..%2Fpwn/submit`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ answers: { q: 1 }, submitted_by: 'gm' }),
    });
    assert.equal(bad.status, 400);
    assert.deepEqual(filesUnder(root).sort(), before, 'the traversal must not create any file');
    // control: the same router accepts a real id, so the 400 above is the guard, not a dead route
    const ok = await fetch(`http://127.0.0.1:${port}/api/questionnaires/qnr_ok_1/submit`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ answers: { q: 1 } }),
    });
    assert.equal(ok.status, 200);
    assert.ok(existsSync(join(orch, 'state', 'feedback', readdirSync(join(orch, 'state', 'feedback'))[0])));
  } finally { server.close(); }
});
