import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAgentState } from '../components/avatar/avatarCore.ts';
import { applySavedLooks, pickLook, resetToDefault, showsLookCard, readDismissed, writeDismissed, canChangeLook } from './avatarLooks.ts';
import { avatarStateFor, arturoState, arturoProvider, stepPatch, STEP_FRESH_MS } from './avatarStatus.ts';

const DEF = { shape: 'star', color: 'ink', eyeColor: 'auto' };

test('chosen: an agent with no saved record is not chosen; a saved one is', () => {
  const hub = createAgentState();
  hub.set('arturo', { ...DEF, chosen: false });
  hub.set('gm', { ...DEF, chosen: false });
  applySavedLooks(hub, { gm: { shape: 'egg', color: '#1E6BE6', eyeColor: 'auto' } });
  assert.equal(hub.get('arturo')!.chosen, false);
  assert.equal(hub.get('gm')!.chosen, true);
  assert.equal(hub.get('gm')!.shape, 'egg');
  assert.equal(hub.model('gm').shape, 'egg');                 // the shared model follows
});

test('a pick shows at once and is chosen; the server agrees, it stays', async () => {
  const hub = createAgentState();
  hub.set('arturo', { ...DEF, chosen: false });
  let during: unknown;
  const err = await pickLook(hub, 'arturo', { shape: 'heart' }, async () => { during = { ...hub.get('arturo') }; return { ok: true }; });
  assert.equal(err, null);
  assert.equal((during as { shape: string }).shape, 'heart');
  assert.equal(hub.get('arturo')!.chosen, true);
});

test('a refused pick is put back, not chosen, with the server\'s reason', async () => {
  const hub = createAgentState();
  hub.set('arturo', { ...DEF, chosen: false });
  const err = await pickLook(hub, 'arturo', { color: '#E5243B' }, async () => ({ ok: false, error: "Only an admin can change an agent's look" }));
  assert.equal(err, "Couldn't save the look: Only an admin can change an agent's look");
  assert.equal(hub.get('arturo')!.color, 'ink');
  assert.equal(hub.get('arturo')!.chosen, false);
});

test('reset: back to the default and not chosen; a refusal puts the saved look back', async () => {
  const hub = createAgentState();
  hub.set('arturo', { shape: 'heart', color: '#E5243B', eyeColor: 'auto', chosen: true });
  assert.equal(await resetToDefault(hub, 'arturo', DEF, async () => ({ ok: true })), null);
  assert.deepEqual([hub.get('arturo')!.shape, hub.get('arturo')!.color, hub.get('arturo')!.chosen], ['star', 'ink', false]);
  hub.set('arturo', { shape: 'heart', color: '#E5243B', chosen: true });
  const err = await resetToDefault(hub, 'arturo', DEF, async () => ({ ok: false, error: 'HTTP 502' }));
  assert.ok(err);
  assert.deepEqual([hub.get('arturo')!.shape, hub.get('arturo')!.chosen], ['heart', true]);
});

test('the "Pick a look" card: unchosen, can save, not dismissed on this device', () => {
  assert.equal(showsLookCard({ chosen: false, canSave: true, dismissed: false }), true);
  assert.equal(showsLookCard({ chosen: true, canSave: true, dismissed: false }), false);    // saved by anyone: gone
  assert.equal(showsLookCard({ chosen: false, canSave: false, dismissed: false }), false);  // cannot save: not offered
  assert.equal(showsLookCard({ chosen: false, canSave: true, dismissed: true }), false);
  // anyone may change a look for now, whatever their role
  assert.equal(canChangeLook(), true);
  const m = new Map<string, string>();
  const st = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } };
  assert.equal(readDismissed(st, 'arturo'), false);
  writeDismissed(st, 'arturo');
  assert.equal(readDismissed(st, 'arturo'), true);
  assert.equal(readDismissed(st, 'gm'), false);                                             // per agent
  assert.equal(readDismissed({ getItem: () => { throw new Error('blocked'); } }, 'arturo'), false);
});

test('a seat\'s avatar state from its detector status', () => {
  assert.equal(avatarStateFor('working', false), 'working');
  assert.equal(avatarStateFor('stalled', false), 'working');
  assert.equal(avatarStateFor('waiting', false), 'needs_input');
  assert.equal(avatarStateFor('idle', true), 'needs_input');                                // a pending menu
  assert.equal(avatarStateFor('crashed', false), 'failed');
  assert.equal(avatarStateFor('idle', false), 'idle');
  assert.equal(avatarStateFor('offline', false), 'idle');
  assert.equal(avatarStateFor('working', true, true), 'idle');                              // a stale feed claims nothing
});

test('Arturo works while a turn or a call is live, or while its steps are fresh', () => {
  const now = 100_000;
  assert.equal(arturoState({ busy: true, inCall: false, now }), 'working');
  assert.equal(arturoState({ busy: false, inCall: true, now }), 'working');
  assert.equal(arturoState({ busy: false, inCall: false, blurb: 'Reading a file', blurbAt: now - 1000, now }), 'working');
  assert.equal(arturoState({ busy: false, inCall: false, blurb: 'Reading a file', blurbAt: now - STEP_FRESH_MS - 1, now }), 'idle');
  assert.equal(arturoState({ busy: false, inCall: false, blurb: '', blurbAt: now, now }), 'idle');
});

test('the step reducer: a frame becomes the seat\'s blurb; a malformed one is ignored', () => {
  assert.deepEqual(stepPatch({ seat: 'arturo', text: 'Reading a file', ts: 1 }, 5000), { seat: 'arturo', blurb: 'Reading a file', blurbAt: 5000 });
  assert.deepEqual(stepPatch({ seat: 'arturo', text: '', ts: 1 }, 5000), { seat: 'arturo', blurb: '', blurbAt: 5000 });   // cleared
  assert.equal(stepPatch({ text: 'x' }, 1), null);
  assert.equal(stepPatch({ seat: 'a', text: 3 }, 1), null);
  assert.equal(stepPatch(null, 1), null);
  assert.equal(stepPatch({ seat: 'a', text: 'y'.repeat(400) }, 1)!.blurb.length, 160);
});

test('one picker: the drawer\'s Avatar page and the "Pick a look" popover both render LookPicker', async () => {
  const { readFileSync } = await import('node:fs');
  const panel = readFileSync(new URL('../components/arturo/ArturoPanel.tsx', import.meta.url), 'utf-8');
  const picker = readFileSync(new URL('../components/avatar/LookPicker.tsx', import.meta.url), 'utf-8');
  const home = readFileSync(new URL('../pages/ArturoHome.tsx', import.meta.url), 'utf-8');
  assert.match(panel, /page === 'avatar' \? <LookPicker agentId=\{agentId\}/);   // the panel's agent: Arturo or any other
  assert.match(picker, /export function LookPickerPopover[\s\S]*<LookPicker agentId=\{agentId\}/);
  assert.match(home, /<LookPickerPopover agentId=\{ARTURO_ID\}/);
  assert.equal((picker.match(/export function \w+/g) || []).length, 2);   // no second picker hiding in there
});

test("Arturo's badge is its actual brain: a picked brain first, else the service's runtime or API provider, else none", () => {
  assert.equal(arturoProvider({ kind: 'runtime', runtime: 'claude', cli: 'claude' }), 'claude');
  assert.equal(arturoProvider({ kind: 'runtime', cli: 'codex' }), 'codex');
  assert.equal(arturoProvider({ kind: 'api', provider: 'anthropic' }), 'anthropic');
  assert.equal(arturoProvider({ kind: 'none' }), null);
  assert.equal(arturoProvider(null), null);
  assert.equal(arturoProvider({ kind: 'runtime', runtime: 'claude' }, { provider: 'gemini' }), 'gemini');
});

test('a reload draws saved looks from the first frame; the server then corrects this browser\'s copy', async () => {
  const { applyCachedLooks, readCachedLooks, writeCachedLooks, LOOKS_CACHE_KEY } = await import('./avatarLooks.ts');
  const m = new Map<string, string>();
  const s = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } };
  const hub = createAgentState();
  applyCachedLooks(hub, s);                                   // nothing remembered: nothing applied
  assert.equal(hub.get('arturo'), undefined);
  writeCachedLooks({ arturo: { shape: 'flower', color: '#1E6BE6', eyeColor: 'auto' } }, s);
  applyCachedLooks(hub, s);
  assert.equal(hub.get('arturo')?.shape, 'flower');
  assert.equal(hub.get('arturo')?.color, '#1E6BE6');
  assert.equal(hub.get('arturo')?.chosen, true);
  m.set(LOOKS_CACHE_KEY, 'not json');
  assert.deepEqual(readCachedLooks(s), {});                   // a bad copy is ignored, never thrown
});
