import { test } from 'node:test';
import assert from 'node:assert/strict';
import { draftSetters, readDraft, nextDraftId, useComposerDrafts, EMPTY_DRAFT } from '../stores/composerDrafts.ts';

const reset = () => useComposerDrafts.setState({ drafts: {} });

test('a draft is kept per agent, so leaving and coming back finds it', () => {
  reset();
  draftSetters('pm-demo').setText('half a thought');
  draftSetters('dev-demo').setText('something else');
  // the composer that wrote it is gone (Dev mode, another agent); a new one reads the same draft
  assert.equal(readDraft('pm-demo').text, 'half a thought');
  assert.equal(readDraft('dev-demo').text, 'something else');
});

test('setters take a value or an updater, like React state', () => {
  reset();
  const s = draftSetters('pm-demo');
  s.setText('a');
  s.setText((t) => t + 'b');
  s.setPastes([{ id: 1, content: 'x\ny', lines: 2, chars: 3 }]);
  s.setPastes((p) => [...p, { id: 2, content: 'z', lines: 1, chars: 1 }]);
  const d = readDraft('pm-demo');
  assert.equal(d.text, 'ab');
  assert.deepEqual(d.pastes.map((p) => p.id), [1, 2]);
});

test('attached files survive too', () => {
  reset();
  const file = { name: 'sample.png', type: 'image/png' } as unknown as File;
  draftSetters('pm-demo').setFiles([{ id: 1, file, preview: 'blob:sample' }]);
  assert.equal(readDraft('pm-demo').files[0].file, file);
});

test('an emptied draft is dropped and reads as empty', () => {
  reset();
  const s = draftSetters('pm-demo');
  s.setText('x');
  s.setText('');
  assert.deepEqual(useComposerDrafts.getState().drafts, {});
  assert.equal(readDraft('pm-demo'), EMPTY_DRAFT);
  assert.equal(readDraft('unknown-demo').text, '');
});

test('new ids start after the ones already in the draft', () => {
  assert.equal(nextDraftId([]), 1);
  assert.equal(nextDraftId([{ id: 3 }, { id: 1 }]), 4);
});
