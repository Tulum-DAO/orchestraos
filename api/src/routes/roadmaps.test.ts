/**
 * The roadmaps reader (services/state-reader.ts getRoadmaps) preferred state/roadmaps.json, but the
 * deployment-state writer (PATCH /api/roadmaps/...) wrote dashboard_v4/roadmaps.json. Once state/ existed,
 * an update landed in a file nothing read. Both now go through roadmapsFile().
 * ORCHESTRA_DIR is read at import by state-reader, so it is set BEFORE the dynamic imports.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const doc = (state: string) => JSON.stringify({ projects: { p1: { phases: [{ tasks: [{ name: 't', deployment_state: state }] }] } } });

async function app(data: string) {
  process.env.ORCHESTRA_DIR = data;
  if (!process.env.ORCHESTRA_CONFIG) {
    process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
  }
  const express = (await import('express')).default;
  const roadmaps = (await import(`./roadmaps.js?t=${Date.now()}`)).default;
  const a = express();
  a.use(express.json());
  a.use('/api/roadmaps', roadmaps);
  const server = a.listen(0);
  return { server, base: `http://127.0.0.1:${(server.address() as any).port}` };
}

const patch = (base: string) => fetch(`${base}/api/roadmaps/p1/tasks/0/0`, {
  method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ deployment_state: 'shipped' }),
});

test('both files exist: the update lands in state/roadmaps.json, the file the reader reads', async () => {
  const data = mkdtempSync(join(tmpdir(), 'roadmaps-'));
  mkdirSync(join(data, 'state'));
  mkdirSync(join(data, 'dashboard_v4'));
  writeFileSync(join(data, 'state', 'roadmaps.json'), doc('planned'));
  writeFileSync(join(data, 'dashboard_v4', 'roadmaps.json'), doc('legacy'));
  const { server, base } = await app(data);
  try {
    assert.equal((await patch(base)).status, 200);
    const current = JSON.parse(readFileSync(join(data, 'state', 'roadmaps.json'), 'utf-8'));
    assert.equal(current.projects.p1.phases[0].tasks[0].deployment_state, 'shipped');
    const legacy = JSON.parse(readFileSync(join(data, 'dashboard_v4', 'roadmaps.json'), 'utf-8'));
    assert.equal(legacy.projects.p1.phases[0].tasks[0].deployment_state, 'legacy');   // untouched
  } finally {
    server.close();
  }
});

test('only the legacy file exists: it is still read and updated', async () => {
  const data = mkdtempSync(join(tmpdir(), 'roadmaps-'));
  mkdirSync(join(data, 'dashboard_v4'));
  writeFileSync(join(data, 'dashboard_v4', 'roadmaps.json'), doc('legacy'));
  const { server, base } = await app(data);
  try {
    assert.equal((await patch(base)).status, 200);
    const legacy = JSON.parse(readFileSync(join(data, 'dashboard_v4', 'roadmaps.json'), 'utf-8'));
    assert.equal(legacy.projects.p1.phases[0].tasks[0].deployment_state, 'shipped');
  } finally {
    server.close();
  }
});
