/**
 * DB ("live") questionnaires on the web (P1, 2026-10-14): every questionnaire made by
 * `approval.py questionnaire` lives in the gateway's QuestionnaireStore (tasks.db), not in the
 * legacy state/questionnaires/index.json. /api/questionnaires listed only the legacy file and its
 * iframe (/:id/html) 404'd, so the web showed a title and nothing to answer. The API now forwards
 * live ones to the gateway's own questionnaire routes (the phone's contract): list, detail, and
 * answers -> the gateway submit (one validator, one resume path). Legacy items are unchanged.
 * Pure over an injected fetch and a temp data dir. Run: npx tsx --test src/routes/questionnaires.live.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { existsSync, mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createQuestionnairesRouter, type QuestionnairesDeps } from './questionnaires.js';

type Call = { url: string; init: RequestInit };

const LIVE_DETAIL = {
  id: 'qnr_test_0001', title: 'Pick the launch plan', status: 'pending', from_agent: 'gm',
  question_count: 2, answered_count: 0, draft_rev: 0,
  questions: [
    { n: 1, prompt: 'Which day?', kind: 'menu', required: 1,
      menu: { options: [{ n: '1', label: 'Monday', input_kind: 'direct' }, { n: '2', label: 'Other', input_kind: 'free_text' }] },
      answer: null },
    { n: 2, prompt: 'Anything else?', kind: 'free_text', required: 0, answer: null },
  ],
};

function dataDir(legacy: any[] = []) {
  const d = mkdtempSync(join(tmpdir(), 'qnr-'));
  mkdirSync(join(d, 'state', 'questionnaires'), { recursive: true });
  writeFileSync(join(d, 'state', 'questionnaires', 'index.json'), JSON.stringify(legacy));
  return d;
}

function makeDeps(over: Partial<QuestionnairesDeps> = {}, respond?: (url: string, init: RequestInit) => { status: number; body: any }) {
  const calls: Call[] = [];
  const deps: any = {
    orchestraDir: dataDir([{ id: 'old-1', title: 'Old form', status: 'completed', html_file: 'state/questionnaires/old-1.html' }]),
    token: () => 'gw-token',
    principal: () => ({ username: 'operator', role: 'admin', allowedAgents: '*', clientScope: null, trusted: false }),
    gatewayUrl: () => 'http://gw.test',
    fetchJson: async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (respond) return respond(url, init);
      if (url.endsWith('/questionnaires')) return { status: 200, body: { ok: true, questionnaires: [
        { id: 'qnr_test_0001', title: 'Pick the launch plan', from_agent: 'gm', question_count: 2,
          answered_count: 0, draft_rev: 0, created_at: '2026-10-14T18:00:00Z', urgency: 1 }] } };
      if (url.includes('/questionnaires/qnr_')) return init.method === 'POST'
        ? { status: 200, body: { ok: true, applied: true, id: 'qnr_test_0001' } }
        : { status: 200, body: { ok: true, questionnaire: LIVE_DETAIL } };
      return { status: 404, body: { ok: false, error: 'not found' } };
    },
    ...over,
  };
  deps.calls = calls;
  return deps as QuestionnairesDeps & { calls: Call[] };
}

async function req(deps: QuestionnairesDeps, method: string, path: string, body?: unknown) {
  const app = express();
  app.use(express.json());
  app.use('/api/questionnaires', createQuestionnairesRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const data = body === undefined ? undefined : JSON.stringify(body);
      const r = http.request({ host: '127.0.0.1', port, path, method,
        headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {} }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.setTimeout(5000, () => r.destroy(new Error(`no response in 5s: ${method} ${path}`)));  // a broken route fails, never hangs
      if (data) r.write(data);
      r.end();
    });
  } finally {
    server.close();
  }
}

test('the list carries live (DB) questionnaires first, marked source live, then legacy', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/questionnaires');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.questionnaires.map((q: any) => [q.id, q.source]),
    [['qnr_test_0001', 'live'], ['old-1', undefined]]);
  assert.equal(r.json.questionnaires[0].status, 'pending');
  assert.equal(r.json.questionnaires[0].created_by, 'gm');                // the cards' legacy field names
  assert.equal(r.json.pending, 1);
  assert.equal(deps.calls[0].url, 'http://gw.test/questionnaires');
  assert.equal((deps.calls[0].init.headers as any).Authorization, 'Bearer gw-token');
});

test('a live row wins over a legacy row with the same id', async () => {
  const deps = makeDeps({ orchestraDir: dataDir([{ id: 'qnr_test_0001', title: 'stale', status: 'completed' }]) });
  const r = await req(deps, 'GET', '/api/questionnaires');
  assert.equal(r.json.questionnaires.length, 1);
  assert.equal(r.json.questionnaires[0].source, 'live');
});

test('with the gateway down the list still serves the legacy history', async () => {
  const deps = makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } });
  const r = await req(deps, 'GET', '/api/questionnaires');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.questionnaires.map((q: any) => q.id), ['old-1']);
});

test('the detail of a live questionnaire comes from the gateway with its questions and options', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/questionnaires/qnr_test_0001');
  assert.equal(r.status, 200);
  assert.equal(r.json.source, 'live');
  assert.equal(r.json.questions.length, 2);
  assert.deepEqual(r.json.questions[0].menu.options.map((o: any) => o.label), ['Monday', 'Other']);
  assert.equal(deps.calls[0].url, 'http://gw.test/questionnaires/qnr_test_0001');
});

test('a legacy id is served from the index and never forwarded', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/questionnaires/old-1');
  assert.equal(r.status, 200);
  assert.equal(r.json.title, 'Old form');
  assert.equal(deps.calls.length, 0);
});

test('answers go to the gateway submit with surface web, token added, answers passed through', async () => {
  const deps = makeDeps();
  const answers = { '1': { option_n: '2', answer_text: 'Thursday' }, '2': { answer_text: 'no' } };
  const r = await req(deps, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers, surface: 'evil' });
  assert.equal(r.status, 200);
  assert.equal(r.json.applied, true);
  const c = deps.calls[0];
  assert.equal(c.url, 'http://gw.test/questionnaires/qnr_test_0001/submit');
  assert.equal(c.init.method, 'POST');
  assert.equal((c.init.headers as any).Authorization, 'Bearer gw-token');
  assert.deepEqual(JSON.parse(String(c.init.body)), { answers, surface: 'web' });
});

test('the gateway answer (missing / 400) is passed back as is', async () => {
  const deps = makeDeps({}, () => ({ status: 200, body: { ok: true, applied: false, missing: [1] } }));
  const r = await req(deps, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: {} });
  assert.deepEqual(r.json, { ok: true, applied: false, missing: [1] });
  const bad = makeDeps({}, () => ({ status: 400, body: { ok: false, error: 'q1: option_n 9 does not match a captured option' } }));
  const r2 = await req(bad, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '1': { option_n: '9' } } });
  assert.equal(r2.status, 400);
});

test('a skipped question goes to the gateway as sent, and its "nothing answered" refusal comes back as is', async () => {
  const deps = makeDeps();
  const answers = { '1': { option_n: '1' }, '2': { skipped: true } };
  const r = await req(deps, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers });
  assert.equal(r.status, 200);
  assert.deepEqual(JSON.parse(String(deps.calls[0].init.body)), { answers, surface: 'web' });
  const none = makeDeps({}, () => ({ status: 400, body: { ok: false, applied: false, error: 'nothing answered: answer at least one question' } }));
  const r2 = await req(none, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '2': { skipped: true } } });
  assert.equal(r2.status, 400);
  assert.equal(r2.json.error, 'nothing answered: answer at least one question');
});

test('the ✕ goes to the gateway discard for the operator only, and never for a legacy id', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'POST', '/api/questionnaires/qnr_test_0001/discard', {});
  assert.equal(r.status, 200);
  assert.equal(deps.calls[0].url, 'http://gw.test/questionnaires/qnr_test_0001/discard');
  assert.equal(deps.calls[0].init.method, 'POST');
  assert.equal((await req(makeDeps(), 'POST', '/api/questionnaires/old-1/discard', {})).status, 404);
  const viewer = makeDeps({ principal: () => ({ username: 'v', role: 'viewer', allowedAgents: '*', clientScope: null, trusted: false }) as any });
  assert.equal((await req(viewer, 'POST', '/api/questionnaires/qnr_test_0001/discard', {})).status, 403);
  assert.equal(viewer.calls.length, 0);
});

test('answers are refused for a non-live id, without a token, or with a bad body', async () => {
  assert.equal((await req(makeDeps(), 'POST', '/api/questionnaires/old-1/answers', { answers: {} })).status, 404);
  assert.equal((await req(makeDeps({ token: () => '' }), 'POST', '/api/questionnaires/qnr_x/answers', { answers: {} })).status, 502);
  assert.equal((await req(makeDeps(), 'POST', '/api/questionnaires/qnr_x/answers', { answers: 'nope' })).status, 400);
});

test('the legacy html route still serves only legacy items (a live id is never an iframe)', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/questionnaires/qnr_test_0001/html');
  assert.equal(r.status, 404);
  assert.equal(deps.calls.length, 0);
});


// --- gm's submit-path conditions ------------------------------------------------------------------
test('answering needs the operator: no identity is a 401, a client-scoped or non-admin caller a 403, none is forwarded', async () => {
  const anon = makeDeps({ principal: () => null });
  const r1 = await req(anon, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '1': { option_n: '1' } } });
  assert.equal(r1.status, 401);
  const client = makeDeps({ principal: () => ({ username: 'c', role: 'viewer', allowedAgents: 'acme', clientScope: 'acme', trusted: true }) });
  const r2 = await req(client, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '1': { option_n: '1' } } });
  assert.equal(r2.status, 403);
  const viewer = makeDeps({ principal: () => ({ username: 'v', role: 'viewer', allowedAgents: '*', clientScope: null, trusted: true }) });
  const r3 = await req(viewer, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '1': { option_n: '1' } } });
  assert.equal(r3.status, 403);   // not the operator: no admin role, even with no client scope
  assert.equal(viewer.calls.length, 0);
  assert.equal(anon.calls.length + client.calls.length, 0);
});

test('with no gateway running, detail and answers say so (502) and the list still works', async () => {
  const down = makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } });
  const d = await req(down, 'GET', '/api/questionnaires/qnr_test_0001');
  assert.equal(d.status, 502);
  assert.equal(d.json.error, 'gateway unreachable');
  const a = await req(down, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '1': { option_n: '1' } } });
  assert.equal(a.status, 502);
  assert.equal((await req(down, 'GET', '/api/questionnaires')).status, 200);
});

test('a second submit is not applied twice: the gateway answer (applied false, status) is passed back', async () => {
  const deps = makeDeps({}, () => ({ status: 200, body: { ok: true, applied: false, id: 'qnr_test_0001', status: 'submitted' } }));
  const r = await req(deps, 'POST', '/api/questionnaires/qnr_test_0001/answers', { answers: { '1': { option_n: '1' } } });
  assert.deepEqual(r.json, { ok: true, applied: false, id: 'qnr_test_0001', status: 'submitted' });
});

test('a live id is never served or submitted by the legacy routes, even when the legacy index holds it', async () => {
  const d = dataDir([{ id: 'qnr_test_0001', title: 'Collides', status: 'pending', html_file: 'state/questionnaires/c.html' }]);
  writeFileSync(join(d, 'state', 'questionnaires', 'c.html'), '<html>legacy</html>');
  const deps = makeDeps({ orchestraDir: d } as any);
  assert.equal((await req(deps, 'GET', '/api/questionnaires/qnr_test_0001/html')).status, 404);
  assert.equal((await req(deps, 'GET', '/api/questionnaires/qnr_test_0001/response')).status, 404);
  const sub = await req(deps, 'POST', '/api/questionnaires/qnr_test_0001/submit', { answers: {} });
  assert.equal(sub.status, 409);
  assert.match(sub.json.error, /\/answers/);
  assert.ok(!existsSync(join(d, 'state', 'feedback')), 'nothing written for a live id');
});

test('a live id the gateway does not know is a 404, never a legacy entry of the same id', async () => {
  const d = dataDir([{ id: 'qnr_test_0001', title: 'Collides', status: 'pending', html_file: 'state/questionnaires/c.html' }]);
  const deps = makeDeps({ orchestraDir: d } as any, () => ({ status: 404, body: { ok: false, error: 'not found' } }));
  const r = await req(deps, 'GET', '/api/questionnaires/qnr_test_0001');
  assert.equal(r.status, 404);
  assert.notEqual(r.json.title, 'Collides');
});
