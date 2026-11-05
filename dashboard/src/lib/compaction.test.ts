import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addNotice, compactionText, isCompaction, latestNotice, withNotices, type CompactionItem } from './compaction.ts';
import { buildRenderList, type ChatItem } from './transcript.ts';

const notice = (over: Partial<CompactionItem> = {}): CompactionItem =>
  ({ kind: 'compaction', id: 'cmp-1', state: 'compacting', ts: '2026-10-31T07:00:05Z', ...over });
const line = (uuid: string, ts: string, text = uuid) => ({ kind: 'text', role: 'assistant', text, uuid, ts }) as ChatItem;

test('the row says "Compacting the chat…", then "The chat was compacted"; a notice that did not finish draws nothing', () => {
  assert.equal(compactionText('compacting'), 'Compacting the chat…');
  assert.equal(compactionText('compacted'), 'The chat was compacted');
  assert.equal(compactionText('failed'), null);
  assert.equal(isCompaction({ kind: 'compaction', id: 'cmp-2', state: 'compacting' }), true);
  assert.equal(isCompaction({ kind: 'compaction', id: '', state: 'compacting' }), false);
  assert.equal(isCompaction({ kind: 'compaction', id: 'cmp-2', state: 'other' }), false);
});

test('one notice, one id, changed in place; a late frame never takes it back; its tokens arrive later', () => {
  let n = addNotice({}, notice());
  n = addNotice(n, notice({ state: 'compacted', after_tokens: null, finished_at: '2026-10-31T07:00:09Z' }));
  assert.equal(n['cmp-1'].state, 'compacted');
  n = addNotice(n, notice());                                         // the "compacting" frame, late
  assert.equal(n['cmp-1'].state, 'compacted');
  n = addNotice(n, notice({ state: 'compacted', after_tokens: 4200 }));   // the first turn counted its tokens
  assert.equal(n['cmp-1'].after_tokens, 4200);
  assert.equal(n['cmp-1'].finished_at, '2026-10-31T07:00:09Z');
  n = addNotice(n, notice({ state: 'compacted', after_tokens: null }));   // a copy without them keeps them
  assert.equal(n['cmp-1'].after_tokens, 4200);
  assert.equal(Object.keys(addNotice(n, { kind: 'text' })).length, 1, 'anything else is ignored');
});

test('a notice from the stream goes where its time puts it; one the items hold is updated where it stands', () => {
  const items = [line('a', '2026-10-31T07:00:00Z'), line('b', '2026-10-31T07:00:10Z')];
  const placed = withNotices(items, addNotice({}, notice()));
  assert.deepEqual(placed.map((x) => ('uuid' in x ? x.uuid : x.id)), ['a', 'cmp-1', 'b']);
  const late = withNotices(items, addNotice({}, notice({ id: 'cmp-9', ts: '2026-10-31T08:00:00Z' })));
  assert.deepEqual(late.map((x) => ('uuid' in x ? x.uuid : x.id)), ['a', 'b', 'cmp-9']);
  const held = [items[0], notice(), items[1]];
  const updated = withNotices(held, addNotice({}, notice({ state: 'compacted' })));
  assert.equal(updated.length, 3);
  assert.equal((updated[1] as CompactionItem).state, 'compacted');
  assert.equal(withNotices(items, {}), items, 'no notices: the same items');
  // Older than the window's first line: it belongs to an older page (which brings it), never at the window's top.
  const old = withNotices(items, addNotice({}, notice({ id: 'cmp-0', ts: '2026-10-31T06:00:00Z' })));
  assert.deepEqual(old.map((x) => ('uuid' in x ? x.uuid : x.id)), ['a', 'b']);
});

test('in the chat: one line per notice, keyed by its id (a change redraws it in place); none for a failed one or none sent', () => {
  const nodes = buildRenderList([line('a', '2026-10-31T07:00:00Z'), notice() as ChatItem, notice({ id: 'cmp-2', state: 'failed' }) as ChatItem]);
  assert.deepEqual(nodes.map((n) => [n.kind, n.key]), [['assistant', 'a:0'], ['compaction', 'cmp-1']]);
  const done = buildRenderList([line('a', '2026-10-31T07:00:00Z'), notice({ state: 'compacted' }) as ChatItem]);
  assert.deepEqual(done.map((n) => [n.kind, n.key]), [['assistant', 'a:0'], ['compaction', 'cmp-1']]);
  // A seat whose notices are off gets none from the server: nothing is drawn, the other rows are untouched.
  assert.deepEqual(buildRenderList([line('a', '2026-10-31T07:00:00Z')]).map((n) => n.key), ['a:0']);
});

test('a /compact the person typed that failed: one short line, kept by its id; a late copy never loses what started it', () => {
  assert.equal(compactionText('failed', 'manual'), "Couldn't compact the chat right now.");
  const failed = notice({ id: 'cmp-3', state: 'failed', trigger: 'manual' });
  const nodes = buildRenderList([line('a', '2026-10-31T07:00:00Z'), failed as ChatItem]);
  assert.deepEqual(nodes.map((n) => [n.kind, n.key]), [['assistant', 'a:0'], ['compaction', 'cmp-3']]);
  assert.equal((nodes[1] as { trigger?: string }).trigger, 'manual');
  let n = addNotice({}, notice({ id: 'cmp-3', trigger: 'manual' }));
  n = addNotice(n, notice({ id: 'cmp-3', state: 'failed' }));          // the finishing copy, without the trigger
  assert.equal(n['cmp-3'].trigger, 'manual');
  assert.equal(compactionText(n['cmp-3'].state, n['cmp-3'].trigger), "Couldn't compact the chat right now.");
});

test('an automatic attempt that failed draws nothing (the server never sends one; nothing shows if it does)', () => {
  assert.equal(compactionText('failed', 'auto'), null);
  const nodes = buildRenderList([line('a', '2026-10-31T07:00:00Z'), notice({ id: 'cmp-4', state: 'failed', trigger: 'auto' }) as ChatItem]);
  assert.deepEqual(nodes.map((x) => x.key), ['a:0']);
});

test('what people read never names the machinery behind it', () => {
  const words = [compactionText('compacting'), compactionText('compacted'), compactionText('failed', 'manual')].join(' ');
  assert.doesNotMatch(words, /\b(session|rotation|rotate|handoff|briefing|packet)\b/i);
});

test('the newest notice of a list, for "Compact now"', () => {
  const a = notice({ id: 'cmp-1', ts: '2026-10-31T07:00:00Z', state: 'compacted' });
  const b = notice({ id: 'cmp-2', ts: '2026-10-31T08:00:00Z' });
  assert.equal(latestNotice([line('x', '2026-10-31T09:00:00Z'), b, a])?.id, 'cmp-2');
  assert.equal(latestNotice([line('x', '2026-10-31T09:00:00Z')]), null);
});
