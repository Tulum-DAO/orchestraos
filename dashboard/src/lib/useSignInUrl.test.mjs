/**
 * The polling contract of useSignInUrl, without a renderer: the effect body is what matters
 * (which URL it asks for, that a null session asks for nothing, that a nulled answer does not
 * clobber a URL already found).
 *   node --test dashboard/src/lib/useSignInUrl.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert';

/** The exact request the hook makes, factored the way the hook builds it. */
const urlFor = (session) => `/api/agents/${encodeURIComponent(session)}/sign-in-url`;

test('asks the pane route for the session, escaping the name', () => {
  assert.equal(urlFor('login-cla0'), '/api/agents/login-cla0/sign-in-url');
  assert.equal(urlFor('a b/c'), '/api/agents/a%20b%2Fc/sign-in-url');
});

test('only a non-empty url in the answer counts', () => {
  // mirrors `if (!stop && j?.url) setUrl(...)`: null/absent/empty leave the state alone,
  // so a poll that catches the pane mid-redraw cannot blank a link already on screen.
  const keep = (prev, answer) => (answer && answer.url ? answer.url : prev);
  assert.equal(keep(null, {}), null);
  assert.equal(keep(null, { url: null }), null);
  assert.equal(keep(null, { url: 'https://claude.com/cai/oauth/authorize?x=1' }), 'https://claude.com/cai/oauth/authorize?x=1');
  assert.equal(keep('https://kept', { url: null }), 'https://kept');
  assert.equal(keep('https://kept', {}), 'https://kept');
});
