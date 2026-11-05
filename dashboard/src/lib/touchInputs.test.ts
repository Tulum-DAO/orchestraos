/**
 * iPhone Safari zooms the page when a text field under 16px gets focus. With Settings › Display › Text size below 100%
 * (87% made Arturo's composer ~14.8px) that happened on every tap. So on touch devices (pointer: coarse) no text field
 * renders under 16px, and on a desktop nothing changes:
 *   - index.css sets --field-min: 16px only inside @media (pointer: coarse); everywhere else it is undefined, so
 *     max(var(--field-min, 0px), X) is exactly X.
 *   - text fields re-declare every --text-* token as max(var(--field-min, 0px), …), so a Tailwind size on a field floors.
 *   - a field with no size of its own (it inherits) floors at max(var(--field-min, 0px), 1em) in the base layer.
 *   - a field sized in a component stylesheet wraps its size the same way (checked for every such rule below).
 * Run: npx tsx --test src/lib/touchInputs.test.ts   (from dashboard/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const INDEX = readFileSync(join(SRC, 'index.css'), 'utf-8');

interface Rule { file: string; at: string[]; selector: string; body: string }

/** Leaf rules of a stylesheet with the at-rules around them. Enough CSS for our own files: comments, nesting, strings. */
function rules(css: string, file: string): Rule[] {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out: Rule[] = [];
  const stack: string[] = [];
  let buf = '';
  for (const ch of src) {
    if (ch === '{') { stack.push(buf.trim()); buf = ''; }
    else if (ch === '}') {
      const sel = stack.pop() ?? '';
      if (buf.trim() && !sel.startsWith('@')) out.push({ file, at: stack.filter((s) => s.startsWith('@')), selector: sel, body: buf.trim() });
      buf = '';
    } else if (ch === ';' && stack.length && !stack[stack.length - 1].startsWith('@')) { buf += ch; }
    else buf += ch;
  }
  return out;
}

function cssFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...cssFiles(p));
    else if (name.endsWith('.css')) out.push(p);
  }
  return out;
}

const COARSE = /@media\s*\(\s*pointer\s*:\s*coarse\s*\)/;
const FIELD_SEL = /\b(input|textarea|select)\b|contenteditable|\.rx-search\b/;

test('--field-min is 16px on touch devices only; nothing outside @media (pointer: coarse) defines it', () => {
  const defs = cssFiles(SRC).flatMap((f) => rules(readFileSync(f, 'utf-8'), relative(SRC, f)))
    .filter((r) => /--field-min\s*:/.test(r.body));
  assert.ok(defs.length > 0, 'index.css defines --field-min');
  for (const r of defs) {
    assert.ok(r.at.some((a) => COARSE.test(a)), `${r.file} ${r.selector}: --field-min outside @media (pointer: coarse)`);
    assert.match(r.body, /--field-min\s*:\s*16px/);
  }
});

test('on a text field every text-size token floors at --field-min', () => {
  const tokens = [...INDEX.matchAll(/^\s*(--text-[a-z0-9]+)\s*:\s*calc\(/gm)].map((m) => m[1]);
  assert.ok(tokens.length >= 19, `found the @theme text tokens (${tokens.length})`);
  const fieldRules = rules(INDEX, 'index.css').filter((r) => /textarea/.test(r.selector) && /contenteditable/.test(r.selector) && /--text-sm/.test(r.body));
  assert.equal(fieldRules.length, 1, 'one rule re-declares the tokens on text fields');
  for (const t of tokens) {
    assert.match(fieldRules[0].body, new RegExp(`${t}\\s*:\\s*max\\(var\\(--field-min, 0px\\),`), `${t} floors on fields`);
  }
});

test('a text field with no size of its own floors too (base layer, max(--field-min, 1em))', () => {
  const r = rules(INDEX, 'index.css').find((x) => /textarea/.test(x.selector) && /contenteditable/.test(x.selector) && /font-size/.test(x.body));
  assert.ok(r, 'a base rule sizes text fields');
  assert.ok(r!.at.some((a) => /@layer\s+base/.test(a)), 'in @layer base, so a Tailwind size class still wins (and is floored by its token)');
  assert.match(r!.body, /font-size\s*:\s*max\(var\(--field-min, 0px\),\s*1em\)/);
  assert.match(r!.selector, /input/);
});

test('every text field sized in a stylesheet wraps its size in max(var(--field-min, 0px), …)', () => {
  const bad: string[] = [];
  for (const f of cssFiles(SRC)) {
    for (const r of rules(readFileSync(f, 'utf-8'), relative(SRC, f))) {
      if (!FIELD_SEL.test(r.selector)) continue;
      for (const m of r.body.matchAll(/(?:^|;)\s*font-size\s*:\s*([^;]+)/g)) {
        if (!/^max\(var\(--field-min, 0px\),/.test(m[1].trim())) bad.push(`${r.file} ${r.selector}: font-size: ${m[1].trim()}`);
      }
    }
  }
  assert.deepEqual(bad, []);
});

test('the fields the review found are covered: Arturo composer, its panel inputs and selects, the emoji search', () => {
  const all = cssFiles(SRC).flatMap((f) => rules(readFileSync(f, 'utf-8'), relative(SRC, f)));
  for (const sel of ['.arturo-composer textarea', '.arturo-pill-panel textarea', '.panel-input', '.arturo-edit-input', '.rx-search', 'select.panel-value']) {
    const r = all.find((x) => x.selector.trim() === sel && /font-size/.test(x.body));
    assert.ok(r, `${sel} has a size`);
    assert.match(r!.body, /font-size\s*:\s*max\(var\(--field-min, 0px\),/, sel);
  }
});
