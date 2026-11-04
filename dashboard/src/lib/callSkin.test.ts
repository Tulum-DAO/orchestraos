// An agent's call transcripts are drawn in its chat's own skin, with the thread's own bubble and words (lib/chatSkin,
// components/kit/Turns), so the two cannot drift. Arturo's call card keeps its own.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BUBBLES_AGENT, BUBBLES_USER, BUBBLES_BODY, BUBBLES_WIDE, BUBBLES_WIDTH, assistantTurnClass, bubbleWidthOf, turnSkinOf } from './chatSkin.ts';

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
  const bubbleClasses = BUBBLES_USER.replace(/[[\]/.()%]/g, (c) => `\\${c}`);
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

test('an agent reply is a bubble again (Bubbles): bordered card on the left, its corner toward the agent', () => {
  for (const c of ['rounded-2xl', 'rounded-tl-sm', 'border', 'border-border', 'bg-card', 'text-card-foreground', 'px-3.5', 'py-2', 'text-[13px]', 'leading-relaxed', 'break-words', 'min-w-0']) {
    assert.ok(BUBBLES_AGENT.split(' ').includes(c), `agent bubble has ${c}`);
  }
  // the bubble lives INSIDE MessageText, so the streaming overlay and the finished reply share it
  const msg = src('components/kit/Message.tsx');
  assert.match(msg, /if \(skin === 'pane'\) return <div className="bubble">/);
  assert.match(msg, /if \(skin === 'modern'\) return <div className="txt">/);
  assert.match(msg, /className=\{`\$\{BUBBLES_AGENT\} \$\{bubbleWidthOf\(widthFrom \?\? text\)\}`\}/);
  assert.match(msg, /data-agent-bubble/);
});

test('both bubbles: 85% of the column up to 560px; a table or fenced code may widen to 85%', () => {
  assert.equal(BUBBLES_WIDTH, 'max-w-[min(85%,560px)]');
  assert.equal(BUBBLES_WIDE, 'max-w-[85%]');
  assert.ok(BUBBLES_USER.startsWith(`${BUBBLES_WIDTH} `));
  assert.ok(!BUBBLES_USER.includes(BUBBLES_WIDE));
  assert.equal(bubbleWidthOf('Plain words, **bold**, a `code` span.'), BUBBLES_WIDTH);
  assert.equal(bubbleWidthOf('Here:\n\n```sh\nls -la\n```'), BUBBLES_WIDE);
  assert.equal(bubbleWidthOf('| a | b |\n|---|---|\n| 1 | 2 |'), BUBBLES_WIDE);
  // a just-sent message has the same width as the bubble that replaces it
  assert.match(src('components/chat/TranscriptCards.tsx'), /clsx\(BUBBLES_WIDTH, 'rounded-2xl rounded-br-sm/);
});

test("a live reply's bubble is as wide as its finished one: a fence or a table row seen while it streams widens it", () => {
  // still being written: the fence is open, the table has only its first row
  assert.equal(bubbleWidthOf('Here:\n\n```sh\nls -la'), BUBBLES_WIDE);
  assert.equal(bubbleWidthOf('The summary:\n\n| check | state |'), BUBBLES_WIDE);
  assert.equal(bubbleWidthOf('A pipe | in a sentence is not a table.'), BUBBLES_WIDTH);
  // the live overlay draws only finished paragraphs, so it measures the whole live text, not what it draws
  const tv = src('components/kit/ThreadView.tsx');
  assert.match(tv, /<MessageText text=\{t!\.text\} skin=\{skin\} widthFrom=\{text\} \/>/);
  assert.match(tv, /<Parts parts=\{c\.parts\} skin=\{skin\} widthFrom=\{parts\} \/>/);
  assert.match(src('components/kit/Message.tsx'), /bubbleWidthOf\(widthFrom \?\? text\)/);
});
