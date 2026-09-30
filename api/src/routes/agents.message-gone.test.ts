/**
 * POST /api/agents/:id/message must be GONE and must write NOTHING.
 *
 * It used to write a JSON file into queue/inbox/<agent>/ and answer {sent:true}. Nothing reads
 * that directory, so every operator message sent through it was silently discarded under a
 * success response — found by test during the 2D QA pass, reproduced through the UI and by
 * curl. This asserts the two properties that matter, and the second one is the point: a 410
 * that still wrote the file would fix the lie and keep the litter.
 *
 * Drives the handler directly with fake req/res, same pattern as agent-send.test.ts.
 * Run: npx tsx --test src/routes/agents.message-gone.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, existsSync, readdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import type { Request, Response } from 'express';
import { handleDeprecatedMessage } from './agents.js';

function fakeRes() {
  const calls: { status?: number; json?: unknown } = {};
  const res = {
    status(code: number) { calls.status = code; return res; },
    json(body: unknown) { calls.json = body; return res; },
  } as unknown as Response;
  return { res, calls };
}

test('the dropped send path answers 410 and points at the durable one', () => {
  const { res, calls } = fakeRes();
  handleDeprecatedMessage(
    { params: { id: 'gm' }, body: { message: 'do the thing' } } as unknown as Request, res);

  assert.equal(calls.status, 410, 'must not be 2xx — reporting success is the original defect');
  const body = calls.json as Record<string, unknown>;
  assert.equal(body.code, 'endpoint_gone');
  assert.equal(body.use_instead, 'POST /api/agents/:id/send',
    'a caller told "gone" with no replacement will just retry or hand-roll another dead path');
  assert.ok(!('sent' in body), 'must not carry a success-shaped field');
});

test('it writes NOTHING to queue/inbox, which is the half a 410 alone would not fix', () => {
  // Point ORCHESTRA_DIR at an empty temp dir: if the handler still wrote, it would land here.
  const prev = process.env.ORCHESTRA_DIR;
  const tmp = mkdtempSync(join(tmpdir(), 'orch-gone-'));
  process.env.ORCHESTRA_DIR = tmp;
  try {
    const { res } = fakeRes();
    handleDeprecatedMessage(
      { params: { id: 'gm' }, body: { message: 'do the thing' } } as unknown as Request, res);

    const inbox = join(tmp, 'queue', 'inbox');
    assert.equal(existsSync(inbox), false, `nothing may be written under ${inbox}`);
    assert.deepEqual(readdirSync(tmp), [], 'the handler must touch no files at all');
  } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_DIR; else process.env.ORCHESTRA_DIR = prev;
  }
});

test('an empty body still 410s — it is gone regardless of what you send it', () => {
  const { res, calls } = fakeRes();
  handleDeprecatedMessage({ params: { id: 'gm' }, body: {} } as unknown as Request, res);
  assert.equal(calls.status, 410, 'must not fall back to the old 400 "message required"');
});
