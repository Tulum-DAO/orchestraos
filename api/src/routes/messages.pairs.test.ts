/**
 * 2D Agents View step 1 (spec §16): the connection-line count and the conversation
 * panel read ONE table — <data>/state/tasks.db `messages` — so the line and the panel
 * header cannot disagree the way "49 vs 40" did.
 *
 * What these tests bite on, specifically:
 *  - a pair is UNORDERED (A->B and B->A are one line);
 *  - the window is a real filter, computed with julianday() so a non-ISO created_at
 *    (the column DEFAULT writes 'YYYY-MM-DD HH:MM:SS') still lands in the window
 *    instead of silently sorting to the bottom;
 *  - `before` paginates strictly older, never re-sending the boundary row, and is NOT
 *    capped by the window;
 *  - has_more is observed (limit + 1), not inferred from a full page.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import Database from 'better-sqlite3';
import { pairCounts, pairMessages } from './messages.js';

function hoursAgo(h: number): string {
  return new Date(Date.now() - h * 3600_000).toISOString();
}

/** Fixture: gm<->build 3 rows in-window + 1 row 40 days old, gm<->plan 1, one self-send,
 *  one archived row, and one row written in the column's DEFAULT format (no 'T', no offset). */
function makeDb(): string {
  const data = mkdtempSync(join(tmpdir(), 'orch-pairs-'));
  mkdirSync(join(data, 'state'));
  const db = new Database(join(data, 'state', 'tasks.db'));
  db.exec(`create table messages (id text primary key, conversation_id text, from_agent text,
           to_agent text, type text, subject text, body text, priority text, status text,
           created_at text, delivered_at text, acknowledged_at text, archived_at text)`);
  const ins = db.prepare(`insert into messages
    (id,from_agent,to_agent,type,subject,body,priority,status,created_at,archived_at)
    values (?,?,?,?,?,?,'medium','pending',?,?)`);
  ins.run('m1', 'gm', 'build', 'task', 's1', 'b1', hoursAgo(5), null);
  ins.run('m2', 'build', 'gm', 'reply', 's2', 'b2', hoursAgo(3), null);   // other direction, same line
  ins.run('m3', 'gm', 'build', 'task', 's3', 'b3', hoursAgo(1), null);    // newest
  ins.run('m4', 'gm', 'build', 'task', 'old', 'b4', hoursAgo(24 * 40), null); // outside a 24h window
  ins.run('m5', 'gm', 'plan', 'task', 's5', 'b5', hoursAgo(2), null);
  ins.run('m6', 'plan', 'plan', 'task', 'self', 'b6', hoursAgo(2), null); // self-send: not a line
  ins.run('m7', 'gm', 'build', 'task', 'gone', 'b7', hoursAgo(2), hoursAgo(1)); // archived
  // Written the way the column DEFAULT datetime('now') writes it: space separator, no offset.
  // Lexicographically this sorts BEFORE every ISO row; julianday() places it correctly.
  ins.run('m8', 'gm', 'review', 'task', 'default-fmt', 'b8',
    new Date(Date.now() - 2 * 3600_000).toISOString().replace('T', ' ').replace(/\..*$/, ''), null);
  db.close();
  return data;
}

test('pairCounts collapses both directions, honours the window, drops self-sends and archived', () => {
  const data = makeDb();
  const rows = pairCounts(data, 24);
  const byPair = new Map(rows.map((r) => [`${r.a}|${r.b}`, r]));

  // m1 + m2 + m3 = 3. m4 is outside the window, m7 is archived — neither counts.
  assert.equal(byPair.get('build|gm')?.count, 3, JSON.stringify(rows));
  assert.equal(byPair.get('gm|plan')?.count, 1);
  assert.equal(byPair.get('plan|plan'), undefined, 'a self-send is not a connection line');
  assert.ok(!rows.some((r) => r.a === 'gm' && r.b === 'build'), 'pair keys are sorted, so only build|gm');

  // The DEFAULT-format row must be inside a 24h window. A lexicographic compare would drop it.
  assert.equal(byPair.get('gm|review')?.count, 1, 'non-ISO created_at must still land in the window');

  assert.ok(rows[0].count >= rows[rows.length - 1].count, 'busiest first');

  // last_at must be the newest row on that line. Cross-checked against the OTHER endpoint
  // rather than a hardcoded timestamp, so the line tooltip and the panel cannot drift.
  assert.equal(byPair.get('build|gm')?.last_at,
    pairMessages(data, 'gm', 'build', 1, null, 24).messages[0].created_at);

  // A wide window pulls the 40-day-old row back in — proof the filter is the window, not a fixture artefact.
  const wide = new Map(pairCounts(data, 24 * 90).map((r) => [`${r.a}|${r.b}`, r]));
  assert.equal(wide.get('build|gm')?.count, 4);

  assert.deepEqual(pairCounts(join(data, 'nowhere'), 24), []);  // no db = empty, never throws
});

test('pairMessages pages newest-first by a created_at cursor, past the window', () => {
  const data = makeDb();

  const first = pairMessages(data, 'gm', 'build', 2, null, 24);
  assert.equal(first.total_in_window, 3, 'matches the line label exactly');
  assert.equal(first.total_all_time, 4, 'what load-older can reach');
  assert.deepEqual(first.messages.map((m) => m.id), ['m3', 'm2'], 'newest first');
  assert.equal(first.has_more, true);
  assert.equal(first.next_before, first.messages[1].created_at);

  const second = pairMessages(data, 'gm', 'build', 2, first.next_before, 24);
  assert.deepEqual(second.messages.map((m) => m.id), ['m1', 'm4'],
    'strictly older than the cursor — m2 is not re-sent, and m4 is past the window');
  assert.equal(second.has_more, false, 'has_more is observed, not inferred from a full page');
  assert.equal(second.next_before, null);

  // Argument order must not matter: it is one line.
  assert.deepEqual(
    pairMessages(data, 'build', 'gm', 40, null, 24).messages.map((m) => m.id),
    pairMessages(data, 'gm', 'build', 40, null, 24).messages.map((m) => m.id));

  // Bodies are carried — the panel renders them without a second fetch.
  assert.equal(first.messages[0].body, 'b3');
  assert.equal(first.messages[0].subject, 's3');

  const none = pairMessages(data, 'gm', 'nobody', 40, null, 24);
  assert.deepEqual(none.messages, []);
  assert.equal(none.total_all_time, 0);
  assert.equal(pairMessages(join(data, 'nowhere'), 'gm', 'build', 40, null, 24).total_all_time, 0);
});
