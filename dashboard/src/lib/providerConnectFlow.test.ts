import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sheetView, flowSteps, countdown, errorWords, looksLikeGoogleCode, isManagedProvider, CODEX_SECURITY_URL, anchorExpiry, connectButtonLabel, codeSubmitWords, sheetKeyAction, copyWithTextarea, type ConnectState } from './providerConnectFlow.ts';

const codex = { id: 'codex', label: 'Codex', installed: false };
const grok = { id: 'grok', label: 'Grok', installed: true };
const gemini = { id: 'gemini', label: 'Gemini', installed: true };
const st = (over: Partial<ConnectState>): ConnectState => ({ provider: 'codex', phase: 'idle', ...over });

test('a member never gets the button, whatever the state', () => {
  assert.equal(sheetView(null, codex, false).kind, 'member');
  assert.equal(sheetView(st({ phase: 'waiting', url: 'u', code: 'c' }), codex, false).primary, null);
});

test('nothing running: Install <Provider> when missing, Sign in to <Provider> when installed; Codex names its prerequisite', () => {
  const v = sheetView(null, codex, true);
  assert.deepEqual(v.primary, { label: 'Install Codex', action: 'start' });
  assert.equal(v.prerequisite?.href, CODEX_SECURITY_URL);
  assert.deepEqual(sheetView(st({ phase: 'idle' }), grok, true).primary, { label: 'Sign in to Grok', action: 'start' });
  assert.equal(sheetView(null, grok, true).prerequisite, null);
});

test('the step list follows the server: install shown only when it is needed', () => {
  assert.deepEqual(flowSteps(st({ phase: 'installing', install: 'needed' }), 'Codex'), [
    { label: 'Check this server', state: 'done' }, { label: 'Install Codex', state: 'active' },
    { label: 'Start sign-in', state: 'todo' }, { label: 'Sign in', state: 'todo' },
  ]);
  assert.deepEqual(flowSteps(st({ phase: 'waiting', install: 'skipped' }), 'Grok').map((s) => s.state), ['done', 'done', 'active']);
  assert.deepEqual(flowSteps(st({ phase: 'signed_in', install: 'skipped' }), 'Grok').map((s) => s.state), ['done', 'done', 'done']);
  assert.deepEqual(flowSteps(st({ phase: 'error', install: 'needed', error: { code: 'install_failed' } }), 'Codex').map((s) => s.state),
    ['done', 'failed', 'todo', 'todo']);
  assert.equal(sheetView(st({ phase: 'installing', install: 'needed' }), codex, true).status, 'Installing Codex…');
});

test('the code: link, code, countdown, Cancel; no paste box for a device code', () => {
  const v = sheetView(st({ phase: 'waiting', install: 'skipped', url: 'https://auth.openai.com/codex/device', code: 'QWER-7TY2U', expiresAt: 1000 }), codex, true);
  assert.equal(v.kind, 'code');
  assert.equal(v.code, 'QWER-7TY2U');
  assert.equal(v.url, 'https://auth.openai.com/codex/device');
  assert.equal(v.expiresAt, 1000);
  assert.equal(v.pasteBox, false);
  assert.equal(v.cancellable, true);
  assert.ok(v.prerequisite);
  assert.equal(v.notice, null);
});

test('a renewed code says so; while it is being fetched the old code is gone', () => {
  const renewing = sheetView(st({ phase: 'starting', notice: 'renewed', url: 'old', code: 'OLD-CODE' }), codex, true);
  assert.equal(renewing.kind, 'working');
  assert.equal(renewing.code, null);
  assert.equal(renewing.status, 'Getting a new code…');
  const fresh = sheetView(st({ phase: 'waiting', notice: 'renewed', url: 'u', code: 'NEW1-23456', renewals: 1 }), codex, true);
  assert.equal(fresh.notice, "That code expired — here's a new one.");
});

test('Gemini: the paste box, and a refused code in plain words', () => {
  const v = sheetView(st({ provider: 'gemini', phase: 'waiting', url: 'https://accounts.google.com/x', needsCode: true }), gemini, true);
  assert.equal(v.pasteBox, true);
  assert.equal(v.code, null);
  assert.equal(v.expiresAt, null);      // agy prints no lifetime: no countdown invented
  const bad = sheetView(st({ provider: 'gemini', phase: 'waiting', url: 'u', needsCode: true, codeError: 'code_rejected' }), gemini, true);
  assert.match(bad.codeError!, /didn't work/);
  const renewed = sheetView(st({ provider: 'gemini', phase: 'waiting', url: 'u2', needsCode: true, notice: 'code_renewed', codeError: 'code_rejected' }), gemini, true);
  assert.equal(renewed.notice, "That code didn't work — here's a new sign-in link.");
  assert.equal(renewed.codeError, null);
  assert.equal(sheetView(st({ provider: 'gemini', phase: 'checking_code', url: 'u', needsCode: true }), gemini, true).status, 'Checking the code…');
});

test('signed in, errors with Try again, and expired with Get a new code', () => {
  const done = sheetView(st({ phase: 'signed_in' }), grok, true);
  assert.equal(done.kind, 'done');
  assert.equal(done.status, 'Signed in to Grok');
  const err = sheetView(st({ phase: 'error', error: { code: 'device_auth_disabled' } }), codex, true);
  assert.deepEqual(err.primary, { label: 'Try again', action: 'retry' });
  assert.match(err.error!, /Device code login is off/);
  assert.ok(err.prerequisite);
  const exp = sheetView(st({ phase: 'expired' }), codex, true);
  assert.deepEqual(exp.primary, { label: 'Get a new code', action: 'new_code' });
});

test('every server error code has words; unknown ones never leak a code', () => {
  for (const code of ['preflight_tools', 'preflight_bin_dir', 'preflight_disk', 'preflight_network', 'install_failed',
    'install_permission', 'install_not_visible', 'start_failed', 'device_auth_disabled', 'cli_exited']) {
    const w = errorWords({ code }, 'Codex');
    assert.ok(w && !w.includes('_'), code);
  }
  assert.equal(errorWords({ code: 'preflight_network', detail: 'x.ai' }, 'Grok'), "This server can't reach x.ai. Check its internet connection.");
  assert.equal(errorWords({ code: 'weird_thing' }, 'Grok'), 'Something went wrong.');
});

test('countdown: m:ss, floors at 0:00, none without a deadline', () => {
  assert.equal(countdown(15 * 60_000, 0), '15:00');
  assert.equal(countdown(61_500, 0), '1:02');
  assert.equal(countdown(1000, 5000), '0:00');
  assert.equal(countdown(null, 0), null);
});

test('client-side code check and which providers the server handles', () => {
  assert.equal(looksLikeGoogleCode(' 4/0AVG7fiQ_abc-DEF '), true);
  assert.equal(looksLikeGoogleCode('4/0AVG 7fiQ'), false);
  assert.equal(looksLikeGoogleCode('short'), false);
  assert.deepEqual(['codex', 'grok', 'gemini', 'claude'].map(isManagedProvider), [true, true, true, false]);
});

test('the countdown runs on this browser\'s clock: received-at + expiresInMs, never the server\'s absolute time', () => {
  const s = st({ phase: 'waiting', url: 'u', code: 'C', expiresAt: 999_999_999, expiresInMs: 840_000 });
  assert.equal(anchorExpiry(s, 5_000).expiresAt, 845_000);
  assert.equal(countdown(anchorExpiry(s, 5_000).expiresAt, 5_000), '14:00');
  assert.equal(anchorExpiry(st({ phase: 'waiting', expiresInMs: null }), 5_000).expiresAt, null);
});

test('no Cancel while the server checks, installs or verifies; Cancel once sign-in starts', () => {
  for (const phase of ['preflight', 'installing', 'verifying'] as const) {
    assert.equal(sheetView(st({ phase, install: 'needed' }), codex, true).cancellable, false, phase);
  }
  assert.equal(sheetView(st({ phase: 'starting' }), codex, true).cancellable, true);
});

test('the picker button says what the sheet does: no "Install Claude" when the sheet can\'t install it', () => {
  assert.equal(connectButtonLabel('codex', 'Codex', false), 'Install Codex');
  assert.equal(connectButtonLabel('gemini', 'Gemini', true), 'Sign in');
  assert.equal(connectButtonLabel('claude', 'Claude', false), 'Set up in a terminal');
  assert.equal(connectButtonLabel('claude', 'Claude', true), 'Sign in');
});

test('a refused code submit in plain words', () => {
  assert.match(codeSubmitWords('not_waiting'), /isn't ready for a code/);
  assert.match(codeSubmitWords('bad_code'), /doesn't look like/);
  assert.equal(codeSubmitWords('HTTP 502'), "Couldn't send the code. Try again.");
});

test('keys in the Claude terminal are the terminal\'s: Esc and Tab left alone, but kept from outer traps', () => {
  const base = { shiftKey: false, atFirst: false, atLast: true, count: 3 };
  assert.deepEqual(sheetKeyAction({ ...base, key: 'Escape', inTerminal: true }), { stop: true, prevent: false, action: 'none' });
  assert.deepEqual(sheetKeyAction({ ...base, key: 'Tab', inTerminal: true }), { stop: true, prevent: false, action: 'none' });
  assert.deepEqual(sheetKeyAction({ ...base, key: 'Tab', shiftKey: true, atFirst: true, inTerminal: true }), { stop: true, prevent: false, action: 'none' });
});

test('elsewhere in the sheet: Esc closes, Tab wraps, and neither reaches an outer handler (ArturoPanel)', () => {
  const k = { inTerminal: false, shiftKey: false, atFirst: false, atLast: false, count: 3 };
  assert.deepEqual(sheetKeyAction({ ...k, key: 'Escape' }), { stop: true, prevent: true, action: 'close' });
  assert.deepEqual(sheetKeyAction({ ...k, key: 'Tab', atLast: true }), { stop: true, prevent: true, action: 'focus-first' });
  assert.deepEqual(sheetKeyAction({ ...k, key: 'Tab', shiftKey: true, atFirst: true }), { stop: true, prevent: true, action: 'focus-last' });
  assert.deepEqual(sheetKeyAction({ ...k, key: 'Tab' }), { stop: true, prevent: false, action: 'none' });   // the browser moves focus
  assert.deepEqual(sheetKeyAction({ ...k, key: 'Tab', count: 0 }), { stop: true, prevent: true, action: 'focus-box' });
  assert.deepEqual(sheetKeyAction({ ...k, key: 'a' }), { stop: false, prevent: false, action: 'none' });
});

test('copy fallback: textarea + execCommand, removed afterwards, focus back on the Copy button', () => {
  const calls: string[] = [];
  const ta = { value: '', style: {} as Record<string, string>, setAttribute: () => {}, select: () => calls.push('select'), remove: () => calls.push('remove') };
  const doc = { createElement: () => ta, body: { appendChild: () => calls.push('append') }, execCommand: (c: string) => { calls.push(`exec ${c}`); return true; } };
  const button = { focus: () => calls.push('focus button') };
  assert.equal(copyWithTextarea(doc, 'QWER-7TY2U', button), true);
  assert.equal(ta.value, 'QWER-7TY2U');
  assert.deepEqual(calls, ['append', 'select', 'exec copy', 'remove', 'focus button']);
  const failing = { ...doc, execCommand: () => { throw new Error('no'); } };
  calls.length = 0;
  assert.equal(copyWithTextarea(failing, 'x', button), false);
  assert.equal(calls.at(-1), 'focus button');
});
