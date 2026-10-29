import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { cleanPrefsChange, readPrefs, writePrefs, isAgentId } from './agentPrefs.js';

const dir = () => mkdtempSync(join(tmpdir(), 'agent-prefs-'));

test('defaults: Arturo is Modern, every other agent Bubbles, no accent', () => {
  const d = dir();
  assert.deepEqual(readPrefs(d, 'operator', 'arturo'), { style: 'modern', accent: null, chosen: [] });
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'), { style: 'bubbles', accent: null, chosen: [] });
});

test('a choice is per person and per agent', () => {
  const d = dir();
  writePrefs(d, 'operator', 'dev-demo', { style: 'dev', accent: '#3b82f6' });
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'), { style: 'dev', accent: '#3b82f6', chosen: ['style', 'accent'] });
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
  all.users.operator['dev-demo'] = { style: 'neon', accent: 'red' };
  writeFileSync(f, JSON.stringify(all));
  assert.deepEqual(readPrefs(d, 'operator', 'dev-demo'), { style: 'bubbles', accent: null, chosen: [] });
});

test('agent ids are checked', () => {
  assert.ok(isAgentId('dev-demo') && isAgentId('arturo'));
  assert.ok(!isAgentId('../x') && !isAgentId('') && !isAgentId('a b'));
});
