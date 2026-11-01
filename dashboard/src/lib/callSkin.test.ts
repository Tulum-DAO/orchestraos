// An agent's call transcripts are drawn in its chat's own skin, with the thread's own bubble and words (lib/chatSkin,
// components/kit/Turns), so the two cannot drift. Arturo's call card keeps its own.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BUBBLES_USER, BUBBLES_BODY, assistantTurnClass, turnSkinOf } from './chatSkin.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('the skin an agent call is drawn in follows its chat style (Dev: the chat it came from)', () => {
  assert.equal(turnSkinOf('bubbles', 'modern'), 'bubbles');
  assert.equal(turnSkinOf('modern', 'bubbles'), 'modern');
  assert.equal(turnSkinOf('dev', 'modern'), 'modern');
  assert.equal(turnSkinOf('dev', 'bubbles'), 'bubbles');
  assert.equal(assistantTurnClass('bubbles'), 'turn-assistant group w-full min-w-0');
  assert.equal(assistantTurnClass('modern'), 'turn-assistant');
});

test('one bubble, one body: the thread and the call share them, nobody keeps a copy', () => {
  const bubbleClasses = BUBBLES_USER.replace(/[[\]/.]/g, (c) => `\\${c}`);
  for (const f of ['components/kit/ThreadView.tsx', 'components/chat/TranscriptCards.tsx', 'components/kit/CallCard.tsx', 'components/call/CallTranscript.tsx']) {
    assert.doesNotMatch(src(f), new RegExp(bubbleClasses), `${f} keeps no copy of the bubble's classes`);
  }
  assert.match(src('components/kit/ThreadView.tsx'), /<UserWords skin="bubbles">/);
  assert.match(src('components/kit/ThreadView.tsx'), /<UserWords skin="modern">/);
  assert.match(src('components/chat/TranscriptCards.tsx'), /className=\{BUBBLES_USER\}/);
  assert.match(src('components/kit/Markdown.tsx'), /skin === 'bubbles' \? BUBBLES_BODY : ''/);
  assert.ok(BUBBLES_BODY.includes('text-[13px]'));
});

test("an agent call's turns use the shared turns in its skin; Bubbles shows no names; Arturo's card is unchanged", () => {
  const card = src('components/kit/CallCard.tsx');
  assert.match(card, /<UserTurn skin=\{turnSkin\} label=\{turnSkin === 'modern' \?/);
  assert.match(card, /<AgentTurn skin=\{turnSkin\} label=\{turnSkin === 'modern' \?/);
  assert.match(card, /<MessageText text=\{e\.turn\.text\} skin=\{turnSkin\} \/>/);
  // Arturo's turns keep his card's own bubbles
  assert.match(card, /<div key=\{t\.key \?\? i\} className=\{bubble\('user'\)\}>/);
  const live = src('components/call/CallTranscript.tsx');
  assert.match(live, /<UserTurn skin=\{skin\} label=\{label\}>/);
  assert.match(live, /skin === 'modern' \? <span/);
  assert.match(src('components/call/CallBar.tsx'), /call\?\.party === 'agent' \? turnSkinOf\(chatStyle, styleBeforeDev\(agentId\)\) : undefined/);
  assert.match(src('components/chat/TranscriptChatView.tsx'), /skin: modern \? 'modern' as const : 'bubbles' as const/);
});
