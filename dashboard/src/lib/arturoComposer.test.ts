import assert from 'node:assert/strict';
import test from 'node:test';
import { arturoComposerText } from './arturoComposer.ts';

test('keeps a typed Arturo request', () => {
  assert.equal(arturoComposerText('  Summarize this  ', 1), 'Summarize this');
});

test('makes one attached file sendable without typed text', () => {
  assert.equal(arturoComposerText('   ', 1), 'Please review the attached file.');
});

test('makes multiple attached files sendable without typed text', () => {
  assert.equal(arturoComposerText('', 2), 'Please review the attached files.');
});

test('does not manufacture a request for an empty composer', () => {
  assert.equal(arturoComposerText('', 0), '');
});

// A user report: "I didn't type that, and it shouldn't display"
import { isAttachmentFiller, arturoShownText } from './arturoComposer.ts';
import { splitAttachmentPreamble } from './arturoUpload.ts';

test('the filler Arturo reads is never shown as the operator\'s words', () => {
  assert.equal(arturoShownText('   '), '');
  assert.equal(arturoShownText(' what is this? '), 'what is this?');
  assert.ok(isAttachmentFiller('Please review the attached file.') && isAttachmentFiller('Please review the attached files.'));
  assert.ok(!isAttachmentFiller('Please review the attached file. Then tell me the price.'));
});

test('a reloaded image-only message shows the image and no text', () => {
  const stored = '[The operator attached a file on this machine. You can read it directly at the path shown:\n'
    + '- shot.png  (/home/x/.orchestra/state/uploads/1_ab.png)]\n\nPlease review the attached file.';
  const sent = splitAttachmentPreamble(stored);
  assert.equal(sent.text, '');
  assert.equal(sent.images.length, 1);
  const typed = splitAttachmentPreamble(stored.replace('Please review the attached file.', 'What is this?'));
  assert.equal(typed.text, 'What is this?');
});
