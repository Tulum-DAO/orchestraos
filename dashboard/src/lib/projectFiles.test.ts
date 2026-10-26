import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupByFolder } from './projectFiles.ts';

test('files are grouped under their folder, root files first', () => {
  const f = (path: string) => ({ path, size: 1, mtime: 0 });
  const groups = groupByFolder([f('site/index.html'), f('README.md'), f('site/img/logo.png'), f('notes.txt')]);
  assert.deepEqual(groups.map(([d, l]) => [d, l.map((x) => x.path)]), [
    ['', ['README.md', 'notes.txt']], ['site', ['site/index.html']], ['site/img', ['site/img/logo.png']],
  ]);
});

import { textViewFor, parseCsv, cascade, focusFor } from './projectFiles.ts';

test('each kind of text file gets its own view', () => {
  assert.equal(textViewFor('deliverables/2026-10-24-audit.md'), 'markdown');
  assert.equal(textViewFor('site/index.html'), 'html');
  assert.equal(textViewFor('research/leads.csv'), 'csv');
  assert.equal(textViewFor('site/app.ts'), 'code');
});

test('a CSV with quotes, commas and newlines inside cells parses', () => {
  assert.deepEqual(parseCsv('name,note\n"Smith, J","said ""hi""\nthen left"\nx,y\n'),
    [['name', 'note'], ['Smith, J', 'said "hi"\nthen left'], ['x', 'y']]);
});

test('windows cascade and stay on screen; Arturo is told the top file, else the project', () => {
  const a = cascade(0, 1400, 900), b = cascade(1, 1400, 900);
  assert.ok(b.x > a.x && b.y > a.y);
  const small = cascade(3, 500, 400);
  assert.ok(small.x >= 8 && small.y >= 8);
  assert.deepEqual(focusFor('acme', 'Acme', '/p/acme', 'deliverables/a.md'),
    { kind: 'file', id: '/p/acme/deliverables/a.md', label: 'Acme: deliverables/a.md' });
  assert.deepEqual(focusFor('acme', 'Acme', '/p/acme', null), { kind: 'project', id: 'acme', label: 'Acme' });
});
