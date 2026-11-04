/**
 * Settings › Voice split by scope (settings spec §4.3, 2026-11-03): the person's own page (Voice: the mic button and
 * its colour; this device) in the OpenBot layout, and the install's rows under Server › Voice &
 * calls (provider, default voice, new agents take calls, daily limit; Everyone, admin) with the provider's options
 * each saying their one line. The pages import CSS, so they are read as source; ChoiceList is rendered.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { providerOptions, UNAVAILABLE, VOICE_INTRO, MIC_DESC, COLOUR_DESC } from './voicePage.ts';
import { visibleSections, settingsSectionOf, sectionLabel, settingsPath } from './settingsRoute.ts';
import { ChoiceList } from '../components/settings/ChoiceList.tsx';
import { choiceListTarget } from '../components/settings/settingsState.ts';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');
(globalThis as { React?: typeof React }).React = React;
const personal = () => read('components/settings/sections/VoiceSection.tsx');
const server = () => read('components/settings/sections/ServerVoiceSection.tsx');
const order = (src: string, marks: string[]) => marks.map((m) => { const i = src.indexOf(m); assert.ok(i >= 0, `missing ${m}`); return i; });

test('Voice is the personal category (same id and path as before); Voice & calls is the install\'s, under Server, before Time zone', () => {
  assert.equal(sectionLabel('voice'), 'Voice');
  assert.equal(settingsSectionOf('/settings/voice'), 'voice');
  assert.equal(settingsPath('server-voice'), '/settings/server-voice');
  assert.equal(settingsSectionOf('/settings/server-voice'), 'server-voice');
  assert.equal(sectionLabel('server-voice'), 'Voice & calls');
  const vis = visibleSections();
  const ids = vis.map((s) => s.id);
  assert.ok(!vis.find((s) => s.id === 'voice')!.group, 'Voice is personal, not under Server');
  assert.equal(vis.find((s) => s.id === 'server-voice')!.group, 'server');
  assert.deepEqual(ids.slice(-2), ['server-voice', 'server']);
  const shell = read('components/settings/SettingsShell.tsx');
  assert.match(shell, /case 'voice': return <VoiceSection \/>;/);
  assert.match(shell, /case 'server-voice': return <ServerVoiceSection \/>;/);
});

test('the personal page: intro, Dictation (Microphone, Voice colour); OpenBot cards, no scope tags; no Call transcript (nothing reads it)', () => {
  const p = personal();
  assert.match(p, /<PageIntro>\{VOICE_INTRO\}<\/PageIntro>/);
  assert.equal(VOICE_INTRO, 'How you talk to agents, on this device.');
  const at = order(p, ['<Card title="Dictation">', '<MicRow />', '<VoiceColourRow />']);
  assert.doesNotMatch(p, /<Card title="Calls">|TranscriptRow|voice\.transcriptMode|setTranscriptMode|'transcript'/);
  assert.deepEqual([...at].sort((a, b) => a - b), at);
  assert.doesNotMatch(p, /<Group /);
  // nothing of the install's on it
  for (const k of ['voice.provider', 'voice.defaultVoice', 'voice.newAgentsTakeCalls', 'voice.dailyLimitMin', 'ServerRows']) assert.doesNotMatch(p, new RegExp(k.replace('.', '\\.')));
  // every row says its scope in words, and has its description
  for (const d of [MIC_DESC, COLOUR_DESC]) assert.match(d, /On this device\.$/);
  for (const [id, d] of [['voice.micMode', 'MIC_DESC'], ['voice.accentVoice', 'COLOUR_DESC']]) {
    assert.match(p, new RegExp(`id="${id.replace('.', '\\.')}" label="[^"]+" description=\\{${d}\\}`), id);
  }
});

test('the personal rows keep their store and effect: the same setters, the same values, still read by the mic button', () => {
  const p = personal();
  assert.match(p, /useAgentSettings\(\)/);
  for (const m of ["s.setMicMode(v)", "s.setAccentVoice(e.target.value)", "value: 'hold' as const, label: 'Hold to talk'",
    "value: 'toggle' as const, label: 'Tap to talk'"]) {
    assert.ok(p.includes(m), m);
  }
  const store = read('stores/agentSettings.ts');
  assert.match(store, /const STORAGE_KEY = 'orchestra\.agentSettings';/);
  // the transcript key stays in the store as it was (no migration); only the row is gone
  assert.match(store, /transcriptMode: 'all-at-once'/);
  const vc = read('components/agent/VoiceControls.tsx');
  assert.match(vc, /settings\.micMode === 'hold'/);
  assert.match(vc, /bg-\[var\(--accent-voice\)\]/);
});

test('Server › Voice & calls: the four install rows, Everyone and admin-locked as before (the same server rows and hook)', () => {
  const s = server();
  assert.match(s, /<Group title="Voice for everyone" scope="everyone">/);
  const at = order(s, ['<ProviderRow />', '<DefaultVoiceRow />', 'k="voice.newAgentsTakeCalls"', 'k="voice.dailyLimitMin"']);
  assert.deepEqual([...at].sort((a, b) => a - b), at);
  assert.match(s, /<ServerSwitchRow k="voice\.newAgentsTakeCalls" label="New agents take calls"/);
  assert.match(s, /<ServerNumberRow k="voice\.dailyLimitMin" label="Daily voice limit" suffix="min" min=\{1\} max=\{1440\}/);
  assert.match(s, /<ServerSelectRow k="voice\.defaultVoice" label="Default voice"/);
  // the provider row: the same hook (lock, revert, ✓ Saved), its note, detail and Changed by, and read-only when not editable
  assert.match(s, /useServerSetting\(PROVIDER\)/);
  assert.match(s, /locked=\{locked\}/);
  assert.match(s, /changedBy=\{row\?\.scope === 'everyone' \? row\.changedBy : null\}/);
  assert.match(s, /disabled=\{!row\.editable\}/);
  assert.match(s, /onChange=\{\(v\) => void save\(v\)\}/);
});

test('provider options: each with the voice service\'s one line; one the install can\'t run is greyed and says so', () => {
  assert.deepEqual(providerOptions([
    { value: 'openai', label: 'GPT-Live', available: true, description: "OpenAI's live voice: listens while it talks" },
    { value: 'hume', label: 'Hume', available: false, reason: UNAVAILABLE, description: 'Expressive voice from Hume' },
    { value: 'elevenlabs', label: 'ElevenLabs', available: true },
    { value: 'x', label: 'X', available: false, description: '  ' },
    { value: 'y', label: 'Y', available: false, description: 'Ends with a stop.' },
    { value: 'z', label: 'Z', available: true, description: 'Kept as is.' },
  ]), [
    { value: 'openai', label: 'GPT-Live', description: "OpenAI's live voice: listens while it talks", disabled: false },
    { value: 'hume', label: 'Hume', description: 'Expressive voice from Hume. Not available on this install.', disabled: true },
    { value: 'elevenlabs', label: 'ElevenLabs', description: null, disabled: false },
    { value: 'x', label: 'X', description: 'Not available on this install.', disabled: true },
    { value: 'y', label: 'Y', description: 'Ends with a stop. Not available on this install.', disabled: true },
    { value: 'z', label: 'Z', description: 'Kept as is', disabled: false },
  ]);
  assert.deepEqual(providerOptions(undefined), []);
});

test('ChoiceList renders a radio group: name and line per option, the one in use checked, one Tab stop, disabled greyed', () => {
  const html = renderToStaticMarkup(React.createElement(ChoiceList, {
    labelledBy: 'setting-voice.provider', value: 'openai', onChange: () => {},
    options: providerOptions([
      { value: 'elevenlabs', label: 'ElevenLabs', available: true, description: 'Natural voice from ElevenLabs' },
      { value: 'hume', label: 'Hume', available: false, description: 'Expressive voice from Hume' },
      { value: 'openai', label: 'GPT-Live', available: true, description: "OpenAI's live voice: listens while it talks" },
    ]),
  }));
  assert.match(html, /role="radiogroup" aria-labelledby="setting-voice\.provider"/);
  assert.equal((html.match(/role="radio"/g) ?? []).length, 3);
  assert.equal((html.match(/aria-checked="true"/g) ?? []).length, 1);
  assert.match(html, /aria-checked="true"[^>]*data-choice="openai" tabindex="0"/);
  assert.equal((html.match(/tabindex="0"/g) ?? []).length, 1);
  assert.match(html, /disabled=""[^>]*data-choice="hume"/);
  assert.match(html, /Natural voice from ElevenLabs/);
  assert.match(html, /Expressive voice from Hume\. Not available on this install\./);
  assert.match(html, /id="setting-voice\.provider-opt-openai"[^>]*>OpenAI&#x27;s live voice: listens while it talks/);
  assert.match(html, /aria-describedby="setting-voice\.provider-opt-openai"/);
  // read-only for a member: every option disabled, the one in use still checked
  const ro = renderToStaticMarkup(React.createElement(ChoiceList, {
    labelledBy: 'l', value: 'a', disabled: true, onChange: () => {}, options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }],
  }));
  assert.equal((ro.match(/disabled=""/g) ?? []).length, 2);
  assert.match(ro, /aria-checked="true"/);
});

test('ChoiceList keys: arrows wrap over the options that can be picked, Home / End; anything else does nothing', () => {
  const e = ['elevenlabs', 'openai'];
  assert.equal(choiceListTarget(e, 'elevenlabs', 'ArrowDown'), 'openai');
  assert.equal(choiceListTarget(e, 'openai', 'ArrowDown'), 'elevenlabs');
  assert.equal(choiceListTarget(e, 'elevenlabs', 'ArrowUp'), 'openai');
  assert.equal(choiceListTarget(e, 'openai', 'Home'), 'elevenlabs');
  assert.equal(choiceListTarget(e, 'elevenlabs', 'End'), 'openai');
  assert.equal(choiceListTarget(e, 'openai', 'Enter'), undefined);
  assert.equal(choiceListTarget(e, 'hume', 'ArrowDown'), undefined);
});

test('deep links: the agent panel\'s provider rows open Server › Voice & calls; Server settings the install\'s category; nothing opens the old voice rows', () => {
  const p = read('components/arturo/ArturoPanel.tsx');
  assert.equal((p.match(/openSettings\('server-voice'\)/g) ?? []).length, 2);
  assert.doesNotMatch(p, /openSettings\('voice'\)/);
  assert.match(p, /openSettings\('server'\)/);
  assert.doesNotMatch(p, /Settings › Voice & calls/);
});

test('ChoiceList: arrows move focus only (every pick saves an install-wide row); Space / Enter or a click picks', () => {
  const c = read('components/settings/ChoiceList.tsx');
  const onKey = c.slice(c.indexOf('const onKey'), c.indexOf('return ('));
  assert.match(onKey, /\.focus\(\)/);
  assert.doesNotMatch(onKey, /onChange/);
  assert.match(c, /onClick=\{\(\) => \{ if \(!on\) onChange\(o\.value\); \}\}/);
});

test('the provider row with no list from the voice service says so instead of an empty box', () => {
  const s = server();
  assert.match(s, /!options\.length \? <span className="arturo-panel-rowvalue" data-provider-none>Couldn't load providers<\/span>/);
});

test('Settings › Voice points to Server › Voice & calls for the provider and limits, and opens it', () => {
  const p = personal();
  assert.match(p, /\{SERVER_VOICE_POINTER\}/);
  assert.match(p, /onClick=\{\(\) => openSettings\('server-voice'\)\}/);
  assert.match(p, /\{serverGroupLabel\(\)\} › Voice &amp; calls/);
});
