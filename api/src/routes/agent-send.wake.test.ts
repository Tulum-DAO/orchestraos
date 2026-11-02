/**
 * postAgentMessage (agent-send.ts): the reaction wake reaches the gateway with NO hold (no accepts:['held'], so a busy
 * pane answers 409 and nothing is stored) and NO force; the web send keeps its body exactly. A fake gateway on an
 * ephemeral port records what arrives.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/agent-send.wake.test.ts   (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type IncomingHttpHeaders } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'agent-send-wake-'));
const seen: { headers: IncomingHttpHeaders; body: any }[] = [];
let reply: { status: number; body: any } = { status: 200, body: { ok: true, delivered: true } };
const gw = createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', () => {
    seen.push({ headers: req.headers, body: JSON.parse(raw || '{}') });
    res.writeHead(reply.status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(reply.body));
  });
});
await new Promise<void>((r) => gw.listen(0, '127.0.0.1', () => r()));
process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${(gw.address() as any).port}`;
process.env.WATCH_GATEWAY_TOKEN_FILE = join(dir, 'gw-token');
writeFileSync(process.env.WATCH_GATEWAY_TOKEN_FILE, 'gw-tok\n');
process.env.ORCHESTRA_DIR = dir;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
after(() => { gw.close(); rmSync(dir, { recursive: true, force: true }); });

const { postAgentMessage, defaultAgentSendDeps } = await import('./agent-send.js');

test('the wake form: no accepts, no force, its own actor', async () => {
  seen.length = 0;
  reply = { status: 200, body: { ok: true, delivered: true } };
  const r = await postAgentMessage('pm-x', 'hello', { hold: false, actor: 'operator:reaction' });
  assert.deepEqual(seen[0].body, { session: 'pm-x', text: 'hello' });
  assert.equal(seen[0].headers['x-actor'], 'operator:reaction');
  assert.equal(seen[0].headers.authorization, 'Bearer gw-tok');
  assert.equal(r.ok, true);
  assert.equal(r.delivered, true);
});

test('the web send is unchanged: accepts held, operator:web, force only when asked', async () => {
  seen.length = 0;
  reply = { status: 200, body: { ok: true, delivered: true } };
  await defaultAgentSendDeps.gatewayInject('pm-x', 'hi');
  await defaultAgentSendDeps.gatewayInject('pm-x', 'hi', true);
  assert.deepEqual(seen[0].body, { session: 'pm-x', text: 'hi', accepts: ['held'] });
  assert.deepEqual(seen[1].body, { session: 'pm-x', text: 'hi', accepts: ['held'], force: true });
  assert.equal(seen[0].headers['x-actor'], 'operator:web');
});

test('a busy pane (composer text) comes back as a 409 with the draft, nothing held', async () => {
  seen.length = 0;
  reply = { status: 409, body: { ok: false, busy: true, reason: 'busy', state: 'idle', composer_text: 'draft' } };
  const r = await postAgentMessage('pm-x', 'hello', { hold: false, actor: 'operator:reaction' });
  assert.equal(r.httpStatus, 409);
  assert.equal(r.ok, false);
  assert.equal(r.composer_text, 'draft');
  assert.equal(r.held, false);
});
