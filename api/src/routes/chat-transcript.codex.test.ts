/**
 * RED first — codex seats render an EMPTY chat pane (found on a real codex-only client install,
 * Ubuntu 26): `GET /api/agents/gm/transcript` returns
 *   {"items":[],"render_items":[],"error":"no transcript resolved"}
 * while the same install's /api/agents row says the seat is alive and working. chat-transcript.ts
 * resolved only ~/.claude/projects and ~/.gemini/antigravity-cli; there was no codex branch.
 *
 * Congruence DEC-1790239929422621. gm ruled TIERED with NO cwd fallback. The peers refuted four of
 * the author's claims with measurements, and those refutations are pinned here as tests:
 *   - every seat shares repo_root by construction (seats.py register_seat setdefault), so cwd can
 *     NEVER identify a seat: resolution must be a POSITIVE seat-name declaration join
 *   - reasoning.summary[] was populated 0 times in 1570 real records; encrypted_content is opaque
 *   - codex has a SECOND tool shape (function_call) besides custom_tool_call
 *   - custom_tool_call args arrive as STRINGS where toolSummary() expects an object
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';
import { parseCodexRollout, findCodexByDeclaration, looksLikeCodex, normalizeTranscript } from './chat-transcript.js';

const rec = (ordinal: number, type: string, payload: any, ts = '2026-09-24T08:40:15.000Z') =>
  JSON.stringify({ timestamp: ts, ordinal, type, payload });

// ---------------------------------------------------------------- parsing

test('a user message becomes a user text item', () => {
  const items = parseCodexRollout([
    rec(2, 'response_item', { type: 'message', id: 'm1', role: 'user',
                              content: [{ type: 'input_text', text: 'spawn my gm agent' }] }),
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, 'text');
  assert.equal(items[0].role, 'user');
  assert.equal(items[0].text, 'spawn my gm agent');
});

test('an assistant message becomes an assistant text item', () => {
  const items = parseCodexRollout([
    rec(3, 'response_item', { type: 'message', id: 'm2', role: 'assistant',
                              content: [{ type: 'output_text', text: 'Seat is up.' }] }),
  ]);
  assert.equal(items[0].role, 'assistant');
  assert.equal(items[0].text, 'Seat is up.');
});

test('a developer message is carried as the system brief, not as operator speech', () => {
  const items = parseCodexRollout([
    rec(1, 'response_item', { type: 'message', id: 'm0', role: 'developer',
                              content: [{ type: 'input_text', text: '<skills_instructions>...' }] }),
  ]);
  assert.equal(items[0].is_system, true);
});

test('agent_message is rendered — subagent replies are part of the conversation', () => {
  const items = parseCodexRollout([
    rec(4, 'response_item', { type: 'agent_message', id: 'a1',
                              content: [{ type: 'output_text', text: 'subagent finished' }] }),
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].text, 'subagent finished');
});

test('BOTH codex tool shapes are carried — custom_tool_call and function_call', () => {
  const items = parseCodexRollout([
    rec(5, 'response_item', { type: 'custom_tool_call', id: 'c1', call_id: 'call_A', name: 'shell',
                              input: '{"command":"ls -la"}' }),
    rec(6, 'response_item', { type: 'custom_tool_call_output', call_id: 'call_A',
                              output: [{ type: 'input_text', text: 'total 4' }] }),
    rec(7, 'response_item', { type: 'function_call', id: 'f1', call_id: 'call_B', name: 'read_file',
                              arguments: '{"path":"/etc/hosts"}' }),
    rec(8, 'response_item', { type: 'function_call_output', call_id: 'call_B',
                              output: 'nameserver 1.1.1.1' }),
  ]);
  const uses = items.filter((i) => i.kind === 'tool_use');
  const results = items.filter((i) => i.kind === 'tool_result');
  assert.equal(uses.length, 2, 'function_call must not be dropped — tool calls would vanish');
  assert.equal(results.length, 2);
  assert.deepEqual(uses.map((u) => u.tool).sort(), ['read_file', 'shell']);
});

test('string tool args are normalised to an object so toolSummary is not blank', () => {
  const items = parseCodexRollout([
    rec(5, 'response_item', { type: 'custom_tool_call', id: 'c1', call_id: 'call_A', name: 'shell',
                              input: '{"command":"ls -la"}' }),
  ]);
  const use = items.find((i) => i.kind === 'tool_use');
  assert.equal(typeof use.input, 'object');
  assert.equal(use.input.command, 'ls -la');
});

test('un-parseable tool args still yield an object, never a raw string', () => {
  const items = parseCodexRollout([
    rec(5, 'response_item', { type: 'custom_tool_call', id: 'c1', call_id: 'x', name: 'shell',
                              input: 'not json at all' }),
  ]);
  const use = items.find((i) => i.kind === 'tool_use');
  assert.equal(typeof use.input, 'object');
});

test('tool_use id and tool_result tool_use_id pair on call_id, so v2 pairing works', () => {
  const items = parseCodexRollout([
    rec(5, 'response_item', { type: 'custom_tool_call', id: 'c1', call_id: 'call_A', name: 'shell', input: '{}' }),
    rec(6, 'response_item', { type: 'custom_tool_call_output', call_id: 'call_A',
                              output: [{ type: 'input_text', text: 'ok' }] }),
  ]);
  const use = items.find((i) => i.kind === 'tool_use');
  const result = items.find((i) => i.kind === 'tool_result');
  assert.equal(use.id, result.tool_use_id);
});

// ------------------------------------------------- privacy: encrypted reasoning

test('reasoning with an empty summary emits NOTHING and never leaks ciphertext', () => {
  const CIPHER = 'gAAAAABqqLiDBg3J4juxoNnJy5ef26SWlyA3rdZAjggyL';
  const items = parseCodexRollout([
    rec(9, 'response_item', { type: 'reasoning', id: 'r1', summary: [], encrypted_content: CIPHER }),
  ]);
  assert.equal(items.length, 0, 'summary is empty in every real record — emit nothing');
  assert.equal(JSON.stringify(items).includes(CIPHER), false, 'ciphertext must never reach the client');
});

test('reasoning WITH a summary renders as thinking, and still never carries ciphertext', () => {
  const items = parseCodexRollout([
    rec(9, 'response_item', { type: 'reasoning', id: 'r1', summary: [{ type: 'summary_text', text: 'weighing options' }],
                              encrypted_content: 'SECRET' }),
  ]);
  assert.equal(items[0].kind, 'thinking');
  assert.equal(items[0].text, 'weighing options');
  assert.equal(JSON.stringify(items).includes('SECRET'), false);
});

// ---------------------------------------------------------------- ordering

test('items come out in ordinal order, not file order', () => {
  const items = parseCodexRollout([
    rec(9, 'response_item', { type: 'message', id: 'b', role: 'user', content: [{ type: 'input_text', text: 'second' }] }),
    rec(2, 'response_item', { type: 'message', id: 'a', role: 'user', content: [{ type: 'input_text', text: 'first' }] }),
  ]);
  assert.deepEqual(items.map((i) => i.text), ['first', 'second']);
});

test('telemetry and lifecycle records are dropped, not rendered', () => {
  const items = parseCodexRollout([
    rec(1, 'session_meta', { session_id: 's', cwd: '/x' }),
    rec(2, 'token_usage_record', { thread_id: 't' }),
    rec(3, 'event_msg', { type: 'token_count', info: {} }),
    rec(4, 'world_state', { full: true }),
    rec(5, 'turn_context', { turn_id: 't' }),
  ]);
  assert.equal(items.length, 0);
});

test('a torn final line does not lose the valid lines before it', () => {
  const items = parseCodexRollout([
    rec(2, 'response_item', { type: 'message', id: 'a', role: 'user', content: [{ type: 'input_text', text: 'kept' }] }),
    '{"timestamp":"2026-09-24T08:40:15.000Z","ordinal":3,"type":"resp',
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0].text, 'kept');
});

// ---------------------------------------------------------------- detection

test('codex lines are detected by shape, so SSE and poll cannot disagree', () => {
  const lines = [rec(1, 'session_meta', { session_id: 's', cwd: '/x' })];
  assert.equal(looksLikeCodex(lines), true);
  assert.equal(looksLikeCodex(['{"type":"user","message":{"role":"user"}}']), false);
});

test('normalizeTranscript routes codex lines through the codex parser', () => {
  const env = normalizeTranscript([
    rec(1, 'session_meta', { session_id: 's', cwd: '/x' }),
    rec(2, 'response_item', { type: 'message', id: 'a', role: 'user', content: [{ type: 'input_text', text: 'hello' }] }),
  ], 'gm', 's');
  assert.equal(env.items.length, 1, 'codex lines fell through to the Claude parser and produced nothing');
  assert.equal(env.items[0].text, 'hello');
});

// ---------------------------------------------------------------- resolution

function fixtureDb(rows: any[]): string {
  const dir = mkdtempSync(join(tmpdir(), 'codexdb-'));
  const db = new Database(join(dir, 'state_5.sqlite'));
  db.exec('CREATE TABLE threads (id TEXT, rollout_path TEXT, cwd TEXT, thread_source TEXT, first_user_message TEXT, updated_at_ms INTEGER)');
  const ins = db.prepare('INSERT INTO threads VALUES (?,?,?,?,?,?)');
  for (const r of rows) ins.run(r.id, r.rollout_path, r.cwd, r.thread_source, r.first_user_message, r.updated_at_ms);
  db.close();
  return join(dir, 'state_5.sqlite');
}

const ROLLOUT = '/home/operator/.codex/sessions/2026/09/24/rollout-x.jsonl';

test('a seat resolves by its OWN declared name', () => {
  const db = fixtureDb([
    { id: 's1', rollout_path: ROLLOUT, cwd: '/home/operator/orchestraos', thread_source: 'user',
      first_user_message: 'You are gm. Read /tmp/agent-init-gm.md and follow all instructions in it.', updated_at_ms: 2 },
  ]);
  const got = findCodexByDeclaration('gm', db);
  assert.equal(got?.path, ROLLOUT);
  assert.equal(got?.sid, 's1');
});

test('THE EXPOSURE FENCE: a seat with no declaration match resolves to NOTHING', () => {
  // Every seat shares repo_root by construction, so a cwd/recency fallback would hand this
  // seat someone else's conversation. If that fallback is ever reintroduced, this test goes red.
  const db = fixtureDb([
    { id: 's1', rollout_path: '/home/operator/.codex/sessions/2026/09/24/rollout-OTHER.jsonl',
      cwd: '/home/operator/orchestraos', thread_source: 'user',
      first_user_message: 'You are dev-x. Read /tmp/agent-init-dev-x.md', updated_at_ms: 99 },
  ]);
  assert.equal(findCodexByDeclaration('gm', db), null,
    'a same-cwd, more-recent thread for ANOTHER seat must never resolve for this one');
});

test('a private project rollout on the same box is never resolved for a seat', () => {
  const db = fixtureDb([
    { id: 's1', rollout_path: '/home/operator/.codex/sessions/2026/09/24/rollout-PRIVATE.jsonl',
      cwd: '/home/operator/private-client-work', thread_source: 'user',
      first_user_message: 'summarise the acquisition terms', updated_at_ms: 999 },
  ]);
  assert.equal(findCodexByDeclaration('gm', db), null);
});

test('subagent threads are excluded — only a real seat thread resolves', () => {
  const db = fixtureDb([
    { id: 'sub', rollout_path: '/x/sub.jsonl', cwd: '/home/operator/orchestraos', thread_source: 'subagent',
      first_user_message: 'You are gm. do a thing', updated_at_ms: 99 },
  ]);
  assert.equal(findCodexByDeclaration('gm', db), null);
});

test('when one seat has several threads, the most recent wins', () => {
  const db = fixtureDb([
    { id: 'old', rollout_path: '/x/old.jsonl', cwd: '/c', thread_source: 'user',
      first_user_message: 'You are gm. older', updated_at_ms: 1 },
    { id: 'new', rollout_path: '/x/new.jsonl', cwd: '/c', thread_source: 'user',
      first_user_message: 'You are gm. newer', updated_at_ms: 5 },
  ]);
  assert.equal(findCodexByDeclaration('gm', db)?.sid, 'new');
});

test('a generation suffix still matches its base seat name', () => {
  const db = fixtureDb([
    { id: 's1', rollout_path: ROLLOUT, cwd: '/c', thread_source: 'user',
      first_user_message: 'You are gm. Read /tmp/agent-init-gm.md', updated_at_ms: 2 },
  ]);
  assert.equal(findCodexByDeclaration('gm-g3', db)?.sid, 's1');
});

test('a seat name is matched whole — "gm" must not match "gm-watcher"', () => {
  const db = fixtureDb([
    { id: 's1', rollout_path: '/x/other.jsonl', cwd: '/c', thread_source: 'user',
      first_user_message: 'You are gm-watcher. Read /tmp/agent-init-gm-watcher.md', updated_at_ms: 9 },
  ]);
  assert.equal(findCodexByDeclaration('gm', db), null);
});

// ---------------------------------------------------------------- degradation

test('a missing database degrades to no match instead of throwing', () => {
  assert.equal(findCodexByDeclaration('gm', '/nonexistent/state_5.sqlite'), null);
});

test('a database with no threads table degrades to no match instead of throwing', () => {
  const dir = mkdtempSync(join(tmpdir(), 'codexdb-'));
  const p = join(dir, 'state_5.sqlite');
  const db = new Database(p);
  db.exec('CREATE TABLE unrelated (x TEXT)');
  db.close();
  assert.equal(findCodexByDeclaration('gm', p), null);
});

test('a corrupt database file degrades to no match instead of throwing', () => {
  const dir = mkdtempSync(join(tmpdir(), 'codexdb-'));
  mkdirSync(dir, { recursive: true });
  const p = join(dir, 'state_5.sqlite');
  writeFileSync(p, 'this is not a sqlite file');
  assert.equal(findCodexByDeclaration('gm', p), null);
});

// ------------------------------------------- plumbing must not read as operator speech

test('codex harness envelopes are marked system, not rendered as the operator talking', () => {
  // Seen on a live seat: codex injects these as role 'user'. Without this they render as blue
  // user bubbles the operator never typed — the defect class already fixed for Claude in 55134d0.
  for (const tag of ['environment_context', 'skills_instructions', 'multi_agent_mode', 'user_instructions']) {
    const items = parseCodexRollout([
      rec(2, 'response_item', { type: 'message', id: 'p', role: 'user',
                                content: [{ type: 'input_text', text: `<${tag}>\n  <cwd>/srv/app</cwd>\n</${tag}>` }] }),
    ]);
    assert.equal(items[0].is_system, true, `<${tag}> must not render as operator speech`);
  }
});

test('a real operator message that merely mentions a tag is still operator speech', () => {
  const items = parseCodexRollout([
    rec(2, 'response_item', { type: 'message', id: 'u', role: 'user',
                              content: [{ type: 'input_text', text: 'why does <environment_context> show up in my chat?' }] }),
  ]);
  assert.notEqual(items[0].is_system, true);
});
