// The one shared call card: every agent's call row is Arturo's card (components/kit/CallCard.tsx), and the call bar
// offers Back to chat while the agent is in Dev. Read from the source, like lib/headerLayout.test.ts.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('one call card: the separate agent card and the gold Bubbles card are gone', () => {
  assert.equal(existsSync(new URL('../components/call/AgentCallCard.tsx', import.meta.url)), false);
  const card = src('components/kit/CallCard.tsx');
  assert.doesNotMatch(card, /AgentCallCard|ChatCallCard|amber-500/);
  // Arturo's header, menu and collapse, for everyone
  for (const k of ['className="call-head"', 'Copy transcript', 'Collapse voice chat', 'call-card is-collapsed']) assert.ok(card.includes(k), k);
  // The parts inside it: thumbs saved through the agent's calls route, You / the agent's name, the work chips
  assert.match(card, /saveCallRating\(agent\.agentId, callId, next\)/);
  assert.match(card, /<span className="call-who">You<\/span>/);
  assert.match(card, /<span className="call-who">\{agent!\.name\}<\/span>/);
  assert.match(card, /data-work-chip=/);
});

test('centred in an agent chat, not left-aligned; a long call scrolls inside a fixed height', () => {
  const cards = src('components/chat/TranscriptCards.tsx');
  assert.doesNotMatch(cards, /justify-start"><CallCard/);
  const css = src('components/kit/callCard.css');
  assert.match(css, /\.call-card \.call-body\{[^}]*max-height:[^;]+;[^}]*overflow-y:auto/);
  assert.match(css, /\.call-card\{ margin-inline:auto; \}/);
  // its own colour set in the Bubbles chat, light and dark
  assert.match(css, /\.call-scope\{/);
  assert.match(css, /\.dark \.call-scope\{/);
});

test('a call to a seat in Dev: the bar offers Back to chat, wired to the page\'s own Back to chat', () => {
  const bar = src('components/call/CallBar.tsx');
  assert.match(bar, /onBackToChat\?: \(\) => void/);
  assert.match(bar, /data-call-back-to-chat/);
  const page = src('pages/AgentPage.tsx');
  assert.match(page, /onBackToChat=\{backToChat\}/);
  assert.match(page, /onBackToChat=\{chosenMode === 'dev' \? backToChat : undefined\}/);
  // the page's Back to chat is the saved chat style, which the headless switch (and its send hold) follows
  assert.match(page, /chooseStyle\(agentId, styleBeforeDev\(agentId\)\)/);
  assert.match(src('hooks/useHeadlessDev.ts'), /if \(!on\) holdSendsUntil\(agentId, asked\)/);
});
