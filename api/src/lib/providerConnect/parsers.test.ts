/**
 * The sign-in screen parsers against the REAL screens captured on a test install (fixtures.ts, verbatim).
 * Run: npx tsx --test src/lib/providerConnect/parsers.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseCodexPane, parseGrokPane, parseAgyPane, agyUrl, agyTokenValid, agyCodeRejected, exitCode, validAgyCode, grokSignedIn, versionOf,
  npmPermissionError,
} from './parsers.js';
import {
  CODEX_SIGNIN_PANE, GROK_SIGNIN_PANE, AGY_MENU_PANE, AGY_URL_PANE, AGY_URL_REBUILT, STALE_GREETING_PANE,
  CODEX_INSTALL_LOG, GROK_INSTALL_LOG, AGY_INSTALL_LOG,
} from './fixtures.js';

test('codex: the fixed device URL, the one-time code and its 15-minute lifetime', () => {
  assert.deepEqual(parseCodexPane(CODEX_SIGNIN_PANE), {
    url: 'https://auth.openai.com/codex/device', code: 'QWER-7TY2U', expiresInMin: 15, deviceAuthDisabled: false,
  });
});

test('codex: device code login switched off on the account is recognised', () => {
  const pane = 'Welcome to Codex [v0.166.0]\nError: device code login is not enabled for this account. Enable device code login in your ChatGPT security settings.\n__OOS_EXIT=1';
  assert.equal(parseCodexPane(pane).deviceAuthDisabled, true);
  assert.equal(exitCode(pane), 1);
});

test('grok: the URL carries the code; the code line agrees; no lifetime printed', () => {
  assert.deepEqual(parseGrokPane(GROK_SIGNIN_PANE), {
    url: 'https://accounts.x.ai/oauth2/device?user_code=AB12-CDEF', code: 'AB12-CDEF', expiresInMin: null,
  });
});

test('agy: the first screen is the login-method menu with Google OAuth selected', () => {
  assert.deepEqual(parseAgyPane(AGY_MENU_PANE), { kind: 'menu', googleSelected: true });
});

test('agy: the hard-wrapped URL between the rulers is rebuilt exactly', () => {
  assert.equal(agyUrl(AGY_URL_PANE), AGY_URL_REBUILT);
  assert.equal(AGY_URL_REBUILT.length, 704);
  assert.deepEqual(parseAgyPane(AGY_URL_PANE), { kind: 'url', url: AGY_URL_REBUILT });
});

test('agy wrong code: only NEW lines after the submit count; the echoed code and Go log lines never do', () => {
  const code = '4/0AVG7fiQ_invalid-ish';
  const typed = AGY_URL_PANE.replace('authorization code...', code);
  // The echo of a code that happens to contain "invalid" is not a refusal.
  assert.equal(agyCodeRejected(AGY_URL_PANE, typed, code), false);
  // agy's own log noise, which says ERROR, is not a refusal.
  assert.equal(agyCodeRejected(AGY_URL_PANE, `${typed}\nERROR: logging before google.Init: E1104 07:51:20.1 auth.go:88] token exchange`, code), false);
  // A bare "error" is too vague.
  assert.equal(agyCodeRejected(AGY_URL_PANE, `${typed}\n Something went wrong, error`, code), false);
  // A real refusal is.
  const refused = `${typed}\n Error: invalid authorization code. Please try again.`;
  assert.equal(agyCodeRejected(AGY_URL_PANE, refused, code), true);
  assert.equal(agyCodeRejected(AGY_URL_PANE, `${typed}\n oauth2: "invalid_grant" "Bad Request"`, code), true);
  // The same refusal still on screen from last time is not new; a second identical one is.
  assert.equal(agyCodeRejected(refused, refused, code), false);
  assert.equal(agyCodeRejected(refused, `${refused}\n Error: invalid authorization code. Please try again.`, code), true);
});

test('agy: a URL block cut off before its closing ruler is not a URL yet', () => {
  const cut = AGY_URL_PANE.split('\n').slice(0, 9).join('\n');
  assert.equal(agyUrl(cut), null);
});

test('a leftover shell (the stale login-agy greeting) is nobody\'s sign-in screen', () => {
  assert.deepEqual(parseAgyPane(STALE_GREETING_PANE), { kind: 'unknown' });
  assert.deepEqual(parseCodexPane(STALE_GREETING_PANE).code, null);
  assert.deepEqual(parseGrokPane(STALE_GREETING_PANE).url, null);
  assert.equal(exitCode(STALE_GREETING_PANE), null);
});

test('the exit marker: the last one wins, none while the CLI runs', () => {
  assert.equal(exitCode(CODEX_SIGNIN_PANE), null);
  assert.equal(exitCode('x\n__OOS_EXIT=1\nagain\n__OOS_EXIT=0'), 0);
});

test('agy codes: Google\'s charset only, trimmed, no shell or terminal characters', () => {
  assert.equal(validAgyCode('  4/0AVG7fiQ_abc-DEF.ghi~jkl  '), '4/0AVG7fiQ_abc-DEF.ghi~jkl');
  for (const bad of ['', 'short', '4/0AVG7fiQ abc', '4/0AVG7fiQ;rm -rf', "4/0AVG7fiQ'x", '4/0AVG7fiQ\nx', '4/0AVG7fiQ$(x)', 'a'.repeat(513), null, 42]) {
    assert.equal(validAgyCode(bad), null, String(bad));
  }
});

test('grok signed-in check and versions from the real install output', () => {
  assert.equal(grokSignedIn('You are logged in with sam@example.com\nmodels...'), true);
  assert.equal(grokSignedIn('You are not authenticated.'), false);
  assert.equal(versionOf('codex-cli 0.166.0'), '0.166.0');
  assert.equal(versionOf('grok 1.0.70 (f50904eaee6c)'), '1.0.70');
  assert.equal(versionOf(AGY_INSTALL_LOG.trim().split('\n').pop()!), '1.5.0');
  assert.match(CODEX_INSTALL_LOG, /codex-cli 0\.166\.0/);
  assert.match(GROK_INSTALL_LOG, /Symlinked \/home\/orchestra\/.local\/bin\/grok/);
  // agy's installer logs "ERROR: logging before google.Init" lines on a SUCCESSFUL install: never an npm permission error.
  assert.equal(npmPermissionError(AGY_INSTALL_LOG), false);
  assert.equal(npmPermissionError('npm error code EACCES\nnpm error syscall mkdir'), true);
});

test('agy token file: valid only with a future token.expiry', () => {
  const now = Date.parse('2026-11-04T08:00:00Z');
  assert.equal(agyTokenValid({ token: { expiry: '2026-11-04T09:00:00Z' } }, now), true);
  assert.equal(agyTokenValid({ token: { expiry: '2026-11-04T07:00:00Z' } }, now), false);
  assert.equal(agyTokenValid({ token: {} }, now), false);
  assert.equal(agyTokenValid(null, now), false);
});
