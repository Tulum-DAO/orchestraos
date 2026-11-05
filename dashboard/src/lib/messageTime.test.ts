// When a message (or a tool call) happened, one formatter for every place the chat says it (components/chat/
// TranscriptCards MessageTime under a bubble, kit/ToolCard's thin tool lines): clock time, "just now" under a minute,
// the full local date and time on hover; nothing for a missing or bad time.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MESSAGE_TIME_TONE, messageTimeLabel, clockWhen } from './messageTime.ts';

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
  assert.match(cards, /const t = messageTimeLabel\(ts, undefined, tz\);/);
  assert.match(cards, /\$\{MESSAGE_TIME_TONE\}/);
  const card = src('components/kit/ToolCard.tsx');
  assert.match(card, /messageTimeLabel\(/);
  assert.match(card, /MESSAGE_TIME_TONE/);
  assert.equal(MESSAGE_TIME_TONE, 'text-px10 text-tone-600');
});

test('with the install time zone, clock times are in that zone', () => {
  const now = Date.parse('2026-11-02T12:00:00Z');
  assert.equal(messageTimeLabel('2026-11-02T10:05:00Z', now, 'America/New_York')?.label, '05:05');
  assert.equal(messageTimeLabel('2026-11-02T10:05:00Z', now, 'Asia/Tokyo')?.label, '19:05');
  assert.equal(messageTimeLabel('2026-11-02T11:59:30Z', now, 'Asia/Tokyo')?.label, 'just now');
});

test('the dated clock label (result cards, the decisions history) uses the same zone: today, Yesterday, this year, before', () => {
  const now = Date.parse('2026-10-31T01:00:00Z');            // Tokyo: Oct 31 10:00; Los Angeles: Oct 30 18:00
  assert.equal(clockWhen(Date.parse('2026-10-30T23:40:00Z'), now, 'Asia/Tokyo'), '08:40');
  assert.equal(clockWhen(Date.parse('2026-10-30T23:40:00Z'), now, 'America/Los_Angeles'), '16:40');
  assert.equal(clockWhen(Date.parse('2026-10-30T14:00:00Z'), now, 'Asia/Tokyo'), 'Yesterday 23:00');
  assert.equal(clockWhen(Date.parse('2026-10-30T14:00:00Z'), now, 'America/Los_Angeles'), '07:00');
  assert.equal(clockWhen(Date.parse('2026-10-28T11:00:00Z'), now, 'Asia/Tokyo'), 'Oct 28, 20:00');
  assert.equal(clockWhen(Date.parse('2025-12-03T08:00:00Z'), now, 'Asia/Tokyo'), 'Dec 3 2025');
  assert.equal(clockWhen(Date.parse('2026-10-30T15:05:00Z'), now, 'Asia/Tokyo'), '00:05', 'midnight is 00, never 24');
  assert.equal(clockWhen(null, now, 'Asia/Tokyo'), '');
  const local = new Date(2026, 9, 30, 15, 0).getTime();
  assert.equal(clockWhen(new Date(2026, 9, 30, 9, 5).getTime(), local), '09:05', 'no zone set: this device\'s clock');
  assert.equal(clockWhen(new Date(2026, 9, 30, 9, 5).getTime(), local, 'Not/AZone'), '09:05', 'an unknown zone: this device\'s clock');
  // every clock label uses it with the install's zone
  assert.match(src('components/kit/ResultCard.tsx'), /clockWhen\(Date\.parse\(at\), Date\.now\(\), tz\)/);
  assert.match(src('components/kit/ResultCard.tsx'), /useInstallTimeZone\(\)/);
  assert.match(src('lib/drawerHistory.ts'), /clockWhen\(at, now, timeZone\)/);
  assert.match(src('components/arturo/DrawerHistory.tsx'), /resolvedLine\(item, now, tz\)/);
});
