/**
 * RED-first: tapping an expanded provider must COLLAPSE it.
 *
 * Reported on staging: "if I click Gemini I can't unclick it without exiting out of the
 * modal" — the tile only ever set the expanded id, so the only way out of an expansion
 * was to close the whole sheet.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextExpanded, initialExpanded } from './modelSelectorFilter.js';

test('tapping a collapsed provider expands it', () => {
  assert.equal(nextExpanded(null, 'gemini'), 'gemini');
});

test('tapping the SAME provider again collapses it', () => {
  assert.equal(nextExpanded('gemini', 'gemini'), null);
});

test('tapping a different provider switches, it does not collapse', () => {
  assert.equal(nextExpanded('gemini', 'claude'), 'claude');
});

test('the add-provider tile toggles by the same rule', () => {
  assert.equal(nextExpanded('__add_provider__', '__add_provider__'), null);
  assert.equal(nextExpanded('claude', '__add_provider__'), '__add_provider__');
});

// A re-probe (the refresh button, or a login-state change) pre-expands the provider in
// use — but only when the operator has not chosen yet. A collapse they performed has to
// survive, or refresh silently re-opens the tile they just closed.

test('an untouched sheet pre-expands the provider in use', () => {
  assert.equal(initialExpanded(undefined, 'claude'), 'claude');
});

test('an untouched sheet with no provider in use stays closed', () => {
  assert.equal(initialExpanded(undefined, null), null);
});

test('a DELIBERATE collapse survives a re-probe', () => {
  assert.equal(initialExpanded(null, 'claude'), null);
});

test('an explicit expansion survives a re-probe', () => {
  assert.equal(initialExpanded('gemini', 'claude'), 'gemini');
});
