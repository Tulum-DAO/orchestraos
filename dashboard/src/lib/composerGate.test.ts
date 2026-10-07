import { test } from 'node:test';
import assert from 'node:assert/strict';
import { composerGate, delegatedWorkLabel, refusalCopy, refusalHeadline, sendPanelHeadline, shouldSendPhoto, clearsComposer, retryText, canForceRetry, STATE_COPY } from './composerGate.ts';

// THE BUG (Shaw, 2026-10-06): a seat running a sub-agent read `working`, so chat refused to
// send — while its CLI was accepting and queueing the same message. Measured live: 17 seats
// had delegated work in flight; three were in exactly that state, one with the operator's
// message already queued on screen.

test('a WORKING seat can be sent to, and is told the message will be queued', () => {
  const g = composerGate({ state: 'working' });
  assert.equal(g.send, 'enabled', 'this is the reported bug: working must not block the send');
  assert.equal((g as { queued: boolean }).queued, true);
  assert.match((g as { reason: string }).reason, /queue/i);
});

test('`thinking` is the same case — the detector word must not slip the gate', () => {
  // The detector emits `thinking`; the web aliases it to `working`. All three live seats in
  // the bug report were `thinking`, so a gate written against `working` alone would miss
  // every real instance of it.
  const g = composerGate({ state: 'thinking' });
  assert.equal(g.send, 'enabled');
  assert.equal((g as { queued: boolean }).queued, true);
});

test('a running sub-agent NEVER gates the send, in any state', () => {
  // gm's ruling: subagents is informational. It must not become a second blocker.
  for (const state of ['working', 'idle', 'thinking', 'stalled']) {
    assert.equal(composerGate({ state, subagents: 3 }).send, 'enabled', state);
  }
});

test('a MENU on screen blocks the send — a keystroke there answers it', () => {
  // The protection actually worth keeping: the operator's message would be read as a choice
  // they never made.
  const g = composerGate({ state: 'idle', pendingMenu: { options: ['a', 'b'] } });
  assert.equal(g.send, 'blocked');
  assert.match((g as { reason: string }).reason, /question on screen|choice/i);
});

test('a menu blocks even when the seat is otherwise perfectly idle AND has subagents', () => {
  // Control: the menu rule must not be reachable only via some other blocked state.
  assert.equal(composerGate({ state: 'idle', subagents: 2, pendingMenu: { options: [] as string[], x: 1 } }).send, 'blocked');
});

test('a permission prompt blocks the send', () => {
  assert.equal(composerGate({ state: 'waiting' }).send, 'blocked');
});

test('a seat that is not running blocks the send', () => {
  for (const state of ['stopped', 'crashed', 'offline', 'retired']) {
    assert.equal(composerGate({ state }).send, 'blocked', state);
  }
});

test('an IDLE seat sends normally, with no queue warning', () => {
  // Positive control. Without this the gate could be "block everything" and still pass the
  // blocking tests above.
  const g = composerGate({ state: 'idle' });
  assert.equal(g.send, 'enabled');
  assert.equal((g as { queued: boolean }).queued, false);
});

test('an UNKNOWN state sends rather than blocks', () => {
  // Refusing on absent information is how a reachable agent becomes unreachable — the same
  // class of mistake as the bug itself.
  assert.equal(composerGate({ state: undefined }).send, 'enabled');
  assert.equal(composerGate({ state: 'something-new' }).send, 'enabled');
});

test('delegatedWorkLabel says nothing when there is nothing to say', () => {
  assert.equal(delegatedWorkLabel(0), null);
  assert.equal(delegatedWorkLabel(undefined), null);
  assert.equal(delegatedWorkLabel(1), '1 agent running');
  assert.equal(delegatedWorkLabel(3), '3 agents running');
});

// ---- THE WORDS, per state (gm msg_a855ebca, 2026-10-07) ---------------------------------------
// Shaw saw "the agent is busy" for a seat that was not busy: it held a draft, or waited on a
// prompt. The wire keeps reason:"busy"; every surface picks its words from `state`, verbatim.

test('every form of the stranded class gets the unsent-text words', () => {
  // The gateway sends "stranded" for BOTH stranded_input and queued_input; the detector words
  // reach the web too. All four must land on the same sentence.
  for (const s of ['stranded', 'stranded_input', 'queued_input', ' Stranded ']) {
    assert.equal(refusalCopy(s), "There's unsent text in this agent's input box — send or clear it first.", s);
  }
});

test('waiting / waiting_permission get the prompt-or-permission words', () => {
  for (const s of ['waiting', 'waiting_permission']) {
    assert.equal(refusalCopy(s), 'Waiting on a prompt or permission in this agent', s);
  }
});

test('POSITIVE CONTROL: states with no refusal words return null, so the caller falls back', () => {
  for (const s of ['working', 'thinking', 'idle', 'stalled', 'unknown', '', undefined]) {
    assert.equal(refusalCopy(s), null, String(s));
  }
});

test('the gate and the refusal say the SAME words for a waiting seat', () => {
  const g = composerGate({ state: 'waiting' });
  assert.equal((g as { reason: string }).reason, refusalCopy('waiting'));
});

test('stalled gets the agreed stalled words, NOT the working "ends this turn" promise', () => {
  const g = composerGate({ state: 'stalled' });
  assert.equal(g.send, 'enabled');
  assert.equal((g as { reason: string }).reason, STATE_COPY.stalledBefore);
  assert.equal(STATE_COPY.stalledBefore,
    "Will be queued — the agent hasn't moved in a while, so your message may wait until it does.");
  // control: working keeps its own words
  assert.match((composerGate({ state: 'working' }) as { reason: string }).reason, /when this turn ends/);
});

test('the refusal HEADLINE puts the state words first and falls back to the gateway text', () => {
  // Shaw's exact Esc case through the web: 409 with state "waiting" and the detector activity.
  assert.equal(refusalHeadline({ state: 'waiting', activity: 'Waiting for permission approval' }),
    'Waiting on a prompt or permission in this agent');
  assert.equal(refusalHeadline({ state: 'stranded', activity: "Unsubmitted input (0s): 'x'" }),
    "There's unsent text in this agent's input box — send or clear it first.");
  // fallbacks unchanged for states with no words of their own
  assert.equal(refusalHeadline({ state: 'crashed', activity: 'No tmux session' }), 'No tmux session');
  assert.equal(refusalHeadline({ state: 'crashed' }), 'crashed');
  assert.equal(refusalHeadline({}), 'agent busy');
});

test('a SUCCESSFUL queued/held send keeps its own note, never the unsent-text words', () => {
  // ChatInput fills the same panel for a success, with state 'queued' or 'held' (review of #197).
  // Telling Shaw to clear a draft that does not exist, for a message that DID reach the server,
  // is the exact kind of false copy this change exists to remove.
  assert.equal(refusalHeadline({ state: 'queued', activity: 'Queued — agent is busy' }), 'Queued — agent is busy');
  assert.equal(refusalHeadline({ state: 'held', activity: 'Held — will deliver at the next turn boundary' }),
    'Held — will deliver at the next turn boundary');
  assert.equal(refusalCopy('queued'), null);
  assert.equal(refusalCopy('held'), null);
});

// ---- gm msg_fbc3b9d0 -------------------------------------------------------------------------
test('a RETIRED seat is blocked with its own words, not the generic not-running ones', () => {
  const g = composerGate({ state: 'retired' });
  assert.equal(g.send, 'blocked');
  assert.equal((g as { reason: string }).reason, 'This agent is retired — a message would go nowhere');
  // control: a merely stopped seat keeps the generic words
  assert.match((composerGate({ state: 'stopped' }) as { reason: string }).reason, /not running/);
});

test('a SUCCESSFUL queued or held send NEVER renders "Not delivered"', () => {
  // gm msg_d8732aa5: a DURABLE queued result promises no turn; a mid-turn hold does (via describeSendState)
  const queued = sendPanelHeadline({ state: 'queued', activity: 'pane busy (busy) — durable-first per B1/D1' });
  assert.equal(queued, 'Saved to its inbox — it will be delivered when the agent can take it');
  assert.equal(sendPanelHeadline({ state: 'held', activity: 'Queued — it will read this when its turn ends' }),
    'Queued — it will read this when its turn ends');
  const held = sendPanelHeadline({ state: 'held', activity: 'agent pane is showing a menu/permission prompt' });
  assert.equal(held, 'agent pane is showing a menu/permission prompt');
  for (const h of [queued, held, sendPanelHeadline({ state: 'held' })]) {
    assert.doesNotMatch(h, /Not delivered/);
  }
});

test('POSITIVE CONTROL: a real refusal still says "Not delivered" with the state words', () => {
  assert.equal(sendPanelHeadline({ state: 'stranded', activity: 'x' }),
    "Not delivered — There's unsent text in this agent's input box — send or clear it first.");
  assert.equal(sendPanelHeadline({ state: 'crashed', activity: 'No tmux session' }),
    'Not delivered — No tmux session');
});

test('the photo: sent normally; dropped on a forced retry of a queued/held send; KEPT on a forced retry of a refusal', () => {
  assert.equal(shouldSendPhoto(true, false, undefined), true);
  assert.equal(shouldSendPhoto(true, true, 'queued'), false, 'already uploaded with the queued send');
  assert.equal(shouldSendPhoto(true, true, 'held'), false, 'already uploaded with the held send');
  assert.equal(shouldSendPhoto(true, true, 'stranded'), true, 'the refusal kept it; the retry must carry it');
  assert.equal(shouldSendPhoto(false, true, 'stranded'), false);
});

test('the composer clears when the message REACHED the server, and keeps it otherwise', () => {
  assert.equal(clearsComposer({ ok: true }), true);
  assert.equal(clearsComposer({ ok: false, queued: true }), true, 'queued reached the server');
  assert.equal(clearsComposer({ ok: false, held: true }), true, 'held reached the server');
  assert.equal(clearsComposer({ ok: false, refused: true }), false, 'a refusal keeps the text');
  assert.equal(clearsComposer({ ok: false }), false, 'a failure keeps the text');
});

test('a forced retry sends the box as it is NOW, not the stale refused attempt', () => {
  assert.equal(retryText(true, 'fixed typo', 'fixd typo'), 'fixed typo');
  assert.equal(retryText(true, '', 'fixd typo'), 'fixd typo', 'empty box -> the refused attempt');
  assert.equal(retryText(false, 'hello', 'old'), 'hello');
});

test('a forced retry KEEPS the attachment markers of the refused attempt on the edited text', () => {
  assert.equal(retryText(true, 'fixed typo', '[IMAGE: /up/a.png] fixd typo'), '[IMAGE: /up/a.png] fixed typo');
  assert.equal(retryText(true, 'x', '[IMAGE: /up/a.png] [FILE: /up/b.pdf] y'), '[IMAGE: /up/a.png] [FILE: /up/b.pdf] x');
  assert.equal(retryText(true, 'x', 'no markers here'), 'x');
  assert.equal(retryText(true, '', '[IMAGE: /up/a.png] y'), '[IMAGE: /up/a.png] y');
});

test('a forced retry of a refused photo-only send is allowed; an empty retry is not', () => {
  assert.equal(canForceRetry('', 'stranded', true), true, 'refused screenshot with no text must still be re-sendable');
  assert.equal(canForceRetry(undefined, 'stranded', true), true);
  assert.equal(canForceRetry('', 'stranded', false), false, 'nothing to send');
  assert.equal(canForceRetry('', 'queued', true), false, 'a queued photo was already uploaded');
  assert.equal(canForceRetry('hello', 'queued', false), true);
});
