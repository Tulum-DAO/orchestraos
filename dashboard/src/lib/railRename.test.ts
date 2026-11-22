import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renameAction } from './railRename.ts';

test('leaving the field with a new name saves it (it used to cancel, and the name never reached the server)', () => {
  assert.equal(renameAction('blur', 'Front Desk', 'gm'), 'save');
  assert.equal(renameAction('blur', '  Front Desk  ', 'gm'), 'save');
});

test('Enter saves a new name', () => {
  assert.equal(renameAction('enter', 'Front Desk', 'gm'), 'save');
});

test('Escape cancels, whatever was typed', () => {
  assert.equal(renameAction('escape', 'Front Desk', 'gm'), 'cancel');
  assert.equal(renameAction('escape', 'gm', 'gm'), 'cancel');
});

test('leaving the field unchanged sends nothing', () => {
  assert.equal(renameAction('blur', 'gm', 'gm'), 'cancel');
  assert.equal(renameAction('blur', ' gm ', 'gm'), 'cancel');
  assert.equal(renameAction('enter', 'gm', 'gm'), 'cancel');
});

test('an empty name is never sent', () => {
  assert.equal(renameAction('blur', '', 'gm'), 'cancel');
  assert.equal(renameAction('blur', '   ', 'gm'), 'cancel');
  assert.equal(renameAction('enter', '', 'gm'), 'cancel');
});

test('a blur after the field acted does nothing: Escape never becomes a save, a save is never sent twice', () => {
  assert.equal(renameAction('blur', 'Front Desk', 'gm', true), 'none');
  assert.equal(renameAction('enter', 'Front Desk', 'gm', true), 'none');
});

test('the rail\'s rename field acts through renameAction on Enter, Escape and blur', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../components/workbench/AgentRail.tsx', import.meta.url), 'utf8');
  const field = src.slice(src.indexOf('function RenameField'), src.indexOf('function FaceRow'));
  assert.match(field, /renameAction\(event, text, initial, settled\.current\)/);
  assert.match(field, /onBlur=\{\(\) => act\('blur'\)\}/);
  assert.match(field, /e\.key === 'Enter'\) \{ e\.preventDefault\(\); act\('enter'\); \}/);
  assert.match(field, /e\.key === 'Escape'\) \{ e\.preventDefault\(\); e\.stopPropagation\(\); act\('escape'\); \}/);
  assert.doesNotMatch(field, /onBlur=\{\(\) => \{[^}]*onDone\(false\)/);    // the old blur that threw the name away
});
