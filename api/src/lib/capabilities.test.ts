/**
 * Data-dir sweep S4 (gm ruling): an optional feature whose script does not ship answers an honest
 * 501 "not available in this install" (it was a 500 with an ENOENT, or a silent empty), and
 * GET /api/capabilities tells the dashboard to hide it. Presence is checked per request.
 * Drives the REAL routers through Express against temp ORCHESTRA_ROOT / ORCHESTRA_DIR trees.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

function trees() {
  const root = mkdtempSync(join(tmpdir(), 'caps-'));
  const code = join(root, 'checkout');
  const data = join(root, 'data');
  mkdirSync(join(code, 'scripts'), { recursive: true });
  mkdirSync(join(data, 'state'), { recursive: true });
  process.env.ORCHESTRA_ROOT = code;
  process.env.ORCHESTRA_DIR = data;
  if (!process.env.ORCHESTRA_CONFIG) {
    process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
  }
  return { code, data };
}

test('capabilities: every optional script absent => all false; present => true (checked per call)', async () => {
  const { code, data } = trees();
  const { capabilities, CAPABILITIES } = await import(`./capabilities.js?t=${Date.now()}`);
  const none = capabilities();
  for (const c of CAPABILITIES) assert.equal(none[c], false, c);
  writeFileSync(join(code, 'scripts', 'project-status-api.py'), 'print("{}")');
  writeFileSync(join(code, 'voice-agent.py'), '');
  writeFileSync(join(code, 'scripts', 'log-interaction.py'), '');
  mkdirSync(join(data, 'skills'));
  writeFileSync(join(data, 'skills', 'inspect-element.js'), '');
  const all = capabilities();                                     // no restart, no re-import
  for (const c of CAPABILITIES) assert.equal(all[c], true, c);
});

async function app() {
  const express = (await import('express')).default;
  const projectStatus = (await import(`../routes/project-status.js?t=${Date.now()}`)).default;
  const voice = (await import(`../routes/voice.js?t=${Date.now()}`)).default;
  const inspect = (await import(`../routes/inspect-feedback.js?t=${Date.now()}`)).default;
  const a = express();
  a.use(express.json());
  a.use('/api/project-status', projectStatus);
  a.use('/api/voice', voice);
  a.use('/api/inspect-feedback', inspect);
  const server = a.listen(0);
  const base = `http://127.0.0.1:${(server.address() as any).port}`;
  return { server, base };
}

test('ROUTES: an absent script is an honest 501 naming the capability, never a 500/ENOENT', async () => {
  trees();
  const { server, base } = await app();
  try {
    const cases: [string, string, string][] = [
      ['GET', '/api/project-status', 'projectStatus'],
      ['POST', '/api/voice/sync-prompts', 'voicePromptSync'],
      ['GET', '/api/inspect-feedback/script.js', 'inspectScript'],
    ];
    for (const [method, path, cap] of cases) {
      const r = await fetch(base + path, { method });
      assert.equal(r.status, 501, path);
      const body = await r.json();
      assert.deepEqual(body, { error: 'not available in this install', capability: cap }, path);
    }
  } finally {
    server.close();
  }
});

test('ROUTES: a present script still runs (project-status from the CHECKOUT, not the data dir)', async () => {
  const { code } = trees();
  writeFileSync(join(code, 'scripts', 'project-status-api.py'), 'import json; print(json.dumps({"projects": [{"slug": "p1"}]}))');
  const { server, base } = await app();
  try {
    const r = await fetch(base + '/api/project-status');
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { projects: [{ slug: 'p1' }] });
  } finally {
    server.close();
  }
});
