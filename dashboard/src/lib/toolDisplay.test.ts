/**
 * RED-first: how a tool call reads on its card (DEC-1790747153605131).
 * Run: npx tsx --test src/lib/toolDisplay.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toolLabel, toolChip, runHeader } from './toolDisplay.js';

test('known tools read as plain actions', () => {
  assert.equal(toolLabel('list_agents'), 'List agents');
  assert.equal(toolLabel('send_telegram'), 'Send Telegram');
});

test('the deep brain is never named as a separate "GM" on screen (one identity)', () => {
  assert.doesNotMatch(toolLabel('gm_command'), /\bgm\b/i);
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
