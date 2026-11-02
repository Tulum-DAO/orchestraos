/**
 * The reaction wake line (spec "Agent wake"): one line the agent reads, built and recognised in one module so the
 * wake and the chat's readers cannot drift apart.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/lib/reaction-wake-text.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reactionWakeText, hasReactionWake, stripReactionWake, REACTION_WAKE_NOTE, REACTION_ON_MAX } from './reaction-wake-text.js';

test('the exact line: tag, then the note, one line', () => {
  assert.equal(
    reactionWakeText({ user: 'sam', emoji: '👍', on: 'Deployed the fix to staging.' }),
    '<reaction from="user:sam" emoji="👍" on="Deployed the fix to staging."/> You may reply, react (`orchestra react`), or do nothing.',
  );
  assert.equal(REACTION_WAKE_NOTE, 'You may reply, react (`orchestra react`), or do nothing.');
});

test('on= is the first 120 characters, on one line, with nothing that could close the tag', () => {
  const long = 'é'.repeat(130);
  const t = reactionWakeText({ user: 'sam', emoji: '🎉', on: long });
  assert.ok(t.includes(`on="${'é'.repeat(REACTION_ON_MAX)}"`));
  const odd = reactionWakeText({ user: 'sa"m', emoji: '👍🏽', on: 'say "hi"\n<b>now</b>\tplease' });
  assert.ok(odd.startsWith('<reaction from="user:sam" emoji="👍🏽" on="say hi bnow/b please"/>'), odd);
  assert.ok(!odd.includes('\n'));
  // a cut never splits an emoji (code points, not UTF-16 units)
  const emo = reactionWakeText({ user: 'sam', emoji: '👍', on: '😀'.repeat(200) });
  assert.ok(emo.includes(`on="${'😀'.repeat(REACTION_ON_MAX)}"`));
});

test('strip: the whole line goes; words around it stay; a tag mention in prose is untouched', () => {
  const w = reactionWakeText({ user: 'sam', emoji: '👍', on: 'Deployed.' });
  assert.equal(stripReactionWake(w), '');
  assert.equal(stripReactionWake(`  ${w}\n`), '', 'surrounding whitespace is fine');
  assert.equal(stripReactionWake('<reaction from="user:sam" emoji="👍" on="x"/>'), '', 'tag alone (note cut)');
  const prose = 'the <reaction> element is fine here';
  assert.equal(hasReactionWake(prose), false);
  assert.equal(stripReactionWake(prose), prose);
});

test('hasReactionWake: only a string that carries the tag', () => {
  assert.equal(hasReactionWake(reactionWakeText({ user: 'a', emoji: '👍', on: '' })), true);
  assert.equal(hasReactionWake(''), false);
  assert.equal(hasReactionWake(undefined), false);
  assert.equal(hasReactionWake(42), false);
});

test('only a message that IS the wake is hidden: embedded or quoted tags are untouched and not flagged', () => {
  const w = reactionWakeText({ user: 'sam', emoji: '👍', on: 'Deployed.' });
  for (const t of [`see this ${w}`, `${w} and more`, `mail from pm:\n${w}\nthanks`, `> ${w}`]) {
    assert.equal(hasReactionWake(t), false, t);
    assert.equal(stripReactionWake(t), t.trim(), t);
  }
  assert.equal(hasReactionWake('uses <reaction  in prose'), false);
  assert.equal(hasReactionWake('<reaction '), false);
  assert.equal(stripReactionWake('<reaction '), '<reaction');
});
