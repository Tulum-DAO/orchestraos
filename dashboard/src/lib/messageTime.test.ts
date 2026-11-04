// When a message (or a tool call) happened, one formatter for every place the chat says it (components/chat/
// TranscriptCards MessageTime under a bubble, kit/ToolCard's thin tool lines): clock time, "just now" under a minute,
// the full local date and time on hover; nothing for a missing or bad time.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MESSAGE_TIME_TONE, messageTimeLabel } from './messageTime.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('clock time (local, 24h) for anything a minute old, "just now" under it, nothing without a time', () => {
  const at = new Date(2026, 10, 2, 9, 5, 0);
  const t = messageTimeLabel(at.toISOString(), at.getTime() + 5 * 60_000)!;
  assert.equal(t.label, '09:05');
  assert.equal(t.title, at.toLocaleString());
  assert.equal(messageTimeLabel(at.toISOString(), at.getTime() + 30_000)!.label, 'just now');
  assert.equal(messageTimeLabel(undefined), null);
  assert.equal(messageTimeLabel('not a date'), null);
});

test('the bubble time and the tool lines use this one formatter and its muted tone', () => {
  const cards = src('components/chat/TranscriptCards.tsx');
  assert.match(cards, /const t = messageTimeLabel\(ts\);/);
  assert.match(cards, /\$\{MESSAGE_TIME_TONE\}/);
  const card = src('components/kit/ToolCard.tsx');
  assert.match(card, /messageTimeLabel\(/);
  assert.match(card, /MESSAGE_TIME_TONE/);
  assert.equal(MESSAGE_TIME_TONE, 'text-[10px] text-tone-600');
});
