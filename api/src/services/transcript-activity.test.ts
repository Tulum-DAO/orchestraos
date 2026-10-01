/**
 * Guards the corroborating activity signal added after the 2026-09-29 finding that the
 * v2 detector reported `idle` for all 14 seats at once, including seats mid-turn.
 *
 * The property that matters most here is the negative one: unknown must never read as
 * "working". A false "working" on the org chart is exactly the authoritative-looking lie
 * this change exists to avoid.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const dir = mkdtempSync(join(tmpdir(), 'tact-'));
mkdirSync(join(dir, 'state'), { recursive: true });

const fresh = join(dir, 'fresh.jsonl');
const stale = join(dir, 'stale.jsonl');
writeFileSync(fresh, '{}\n');
writeFileSync(stale, '{}\n');
const longAgo = new Date(Date.now() - 3600_000);
utimesSync(stale, longAgo, longAgo);

writeFileSync(join(dir, 'state', 'agent-sessions.json'), JSON.stringify({
  busy: { conversation_path: fresh, tmux_session: 'busy' },
  quiet: { conversation_path: stale, tmux_session: 'quiet' },
  gone: { conversation_path: join(dir, 'does-not-exist.jsonl'), tmux_session: 'gone' },
  nopath: { tmux_session: 'nopath' },
}));

process.env.ORCHESTRA_DIR = dir;
const { isTranscriptActive, transcriptAgeMs, WORK_WINDOW_MS_FOR_TEST } =
  await import('./transcript-activity.js');

test('a transcript being appended to right now reads as working', () => {
  assert.equal(isTranscriptActive('busy'), true);
});

test('a long-quiet transcript does not read as working', () => {
  assert.equal(isTranscriptActive('quiet'), false);
});

test('a missing transcript file is NOT working — unknown is never active', () => {
  assert.equal(isTranscriptActive('gone'), false);
  assert.equal(transcriptAgeMs('gone'), null, 'a stale path must report unknown, not an age');
});

test('a session entry with no conversation_path is NOT working', () => {
  assert.equal(isTranscriptActive('nopath'), false);
  assert.equal(transcriptAgeMs('nopath'), null);
});

test('an agent absent from the index is NOT working', () => {
  assert.equal(isTranscriptActive('never-heard-of-it'), false);
  assert.equal(transcriptAgeMs('never-heard-of-it'), null);
});

test('age is reported in milliseconds and is small for a fresh file', () => {
  const age = transcriptAgeMs('busy');
  assert.ok(age !== null && age >= 0 && age < 10_000, `implausible age: ${age}`);
});

test('the work window is wide enough for observed write gaps', () => {
  // An actively-working seat was measured going ~19s between transcript writes (a long
  // tool call appends nothing meanwhile). /field's 8s window would blink such a seat to
  // idle mid-turn; on an org chart that reads as broken.
  assert.ok(WORK_WINDOW_MS_FOR_TEST >= 25_000,
    `work window ${WORK_WINDOW_MS_FOR_TEST}ms is under the observed ~19s write gap`);
});
