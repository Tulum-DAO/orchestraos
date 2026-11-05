// The Modern chat style draws the WHOLE thread in Modern (gm, 2026-11-04: an agent set to Modern drew its call rows,
// and a message just sent, as Bubbles). Built from gm's real transcript (fixtures/gm-chat-skin.json): every operator
// line and every reply is a Modern message; only a machine line (another agent's message, a decision answer) keeps its card.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildRenderList, type ChatItem } from './transcript.ts';
import { markResults } from './threadKit/results.ts';
import { groupToolRuns } from './toolGroups.ts';
import { foldCardAnswers } from './systemMessages.ts';
import { interleave } from './decisions.ts';
import { agentRows, modernUserParts } from './threadKit/view.ts';
import { pendingLabel } from './pendingSends.ts';

const FIXTURE = JSON.parse(readFileSync(new URL('./fixtures/gm-chat-skin.json', import.meta.url), 'utf-8')) as { items: ChatItem[] };
const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

function modernRows() {
  const nodes = groupToolRuns(markResults(buildRenderList(FIXTURE.items), 'gm'));
  const shown = foldCardAnswers(nodes as never, new Set()) as typeof nodes;
  return agentRows(interleave(shown, []), [], 'idle', { modern: true });
}

test('gm in Modern: every operator line and reply is a Modern message, call rows included; only machine lines keep cards', () => {
  const rows = modernRows().flatMap((r) => (r.kind === 'node' && (r.node.kind === 'user' || r.node.kind === 'assistant') ? [r] : []));
  const cards = rows.filter((r) => !r.message).map((r) => r.node as { kind: string; text: string; machine?: string });
  assert.deepEqual(cards.map((n) => n.machine), ['relay', 'decision', 'relay', 'relay'], JSON.stringify(cards.map((n) => n.text.slice(0, 50))));
  const calls = rows.filter((r) => r.node.kind === 'user' && r.node.text.includes('[voice-call:'));
  assert.equal(calls.length, 6);
  for (const r of calls) assert.equal(r.message?.role, 'user');
});

test("a call row in Modern: its words and its call card, in order; a call's ending line is chrome", () => {
  assert.deepEqual(modernUserParts('Weird\n[voice-call: vc_client_108666099675 <data-dir>/state/voice-calls/vc_client_108666099675.json]'),
    [{ kind: 'words', text: 'Weird' }, { kind: 'call', callId: 'vc_client_108666099675' }]);
  assert.deepEqual(modernUserParts('Call ended (vc_sample_call01). [voice-call: vc_sample_call01 /data/x.json]'),
    [{ kind: 'call', callId: 'vc_sample_call01' }]);
  assert.deepEqual(modernUserParts('just words'), [{ kind: 'words', text: 'just words' }]);
});

test("the Modern user message draws a call row's parts itself: Modern words and the Modern call card", () => {
  const view = src('components/kit/ThreadView.tsx');
  assert.match(view, /modernUserParts\(m\.text\)/);
  assert.match(view, /<CallCard key=\{i\} skin="modern" callId=\{p\.callId\} \/>/);
});

test("a just-sent message is drawn in the thread's skin: Modern words in Modern, the bubble in Bubbles; one wording", () => {
  assert.deepEqual((['sent', 'queuedInTurn', 'held', 'queued'] as const).map(pendingLabel),
    [null, 'queued', 'held for its next turn', 'saved to its inbox']);
  const view = src('components/kit/ThreadView.tsx');
  assert.match(view, /skin === 'modern' \? <ModernPending key=\{r\.key\} text=\{r\.text\} kind=\{r\.pending\} \/> : <PendingBubble /);
  assert.match(src('components/chat/TranscriptCards.tsx'), /pendingLabel\(kind\)/);
});
