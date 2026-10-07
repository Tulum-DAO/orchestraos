/** POST /feedback cannot write outside state/feedback. Imports the REAL module. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join, resolve, sep } from 'node:path';
import { feedbackFilename, containedPath, cleanSubmittedBy, BadFeedbackInput, SAFE_QID } from './feedbackPath.js';

const DIR = '/srv/orchestra/state/feedback';
const NOW = 1791000000000;

test('THE HOLE: a traversal id is refused before any path is built', () => {
  for (const id of ['../../x', '../escape', 'a/../../b', '..', '/etc/cron.d/x', 'a/b', 'a\\b', '.hidden', 'x\u0000y']) {
    assert.throws(() => feedbackFilename(id, NOW), BadFeedbackInput, `accepted ${JSON.stringify(id)}`);
  }
});

test('the precondition the fix relies on: the OLD construction really did escape', () => {
  // Proves the test above guards something real, not a hypothetical.
  const old = join(DIR, `${'../../x'}_${NOW}.json`);
  assert.ok(!resolve(old).startsWith(resolve(DIR) + sep), `old code wrote to ${old}`);
});

test('every real questionnaire id shape is still accepted', () => {
  // The 18 live ids are all [A-Za-z0-9_-]; representative shapes:
  for (const id of ['skyline-onboarding-review-v1', 'page-structure-v1', 'kai_instance_v1', 'Q42']) {
    assert.equal(feedbackFilename(id, NOW), `${id}_${NOW}.json`);
  }
});

test('a missing id still falls back to q_<ts>, as before', () => {
  for (const id of [undefined, null, '']) assert.equal(feedbackFilename(id, NOW), `q_${NOW}.json`);
});

test('a non-string id is refused, not stringified into a path', () => {
  for (const id of [{ toString: () => '../x' }, ['..', 'x'], 42]) {
    assert.throws(() => feedbackFilename(id as unknown, NOW), BadFeedbackInput);
  }
});

test('SECOND GUARD: containedPath refuses anything that resolves outside the directory', () => {
  // Independent of the id pattern — catches whatever a future loosening of SAFE_QID admits.
  assert.throws(() => containedPath(DIR, '../../x.json'), BadFeedbackInput);
  assert.throws(() => containedPath(DIR, '/etc/passwd'), BadFeedbackInput);
  assert.throws(() => containedPath(DIR, '.'), BadFeedbackInput, 'the directory itself is not a file inside it');
  assert.equal(containedPath(DIR, `ok_${NOW}.json`), resolve(DIR, `ok_${NOW}.json`));
});

test('a sibling directory sharing the prefix is NOT inside', () => {
  // state/feedback-evil starts with "state/feedback" as a string; the separator check stops it.
  assert.throws(() => containedPath(DIR, '../feedback-evil/x.json'), BadFeedbackInput);
});

test('ATTRIBUTION IS KEPT: real answerers are not overwritten with the operator', () => {
  for (const who of ['kai', 'noah', 'shaw', 'shaw · tranche A']) {
    assert.equal(cleanSubmittedBy(who, 'operator'), who);
  }
});

test('attribution is bounded, never trusted: junk falls back to the operator', () => {
  for (const bad of [undefined, null, 42, '', '   ', 'x'.repeat(65), 'a\nb', 'a\u0000b', { name: 'x' }]) {
    assert.equal(cleanSubmittedBy(bad as unknown, 'operator'), 'operator', `kept ${JSON.stringify(bad)}`);
  }
});

test('SAFE_QID is anchored at both ends', () => {
  assert.ok(!SAFE_QID.test('ok/../../x'));
  assert.ok(!SAFE_QID.test('../x-ok'));
});
