/**
 * The full body behind a "Message from <sender>" line in an agent's chat: read only for the agent it was sent to.
 *
 * Run: npx tsx --test src/routes/agent-messages.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import Database from 'better-sqlite3';
import { readAgentMessage } from './agent-messages.js';

function store(): string {
  const p = join(mkdtempSync(join(tmpdir(), 'agent-msg-')), 'tasks.db');
  const db = new Database(p);
  db.exec(`create table messages (id text primary key, from_agent text, to_agent text, subject text, body text,
           priority text, created_at text, type text)`);
  db.prepare('insert into messages values (?,?,?,?,?,?,?,?)').run(
    'msg_demo_1', 'pm-demo', 'dev-demo', 'Sample subject', 'Example body.\n\nSecond paragraph.', 'high', '2026-10-28T12:00:00Z',
    'phase_report');
  db.close();
  return p;
}

test('the agent it was sent to reads the whole body', () => {
  const m = readAgentMessage(store(), 'dev-demo', 'msg_demo_1');
  assert.equal(m?.from_agent, 'pm-demo');
  assert.equal(m?.body, 'Example body.\n\nSecond paragraph.');
  assert.equal(m?.priority, 'high');
  assert.equal(m?.type, 'phase_report', 'the type says whether it is a hand-back');
});

test("a later generation of the same seat reads it too", () => {
  assert.ok(readAgentMessage(store(), 'dev-demo-g2', 'msg_demo_1'));
});

test('another agent cannot read it, and unknown or malformed ids are null', () => {
  const p = store();
  assert.equal(readAgentMessage(p, 'pm-demo', 'msg_demo_1'), null);
  assert.equal(readAgentMessage(p, 'dev-demo', 'msg_missing'), null);
  assert.equal(readAgentMessage(p, 'dev-demo', "x' or '1'='1"), null);
  assert.equal(readAgentMessage(join(tmpdir(), 'no-such-dir', 'tasks.db'), 'dev-demo', 'msg_demo_1'), null);
});
