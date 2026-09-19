/**
 * RED-first: pull a sign-in URL back out of a terminal pane.
 *
 * The operator hit the wall this exists for: the Antigravity CLI prints its OAuth URL into
 * the pane, HARD-WRAPPED across ten lines. You cannot click it, and selecting it drags in
 * the line breaks, so pasting it into a browser gives a broken URL. A terminal is where the
 * CLI can talk, but it is not a place a human can get 570 characters out of by hand.
 *
 * So the server reads the pane and hands the UI one intact string.
 *
 * Run: npx tsx --test src/routes/pane-url.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractSignInUrl } from './pane-url.js';

// A faithful reduction of the real capture: the URL split across consecutive lines with no
// spaces, preceded and followed by ordinary prose.
const REAL_PANE = [
  'Welcome to the Antigravity CLI.',
  '',
  'Your browser should open automatically. If not:',
  '',
  'https://accounts.google.com/o/oauth2/auth?access_type=off',
  'line&client_id=1071006060591-tmhssin2h21lcre235vtolojh4g4',
  '03ep.apps.googleusercontent.com&code_challenge=odc28Ci1GZ',
  'kSfyQrfLvzY7xCfChlIMFAUjLpEIPLh5Y&code_challenge_method=S',
  '256&prompt=consent&redirect_uri=https%3A%2F%2Fantigravity',
  '.google%2Foauth-callback&response_type=code',
  '',
  '  (1-19 of 27 lines)',
].join('\n');

test('joins a hard-wrapped URL back into one intact string', () => {
  const url = extractSignInUrl(REAL_PANE);
  assert.ok(url, 'a URL should be found');
  assert.ok(url!.startsWith('https://accounts.google.com/o/oauth2/auth?'));
  assert.match(url!, /client_id=/);
  assert.match(url!, /code_challenge=/);
  assert.match(url!, /redirect_uri=/);
  assert.match(url!, /response_type=code$/);
  assert.doesNotMatch(url!, /\s/);              // no line breaks survived
  assert.ok(url!.length > 200, 'the whole thing, not the first line');
});

test('stops at the first line that is not a continuation', () => {
  const url = extractSignInUrl(['https://example.com/a?x=1', 'bcd', 'then prose with spaces', 'efg'].join('\n'));
  assert.equal(url, 'https://example.com/a?x=1bcd');   // 'efg' is after a break, not part of it
});

test('a blank line ends the URL', () => {
  const url = extractSignInUrl(['https://example.com/a', 'bcd', '', 'efg'].join('\n'));
  assert.equal(url, 'https://example.com/abcd');
});

test('returns null when the pane has no URL at all', () => {
  assert.equal(extractSignInUrl('orchestra@host:~$ \nnothing to see'), null);
  assert.equal(extractSignInUrl(''), null);
});

test('prefers a sign-in URL over an unrelated one earlier in the pane', () => {
  const pane = [
    'see https://antigravity.google/docs for help',
    '',
    'Your browser should open automatically. If not:',
    'https://accounts.google.com/o/oauth2/auth?access_type=offline',
    '&client_id=abc',
  ].join('\n');
  const url = extractSignInUrl(pane);
  assert.match(url!, /accounts\.google\.com/);
  assert.match(url!, /client_id=abc$/);
});

test('a URL already on one line is returned unchanged', () => {
  const one = 'https://accounts.google.com/o/oauth2/auth?client_id=x&scope=y';
  assert.equal(extractSignInUrl(`prose\n${one}\nmore prose here`), one);
});

test('trailing punctuation from prose is not swallowed', () => {
  assert.equal(extractSignInUrl('open https://example.com/x.'), 'https://example.com/x');
});
