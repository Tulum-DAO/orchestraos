// Agent-written HTML (questionnaires, proposal one-pagers) is framed without the dashboard's origin (lib/sandboxedHtml,
// components/kit/SandboxedHtml): its own policy first, a storage stand-in and the fetch bridge before the page's
// scripts, and the parent sends only a questionnaire's own answers. Components have no DOM test runner here, so the
// four frames' wiring is read from the source, as callSkin.test.ts does.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { FRAME_POLICY, SANDBOX, BRIDGE, sandboxedPage, bridgeTarget, answerBridge } from './sandboxedHtml.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('the sandbox never grants the dashboard origin, top navigation or popups', () => {
  const flags = SANDBOX.split(/\s+/);
  assert.deepEqual(flags.sort(), ['allow-forms', 'allow-scripts']);
});

test('the frame policy fetches nothing, posts no form, sets no base and frames nothing', () => {
  const d = Object.fromEntries(FRAME_POLICY.split(';').map((s) => s.trim().split(/\s+/)).map(([k, ...v]) => [k, v.join(' ')]));
  assert.equal(d['default-src'], "'none'");
  assert.equal(d['connect-src'], "'none'");
  assert.equal(d['form-action'], "'none'");
  assert.equal(d['base-uri'], "'none'");
  assert.equal(d['frame-src'], "'none'");
  assert.equal(d['script-src'], "'unsafe-inline'");
});

test('the policy, the storage stand-in and the bridge come before anything of the page, doctype or not', () => {
  for (const html of ['<!DOCTYPE html><html><head><script>x()</script></head></html>', '<div>frag</div><script>y()</script>', '  <!doctype html>\n<p>z</p>']) {
    const out = sandboxedPage(html, { bridge: true });
    assert.ok(out.startsWith('<!doctype html><meta http-equiv="Content-Security-Policy" content="'), out.slice(0, 80));
    const csp = out.indexOf('Content-Security-Policy'), store = out.indexOf('localStorage'), bridge = out.indexOf(`'${BRIDGE}'`);
    const page = out.indexOf(html.replace(/^\s*<!doctype[^>]*>/i, '').slice(0, 6));
    assert.ok(csp < store && store < bridge && bridge < page, html);
    assert.equal((out.match(/<!doctype/gi) ?? []).length, 1);
  }
  const plain = sandboxedPage('<p>one-pager</p>', { bridge: false });
  assert.ok(!plain.includes(`'${BRIDGE}'`), 'no bridge for a page that answers nothing');
  assert.ok(plain.includes('localStorage'), 'the storage stand-in is always there');
});

test('the page gets working stand-ins for localStorage and sessionStorage when the sandbox refuses them', () => {
  const out = sandboxedPage('', { bridge: false });
  const script = /<script>([\s\S]*?)<\/script>/.exec(out)![1];
  const refused = () => { throw new Error('SecurityError'); };
  const win: Record<string, unknown> = {};
  Object.defineProperty(win, 'localStorage', { get: refused, configurable: true });
  Object.defineProperty(win, 'sessionStorage', { get: refused, configurable: true });
  new Function('window', script)(win);
  for (const n of ['localStorage', 'sessionStorage']) {
    const s = win[n] as Storage;
    assert.equal(s.getItem('k'), null);
    s.setItem('k', 1 as unknown as string);
    assert.equal(s.getItem('k'), '1');
    assert.equal(s.length, 1);
    assert.equal(s.key(0), 'k');
    s.removeItem('k');
    assert.equal(s.length, 0);
  }
  // A storage the page may use is left alone.
  const mine = { length: 0 };
  const ok: Record<string, unknown> = { localStorage: mine, sessionStorage: mine };
  new Function('window', script)(ok);
  assert.equal(ok.localStorage, mine);
});

test('a questionnaire may post its answers and feedback, from any spelling of the URL, and nothing else', () => {
  const q = 'scope-v1';
  for (const u of ['/api/learning/feedback', 'null/api/learning/feedback', 'https://dashboard.example.ts.net/api/learning/feedback', 'api/learning/feedback']) {
    assert.equal(bridgeTarget(u, 'POST', q), '/api/learning/feedback', u);
  }
  assert.equal(bridgeTarget('/api/learning/feedback?x=1', 'post', q), '/api/learning/feedback?x=1');
  assert.equal(bridgeTarget('null/api/questionnaires/scope-v1/submit', 'POST', q), '/api/questionnaires/scope-v1/submit');
  assert.equal(bridgeTarget('/api/questionnaires/scope%2Dv1/submit', 'POST', q), '/api/questionnaires/scope-v1/submit');
  // Refused: another questionnaire, any other route, any other method, no questionnaire, a path that climbs out.
  assert.equal(bridgeTarget('/api/questionnaires/other-v1/submit', 'POST', q), null);
  assert.equal(bridgeTarget('/api/agents/gm/message', 'POST', q), null);
  assert.equal(bridgeTarget('/api/agents', 'GET', q), null);
  assert.equal(bridgeTarget('/api/learning/feedback', 'GET', q), null);
  assert.equal(bridgeTarget('/api/learning/feedback', 'POST', undefined), null);
  assert.equal(bridgeTarget('/api/learning/feedback/../../agents/gm/message', 'POST', q), null);
  assert.equal(bridgeTarget('/api/questionnaires/%E0%A4%A/submit', 'POST', q), null);
  assert.equal(bridgeTarget(42, 'POST', q), null);
});

test('the parent answers a bridged request: allowed ones are sent as JSON posts, refused ones never leave', async () => {
  const sent: Array<{ url: string; init: RequestInit }> = [];
  const doFetch = async (url: string, init: RequestInit) => {
    sent.push({ url, init });
    return new Response('{"ok":true}', { status: 200, headers: { 'Content-Type': 'application/json; charset=utf-8' } });
  };
  assert.equal(answerBridge({ type: 'other' }, 'q1', doFetch), null);
  assert.equal(answerBridge(null, 'q1', doFetch), null);
  const ok = await answerBridge({ type: BRIDGE, id: 3, url: 'null/api/questionnaires/q1/submit', method: 'POST', body: '{"a":1}' }, 'q1', doFetch);
  assert.deepEqual(ok, { type: `${BRIDGE}:reply`, id: 3, status: 200, body: '{"ok":true}', contentType: 'application/json; charset=utf-8' });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, '/api/questionnaires/q1/submit');
  assert.equal(sent[0].init.method, 'POST');
  assert.equal(sent[0].init.body, '{"a":1}');
  assert.deepEqual(sent[0].init.headers, { 'Content-Type': 'application/json' });
  const no = await answerBridge({ type: BRIDGE, id: 4, url: '/api/agents/gm/message', method: 'POST', body: '{}' }, 'q1', doFetch);
  assert.equal(no!.status, 403);
  assert.equal(no!.id, 4);
  assert.equal(sent.length, 1, 'a refused request is never sent');
  const bad = await answerBridge({ type: BRIDGE, id: 5, url: '/api/learning/feedback', method: 'POST', body: { a: 1 } }, 'q1', doFetch);
  assert.equal(bad!.status, 400);
  assert.equal(sent.length, 1);
  const down = await answerBridge({ type: BRIDGE, id: 6, url: '/api/learning/feedback', method: 'POST', body: '{}' }, 'q1',
    async () => { throw new Error('offline'); });
  assert.equal(down!.status, 502);
});

test('wiring: all four frames of agent HTML are sandboxed; only questionnaires get the bridge', () => {
  const q = src('pages/Questionnaires.tsx'), inbox = src('pages/Inbox.tsx'), extras = src('components/chat/ApprovalExtras.tsx');
  for (const [name, f] of [['Questionnaires', q], ['Inbox', inbox], ['ApprovalExtras', extras]] as const) {
    assert.doesNotMatch(f, /<iframe/, `${name} frames nothing itself`);
  }
  assert.equal((q.match(/<SandboxedHtml src=\{`\/api\/questionnaires\/\$\{q\.id\}\/html`\} questionnaireId=\{q\.id\}/g) ?? []).length, 2);
  assert.equal((inbox.match(/<SandboxedHtml src=\{`\/api\/questionnaires\/\$\{q\.id\}\/html`\} questionnaireId=\{q\.id\}/g) ?? []).length, 1);
  assert.match(extras, /<SandboxedHtml src=\{item\.onepager_url\} title="Proposal details"/);
  assert.doesNotMatch(extras, /questionnaireId/);
  const frame = src('components/kit/SandboxedHtml.tsx');
  assert.match(frame, /<iframe ref=\{ref\} sandbox=\{SANDBOX\} srcDoc=\{doc\}/);
  assert.match(frame, /if \(!win \|\| e\.source !== win\) return;/);
  assert.match(frame, /sandboxedPage\(text, \{ bridge: !!questionnaireId \}\)/);
  assert.doesNotMatch(frame, /allow-same-origin/);
});
