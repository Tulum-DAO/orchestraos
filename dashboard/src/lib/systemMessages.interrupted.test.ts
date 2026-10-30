// A headless seat's cut turn: the API's line (api/src/routes/chat-transcript.ts interruptedLine) reads as a red
// "Turn interrupted" card naming the message, and other system lines are untouched.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSystemMessage, machineKindOf } from './systemMessages.ts';

const LINE = '[TURN INTERRUPTED] The agent\'s process stopped before it finished answering this message, '
  + 'and the message was not sent again. Message: "Sample: deploy the drawer"';

test('a cut turn is a machine line', () => {
  assert.equal(machineKindOf(LINE), 'system');
});

test('it reads as a red "Turn interrupted" card naming the message', () => {
  const m = parseSystemMessage(LINE, 'system')!;
  assert.equal(m.headline, 'Turn interrupted');
  assert.equal(m.subject, 'Sample: deploy the drawer');
  assert.equal(m.outcome, 'failed');
  assert.match(m.note || '', /Not sent again/);
});

test('other system lines keep their rendering', () => {
  assert.equal(parseSystemMessage('[SOMETHING ELSE] x', 'system'), null);
});
