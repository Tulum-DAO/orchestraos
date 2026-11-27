/**
 * RED-first: how a tool call reads on its card (DEC-1790747153605131).
 * Run: npx tsx --test src/lib/toolDisplay.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toolLabel, toolRunningLabel, toolChip, runHeader, toolResultLines } from './toolDisplay.js';

test('known tools read as plain actions', () => {
  assert.equal(toolLabel('list_agents'), 'List agents');
  assert.equal(toolLabel('send_telegram'), 'Send Telegram');
});

// Shaw, 2026-11-26: his voice call asked gm three times and he could not see it ("the issue wasn't that the tool calls
// didn't happen, it's that they weren't logged in the chat"). gm_command messages gm and gm answers (Arturo says so
// on the call), so the chip says it asked gm. Replaces the 2026-09-30 "one identity" rule (gm never named on screen).
test('a question to gm reads as asking gm, never as Arturo thinking', () => {
  assert.equal(toolLabel('gm_command'), 'Ask gm');
  assert.equal(toolRunningLabel('gm_command'), 'Asking gm');
  assert.doesNotMatch(toolLabel('gm_command'), /think/i);
});

test('an unknown tool still reads, never as a raw identifier', () => {
  assert.equal(toolLabel('fetch_weather_now'), 'Fetch weather now');
});

test('the chip shows the arguments, not JSON', () => {
  assert.equal(toolChip('{}'), '');
  assert.equal(toolChip('{"id":"mgr-1"}'), 'mgr-1');
  assert.equal(toolChip('{"tier":1,"role":"dev"}'), 'tier: 1 · role: dev');
});

test('a summary cut off mid-JSON (the server caps it at 200) still shows something readable', () => {
  assert.equal(toolChip('{"text":"a very long messa'), 'text: a very long messa…');
});

test('the run header says what is running, then how many ran', () => {
  const t = (status: 'running' | 'ok' | 'failed', name = 'list_agents') =>
    ({ kind: 'tool' as const, callId: name + status, name, argsSummary: '{}', status });
  assert.equal(runHeader([t('ok'), t('running', 'query_roadmap')]), 'Checking roadmap…');
  assert.equal(runHeader([t('ok')]), '1 tool call');
  assert.equal(runHeader([t('ok'), t('failed')]), '2 tool calls · 1 failed');
});

// A tool's result is written for the model, and some carry advice to it ("consider using
// gm_command or read_file"). The card is for the operator: a sentence that names an internal
// tool is the model's, not theirs — and "gm" on screen breaks Arturo's one identity.
test('a result sentence addressed to the model is not shown on the card', () => {
  assert.deepEqual(
    toolResultLines("No knowledge found for 'focus'. This may require a deeper investigation — consider using gm_command or read_file."),
    ["No knowledge found for 'focus'."]);
});

test('an ordinary result is shown whole, line by line', () => {
  assert.deepEqual(toolResultLines('4 sessions on VPS.\nAgent sessions: a, b'), ['4 sessions on VPS.', 'Agent sessions: a, b']);
});

test('a result that is all model advice leaves nothing to expand', () => {
  assert.deepEqual(toolResultLines('Use gm_command for this.'), []);
});

test('a one-word tool name used as an ordinary word does not hide the sentence', () => {
  assert.deepEqual(toolResultLines('No research notes yet.'), ['No research notes yet.']);
});
