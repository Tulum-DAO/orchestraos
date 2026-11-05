import { test } from 'node:test';
import assert from 'node:assert/strict';
import { describeSendState, isFailed } from './agentSend.ts';

test('a refused "/compact" says the server\'s reason in words, not the HTTP status', () => {
  const r = { httpStatus: 409, ok: false, reason: 'compact_refused', detail: "Compacting isn't turned on for this agent yet." } as never;
  assert.equal(isFailed(r), true);
  assert.equal(describeSendState(r), "Compacting isn't turned on for this agent yet.");
  assert.equal(describeSendState({ httpStatus: 409 } as never), 'Send failed (HTTP 409) — not delivered');   // no reason given: as before
});
