/**
 * Text scales with --ui-text-scale (Settings › Display › Text size). A fixed-pixel text class would ignore it, so none
 * may come back: use a Tailwind size (text-xs, text-sm…) or a scaled token (text-px11, text-px12h… in index.css).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const SKIP = /(^|\/)qa-[^/]*$|\.test\.(ts|mjs)$/;

function files(dir: string, exts: string[]): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p, exts));
    else if (exts.some((e) => name.endsWith(e))) out.push(p);
  }
  return out;
}

function hits(exts: string[], re: RegExp): string[] {
  const out: string[] = [];
  for (const f of files(SRC, exts)) {
    const rel = relative(SRC, f);
    if (SKIP.test(rel)) continue;
    readFileSync(f, 'utf-8').split('\n').forEach((line, i) => {
      for (const m of line.matchAll(re)) out.push(`${rel}:${i + 1}: ${m[0]}`);
    });
  }
  return out;
}

test('no fixed-pixel text classes (text-[12px] …): text must scale with --ui-text-scale', () => {
  const found = hits(['.ts', '.tsx'], /text-\[\d+(?:\.\d+)?px\]/g);
  assert.deepEqual(found, [], `Use a Tailwind size or a text-pxN token (index.css @theme):\n${found.join('\n')}`);
});

test('every text-pxN class in use has its scaled token in index.css', () => {
  const css = readFileSync(join(SRC, 'index.css'), 'utf-8');
  const used = new Set(hits(['.ts', '.tsx'], /\btext-px\d+h?\b/g).map((h) => h.split(': ')[1]));
  const missing = [...used].filter((c) => !new RegExp(`--${c}:\\s*calc\\(`).test(css));
  assert.deepEqual(missing, []);
});

test('no fixed-pixel font-size in CSS: font-size: calc(Npx * var(--ui-text-scale, 1))', () => {
  const found = hits(['.css'], /font-size:\s*\d+(?:\.\d+)?px/g);
  assert.deepEqual(found, [], found.join('\n'));
});

test('no fixed line-height classes (leading-4 …) on text that scales: they overlap lines at 125–150%', () => {
  const found = hits(['.tsx'], /\bleading-\d+(?:\.\d+)?\b/g);
  assert.deepEqual(found, [], `Scale it (leading-[calc(1rem*var(--ui-text-scale,1))]) or use a unitless leading:\n${found.join('\n')}`);
});

test('no numeric inline fontSize in components', () => {
  const found = hits(['.tsx'], /fontSize:\s*\d/g);
  assert.deepEqual(found, [], found.join('\n'));
});
