// Every case of the iOS SendOutcome grader, ported one for one, so the web and the phone agree on
// what may be said after a send. A 502 unverified_submit means delivery UNKNOWN: probe the
// transcript. It never means "not sent".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, requiresProbe, rowEvidences, chipDodgeVisibleText, normalize, prefixChars, UNVERIFIED_REASON,
} from './sendOutcome.ts';

test('502 unverified_submit: must be probed, never reported', () => {
  assert.equal(requiresProbe(UNVERIFIED_REASON), true);
});
test('502 unverified_submit: probe says the agent has it -> delivered', () => {
  assert.equal(classify('unverified_submit', true), 'delivered');
});
test('502 unverified_submit: probe says absent -> only NOW is "not sent" honest', () => {
  assert.equal(classify('unverified_submit', false), 'notSent');
});
test('502 unverified_submit: probe could not run -> unknown, never a failure claim', () => {
  assert.equal(classify('unverified_submit', null), 'unknown');
});

test('send_failed: needs no probe', () => {
  assert.equal(requiresProbe('send_failed'), false);
});
test('send_failed: a genuine not-sent even if a probe would have said true', () => {
  assert.equal(classify('send_failed', true), 'notSent');
});
test('send_failed: and with no probe at all', () => {
  assert.equal(classify('send_failed', null), 'notSent');
});

test('a missing reason is probed', () => {
  assert.equal(requiresProbe(null), true);
});
test('a reason nobody has mapped yet is probed', () => {
  assert.equal(requiresProbe('some_future_reason'), true);
});
test('unmapped reason + could not check -> unknown, not failure', () => {
  assert.equal(classify('some_future_reason', null), 'unknown');
});

const plain = 'Disregard the following:';
test('ordinary row: exact row matches', () => {
  assert.equal(rowEvidences(plain, plain), true);
});
test('ordinary row: row with surrounding text matches', () => {
  assert.equal(rowEvidences(plain, `${plain} thanks`), true);
});
test('ordinary row: an unrelated row does not match', () => {
  assert.equal(rowEvidences(plain, 'something else entirely'), false);
});
test('ordinary row: whitespace differences are normalized away', () => {
  assert.equal(rowEvidences('a  b\nc', 'a b c'), true);
});

// A chip-dodged row is SHORTER than what was sent, so a plain "contains" can never match it.
const sentLong = 'These three photos tell the story of attempting to send a photo that had not yet been finalized in. It\'s loading. (While attempting to send it to an idle Agent)\n[attached: "Photo Oct8 06.36.47.jpg" /home/demo/uploads/1791455816_dr29ix.jpg (jpg, 1.8 MB)]';
const banner = '[LONG-MSG chip-dodge] These three photos tell the story of attempting to send a photo that had not yet been finalized in. It\'s loading. (While... — FULL TEXT (529 chars): read /tmp/agent-inject-1791455840-9fc355e7.md';
test('chip-dodge: the banner IS recognised as evidence of the send', () => {
  assert.equal(rowEvidences(sentLong, banner), true);
});
test('chip-dodge: the pointer alone is enough, exactly', () => {
  assert.equal(rowEvidences('anything at all', banner, '/tmp/agent-inject-1791455840-9fc355e7.md'), true);
});
test('chip-dodge: a banner for a DIFFERENT message is not evidence', () => {
  assert.equal(rowEvidences('Completely unrelated text that shares no prefix', banner), false);
});

// The case that really needs the reversed direction: a SHORT typed line plus three attachment
// lines chip-dodges on newline count, so the key runs into markers the banner never contains.
const sentShort = 'Disregard the following:\n[attached: "Photo Oct8 06.36.47.jpg" /home/demo/uploads/1791455816_dr29ix.jpg (jpg, 1.8 MB)]\n[attached: "Photo Oct8 06.36.49.jpg" /home/demo/uploads/1791455819_lj7o4v.jpg (jpg, 2.1 MB)]\n[attached: "Photo Oct8 06.36.52.jpg" /home/demo/uploads/1791455819_35zw0o.jpg (jpg, 1.9 MB)]';
const shortTextBanner = '[LONG-MSG chip-dodge] Disregard the following:... — FULL TEXT (402 chars): read /tmp/agent-inject-1791455840-9fc355e7.md';
test('short text + 3 photos: the ordinary contains path CANNOT match this', () => {
  assert.equal(normalize(shortTextBanner).includes(prefixChars(normalize(sentShort), 60)), false);
});
test('short text + 3 photos: but the reversed prefix branch DOES recognise it', () => {
  assert.equal(rowEvidences(sentShort, shortTextBanner), true);
});
test('short text + 3 photos: and the pointer recognises it too, independently', () => {
  assert.equal(rowEvidences(sentShort, shortTextBanner, '/tmp/agent-inject-1791455840-9fc355e7.md'), true);
});
test('the visible prefix is extracted without tag or pointer tail', () => {
  assert.equal(chipDodgeVisibleText(banner)?.startsWith('These three photos tell the story'), true);
});
test('and it does not keep the FULL TEXT tail', () => {
  assert.equal(chipDodgeVisibleText(banner)?.includes('FULL TEXT'), false);
});
test('a non-banner row yields no visible prefix', () => {
  assert.equal(chipDodgeVisibleText(plain), null);
});

// False positives are the worse failure: they clear an undelivered draft.
const shortBanner = '[LONG-MSG chip-dodge] ok... — FULL TEXT (900 chars): read /tmp/agent-inject-zzz.md';
test('a banner shorter than the overlap floor is not evidence', () => {
  assert.equal(rowEvidences('ok but here is the actual long message', shortBanner), false);
});
test('an empty send is never evidenced', () => {
  assert.equal(rowEvidences('', banner), false);
});
test('an empty pointer is ignored rather than matching everything', () => {
  assert.equal(rowEvidences('nope', banner, ''), false);
});
