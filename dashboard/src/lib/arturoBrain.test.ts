/**
 * RED-first: the web half of per-turn brain routing (DEC-1790669162399904 spec v4 §1.5-1.6).
 * Pure functions only. No DOM; run with `npm test` (tsx + node:test).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { ARTURO_BRAIN_KEY, loadArturoBrain, saveArturoBrain, brainFromThread, toWireBrain, describeTurnError } from './arturoBrain';
import { buildTextBody, contextLine } from './arturo';

const here = dirname(fileURLToPath(import.meta.url));

function memStorage(seed: Record<string, string> = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k: string) => (m.has(k) ? m.get(k)! : null), setItem: (k: string, v: string) => void m.set(k, v),
           removeItem: (k: string) => void m.delete(k), dump: () => Object.fromEntries(m) };
}

// ---- the Arturo-scoped, versioned selection ------------------------------------------------

test('nothing chosen yet reads as null (the default brain)', () => {
  assert.equal(loadArturoBrain(memStorage()), null);
});

test('the legacy shared model selection is NEVER read', () => {
  // The old picker persisted choices that did nothing; honouring them now would silently reroute.
  const s = memStorage({ 'orchestra.modelSelection': JSON.stringify({ providerId: 'codex', modelId: 'gpt-5.6-terra' }) });
  assert.equal(loadArturoBrain(s), null);
});

test('a saved choice round-trips under the versioned key', () => {
  const s = memStorage();
  saveArturoBrain(s, { provider: 'gemini', model: 'gemini-3.7-flash-high', label: 'Gemini 3.7 Flash (High)' });
  assert.equal(ARTURO_BRAIN_KEY, 'orchestra.arturoBrain.v1');
  assert.deepEqual(loadArturoBrain(s), { provider: 'gemini', model: 'gemini-3.7-flash-high', label: 'Gemini 3.7 Flash (High)' });
});

test('clearing goes back to the default brain', () => {
  const s = memStorage();
  saveArturoBrain(s, { provider: 'claude', model: '', label: 'Claude' });
  saveArturoBrain(s, null);
  assert.equal(loadArturoBrain(s), null);
});

test('a corrupt or wrong-version value reads as null, never throws', () => {
  for (const v of ['{', '[]', JSON.stringify({ v: 2, provider: 'claude', model: '' }), JSON.stringify({ v: 1, model: 'x' })]) {
    assert.equal(loadArturoBrain(memStorage({ 'orchestra.arturoBrain.v1': v })), null, v);
  }
});

test('storage that throws (private mode) reads as null and saving is a no-op', () => {
  const broken = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); }, removeItem: () => {} };
  assert.equal(loadArturoBrain(broken as any), null);
  assert.doesNotThrow(() => saveArturoBrain(broken as any, { provider: 'claude', model: '', label: 'Claude' }));
});

// ---- the wire -------------------------------------------------------------------------------

test('the wire brain is {provider, model} only, and absent when nothing is chosen', () => {
  assert.deepEqual(toWireBrain({ provider: 'codex', model: 'gpt-5.6-terra', label: 'x' }), { provider: 'codex', model: 'gpt-5.6-terra' });
  assert.equal(toWireBrain(null), undefined);
});

test('the text body sends context as a FIELD and never prepends the context line', () => {
  const ctx = { route: '/agent', entityKind: 'agent', entityId: 'gm' };
  const body = buildTextBody('what is this', 'c1', ctx, { provider: 'claude', model: '' });
  assert.deepEqual(body, { text: 'what is this', conversation_id: 'c1', context: ctx, brain: { provider: 'claude', model: '' } });
});

test('a plain turn body is exactly {text, conversation_id}', () => {
  assert.deepEqual(buildTextBody('hi', 'c1', null, undefined), { text: 'hi', conversation_id: 'c1' });
});

test('contextLine matches the proxy byte for byte (shared fixture)', () => {
  const cases = JSON.parse(readFileSync(join(here, '../../../services/arturo/fixtures/context_line_cases.json'), 'utf-8'));
  assert.ok(cases.length >= 4);
  for (const c of cases) assert.equal(contextLine(c.ctx), c.line);
});

// ---- reopening a thread restores its brain --------------------------------------------------

test("a thread's last_brain becomes the selection; a default thread clears it", () => {
  const labels = { 'claude:claude-sonnet-5': 'Sonnet 5' };
  assert.deepEqual(brainFromThread({ last_brain: { provider: 'claude', model: 'claude-sonnet-5' } }, labels),
                   { provider: 'claude', model: 'claude-sonnet-5', label: 'Sonnet 5' });
  assert.equal(brainFromThread({ last_brain: null }, labels), null);
  assert.equal(brainFromThread(null, labels), null);
  assert.deepEqual(brainFromThread({ last_brain: { provider: 'codex', model: '' } }, labels),
                   { provider: 'codex', model: '', label: 'codex' });
  // an unlabelled model shows its own id, not just the provider
  assert.deepEqual(brainFromThread({ last_brain: { provider: 'codex', model: 'gpt-5.6-luna' } }, labels),
                   { provider: 'codex', model: 'gpt-5.6-luna', label: 'gpt-5.6-luna' });
});

// ---- honest error states --------------------------------------------------------------------

test('409 provider_unavailable says which provider and offers to connect it', () => {
  const e = describeTurnError({ ok: false, status: 409, error: 'provider_unavailable', provider: 'codex', reason: 'its CLI is installed but not logged in' } as any);
  assert.equal(e.action, 'connect');
  assert.equal(e.provider, 'codex');
  assert.match(e.message, /Codex/);
  assert.match(e.message, /not logged in/);
});

test('502 brain_failed names the brain and lists actions that already ran', () => {
  const e = describeTurnError({ ok: false, status: 502, error: 'brain_failed', provider: 'claude', model: 'claude-sonnet-5', tools_called: ['send_telegram', 'spawn_agent'] } as any);
  assert.equal(e.action, 'retry');
  assert.match(e.message, /Claude/);
  assert.match(e.message, /send_telegram/);
  assert.match(e.message, /already ran/);
});

test('502 with no tools run does not claim anything ran', () => {
  const e = describeTurnError({ ok: false, status: 502, error: 'empty_response', provider: 'gemini', tools_called: [] } as any);
  assert.doesNotMatch(e.message, /already ran/);
  assert.match(e.message, /Gemini/);
});

test('400 unknown_model offers to pick another model', () => {
  const e = describeTurnError({ ok: false, status: 400, error: 'unknown_model' } as any);
  assert.equal(e.action, 'pick');
});

test('anything else is null (the existing error handling stays in charge)', () => {
  assert.equal(describeTurnError({ ok: false, status: 504, error: 'timeout' } as any), null);
  assert.equal(describeTurnError({ ok: true } as any), null);
});
