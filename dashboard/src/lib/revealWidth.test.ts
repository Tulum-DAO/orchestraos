/**
 * A streamed reply's measure in Modern (components/kit/reveal.css): 92% of the reply's row from the first word, live and
 * after the hand-off, in percentages of the row, never a query container.
 *
 * Why (an iPhone, 2026-11-02, the agent chat in Modern): with the row a query container (container-type, for its
 * 92cqw), WebKit lays a container out before it styles a subtree just put in it, that subtree counting as no height.
 * At the hand-off the finished row goes in whole, so for that one layout the thread was a reply shorter: Safari clamped
 * the scroller to it, the reader at the bottom was thrown up by the reply's height (≈300 px on a phone), and the follow
 * read it as the reader scrolling up and stayed there. Bubbles has no container and never jumped.
 *
 * Run: npx tsx --test src/lib/revealWidth.test.ts   (from dashboard/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf8');

/** The rules of a stylesheet: each selector (one per comma part) with its declarations, comments dropped. Flat rules
 *  and the rules inside an @media block alike. */
function rules(css: string): { selector: string; decls: Record<string, string> }[] {
  const out: { selector: string; decls: Record<string, string> }[] = [];
  const flat = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of flat.matchAll(/([^{}@;]+)\{([^{}]*)\}/g)) {
    const decls: Record<string, string> = {};
    for (const d of m[2].split(';')) {
      const i = d.indexOf(':');
      if (i > 0) decls[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
    for (const sel of m[1].split(',')) out.push({ selector: sel.trim().replace(/\s+/g, ' '), decls });
  }
  return out;
}

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? cssFiles(p) : f.endsWith('.css') ? [p] : [];
  });
}

const REPLY = /turn-assistant|turn-parts|\.txt\b|data-reveal-md|data-live-text/;

test('no rule for a reply makes a query container or measures in container units (the WebKit hand-off jump)', () => {
  for (const file of cssFiles(SRC)) {
    const css = readFileSync(file, 'utf8');
    for (const r of rules(css)) {
      if (!REPLY.test(r.selector)) continue;
      const where = `${file.slice(SRC.length)}: ${r.selector}`;
      assert.ok(!('container-type' in r.decls) && !('container' in r.decls), `${where} makes a query container`);
      for (const [k, v] of Object.entries(r.decls)) assert.ok(!/\d(cqw|cqi|cqh|cqb|cqmin|cqmax)\b/.test(v), `${where} measures ${k} in container units: ${v}`);
    }
  }
});

test("a streamed reply's words are 92% of its row in every shape the view draws them in, in percentages", () => {
  const rs = rules(read('components/kit/reveal.css'));
  const decl = (selector: string) => rs.find((r) => r.selector === selector)?.decls;
  // the words straight in the row (Arturo's page: no reaction anchor; a live block with none)
  assert.equal(decl('.turn-assistant > .txt:has(> [data-reveal-md])')?.width, '92%');
  // the words in the box they are measured in (the reaction anchor; the live block's box): the box is 92%, the words fill it
  assert.equal(decl('.turn-assistant > :not(.turn-parts):has(> .txt > [data-reveal-md])')?.width, '92%');
  assert.equal(decl('.turn-assistant > :not(.turn-parts) > .txt:has(> [data-reveal-md])')?.width, 'auto');
  // a reply drawn in parts (as wide as the row): the words are 92% of them; the live parts' box is as wide as the row
  assert.equal(decl('.turn-parts > .txt:has(> [data-reveal-md])')?.width, '92%');
  assert.equal(decl('.turn-assistant > :not(.turn-parts):has(> .turn-parts > .txt > [data-reveal-md])')?.['align-self'], 'stretch');
  // a long reply's own cap (arturo.css .turn-assistant .txt max-width: 92%) is lifted where the measure is set
  for (const s of ['.turn-assistant > .txt:has(> [data-reveal-md])', '.turn-assistant > :not(.turn-parts) > .txt:has(> [data-reveal-md])',
    '.turn-parts > .txt:has(> [data-reveal-md])']) assert.equal(decl(s)?.['max-width'], 'none', s);
  // the parts these percentages are taken of are as wide as the row
  assert.equal(rules(read('components/arturo/arturo.css')).find((r) => r.selector === '.turn-parts')?.decls.width, '100%');
});
