/**
 * F0 fixture-conformance suite — TS side.
 *
 * THE drift backstop for the shared chat contract (transcript.v2.schema.json):
 * feeds captured-shape transcript fixtures (Claude JSONL + Antigravity brain
 * log) through the server normalizer and asserts the FULL v2 envelope —
 * grammar_version stamp, canonical server-computed tool summary, size-capped
 * input + input_full, is_system spawn-brief flag, and server-paired
 * render_items. The Swift side decodes the SAME .expected.json fixtures
 * (Tests/Conformance in watch-approval-app) — identical fixtures, identical
 * nodes, or CI fails.
 *
 * Run: cd api && npm run conformance
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { normalizeTranscript, GRAMMAR_VERSION } from '../../../api/src/routes/chat-transcript.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, '..', 'fixtures');

function loadFixture(name: string): { lines: string[]; expected: any } {
  const lines = readFileSync(join(FIXTURES, `${name}.input.jsonl`), 'utf-8').split('\n');
  const expected = JSON.parse(readFileSync(join(FIXTURES, `${name}.expected.json`), 'utf-8'));
  return { lines, expected };
}

test('grammar version is 2', () => {
  assert.equal(GRAMMAR_VERSION, 2);
});

test('claude fixture -> exact v2 envelope (summary, cap, input_full, is_system, paired render_items)', () => {
  const { lines, expected } = loadFixture('claude-basic');
  const env = normalizeTranscript(lines, 'fixture-agent', 'fixture-session');
  assert.deepStrictEqual(env, expected);
});

test('antigravity fixture -> exact v2 envelope', () => {
  const { lines, expected } = loadFixture('antigravity-basic');
  const env = normalizeTranscript(lines, 'fixture-agy', 'fixture-brain');
  assert.deepStrictEqual(env, expected);
});

test('codex fixture -> exact v2 envelope', () => {
  const { lines, expected } = loadFixture('codex-basic');
  const env = normalizeTranscript(lines, 'fixture-codex', '01a01801-b232-7760-b6f0-c6ea1d6e9152');
  assert.deepStrictEqual(env, expected);
});

test('codex tool output array fixture -> exact v2 envelope', () => {
  const { lines, expected } = loadFixture('codex-tool-output-array');
  const env = normalizeTranscript(lines, 'fixture-codex', '01a01801-b232-7760-b6f0-c6ea1d6e9152');
  assert.deepStrictEqual(env, expected);
});

test('limit slices items to the last N and render_items pair within that window', () => {
  const { lines, expected } = loadFixture('claude-basic');
  const env = normalizeTranscript(lines, 'fixture-agent', 'fixture-session', 3);
  assert.deepStrictEqual(env.items, expected.items.slice(-3));
  // window = [tool_result T2, text "ship it", tool_use Read] -> the T2 result
  // has no tool_use in-window (dropped), Read stays unpaired.
  assert.deepStrictEqual(
    env.render_items.map((r: any) => r.kind),
    ['user', 'tool'],
  );
  const readNode = env.render_items[1];
  assert.equal(readNode.tool, 'Read');
  assert.equal(readNode.result, undefined);
});

test('every emitted tool node carries the canonical summary (never client-derived)', () => {
  const { lines } = loadFixture('claude-basic');
  const env = normalizeTranscript(lines, 'fixture-agent', 'fixture-session');
  for (const it of env.items) {
    if (it.kind === 'tool_use') {
      assert.equal(typeof it.summary, 'string');
      assert.ok(it.summary.length <= 120);
      assert.ok(!it.summary.includes('\n'));
    }
  }
  for (const r of env.render_items) {
    if (r.kind === 'tool') assert.equal(typeof r.summary, 'string');
  }
});

test('input strings are capped at 2000 chars; input_full present only when truncated', () => {
  const { lines } = loadFixture('claude-basic');
  const env = normalizeTranscript(lines, 'fixture-agent', 'fixture-session');
  const tools = env.items.filter((i: any) => i.kind === 'tool_use');
  for (const t of tools) {
    for (const v of Object.values(t.input)) {
      if (typeof v === 'string') assert.ok(v.length <= 2000 + '…[truncated]'.length);
    }
  }
  const edit = tools.find((t: any) => t.tool === 'Edit');
  const bash = tools.find((t: any) => t.tool === 'Bash');
  assert.ok(edit.input_full && edit.input_full.includes('const x = 1;'));
  assert.equal(bash.input_full, undefined);
});
