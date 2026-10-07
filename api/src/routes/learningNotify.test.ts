/**
 * POST /api/learning/feedback: the creator's inbox note must carry the SAME cleaned submitted_by as the
 * stored file (review of #195: the note used the raw request value, the junk cleanSubmittedBy bounds).
 * The real router, mounted in Express, against a temp ORCHESTRA_DIR.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

test('ROUTE: the inbox note and the stored file carry the same CLEANED submitted_by', async () => {
  const orch = mkdtempSync(join(tmpdir(), 'lnotify-'));
  mkdirSync(join(orch, 'state', 'questionnaires'), { recursive: true });
  writeFileSync(join(orch, 'state', 'questionnaires', 'index.json'), JSON.stringify([
    { id: 'qnr_ok_1', title: 'Real one', created_by: 'gm', status: 'pending' },
  ]));
  process.env.ORCHESTRA_DIR = orch;
  if (!process.env.ORCHESTRA_CONFIG) {
    process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
  }
  const express = (await import('express')).default;
  const router = (await import(`./learning.js?t=${Date.now()}`)).default;
  const app = express();
  app.use(express.json());
  app.use('/api/learning', router);
  const server = app.listen(0);
  try {
    const port = (server.address() as any).port;
    const junk = { not: 'a string' };   // an object is exactly what cleanSubmittedBy rejects
    const r = await fetch(`http://127.0.0.1:${port}/api/learning/feedback`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ questionnaire_id: 'qnr_ok_1', answers: { q: 1 }, submitted_by: junk }),
    });
    assert.equal(r.status, 200);
    const fb = join(orch, 'state', 'feedback');
    const stored = JSON.parse(readFileSync(join(fb, readdirSync(fb)[0]), 'utf-8'));
    const inbox = join(orch, 'queue', 'inbox', 'gm');
    const note = JSON.parse(readFileSync(join(inbox, readdirSync(inbox)[0]), 'utf-8'));
    assert.equal(typeof stored.submitted_by, 'string');
    assert.equal(note.submitted_by, stored.submitted_by, 'the note must not carry the raw object');
  } finally { server.close(); }
});
