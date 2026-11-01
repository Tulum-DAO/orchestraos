import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compactNowView, COMPACT_OFF } from './compactNow.ts';
import type { CompactionItem } from './compaction.ts';

const at = Date.parse('2026-10-31T08:00:00Z');
const notice = (over: Partial<CompactionItem>): CompactionItem =>
  ({ kind: 'compaction', id: 'cmp-1', state: 'compacting', trigger: 'manual', ts: '2026-10-31T08:00:10Z', ...over });

test('a seat that cannot compact yet: no button to tap, the reason up front', () => {
  const v = compactNowView({ canCompact: false, ask: { phase: 'idle' }, notice: null, showNotices: true });
  assert.deepEqual(v, { enabled: false, busy: false, line: COMPACT_OFF, tone: 'note' });
});

test('a server that does not say ahead: the tap is offered, and its refusal shown as written (and not offered again)', () => {
  assert.equal(compactNowView({ canCompact: undefined, ask: { phase: 'idle' }, notice: null, showNotices: true }).enabled, true);
  const v = compactNowView({ canCompact: undefined, ask: { phase: 'refused', text: COMPACT_OFF, final: true }, notice: null, showNotices: true });
  assert.deepEqual(v, { enabled: false, busy: false, line: COMPACT_OFF, tone: 'error' });
});

test('after a tap: waiting, then compacting, then the outcome, read from the chat\'s own notice', () => {
  const ask = { phase: 'sent' as const, at };
  const view = (n: CompactionItem | null) => compactNowView({ canCompact: true, ask, notice: n, showNotices: true });
  assert.deepEqual([view(null).busy, view(null).line], [true, 'Waiting for the agent to pause…']);
  // An older notice is not this tap's answer.
  assert.equal(view(notice({ ts: '2026-10-31T07:00:00Z', state: 'compacted' })).line, 'Waiting for the agent to pause…');
  assert.deepEqual([view(notice({})).busy, view(notice({})).line], [true, 'Compacting the chat…']);
  assert.deepEqual(view(notice({ state: 'compacted' })), { enabled: true, busy: false, line: 'The chat was compacted', tone: 'done' });
  assert.deepEqual(view(notice({ state: 'failed' })), { enabled: true, busy: false, line: "Couldn't compact the chat right now.", tone: 'error' });
  assert.equal(compactNowView({ canCompact: true, ask: { phase: 'sending', at }, notice: null, showNotices: true }).line, 'Asking…');
});

test('with compaction messages hidden there is no notice to read: it says only that it was asked', () => {
  const v = compactNowView({ canCompact: true, ask: { phase: 'sent', at }, notice: null, showNotices: false });
  assert.deepEqual(v, { enabled: true, busy: false, line: 'Asked. The chat compacts once the agent pauses.', tone: 'note' });
});
