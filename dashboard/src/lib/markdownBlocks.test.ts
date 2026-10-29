import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseBlocks, parseInline, safeHref, splitAttachments } from './markdownBlocks.ts';

test('code fences, headings and paragraphs', () => {
  const b = parseBlocks('# Title\n\nSome words\nmore words\n\n```ts\nconst a = 1;\n```\nafter');
  assert.deepEqual(b.map((x) => x.t), ['heading', 'para', 'code', 'para']);
  assert.deepEqual(b[2], { t: 'code', code: 'const a = 1;', lang: 'ts' });
  assert.equal((b[1] as { text: string }).text, 'Some words\nmore words');
});

test('numbered lists keep their numbers; bullets stay bullets', () => {
  const b = parseBlocks('3. third\n4. fourth\n\n- a\n* b');
  assert.deepEqual(b[0], { t: 'list', ordered: true, start: 3, items: ['third', 'fourth'] });
  assert.deepEqual(b[1], { t: 'list', ordered: false, start: 1, items: ['a', 'b'] });
});

test('a table: header, alignment, rows padded to the header, escaped pipes', () => {
  const b = parseBlocks('| Name | Count | Note |\n|:--|--:|:-:|\n| a | 1 | x \\| y |\n| b | 2 |\n\nafter');
  assert.deepEqual(b[0], {
    t: 'table', head: ['Name', 'Count', 'Note'], align: ['left', 'right', 'center'],
    rows: [['a', '1', 'x | y'], ['b', '2', '']],
  });
  assert.equal(b[1].t, 'para');
});

test('a line with a pipe but no separator row is just a paragraph', () => {
  assert.deepEqual(parseBlocks('a | b\nc | d').map((x) => x.t), ['para']);
});

test('quotes', () => {
  assert.deepEqual(parseBlocks('> one\n> two'), [{ t: 'quote', text: 'one\ntwo' }]);
});

test('inline: code, bold, italic and links, in order', () => {
  assert.deepEqual(parseInline('Run `ls` **now**, see [docs](https://example.com/a) or *later*.'), [
    { t: 'text', text: 'Run ' }, { t: 'code', text: 'ls' }, { t: 'text', text: ' ' }, { t: 'bold', text: 'now' },
    { t: 'text', text: ', see ' }, { t: 'link', text: 'docs', href: 'https://example.com/a' },
    { t: 'text', text: ' or ' }, { t: 'italic', text: 'later' }, { t: 'text', text: '.' },
  ]);
});

test('a bare address is a link, without the sentence\'s full stop', () => {
  assert.deepEqual(parseInline('Open https://example.com/x.'), [
    { t: 'text', text: 'Open ' }, { t: 'link', text: 'https://example.com/x', href: 'https://example.com/x' }, { t: 'text', text: '.' },
  ]);
});

test('only http, https and mailto links are followed', () => {
  assert.equal(safeHref('javascript:alert(1)'), null);
  assert.equal(safeHref('data:text/html,x'), null);
  assert.equal(safeHref('/relative'), null);
  assert.equal(safeHref('mailto:someone@example.com'), 'mailto:someone@example.com');
  assert.deepEqual(parseInline('[click](javascript:alert(1))').map((p) => p.t), ['text']);
});

test('a code span is never read as a link or bold', () => {
  assert.deepEqual(parseInline('`**x** https://example.com`'), [{ t: 'code', text: '**x** https://example.com' }]);
});

test('attachments: an uploaded image shows from /uploads, anything else is a chip', () => {
  const r = splitAttachments('[IMAGE: /srv/data/state/uploads/sample-1.png] [FILE: /tmp/notes.txt] look at this');
  assert.deepEqual(r.attachments, [
    { kind: 'image', name: 'sample-1.png', url: '/uploads/sample-1.png' },
    { kind: 'file', name: 'notes.txt', url: null },
  ]);
  assert.equal(r.text, 'look at this');
  assert.deepEqual(splitAttachments('no markers [IMAGE: x]'), { attachments: [], text: 'no markers [IMAGE: x]' });
  assert.equal(splitAttachments('[IMAGE: /srv/state/uploads/../etc/passwd]').attachments[0].url, null);
});
