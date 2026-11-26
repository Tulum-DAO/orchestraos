/**
 * Guards the corroborating activity signal added after the 2026-09-29 finding that the
 * v2 detector reported `idle` for all 14 seats at once, including seats mid-turn.
 *
 * The property that matters most here is the negative one: unknown must never read as
 * "working". A false "working" on the org chart is exactly the authoritative-looking lie
 * this change exists to avoid.
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, utimesSync } from 'fs';
import { join } from 'path';

const dir = testTmpDir('tact-');
mkdirSync(join(dir, 'state'), { recursive: true });

const fresh = join(dir, 'fresh.jsonl');
const stale = join(dir, 'stale.jsonl');
writeFileSync(fresh, '{}\n');
writeFileSync(stale, '{}\n');
const longAgo = new Date(Date.now() - 3600_000);
utimesSync(stale, longAgo, longAgo);

// A seat as the projection actually writes it (2026-11-25: conversation_path is empty for 583 of
// 587 online seats): only session_id + cwd. Its transcript must still be found, from the sid.
const SID = '0c0ffee0-0000-4000-8000-000000000001';
const seatCwd = '/work/seat-a';
const projDir = join(dir, 'home', '.claude', 'projects', seatCwd.replace(/[/.]/g, '-'));
mkdirSync(projDir, { recursive: true });
writeFileSync(join(projDir, `${SID}.jsonl`), '{}\n');
process.env.HOME = join(dir, 'home');
// The resolver probes panes: point tmux at an empty socket dir so no test ever reaches the real server.
process.env.TMUX_TMPDIR = join(dir, 'no-tmux');
mkdirSync(process.env.TMUX_TMPDIR, { recursive: true });
delete process.env.TMUX;

// A seat whose recorded session has no file, in a project dir whose only (fresh) transcript is ANOTHER
// session's: the newest-file guess must not be taken as this seat's activity.
const OTHER_CWD = '/work/seat-b';
const otherDir = join(dir, 'home', '.claude', 'projects', OTHER_CWD.replace(/[/.]/g, '-'));
mkdirSync(otherDir, { recursive: true });
writeFileSync(join(otherDir, '0c0ffee0-0000-4000-8000-0000000000aa.jsonl'), '{}\n');
// The file a traversing sid ('../../fresh' from the seat's project dir) would land on, fresh on purpose.
writeFileSync(join(dir, 'home', '.claude', 'fresh.jsonl'), '{}\n');

writeFileSync(join(dir, 'state', 'agent-sessions.json'), JSON.stringify({
  bysid: { session_id: SID, cwd: seatCwd, tmux_session: 'bysid-no-such-pane', runtime: 'claude' },
  othersid: { session_id: '0c0ffee0-0000-4000-8000-000000000002', cwd: OTHER_CWD, tmux_session: 'othersid', runtime: 'claude' },
  badsid: { session_id: '../../fresh', cwd: seatCwd, tmux_session: 'badsid', runtime: 'claude' },
  busy: { conversation_path: fresh, tmux_session: 'busy' },
  quiet: { conversation_path: stale, tmux_session: 'quiet' },
  gone: { conversation_path: join(dir, 'does-not-exist.jsonl'), tmux_session: 'gone' },
  nopath: { tmux_session: 'nopath' },
}));

process.env.ORCHESTRA_DIR = dir;
const { isTranscriptActive, transcriptAgeMs, WORK_WINDOW_MS_FOR_TEST } =
  await import('./transcript-activity.js');

test('a seat with no conversation_path is found from its session id (the real projection shape)', () => {
  assert.equal(isTranscriptActive('bysid'), true);
  const age = transcriptAgeMs('bysid');
  assert.ok(age !== null && age < 60_000, `expected a fresh age, got ${age}`);
});

test("another session's fresh transcript in the seat's project dir is NOT the seat working", () => {
  assert.equal(isTranscriptActive('othersid'), false);
  assert.equal(transcriptAgeMs('othersid'), null);
});

test('a session id that is not a plain id never becomes a path', () => {
  assert.equal(transcriptAgeMs('badsid'), null);
});

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
