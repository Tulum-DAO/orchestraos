/**
 * Images ride from the transcript item to the row every surface draws: the render node
 * (lib/transcript.ts), the thread kit's message and tool run (lib/threadKit/thread.ts, Modern and
 * Bubbles alike), and Arturo's turns (the API's image_refs on his thread, lib/arturoResume).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRenderList, type ChatItem } from './transcript';
import { agentItem, arturoMessage, threadFromTranscript } from './threadKit/thread';
import { groupToolRuns } from './toolGroups';
import { hydrateTurns } from './arturoResume';

const ID = 'cd'.repeat(16);
const img = { id: ID, path: '/srv/p/shot.png', name: 'shot.png' };

test('a stamped reply becomes an assistant node with its images; one without stays bare', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'show me', uuid: 'u1' },
    { kind: 'text', role: 'assistant', text: 'Here: /srv/p/shot.png', uuid: 'u2', images: [img] },
    { kind: 'text', role: 'assistant', text: 'plain', uuid: 'u3' },
  ] as ChatItem[];
  const nodes = buildRenderList(items);
  assert.equal(nodes[1].kind, 'assistant');
  assert.deepEqual((nodes[1] as { images?: unknown }).images, [img]);
  assert.equal('images' in nodes[2], false);
  assert.equal('images' in nodes[0], false);
});

test('a Write that made an image keeps it on the tool block, through the group, into the thread run', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'plot', uuid: 'u1' },
    { kind: 'tool_use', role: 'assistant', tool: 'Write', input: { file_path: '/srv/p/shot.png' }, summary: 'shot.png', id: 't1', uuid: 'u2', images: [img] },
    { kind: 'tool_result', role: 'user', text: 'ok', tool_use_id: 't1', uuid: 'u3' },
    { kind: 'tool_use', role: 'assistant', tool: 'Bash', input: { command: 'ls' }, summary: 'ls', id: 't2', uuid: 'u4' },
    { kind: 'tool_result', role: 'user', text: 'a', tool_use_id: 't2', uuid: 'u5' },
  ] as ChatItem[];
  const nodes = buildRenderList(items);
  const tool = nodes.find((n) => n.kind === 'tool')!;
  assert.deepEqual((tool as { images?: unknown }).images, [img]);
  const grouped = groupToolRuns(nodes);
  const group = grouped.find((n) => n.kind === 'tool_group')!;
  const run = agentItem(group)!;
  assert.equal(run.kind, 'tools');
  assert.deepEqual((run as { images?: unknown }).images, [img]);
  // The whole way, as the chat builds it.
  const thread = threadFromTranscript(items, 'gm');
  const tools = thread.items.find((x) => x.kind === 'tools')!;
  assert.deepEqual((tools as { images?: unknown }).images, [img]);
});

test('the thread message of a stamped reply carries its images (Modern draws from it)', () => {
  const m = agentItem({ kind: 'assistant', text: 'Here: /srv/p/shot.png', key: 'k', images: [img] })!;
  assert.equal(m.kind, 'message');
  assert.deepEqual((m as { images?: unknown }).images, [img]);
  const bare = agentItem({ kind: 'assistant', text: 'plain', key: 'k2' })!;
  assert.equal('images' in bare, false);
});

test('Arturo\'s turn carries the API\'s image_refs through hydration into the thread message; the client finds none itself', () => {
  const turns = hydrateTurns([
    { role: 'user', content: 'look at /tmp/out/plot.png', ts: 1 },
    { role: 'assistant', content: 'Made /tmp/out/plot.png for you', ts: 2, image_refs: [{ id: ID, path: '/tmp/out/plot.png', name: 'plot.png' }] },
    { role: 'assistant', content: 'Also /tmp/out/unstamped.png', ts: 3 },
  ]);
  assert.deepEqual(turns[1].imageRefs, [{ id: ID, path: '/tmp/out/plot.png', name: 'plot.png' }]);
  assert.equal('imageRefs' in turns[0], false, 'the operator\'s turn never carries refs');
  assert.equal('imageRefs' in turns[2], false, 'a path the server did not stamp is just words');
  assert.deepEqual(arturoMessage(turns[1], 'a').images, [{ id: ID, path: '/tmp/out/plot.png', name: 'plot.png' }]);
  assert.equal('images' in arturoMessage(turns[2], 'b'), false);
  assert.equal('images' in arturoMessage({ role: 'arturo', text: 'streaming /tmp/out/plot.png', streaming: true }, 'c'), false);
  // A ref whose id is not an id (a path smuggled in) is dropped on the client too.
  assert.equal('images' in arturoMessage({ role: 'arturo', text: 'x', imageRefs: [{ id: '/tmp/out/plot.png', path: '/tmp/out/plot.png', name: 'plot.png' }] }, 'd'), false);
});
