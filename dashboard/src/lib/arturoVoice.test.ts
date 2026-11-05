import { test } from 'node:test';
import assert from 'node:assert/strict';
import { voiceRowValue, pickVoice, canChangeVoice, type VoiceVendor } from './arturoVoice.ts';
import { setInstallRole } from './myRole.ts';

const ON: VoiceVendor = { id: 'openai', title: 'GPT-Live', available: true };
const OFF: VoiceVendor = { id: 'openai', title: 'GPT-Live', available: false, reason: 'missing OPENAI_API_KEY' };

test('Voice row value: the engine and the voice the next call uses', () => {
  assert.equal(voiceRowValue(ON, { voice_id: 'marin', title: 'Marin' }, true), 'GPT-Live · Marin');
  assert.equal(voiceRowValue(ON, { voice_id: 'cedar', title: null }, true), 'GPT-Live · Cedar');
  assert.equal(voiceRowValue(ON, null, true), 'GPT-Live');                      // voice not readable
  assert.equal(voiceRowValue(OFF, null, false), 'Not set up');
  assert.equal(voiceRowValue(null, null, true), 'GPT-Live');                    // only the health check answered
  assert.equal(voiceRowValue(null, null, false), 'Not set up');
  assert.equal(voiceRowValue(null, null, null), '');                            // still loading
});

test('picking a voice shows it at once and keeps it when the server accepts', async () => {
  const shown: { current: string | null; error: string | null }[] = [];
  const ok = await pickVoice('marin', 'cedar', async () => ({ ok: true }), (s) => shown.push(s));
  assert.equal(ok, true);
  assert.deepEqual(shown, [{ current: 'cedar', error: null }]);
});

test('a refused pick rolls back to the old voice with the server\'s reason', async () => {
  const shown: { current: string | null; error: string | null }[] = [];
  let during: string | null = null;
  const ok = await pickVoice('marin', 'nova', async () => { during = shown.at(-1)!.current; return { ok: false, error: "'nova' is not an openai voice" }; }, (s) => shown.push(s));
  assert.equal(ok, false);
  assert.equal(during, 'nova');                                                  // the check moved before the answer
  assert.deepEqual(shown.at(-1), { current: 'marin', error: "Couldn't change the voice: 'nova' is not an openai voice" });
});

test('only an admin may change the voice; a member (the default until the server says admin) may not', () => {
  assert.equal(canChangeVoice(), false);
  setInstallRole('admin');
  try { assert.equal(canChangeVoice(), true); } finally { setInstallRole('member'); }
});

test('the voice row splits: the provider is the server\'s (greyed), the voice is the agent\'s own', async () => {
  const { voiceProviderValue, voiceNameValue, PROVIDER_SCOPE_NOTE } = await import('./arturoVoice.ts');
  const ON: VoiceVendor = { id: 'openai', title: 'GPT-Live', available: true };
  const OFF: VoiceVendor = { id: 'openai', title: 'GPT-Live', available: false };
  assert.equal(PROVIDER_SCOPE_NOTE, 'Set for the whole server');
  assert.equal(voiceProviderValue(ON, true), 'GPT-Live');
  assert.equal(voiceProviderValue(OFF, false), 'Not set up');
  assert.equal(voiceProviderValue(null, null), '');
  assert.equal(voiceNameValue(ON, { voice_id: 'marin', title: 'Marin' }, true), 'Marin');
  assert.equal(voiceNameValue(ON, { voice_id: 'cedar', title: null }, true), 'Cedar');
  assert.equal(voiceNameValue(OFF, { voice_id: 'marin', title: 'Marin' }, false), '', 'no provider: no voice to show');
});
