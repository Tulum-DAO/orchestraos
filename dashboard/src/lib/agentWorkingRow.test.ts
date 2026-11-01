import { test } from 'node:test';
import assert from 'node:assert/strict';
import { workingRowFor, dotState, rowShown, type WorkingRowInput } from './agentWorkingRow.ts';
import { normalizeAgentState, STATE_STYLE } from './agentStatus.ts';
import { railPendingMenu, railStatus } from './agentChip.ts';
import { avatarStateFor } from './avatarStatus.ts';

const live = { health: 'live' as const, lastSeenAt: Date.parse('2026-01-01T10:15:00') };
const NOW = Date.parse('2026-01-01T10:20:00Z');
const since = new Date(NOW - 64_000).toISOString();

// Every state the old status line had, and what the working row must still say for it.
const TABLE: Array<[string, WorkingRowInput, { kind: string; text: RegExp; elapsed?: string; resume?: boolean; state?: string | null }]> = [
  ['disconnected', { status: 'working', feed: { health: 'disconnected', lastSeenAt: live.lastSeenAt } },
    { kind: 'disconnected', text: /^Not connected · last seen 10:15$/, state: null }],
  ['stale feed', { status: 'idle', feed: { health: 'stale', lastSeenAt: live.lastSeenAt } },
    { kind: 'disconnected', text: /^Connection lost · last seen/, state: null }],
  ['updating', { status: 'working', feed: { health: 'updating' } }, { kind: 'updating', text: /^Status updating…$/, state: null }],
  ['live step: tool', { status: 'working', feed: live, step: { agent_id: 'dev-demo', kind: 'tool', tool: 'Read', summary: 'notes.md', since } },
    { kind: 'step', text: /^Reading notes\.md$/, elapsed: '1m 4s', state: 'working' }],
  ['live step: thinking', { status: 'thinking', feed: live, step: { agent_id: 'dev-demo', kind: 'thinking', since } },
    { kind: 'step', text: /^Thinking…$/, state: 'working' }],
  ['live step: waiting', { status: 'waiting_permission', feed: live, step: { agent_id: 'dev-demo', kind: 'waiting' } },
    { kind: 'waitingStep', text: /^Waiting for your answer$/, state: 'waiting' }],
  ['live step: idle', { status: 'idle', feed: live, step: { agent_id: 'dev-demo', kind: 'idle' } },
    { kind: 'idleStep', text: /^Idle · ready for a message$/, state: 'idle' }],
  ['working with elapsed and tool', { status: 'working', feed: live, stateAgeS: 185, tool: 'Bash' },
    { kind: 'working', text: /^Working · Bash$/, elapsed: '3m 5s', state: 'working' }],
  ['stalled with elapsed', { status: 'stalled', feed: live, stateAgeS: 720 },
    { kind: 'working', text: /^Working$/, elapsed: '12m 0s', state: 'stalled' }],
  ['waiting', { status: 'waiting_permission', feed: live }, { kind: 'waiting', text: /^Waiting for you to answer$/, state: 'waiting' }],
  ['stranded', { status: 'stranded_input', feed: live }, { kind: 'stranded', text: /unsent draft/, state: 'stranded' }],
  ['queued', { status: 'queued_input', feed: live }, { kind: 'queued', text: /did not run — send it again/, state: 'queued' }],
  ['queued, idle step', { status: 'queued_input', feed: live, step: { agent_id: 'dev-demo', kind: 'idle' } },
    { kind: 'queued', text: /send it again/, state: 'queued' }],
  ['stopped', { status: 'stopped', feed: live }, { kind: 'down', text: /^Stopped — /, resume: true, state: 'stopped' }],
  ['crashed', { status: 'crashed', feed: live }, { kind: 'down', text: /^Crashed — /, resume: true, state: 'crashed' }],
  ['offline', { status: 'offline', feed: live }, { kind: 'down', text: /^Offline — /, resume: true, state: 'offline' }],
  ['retired: no Resume', { status: 'retired', feed: live }, { kind: 'down', text: /^Retired — /, resume: false, state: 'retired' }],
  ['stopped keeps its line over a step', { status: 'stopped', feed: live, step: { agent_id: 'dev-demo', kind: 'tool', tool: 'Read' } },
    { kind: 'down', text: /^Stopped/, resume: true, state: 'stopped' }],
  ['idle', { status: 'idle', feed: live }, { kind: 'idle', text: /^Idle · at the prompt, ready for a message$/, state: 'idle' }],
  ['unknown', { status: 'something-new', feed: live }, { kind: 'unknown', text: /^State unknown$/, state: 'unknown' }],
  ['absent', { feed: live }, { kind: 'unknown', text: /^State unknown$/, state: 'unknown' }],
  ['a menu on screen needs you', { status: 'working', pendingMenu: true, feed: live },
    { kind: 'waiting', text: /^Waiting for you to answer$/, state: 'working' }],
  ['the chip decides, as on the dot: down', { status: 'idle', chip: { status: 'down' }, feed: live },
    { kind: 'down', text: /^Crashed/, resume: true, state: 'crashed' }],
  ['the chip decides: needs you', { status: 'idle', chip: { status: 'needs_you' }, feed: live },
    { kind: 'waiting', text: /^Waiting for you/, state: 'waiting' }],
  ['the chip decides: external is unknown', { status: 'working', chip: { kind: 'external', status: 'working' }, feed: live },
    { kind: 'unknown', text: /^State unknown$/, state: 'unknown' }],
];

for (const [name, input, want] of TABLE) {
  test(`working row: ${name}`, () => {
    const row = workingRowFor({ ...input, now: NOW });
    assert.equal(row.kind, want.kind);
    assert.match(row.text, want.text);
    if (want.elapsed !== undefined) assert.equal(row.elapsed, want.elapsed);
    assert.equal(row.resume, want.resume ?? false);
    if (want.state !== undefined) assert.equal(row.state, want.state);
  });
}

test('the row reads the same state as the sidebar dot, through the same normalizeAgentState', () => {
  for (const [, input] of TABLE) {
    const rail = normalizeAgentState(railStatus(input.chip, input.status));
    const d = dotState(input);
    assert.equal(d.state, rail);
    assert.equal(d.menu, railPendingMenu(input.chip, !!input.pendingMenu));
    // Each state has the dot's own style, and the avatar's face comes from the same reading as the rail's.
    assert.ok(STATE_STYLE[d.state]);
    assert.equal(avatarStateFor(d.state, d.menu), avatarStateFor(railStatus(input.chip, input.status), railPendingMenu(input.chip, !!input.pendingMenu)));
    const row = workingRowFor({ ...input, now: NOW });
    if (row.state !== null) assert.equal(row.state, rail);
  }
});

test('the row shows for work and for every state that needs the person; idle and unknown hide it', () => {
  const shown = (i: WorkingRowInput) => rowShown(workingRowFor({ ...i, now: NOW }));
  const hidden = TABLE.filter(([, i]) => !shown(i)).map(([n]) => n);
  assert.deepEqual(hidden, ['live step: idle', 'idle', 'unknown', 'absent', 'the chip decides: external is unknown']);
  for (const name of ['disconnected', 'stale feed', 'updating', 'live step: tool', 'live step: thinking', 'live step: waiting',
    'working with elapsed and tool', 'stalled with elapsed', 'waiting', 'stranded', 'queued', 'queued, idle step', 'stopped', 'crashed',
    'offline', 'retired: no Resume', 'a menu on screen needs you', 'the chip decides, as on the dot: down', 'the chip decides: needs you']) {
    const row = TABLE.find(([n]) => n === name);
    assert.ok(row && shown(row[1]), name);
  }
});
