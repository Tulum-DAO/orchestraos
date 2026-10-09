/**
 * A Gemini/Antigravity seat is found by its recorded RUNTIME, not by a name prefix or a built-in
 * seat name: a gemini seat called "helper" gets its own brain transcript, not the newest file in a
 * Claude project dir it happens to share.
 * Run: npx tsx --test src/routes/chat-transcript.runtime.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const root = mkdtempSync(join(tmpdir(), 'transcript-runtime-'));
const home = join(root, 'home');
const orch = join(root, 'orch');
const brain = join(home, '.gemini', 'antigravity-cli', 'brain', 'cid-helper', '.system_generated', 'logs');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(brain, { recursive: true });
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
writeFileSync(join(brain, 'transcript.jsonl'), '{"text": "You are helper, a seat."}\n');
writeFileSync(join(projects, '99999999-2222-4333-8444-000000000001.jsonl'), '{}\n');   // someone else's
writeFileSync(join(orch, 'state', 'agent-sessions.json'),
  JSON.stringify({ helper: { runtime: 'gemini', cwd: '/repo' } }));
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;

const { resolveTranscriptPath } = await import('./chat-transcript.js');

test('a gemini-runtime seat resolves to its own brain, whatever it is called', () => {
  assert.equal(resolveTranscriptPath('helper').sid, 'cid-helper');
});

test('no built-in seat name in the resolver', () => {
  assert.ok(!readFileSync(new URL('./chat-transcript.ts', import.meta.url), 'utf-8').includes("'agy-ops'"));
});
