import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cells, parseBlocks, parseInline, tableLayout, tableShares, TABLE_FIT_COLUMNS, type Block } from './threadKit/markdown.ts';

// Table fixtures for the thread kit's Markdown (components/kit/Markdown.tsx), shaped on real agent replies with the
// names made neutral (Sample, Example).

type Table = Extract<Block, { t: 'table' }>;
const table = (md: string): Table => {
  const b = parseBlocks(md).find((x) => x.t === 'table');
  assert.ok(b, 'a table');
  return b as Table;
};

// A site audit's findings: four columns, bold and an em dash in the cells.
const FOUR_COL = [
  'Findings across the three sets of pages:',
  '',
  '| finding | 17 live-template | of 12 | of 14 (orig) |',
  '|---|---|---|---|',
  '| meta description missing | **17 of 17** | 12 of 12 | 0 of 14 had one |',
  '| og:description missing | **17 of 17** | 12 of 12 | — |',
  '| no H1 at all | **16 of 17** (homepage has 6) | 11 of 12 | 13 of 14 |',
  '| canonical missing | **0 of 17** | 0 of 12 | 13 of 14 had one |',
].join('\n');

// A release check: seven columns, alignment marks, inline code and a link.
const SEVEN_COL = [
  '| Check | Page | Status | Time (ms) | Size | Owner | Note |',
  '|:--|:--|:-:|--:|--:|:--|:--|',
  '| home | `/` | pass | 182 | 41 kB | pm-demo | fine |',
  '| quote | `/get-a-quote/` | **fail** | 2310 | 12 kB | dev-demo | both app links answer 404; see [the report](https://example.com/sample-report) |',
  '| blog | `/blog/` | pass | 640 | 88 kB | qa-demo | the newest post is three months old, which reads as abandoned on a phone |',
].join('\n');

test('a four-column table: header, rows and inline markdown in the cells; it fits the message', () => {
  const t = table(FOUR_COL);
  assert.deepEqual(t.head, ['finding', '17 live-template', 'of 12', 'of 14 (orig)']);
  assert.equal(t.rows.length, 4);
  assert.deepEqual(t.rows[1], ['og:description missing', '**17 of 17**', '12 of 12', '—']);
  assert.deepEqual(parseInline(t.rows[2][1]).map((p) => p.t), ['bold', 'text']);
  assert.equal(tableLayout(t), 'fit');
  assert.equal(TABLE_FIT_COLUMNS, 4);
});

test('a seven-column table: every column, alignment marks kept; it scrolls in its own box', () => {
  const t = table(SEVEN_COL);
  assert.equal(t.head.length, 7);
  assert.deepEqual(t.align, ['left', 'left', 'center', 'right', 'right', 'left', 'left']);
  assert.ok(t.rows.every((r) => r.length === 7));
  assert.equal(t.rows[1][1], '`/get-a-quote/`');
  assert.deepEqual(parseInline(t.rows[1][6]).map((p) => p.t), ['text', 'link']);
  assert.equal(tableLayout(t), 'scroll');
});

test('a pipe inside inline code stays in its cell', () => {
  const t = table([
    '| Step | Command | Result |',
    '|---|---|---|',
    '| count | `grep -c TODO src/*.ts | sort -n` | 12 files |',
    '| pick | ``echo `a|b` | cut -d"|" -f1`` | a |',
    '| escaped | x \\| y | plain |',
  ].join('\n'));
  assert.deepEqual(t.rows[0], ['count', '`grep -c TODO src/*.ts | sort -n`', '12 files']);
  assert.deepEqual(t.rows[1], ['pick', '``echo `a|b` | cut -d"|" -f1``', 'a']);
  assert.deepEqual(t.rows[2], ['escaped', 'x | y', 'plain']);
  assert.deepEqual(parseInline(t.rows[0][1]), [{ t: 'code', text: 'grep -c TODO src/*.ts | sort -n' }]);
});

test('an unclosed backtick is plain text: the pipes after it still split', () => {
  assert.deepEqual(cells('| a `b | c |'), ['a `b', 'c']);
});

test('a ragged row is padded to the header, an over-long one cut to it', () => {
  const t = table('| Where | What happens |\n|---|---|\n| Sample hero buttons |\n| Desktop | no extra button | extra |\n');
  assert.deepEqual(t.rows, [['Sample hero buttons', ''], ['Desktop', 'no extra button']]);
});

test('a code-span pipe in the header does not break the header/separator match', () => {
  const t = table('| Name | `a | b` |\n|---|---|\n| x | y |');
  assert.deepEqual(t.head, ['Name', '`a | b`']);
  assert.deepEqual(t.rows, [['x', 'y']]);
});

test('a fitting table shares the width by its words: a floor of its longest word, a share by its longest cell', () => {
  const { columns, ch } = tableShares(table(FOUR_COL));
  assert.deepEqual(columns.map((c) => c.word), [14, 13, 2, 6]);       // og:description, live-template, 12, (orig)
  // "meta description missing" and "16 of 17 (homepage has 6)" outweigh "12 of 12"
  assert.ok(columns[0].weight > columns[2].weight && columns[1].weight > columns[2].weight);
  assert.ok(columns.every((c) => c.weight >= c.word && c.weight <= 30));
  // inline markers are not counted: "**17 of 17**" is 8 characters wide, not 12
  const bold = tableShares({ head: ['a', 'b'], rows: [['**17 of 17**', '17 of 17']] });
  assert.deepEqual(bold.columns[0], bold.columns[1]);
  // a small table asks for a small width; a wordy one is capped per column
  assert.ok(tableShares({ head: ['Page', 'H1s'], rows: [['home', '6']] }).ch < 20);
  assert.ok(ch <= Math.ceil(4 * 30 * 1.1 + 12));
});

test('the older import name reads the same blocks', async () => {
  const old = await import('./markdownBlocks.ts');
  assert.deepEqual(old.parseBlocks(SEVEN_COL), parseBlocks(SEVEN_COL));
});
