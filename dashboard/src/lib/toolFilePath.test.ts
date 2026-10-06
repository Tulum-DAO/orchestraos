import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toolFilePath, displayPath } from './transcript.ts';

// The path shown in a tool row used to be display-only text inside the summary string: you
// could read a truncated tail of it and could not get the real path back out. These pin the
// extractor that makes it a thing you can take away.

test('the file tools each yield their path, across both naming families', () => {
  const cases: Array<[string, Record<string, unknown>, string]> = [
    ['Read', { file_path: '/home/x/a.ts' }, '/home/x/a.ts'],
    ['Read', { AbsolutePath: '/home/x/b.ts' }, '/home/x/b.ts'],
    ['view_file', { path: '/home/x/c.ts' }, '/home/x/c.ts'],
    ['Edit', { file_path: '/home/x/d.ts' }, '/home/x/d.ts'],
    ['Edit', { TargetFile: '/home/x/e.ts' }, '/home/x/e.ts'],
    ['replace_file_content', { TargetFile: '/home/x/f.ts' }, '/home/x/f.ts'],
    ['Write', { file_path: '/home/x/g.ts' }, '/home/x/g.ts'],
    ['write_to_file', { TargetFile: '/home/x/h.ts' }, '/home/x/h.ts'],
    ['NotebookEdit', { notebook_path: '/home/x/i.ipynb' }, '/home/x/i.ipynb'],
  ];
  for (const [tool, input, want] of cases) {
    assert.equal(toolFilePath(tool, input), want, `${tool} did not yield its path`);
  }
});

test('a tool that is not about one file yields null', () => {
  // Bash and Grep carry paths in their arguments; treating those as "the file this acted on"
  // would put a file chip on a command, which is a different claim from the one being made.
  for (const tool of ['Bash', 'Grep', 'Glob', 'Task', 'WebFetch', 'Skill', 'run_command']) {
    assert.equal(toolFilePath(tool, { file_path: '/home/x/a.ts', command: 'ls' }), null, tool);
  }
});

test('a directory or a glob is not a file', () => {
  assert.equal(toolFilePath('Read', { file_path: '/home/x/dir/' }), null);
  assert.equal(toolFilePath('Read', { file_path: '/home/x/*.ts' }), null);
  assert.equal(toolFilePath('Edit', { file_path: '/home/x/a?.ts' }), null);
});

test('a missing or non-string path yields null rather than a stringified object', () => {
  assert.equal(toolFilePath('Read', {}), null);
  assert.equal(toolFilePath('Read', { file_path: '' }), null);
  assert.equal(toolFilePath('Read', { file_path: { nested: true } }), null);
});

test('the path is returned UNSHORTENED, and shortening is display-only', () => {
  // The whole point of a copy affordance: what you copy must be what the tool opened, not
  // the `~`-abbreviated thing on screen.
  const full = '/home/someone/repos/p/a.ts';
  assert.equal(toolFilePath('Read', { file_path: full }), full);
  assert.equal(displayPath(full), '~/repos/p/a.ts');
  assert.notEqual(displayPath(full), full);
});
