/**
 * GET /api/questionnaires/:id/html serves agent-written HTML same-origin. It must carry the agent-HTML
 * Content-Security-Policy (lib/agentHtmlCsp.ts) so an inline script cannot reach the API or any host as
 * the operator, whatever frame it lands in. Found by a security audit, 2026-11-25.
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createQuestionnairesRouter, type QuestionnairesDeps } from './questionnaires.js';
import { AGENT_HTML_CSP } from '../lib/agentHtmlCsp.js';

const PAGE = '<html><body><script>fetch("/api/questionnaires/old-1/submit",{method:"POST"})</script></body></html>';

function deps(): QuestionnairesDeps {
  const d = testTmpDir('qnr-csp-');
  mkdirSync(join(d, 'state', 'questionnaires'), { recursive: true });
  writeFileSync(join(d, 'state', 'questionnaires', 'index.json'), JSON.stringify([
    { id: 'old-1', title: 'Old form', status: 'pending', html_file: 'state/questionnaires/old-1.html' },
    { id: 'done-1', title: 'Done form', status: 'completed', html_file: 'state/questionnaires/done-1.html', response_file: 'state/questionnaires/done-1.json' },
  ]));
  writeFileSync(join(d, 'state', 'questionnaires', 'old-1.html'), PAGE);
  writeFileSync(join(d, 'state', 'questionnaires', 'done-1.html'), '<html><input name="q1"></html>');
  writeFileSync(join(d, 'state', 'questionnaires', 'done-1.json'), JSON.stringify({ answers: { q1: 'yes' } }));
  return {
    orchestraDir: d,
    token: () => 'gw-token',
    principal: () => ({ username: 'operator', role: 'admin', allowedAgents: '*', clientScope: null, trusted: false }),
    gatewayUrl: () => 'http://gw.test',
    fetchJson: async () => ({ status: 404, body: { ok: false, error: 'not found' } }),
  } as any;
}

async function get(path: string) {
  const app = express();
  app.use('/api/questionnaires', createQuestionnairesRouter(deps()));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; headers: http.IncomingHttpHeaders; body: string }>((resolve, reject) => {
      const r = http.get({ host: '127.0.0.1', port, path }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, headers: res.headers, body: out }));
      });
      r.on('error', reject);
      r.setTimeout(5000, () => r.destroy(new Error('no response in 5s')));
    });
  } finally {
    server.close();
  }
}

test('the questionnaire HTML carries the agent-HTML policy, nosniff and no referrer, body as written', async () => {
  const r = await get('/api/questionnaires/old-1/html');
  assert.equal(r.status, 200);
  assert.equal(r.headers['content-security-policy'], AGENT_HTML_CSP);
  assert.equal(r.headers['x-content-type-options'], 'nosniff');
  assert.equal(r.headers['referrer-policy'], 'no-referrer');
  assert.match(r.headers['content-type'] || '', /text\/html; charset=utf-8/);
  assert.equal(r.body, PAGE);
});

test('a completed questionnaire with answers injected carries the same policy', async () => {
  const r = await get('/api/questionnaires/done-1/html');
  assert.equal(r.status, 200);
  assert.equal(r.headers['content-security-policy'], AGENT_HTML_CSP);
  assert.match(r.body, /<input name="q1"/, 'the completed path still serves the page (what it injects is questionnaireInject.ts business)');
});

test('a 404 from the html route is JSON and carries no agent-HTML policy', async () => {
  const r = await get('/api/questionnaires/nope/html');
  assert.equal(r.status, 404);
  assert.notEqual(r.headers['content-security-policy'], AGENT_HTML_CSP);
});
