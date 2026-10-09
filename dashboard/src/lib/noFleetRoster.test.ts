/**
 * The dashboard ships to every install, so it names no seat of any particular fleet: a voice card
 * is labelled from the install's own state/voice-agents.json, never from a built-in roster.
 * Run: npx tsx --test src/lib/noFleetRoster.test.ts   (from dashboard/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';

const RETIRED_ROSTER = ['gemini-gm', 'pm-products', 'pm-clients', 'pm-infra', 'jarvis-poc'];

function runtimeFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return runtimeFiles(p);
    return /\.(ts|tsx|mjs)$/.test(f) && !/\.test\./.test(f) ? [p] : [];
  });
}

test('no dashboard source names a seat from a built-in roster', () => {
  const hits: string[] = [];
  for (const f of runtimeFiles(join(import.meta.dirname, '..'))) {
    readFileSync(f, 'utf-8').split('\n').forEach((line, i) => {
      for (const name of RETIRED_ROSTER) {
        if (new RegExp(`['"\`]${name}['"\`]`).test(line)) hits.push(`${f}:${i + 1} ${name}`);
      }
    });
  }
  assert.deepEqual(hits, []);
});
