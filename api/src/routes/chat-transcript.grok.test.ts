/**
 * Grok chat reads updates.jsonl. A grok seat must not fall through onto a
 * Claude JSONL that shares its cwd, and the Claude parser must not be handed
 * ACP session/update lines (that produced an empty chat for Codex before
 * parseCodexRollout existed).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const SEAT = 'zz-grok-seat';
const SID = '0199aaaa-0000-7000-8000-0000000000b1';
const root = mkdtempSync(join(tmpdir(), 'grok-transcript-'));
const home = join(root, 'home');
const orch = join(root, 'orch');
const project = join(home, '.claude', 'projects', '-repo');
mkdirSync(project, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;

// The seat's own records say grok (agent-sessions.json is what the resolver reads). No tmux
// session of this name exists, so there is no live process: the stamped sid is the fallback.
writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({
  [SEAT]: { session_id: SID, runtime: 'grok', tmux_session: SEAT, cwd: '/repo' },
}));
const grokHome = join(root, 'grok-home');
process.env.GROK_HOME = grokHome;

// A Claude transcript sitting where step 1 of the resolver would pick it up
// if the grok seat were allowed to fall through.
writeFileSync(join(project, `${SID}.jsonl`), JSON.stringify({
  type: 'user', message: { role: 'user', content: 'this is a claude file' },
}) + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({
  session_id: SID, cwd: '/repo', runtime: 'grok',
}));

const {
  looksLikeGrok, parseGrokUpdates, normalizeTranscript, resolveTranscriptPath,
  grokSidFromArgv, grokUpdatesPath,
} = await import('./chat-transcript.js');

const TS = 1791666990302;

function line(update: any, extra: any = {}): string {
  return JSON.stringify({
    timestamp: Math.floor(TS / 1000),
    method: 'session/update',
    params: {
      sessionId: SID,
      update,
      _meta: { eventId: extra.eventId || 'evt-1', agentTimestampMs: extra.ms || TS },
    },
  });
}

test('a grok seat does not resolve a Claude JSONL that shares its session id', () => {
  const got = resolveTranscriptPath(SEAT);
  assert.equal(got.path, null);
  assert.equal(got.sid, SID);
});

test('once the pinned session has an updates.jsonl, the grok seat resolves to it', () => {
  const dir = join(grokHome, 'sessions', '%2Frepo', SID);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'updates.jsonl'), '');
  const got = resolveTranscriptPath(SEAT);
  assert.equal(got.path, join(dir, 'updates.jsonl'));
  assert.equal(got.sid, SID);
  // a second dir with the same UUID is ambiguous: fail closed, never pick one
  mkdirSync(join(grokHome, 'sessions', '%2Fother', SID), { recursive: true });
  assert.equal(grokUpdatesPath(SID, join(grokHome, 'sessions')), null);
});

test('grok argv: -s wins, -r is the resume binding, a bare -r or a title is no identity', () => {
  const U = '0199aaaa-0000-7000-8000-0000000000b2';
  assert.equal(grokSidFromArgv(['/x/grok', '--always-approve', '-s', SID, '-r', U]), SID);
  assert.equal(grokSidFromArgv(['grok', '--minimal', '-r', U, '-m', 'grok-4.7']), U);
  assert.equal(grokSidFromArgv(['grok-linux-x86_64', `--resume=${U}`]), U);
  assert.equal(grokSidFromArgv(['grok', '-r', '-m', 'grok-4.7']), null);
  assert.equal(grokSidFromArgv(['grok', '-r', 'my title']), null);
  assert.equal(grokSidFromArgv(['grok', '-c']), null);
  assert.equal(grokSidFromArgv(['claude', '-s', SID]), null);
});

test('session/update lines are grok, not codex and not claude', () => {
  const lines = [line({ sessionUpdate: 'user_message_chunk', content: { type: 'text', text: 'hello' } })];
  assert.equal(looksLikeGrok(lines), true);
  const env = normalizeTranscript(lines, SEAT, SID, 50);
  assert.equal(env.items.length, 1);
  assert.equal(env.items[0].kind, 'text');
  assert.equal(env.items[0].role, 'user');
  assert.equal(env.items[0].text, 'hello');
  assert.equal(env.grammar_version, 2);
});

test('a codex rollout is not sniffed as grok', () => {
  const lines = [JSON.stringify({
    timestamp: '2026-10-10T00:00:00.000Z', ordinal: 1, type: 'response_item',
    payload: { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'hi' }] },
  })];
  assert.equal(looksLikeGrok(lines), false);
  const env = normalizeTranscript(lines, SEAT, SID, 50);
  assert.equal(env.items[0].text, 'hi');
  assert.equal(env.items[0].role, 'user');
});

test('user_query is unwrapped and a system reminder is not operator speech', () => {
  const items = parseGrokUpdates([
    line({ sessionUpdate: 'user_message_chunk', content: { type: 'text', text: '<user_query>\nBuild it\n</user_query>' } }, { eventId: 'u1' }),
    line({ sessionUpdate: 'turn_completed', stop_reason: 'end_turn' }, { eventId: 'gap' }),
    line({ sessionUpdate: 'user_message_chunk', content: { type: 'text', text: '<system-reminder>\nstay quiet\n</system-reminder>' } }, { eventId: 'u2' }),
  ]);
  assert.equal(items[0].text, 'Build it');
  assert.equal(items[0].is_system, undefined);
  assert.equal(items[1].is_system, true);
});

test('adjacent text chunks coalesce and a tool call keeps thoughts apart', () => {
  const items = parseGrokUpdates([
    line({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Hello ' } }, { eventId: 'a1' }),
    line({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'there' } }, { eventId: 'a2' }),
    line({ sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'one' } }, { eventId: 't1' }),
    line({
      sessionUpdate: 'tool_call', toolCallId: 'call-1', title: 'read_file',
      rawInput: { target_file: '/tmp/x' },
      _meta: { 'x.ai/tool': { name: 'read_file' } },
    }, { eventId: 'c1' }),
    line({ sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'two' } }, { eventId: 't2' }),
  ]);
  assert.equal(items[0].text, 'Hello there');
  assert.equal(items.filter((i: any) => i.kind === 'thinking').length, 2);
});

test('a completed tool pairs on toolCallId and a diff does not dump file bodies', () => {
  const env = normalizeTranscript([
    line({
      sessionUpdate: 'tool_call', toolCallId: 'call-9', title: 'search_replace',
      rawInput: { file_path: '/repo/src/a.ts', old_string: 'x'.repeat(50), new_string: 'y' },
      _meta: { 'x.ai/tool': { name: 'search_replace' } },
    }, { eventId: 'c9' }),
    line({
      sessionUpdate: 'tool_call_update', toolCallId: 'call-9', status: 'in_progress',
      content: [{ type: 'content', content: { type: 'text', text: 'partial' } }],
    }, { eventId: 'p9' }),
    line({
      sessionUpdate: 'tool_call_update', toolCallId: 'call-9', status: 'completed',
      content: [{ type: 'diff', path: '/repo/src/a.ts', oldText: 'SECRET-OLD', newText: 'SECRET-NEW' }],
    }, { eventId: 'd9' }),
  ], SEAT, SID, 50);
  const tool = env.render_items.find((n: any) => n.kind === 'tool');
  assert.equal(tool.tool, 'search_replace');
  assert.match(tool.summary, /a\.ts/);
  assert.equal(tool.result, 'edited /repo/src/a.ts');
  assert.equal(JSON.stringify(env).includes('SECRET-OLD'), false);
});

test('a long tool result is capped and task_completed replaces the earlier result', () => {
  const big = 'z'.repeat(5000);
  const env = normalizeTranscript([
    line({
      sessionUpdate: 'tool_call', toolCallId: 'call-b', title: 'run_terminal_command',
      rawInput: { command: 'echo hi', description: 'say hi' },
      _meta: { 'x.ai/tool': { name: 'run_terminal_command' } },
    }, { eventId: 'cb' }),
    line({
      sessionUpdate: 'tool_call_update', toolCallId: 'call-b', status: 'completed',
      content: [{ type: 'content', content: { type: 'text', text: 'backgrounded' } }],
    }, { eventId: 'ub' }),
    line({
      sessionUpdate: 'task_completed',
      task_snapshot: { task_id: 'call-b', output: big, exit_code: 0 },
    }, { eventId: 'tb' }),
  ], SEAT, SID, 50);
  const tool = env.render_items.find((n: any) => n.kind === 'tool');
  assert.equal(tool.summary, 'echo hi');
  assert.equal(tool.is_error, false);
  assert.ok(tool.result.startsWith('exit: 0\nzzz'));
  assert.ok(tool.result.endsWith('…[truncated]'));
  assert.ok(tool.result.length < big.length);
});

test('the spawn line is the system brief and a later operator line is not', () => {
  const env = normalizeTranscript([
    line({ sessionUpdate: 'user_message_chunk', content: { type: 'text', text: 'You are zz-grok-seat. Read the init.' } }, { eventId: 'boot' }),
    line({ sessionUpdate: 'turn_completed', stop_reason: 'end_turn' }, { eventId: 'gap' }),
    line({ sessionUpdate: 'user_message_chunk', content: { type: 'text', text: 'Build the adapter' } }, { eventId: 'ask', ms: TS + 1000 }),
  ], SEAT, SID, 50);
  const users = env.render_items.filter((n: any) => n.kind === 'user');
  assert.equal(users[0].is_system, true);
  assert.equal(users[1].is_system, undefined);
  assert.equal(users[1].text, 'Build the adapter');
});
