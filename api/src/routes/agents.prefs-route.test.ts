/**
 * GET/PUT /api/agents/:id/prefs carries `style_before_dev`, which the server records when a PUT turns the style to
 * Dev, so Back to chat works from any client and after a restart. Runs the real router against a temp data dir.
 *
 * Run: npx tsx --test src/routes/agents.prefs-route.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('agents-prefs-');
mkdirSync(join(dir, 'state'));
process.env.ORCHESTRA_DIR = dir;

async function withApp(fn: (call: (method: string, body?: unknown) => Promise<any>) => Promise<void>) {
  const { default: agentsRouter } = await import('./agents.js');
  const app = express();
  app.use(express.json());
  app.use('/api/agents', agentsRouter);
  const server = app.listen(0);
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents/dev-demo/prefs`;
  try {
    await fn(async (method, body) => (await fetch(base, {
      method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
    })).json());
  } finally { server.close(); }
}

test('a PUT to Dev records the style it left; Back to chat reads it; a fresh router still has it', async () => {
  await withApp(async (call) => {
    assert.equal((await call('GET')).style_before_dev, 'bubbles', 'nothing recorded: the default');
    await call('PUT', { style: 'modern' });
    const dev = await call('PUT', { style: 'dev' });
    assert.deepEqual([dev.ok, dev.style, dev.style_before_dev], [true, 'dev', 'modern']);
    const back = await call('PUT', { style: (await call('GET')).style_before_dev });
    assert.deepEqual([back.style, back.style_before_dev], ['modern', 'modern']);
    assert.equal((await call('PUT', { style_before_dev: 'bubbles' })).ok, false, 'a client cannot set it');
  });
  await withApp(async (call) => { assert.equal((await call('GET')).style_before_dev, 'modern', 'kept on disk'); });
});
