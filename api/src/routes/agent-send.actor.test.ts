/**
 * The web chat says a PERSON typed its message (X-Actor: operator:web), so the gateway counts it
 * toward the agent's last_user_message_at; the bare fleet token alone is automation.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const dir = mkdtempSync(join(tmpdir(), 'actor-'));
const tok = join(dir, 'token');
writeFileSync(tok, 'tok-value');
process.env.WATCH_GATEWAY_TOKEN_FILE = tok;
const { defaultAgentSendDeps: defaultDeps } = await import('./agent-send.js');

test('the web chat send names its sender as a person', async () => {
  const seen: any[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (url: any, init: any) => {
    seen.push({ url: String(url), headers: init?.headers });
    return new Response(JSON.stringify({ ok: true, delivered: true }), { status: 200 });
  }) as any;
  try {
    const r = await defaultDeps.gatewayInject('gm', 'hello');
    assert.equal(r.ok, true);
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(seen.length, 1);
  assert.match(seen[0].url, /\/agent-message$/);
  assert.equal(seen[0].headers['X-Actor'], 'operator:web');
});
