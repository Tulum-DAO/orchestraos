import { test } from 'node:test';
import assert from 'node:assert/strict';
import { styleOptions, effectiveStyle, defaultStyle, chooseStyle, loadPrefs, useAgentPrefsStore } from './agentPrefs.ts';

const reset = () => useAgentPrefsStore.setState({ styles: {}, errors: {} });

test('which styles each agent can show today, and why not', () => {
  const agent = Object.fromEntries(styleOptions('dev-demo').map((o) => [o.id, o]));
  assert.equal(agent.bubbles.available, true);
  assert.equal(agent.dev.available, true);
  assert.equal(agent.modern.available, false);
  assert.match(agent.modern.reason ?? '', /shared thread/);
  const arturo = Object.fromEntries(styleOptions('arturo').map((o) => [o.id, o]));
  assert.equal(arturo.modern.available, true);
  assert.match(arturo.dev.reason ?? '', /no terminal/);
});

test('an agent is drawn in its saved style only when it can show it, else its default', () => {
  assert.equal(defaultStyle('arturo'), 'modern');
  assert.equal(defaultStyle('dev-demo'), 'bubbles');
  assert.equal(effectiveStyle('dev-demo', 'dev'), 'dev');
  assert.equal(effectiveStyle('dev-demo', 'modern'), 'bubbles');     // not yet: its default
  assert.equal(effectiveStyle('arturo', 'dev'), 'modern');
  assert.equal(effectiveStyle('pm-demo', null), 'bubbles');
});

const ok = (body: unknown, status = 200) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

test('a choice shows at once and is saved on the server', async () => {
  reset();
  let sent: { url: string; body: string } | null = null;
  const fetchFn = (async (url: string, init: RequestInit) => {
    sent = { url, body: String(init.body) };
    return new Response(JSON.stringify({ ok: true, style: 'dev' }));
  }) as unknown as typeof fetch;
  assert.equal(await chooseStyle('dev-demo', 'dev', fetchFn), true);
  assert.equal(useAgentPrefsStore.getState().styles['dev-demo'], 'dev');
  assert.deepEqual(sent, { url: '/api/agents/dev-demo/prefs', body: '{"style":"dev"}' });
});

test('a refused choice puts the old style back, with the reason', async () => {
  reset();
  useAgentPrefsStore.getState().set('dev-demo', 'bubbles');
  assert.equal(await chooseStyle('dev-demo', 'dev', ok({ ok: false, error: 'no identity' }, 401)), false);
  assert.equal(useAgentPrefsStore.getState().styles['dev-demo'], 'bubbles');
  assert.match(useAgentPrefsStore.getState().errors['dev-demo'] ?? '', /no identity/);
});

test('the saved style is read per agent; offline keeps the last answer', async () => {
  reset();
  await loadPrefs('pm-demo', ok({ ok: true, style: 'dev' }));
  assert.equal(useAgentPrefsStore.getState().styles['pm-demo'], 'dev');
  await loadPrefs('pm-demo', (async () => { throw new Error('offline'); }) as unknown as typeof fetch);
  assert.equal(useAgentPrefsStore.getState().styles['pm-demo'], 'dev');
});
