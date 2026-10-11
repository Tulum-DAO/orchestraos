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
