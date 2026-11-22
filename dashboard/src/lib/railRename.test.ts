import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RenameMachine } from './railRename.ts';

const SAVE = (name: string) => ({ kind: 'save', name });
const CANCEL = { kind: 'cancel' };
const NONE = { kind: 'none' };

test('leaving the field with a new name saves it (it used to cancel, and the name never reached the server)', () => {
  assert.deepEqual(new RenameMachine('gm').blur('Front Desk'), SAVE('Front Desk'));
  assert.deepEqual(new RenameMachine('gm').blur('  Front Desk  '), SAVE('Front Desk'));
});

test('Enter saves a new name; other keys do nothing', () => {
  assert.deepEqual(new RenameMachine('gm').key('Enter', 'Front Desk'), SAVE('Front Desk'));
  assert.deepEqual(new RenameMachine('gm').key('a', 'Front Desk'), NONE);
});

test('Escape cancels, whatever was typed', () => {
  assert.deepEqual(new RenameMachine('gm').key('Escape', 'Front Desk'), CANCEL);
  assert.deepEqual(new RenameMachine('gm').key('Escape', 'gm'), CANCEL);
});

test('leaving the field unchanged sends nothing', () => {
  assert.deepEqual(new RenameMachine('gm').blur('gm'), CANCEL);
  assert.deepEqual(new RenameMachine('gm').blur(' gm '), CANCEL);
  assert.deepEqual(new RenameMachine('gm').key('Enter', 'gm'), CANCEL);
});

test('an empty name is never sent', () => {
  assert.deepEqual(new RenameMachine('gm').blur(''), CANCEL);
  assert.deepEqual(new RenameMachine('gm').blur('   '), CANCEL);
  assert.deepEqual(new RenameMachine('gm').key('Enter', ''), CANCEL);
});

test('Escape then the blur as the field closes: nothing is saved', () => {
  const m = new RenameMachine('gm');
  assert.deepEqual(m.key('Escape', 'Front Desk'), CANCEL);
  assert.deepEqual(m.blur('Front Desk'), NONE);
  assert.equal(m.state, 'done');
});

test('Enter then the blur as the input goes disabled: one save, not two', () => {
  const m = new RenameMachine('gm');
  assert.deepEqual(m.key('Enter', 'Front Desk'), SAVE('Front Desk'));
  assert.deepEqual(m.blur('Front Desk'), NONE);
  assert.deepEqual(m.key('Enter', 'Front Desk'), NONE);
  assert.equal(m.result(true), 'close');
  assert.deepEqual(m.blur('Front Desk'), NONE);
});

test('a refused Enter: back to editing with focus asked back, the next blur retries, and a success closes', () => {
  const m = new RenameMachine('gm');
  assert.deepEqual(m.key('Enter', 'Taken'), SAVE('Taken'));
  assert.deepEqual(m.blur('Taken'), NONE);                  // the input went disabled while saving
  assert.equal(m.result(false), 'refocus');
  assert.equal(m.state, 'editing');
  assert.deepEqual(m.blur('Front Desk'), SAVE('Front Desk'));
  assert.equal(m.result(true), 'close');
  assert.equal(m.state, 'done');
});

test('a refused blur: back to editing, focus left where the person put it', () => {
  const m = new RenameMachine('gm');
  assert.deepEqual(m.blur('Taken'), SAVE('Taken'));
  assert.equal(m.result(false), 'stay');
  assert.deepEqual(m.key('Escape', 'Taken'), CANCEL);
});

test('switching apps or tabs is not leaving the field: nothing is saved, and the field still works after', () => {
  const m = new RenameMachine('gm');
  assert.deepEqual(m.blur('Front De', false), NONE);
  assert.equal(m.state, 'editing');
  assert.deepEqual(m.key('Enter', 'Front Desk'), SAVE('Front Desk'));
});

test('an answer with no save in flight changes nothing', () => {
  const m = new RenameMachine('gm');
  assert.equal(m.result(true), 'stay');
  assert.equal(m.state, 'editing');
});

test('the rail\'s rename field forwards its key, blur and save answer to the machine', async () => {
  // No DOM test library in this package, so the wiring is checked on the component's source.
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../components/workbench/AgentRail.tsx', import.meta.url), 'utf8');
  const field = src.slice(src.indexOf('function RenameField'), src.indexOf('function FaceRow'));
  assert.match(field, /new RenameMachine\(initial\)/);
  assert.match(field, /machine\.key\(e\.key, text\)/);
  assert.match(field, /machine\.blur\(text, document\.hasFocus\(\)\)/);
  assert.match(field, /machine\.result\(r\.ok\)/);
  assert.doesNotMatch(field, /onBlur=\{\(\) => \{[^}]*onDone\(false\)/);    // the old blur that threw the name away
});
