import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { cleanPrefsChange, readPrefs, writePrefs, isAgentId } from './agentPrefs.js';
import { writeUserPref } from './userPrefs.js';

const dir = () => mkdtempSync(join(tmpdir(), 'agent-prefs-'));

test('defaults: Arturo is Modern, every other agent Bubbles, no accent', () => {
  const d = dir();
  assert.deepEqual(readPrefs(d, 'operator', 'arturo'), { style: 'modern', accent: null, style_before_dev: 'modern', chosen: [] });
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'), { style: 'bubbles', accent: null, style_before_dev: 'bubbles', chosen: [] });
});

test('a choice is per person and per agent', () => {
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev', accent: '#3b82f6' });
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'), { style: 'dev', accent: '#3b82f6', style_before_dev: 'bubbles', chosen: ['style', 'accent'] });
  assert.equal(readPrefs(d, 'operator', 'pm-demo').style, 'bubbles');      // another agent
  assert.equal(readPrefs(d, 'sample-user', 'dev-demo').style, 'bubbles');  // another person
});

test('null clears a field back to the default, and an empty record is dropped', () => {
  const d = dir();
  writePrefs(d, 'operator', 'arturo', { style: 'bubbles' });
  writePrefs(d, 'operator', 'arturo', { style: null });
  assert.equal(readPrefs(d, 'operator', 'arturo').style, 'modern');
  const saved = JSON.parse(readFileSync(join(d, 'state', 'agent-prefs.json'), 'utf8'));
  assert.deepEqual(saved.users.operator, {});
});

test('a change is checked field by field', () => {
  assert.deepEqual(cleanPrefsChange({ style: 'modern' }), { change: { style: 'modern' } });
  assert.deepEqual(cleanPrefsChange({ accent: null }), { change: { accent: null } });
  assert.match((cleanPrefsChange({ style: 'neon' }) as { error: string }).error, /style must be one of/);
  assert.match((cleanPrefsChange({ accent: 'blue' }) as { error: string }).error, /#rrggbb/);
  assert.match((cleanPrefsChange({}) as { error: string }).error, /nothing to change/);
  assert.match((cleanPrefsChange(['x']) as { error: string }).error, /expected/);
});

test('a hand-edited bad value reads as the default', () => {
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev' });
  const f = join(d, 'state', 'agent-prefs.json');
  const all = JSON.parse(readFileSync(f, 'utf8'));
  all.users.operator['dev-demo'] = { style: 'neon', accent: 'red', style_before_dev: 'dev' };
  writeFileSync(f, JSON.stringify(all));
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'), { style: 'bubbles', accent: null, style_before_dev: 'bubbles', chosen: [] });
});

test('agent ids are checked', () => {
  assert.ok(isAgentId('dev-demo') && isAgentId('arturo'));
  assert.ok(!isAgentId('../x') && !isAgentId('') && !isAgentId('a b'));
});

test('choosing Dev records the style it leaves, for Back to chat', () => {
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'modern' });
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev' });
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style, 'dev');
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style_before_dev, 'modern');
  // Dev chosen again while in Dev: the record is the style before the first one, never 'dev'.
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev' });
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style_before_dev, 'modern');
});

test('with no style chosen, Dev records the agent\'s default', () => {
  const d = dir();
  writePrefs(d, 'operator', 'arturo', { style: 'dev' });
  assert.equal(readPrefs(d, 'operator', 'arturo').style_before_dev, 'modern');
  writePrefs(d, 'operator', 'pm-demo', { style: 'dev' });
  assert.equal(readPrefs(d, 'operator', 'pm-demo').style_before_dev, 'bubbles');
});

test('Back to chat (a PUT of the recorded style) leaves the record, and other fields never touch it or the style', () => {
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'modern' });
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev' });
  writePrefs(d, 'operator', 'dev-demo', { accent: '#112233' });
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style, 'dev', 'an accent change keeps the chosen style');
  writePrefs(d, 'operator', 'dev-demo', { style: readPrefs(d, 'operator', 'dev-demo').style_before_dev });
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'),
    { style: 'modern', accent: '#112233', style_before_dev: 'modern', chosen: ['style', 'accent'] });
  // The next trip to Dev records whatever the style is then.
  writePrefs(d, 'operator', 'dev-demo', { style: 'bubbles' });
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev' });
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style_before_dev, 'bubbles');
});

test('the record is per person and per agent, and survives a fresh read of the file', () => {
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'modern' });
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev' });
  assert.equal(readPrefs(d, 'sample-user', 'dev-demo').style_before_dev, 'bubbles');
  assert.equal(readPrefs(d, 'operator', 'pm-demo').style_before_dev, 'bubbles');
  const saved = JSON.parse(readFileSync(join(d, 'state', 'agent-prefs.json'), 'utf8'));
  assert.equal(saved.users.operator['dev-demo'].style_before_dev, 'modern', 'on disk, so a restart keeps it');
});

test('a client cannot set the record itself', () => {
  assert.match((cleanPrefsChange({ style_before_dev: 'modern' }) as { error: string }).error, /nothing to change/);
  const r = cleanPrefsChange({ style: 'bubbles', style_before_dev: 'modern' });
  assert.deepEqual(r, { change: { style: 'bubbles' } });
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev', style_before_dev: 'modern' } as any);
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style_before_dev, 'bubbles', 'the server records what it saw');
});

test('an agent with no style chosen uses the person\'s default chat style; Arturo stays Modern', () => {
  const d = dir();
  writeUserPref(d, 'operator', 'default_chat_style', 'modern');
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style, 'modern');
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style_before_dev, 'modern');
  assert.equal(readPrefs(d, 'sample-user', 'dev-demo').style, 'bubbles', 'another person keeps the built-in default');
  writeUserPref(d, 'operator', 'default_chat_style', 'bubbles');
  assert.equal(readPrefs(d, 'operator', 'arturo').style, 'modern');
  writePrefs(d, 'operator', 'dev-demo', { style: 'bubbles' });
  writeUserPref(d, 'operator', 'default_chat_style', 'modern');
  assert.equal(readPrefs(d, 'operator', 'dev-demo').style, 'bubbles', 'a chosen style always wins');
});
