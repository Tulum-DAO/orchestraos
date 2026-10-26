import { test } from 'node:test';
import assert from 'node:assert';
import { attachmentImages, attachmentPreamble, splitAttachmentPreamble, uploadUrl } from './arturoUpload.ts';

test('a sent message keeps its images after a reload, without the note for the brain', () => {
  const files = [{ name: 'shot.png', path: '/home/x/.orchestra/state/uploads/1792_ab.png', size: 10 },
                 { name: 'spec.docx', path: '/home/x/.orchestra/state/uploads/1792_cd.docx', size: 10 }];
  const stored = `${attachmentPreamble(files)}\n\nwhat's in this?`;
  const out = splitAttachmentPreamble(stored);
  assert.equal(out.text, "what's in this?");
  assert.deepEqual(out.images, ['/uploads/1792_ab.png']);
  assert.deepEqual(attachmentImages(files), ['/uploads/1792_ab.png']);
  assert.deepEqual(splitAttachmentPreamble('just text'), { text: 'just text', images: [] });
});

test('only a file inside the uploads directory gets a URL', () => {
  assert.equal(uploadUrl('/etc/passwd'), null);
  assert.equal(uploadUrl('/home/x/.orchestra/state/uploads/../x.png'), null);
  assert.equal(uploadUrl('/home/x/.orchestra/state/uploads/a b.png'), null);
});
