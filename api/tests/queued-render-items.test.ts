/**
 * buildRenderItems coherence for queued-native-render nodes (P1, DEC-1789392107605493).
 * Proves the server render_items[] carries the new node + the queued flag, so a
 * client consuming render_items renders identically to one consuming items[].
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRenderItems } from '../src/routes/chat-transcript.js';

test('queued_batch item passes through to a queued_batch render node', () => {
  const items = [
    { kind: 'queued_batch', count: 2, key: 'batch:BATCH1', ts: '2026-09-14T13:30:10.000+00:00',
      entries: [
        { agent: 'orchestra-builder', sent_ts: '2026-09-14T13:30:05.000+00:00', body: 'newer' },
        { agent: 'pm-infra', sent_ts: '2026-09-14T13:30:00.000+00:00', body: 'older' },
      ] },
  ];
  const nodes = buildRenderItems(items);
  assert.equal(nodes.length, 1);
  const n = nodes[0];
  assert.equal(n.kind, 'queued_batch');
  assert.equal(n.count, 2);
  assert.equal(n.key, 'batch:BATCH1');
  assert.equal(n.entries[0].body, 'newer');
  assert.equal(n.entries[1].body, 'older');
});

test('queued flag on a user text item is carried onto the render user node', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'hi from phone', queued: true, ts: '2026-09-14T13:40:00.000+00:00', uuid: 'queued:h1' },
  ];
  const nodes = buildRenderItems(items);
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].kind, 'user');
  assert.equal(nodes[0].queued, true);
  assert.equal(nodes[0].text, 'hi from phone');
});

test('a plain (non-queued) user turn does NOT gain a queued flag (backward-safe)', () => {
  const nodes = buildRenderItems([{ kind: 'text', role: 'user', text: 'normal', ts: '2026-09-14T13:00:00.000+00:00' }]);
  assert.equal(nodes[0].kind, 'user');
  assert.equal(nodes[0].queued, undefined);
});
