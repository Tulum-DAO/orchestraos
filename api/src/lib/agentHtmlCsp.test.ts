/**
 * Agent-written HTML (questionnaire pages, learning one-pagers) is served same-origin and framed by the
 * dashboard. Without a Content-Security-Policy a <script> in that HTML runs as the dashboard's origin
 * with the operator's session (Nod audit, 2026-11-25). These tests pin the policy the two routes send and
 * the inert baseline every other API response carries.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { AGENT_HTML_CSP, API_BASELINE_CSP, setAgentHtmlHeaders, baselineHeaders } from './agentHtmlCsp.js';

function directives(csp: string): Map<string, string> {
  const m = new Map<string, string>();
  for (const part of csp.split(';')) {
    const [name, ...rest] = part.trim().split(/\s+/);
    if (name) m.set(name, rest.join(' '));
  }
  return m;
}

test('the agent-HTML policy: no data out, no navigation, inline script, presentation allowed, opaque origin', () => {
  const d = directives(AGENT_HTML_CSP);
  assert.equal(d.get('default-src'), "'none'");
  assert.equal(d.get('script-src'), "'unsafe-inline'");
  assert.equal(d.get('style-src'), "'unsafe-inline' https://fonts.googleapis.com");
  assert.equal(d.get('img-src'), 'https: data:');
  assert.equal(d.get('font-src'), 'https://fonts.gstatic.com');
  assert.equal(d.get('connect-src'), "'none'");
  assert.equal(d.get('form-action'), "'none'");
  assert.equal(d.get('frame-ancestors'), "'self'");
  // The CSP sandbox makes the document an opaque origin even when a frame forgets its sandbox attribute.
  assert.equal(d.get('sandbox'), 'allow-scripts allow-forms');
  assert.ok(!/allow-same-origin/.test(AGENT_HTML_CSP), 'allow-same-origin would hand the dashboard origin back');
  // Presentation may reach out (fonts, pictures); nothing that carries data out may.
  for (const name of ['script-src', 'connect-src', 'form-action']) assert.ok(!/https?:/.test(d.get(name) || ''), `${name} names no host`);
  assert.equal(d.get('default-src'), "'none'", 'anything not named (frames, media, workers, objects) is off');
});

test('the baseline every API response carries is inert for JSON and refuses framing by other sites', () => {
  const d = directives(API_BASELINE_CSP);
  assert.equal(d.get('default-src'), "'none'");
  assert.equal(d.get('frame-ancestors'), "'self'");
  assert.equal(d.size, 2, 'the baseline says only what JSON needs; the HTML routes set their own');
});

async function get(app: express.Express, path: string) {
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

test('setAgentHtmlHeaders: the policy, nosniff and no referrer ride the HTML response; a route header beats the baseline', async () => {
  const app = express();
  app.use(baselineHeaders);
  app.get('/json', (_req, res) => { res.json({ ok: true }); });
  app.get('/page', (_req, res) => { setAgentHtmlHeaders(res); res.send('<html><script>fetch("/api/x")</script></html>'); });
  const j = await get(app, '/json');
  assert.equal(j.headers['content-security-policy'], API_BASELINE_CSP);
  assert.equal(j.headers['x-content-type-options'], 'nosniff');
  assert.deepEqual(JSON.parse(j.body), { ok: true });
  const p = await get(app, '/page');
  assert.equal(p.headers['content-security-policy'], AGENT_HTML_CSP);
  assert.equal(p.headers['x-content-type-options'], 'nosniff');
  assert.equal(p.headers['referrer-policy'], 'no-referrer');
  assert.match(p.headers['content-type'] || '', /text\/html/);
  assert.match(p.body, /fetch\("\/api\/x"\)/, 'the HTML itself is served as written; the browser enforces the policy');
});
