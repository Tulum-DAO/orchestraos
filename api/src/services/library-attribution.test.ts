import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseClaudeLines, itemsFromEvents, bashOutputPaths, pathsInText, linksInText, ownersOf, isReadOnlyCommand, markdownTitle, titleFor, subtitleFor, altTextsIn, type Stat } from './library-attribution.js';

// A Claude Code log in miniature (the shapes it writes: one line per block, tool results as user lines).
const T0 = Date.parse('2026-10-30T03:00:00Z');
const at = (s: number) => new Date(T0 + s * 1000).toISOString();
let n = 0;
const line = (o: any) => JSON.stringify({ uuid: `u${++n}`, cwd: '/home/demo/.orchestra/projects/sample', ...o });
const user = (text: string, s: number) => line({ type: 'user', timestamp: at(s), message: { role: 'user', content: text } });
const say = (text: string, s: number) => line({ type: 'assistant', timestamp: at(s), message: { role: 'assistant', content: [{ type: 'text', text }] } });
const use = (id: string, name: string, input: any, s: number) => line({ type: 'assistant', timestamp: at(s), message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const res = (id: string, text: string, s: number, isError = false) => line({ type: 'user', timestamp: at(s), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content: text, is_error: isError }] } });

const P = '/home/demo/.orchestra/projects/sample';
// The stand-in file system: path -> when it last changed (seconds after T0), and when it was born.
function fsOf(files: Record<string, { m: number; b?: number; size?: number }>): Stat {
  return (p) => { const f = files[p]; return f ? { mtimeMs: T0 + f.m * 1000, birthtimeMs: f.b !== undefined ? T0 + f.b * 1000 : 0, size: f.size ?? 100, isFile: true } : null; };
}

const LOG = [
  user('Make the sample report, a screenshot of the page, and tell me where they are.', 0),
  use('w1', 'Write', { file_path: `${P}/notes/report.md`, content: '# Report' }, 5),
  res('w1', `File created successfully at: ${P}/notes/report.md`, 6),
  use('b1', 'Bash', { command: `cd ${P} && chrome --headless --screenshot=research/raw/home.png https://example.com`, description: 'Screenshot the sample home page' }, 10),
  res('b1', 'Written to file research/raw/home.png', 14),
  use('b2', 'Bash', { command: 'echo scratch > /tmp/demo/scratch.txt', description: 'scratch note' }, 15),
  res('b2', '', 16),
  use('r1', 'Read', { file_path: '/home/demo/orchestraos/README.md' }, 17),
  res('r1', '120 lines', 18),
  say(`Done. Report: ${P}/notes/report.md, screenshot: ${P}/research/raw/home.png. I also looked at /home/demo/orchestraos/README.md. Format: https://example.com/sample-report`, 20),
];

test('a throwaway-like turn: two made (Write, Bash screenshot), one mentioned, one link; scratch left out', () => {
  const { events, turns } = parseClaudeLines(LOG, 's1');
  assert.equal(turns.length, 1);
  assert.equal(turns[0].start, T0);
  assert.equal(turns[0].end, T0 + 20_000);
  const stat = fsOf({
    [`${P}/notes/report.md`]: { m: 6, b: 6 },
    [`${P}/research/raw/home.png`]: { m: 13, b: 13 },
    '/tmp/demo/scratch.txt': { m: 16 },
    '/home/demo/orchestraos/README.md': { m: -86400 },
  });
  const items = itemsFromEvents('dev-demo', events, stat, 's1');
  const by = (pred: (i: any) => boolean) => items.filter(pred).map((i) => `${i.action}:${i.path || i.url}`).sort();
  assert.deepEqual(by((i) => i.made), [`created:${P}/notes/report.md`, `created:${P}/research/raw/home.png`]);
  assert.deepEqual(by((i) => i.action === 'mentioned'), ['mentioned:/home/demo/orchestraos/README.md']);
  assert.deepEqual(by((i) => i.kind === 'link'), ['posted:https://example.com/sample-report']);
  const shot = items.find((i) => i.path?.endsWith('home.png'))!;
  assert.equal(shot.kind, 'media');
  assert.equal(shot.title, 'home.png', 'the title is the file name');
  assert.equal(shot.subtitle, 'Screenshot the sample home page', 'the command\'s words go under it');
  assert.equal(shot.source, 'bash');
  assert.ok(!items.some((i) => i.path === '/tmp/demo/scratch.txt'), 'scratch it never showed or named is left out');
});

test('a path a command names but did not change during the call is not made (never guess)', () => {
  const lines = [user('go', 0), use('b', 'Bash', { command: `ls > ${P}/old.txt` }, 10), res('b', '', 11)];
  const { events } = parseClaudeLines(lines);
  assert.deepEqual(itemsFromEvents('dev-demo', events, fsOf({ [`${P}/old.txt`]: { m: -3600 } })), []);
  assert.deepEqual(itemsFromEvents('dev-demo', events, fsOf({})), [], 'missing: nothing');
});

test('a failed write and a failed command make nothing; Edit is edited; a Write that updated is edited', () => {
  const lines = [user('go', 0),
    use('w', 'Write', { file_path: `${P}/a.md` }, 1), res('w', '<tool_use_error>File has not been read yet</tool_use_error>', 2, true),
    use('e', 'Edit', { file_path: `${P}/b.md` }, 3), res('e', 'The file has been updated.', 4),
    use('w2', 'Write', { file_path: `${P}/c.md` }, 5), res('w2', `The file ${P}/c.md has been updated successfully.`, 6),
    use('x', 'Bash', { command: `false > ${P}/d.txt` }, 7), res('x', 'Exit code 1', 8, true)];
  const { events } = parseClaudeLines(lines);
  const items = itemsFromEvents('dev-demo', events, fsOf({ [`${P}/a.md`]: { m: 2 }, [`${P}/b.md`]: { m: 4 }, [`${P}/c.md`]: { m: 6 }, [`${P}/d.txt`]: { m: 8 } }));
  assert.deepEqual(items.map((i) => `${i.action}:${i.path}`).sort(), [`edited:${P}/b.md`, `edited:${P}/c.md`]);
});

test("the operator's upload into the chat is made (uploaded); a scratch file it showed counts", () => {
  const up = '/home/demo/.orchestra/state/uploads/sample-1.png';
  const lines = [user(`Here is the mockup: ${up}`, 0),
    use('b', 'Bash', { command: 'chrome --screenshot=/tmp/demo/shot.png x', description: 'Shot of the result' }, 5), res('b', '', 8),
    use('r', 'Read', { file_path: '/tmp/demo/shot.png' }, 9), res('r', '[image]', 10)];
  const { events } = parseClaudeLines(lines);
  const items = itemsFromEvents('dev-demo', events, fsOf({ [up]: { m: -10 }, '/tmp/demo/shot.png': { m: 7, b: 7 } }));
  assert.deepEqual(items.map((i) => `${i.action}:${i.path}`).sort(), [`created:/tmp/demo/shot.png`, `uploaded:${up}`]);
});

test('subagent (sidechain) lines extend the turn but their tools are not the agent\'s', () => {
  const lines = [user('go', 0),
    JSON.stringify({ uuid: 'sc1', isSidechain: true, type: 'assistant', timestamp: at(30), message: { content: [{ type: 'tool_use', id: 's', name: 'Write', input: { file_path: `${P}/sub.md` } }] } }),
    JSON.stringify({ uuid: 'sc2', isSidechain: true, type: 'user', timestamp: at(31), message: { content: [{ type: 'tool_result', tool_use_id: 's', content: 'File created successfully' }] } })];
  const { events, turns } = parseClaudeLines(lines);
  assert.equal(turns[0].end, T0 + 31_000);
  assert.deepEqual(itemsFromEvents('dev-demo', events, fsOf({ [`${P}/sub.md`]: { m: 31 } })), []);
});

test('output paths in commands', () => {
  assert.deepEqual(bashOutputPaths('node shot.js > out/log.txt 2>&1', '/w'), ['/w/out/log.txt']);
  assert.deepEqual(bashOutputPaths('cat > /w/a.md <<EOF\nhi\nEOF'), ['/w/a.md']);
  assert.deepEqual(bashOutputPaths('npx playwright screenshot --full-page https://x /w/s.png -o /w/s2.png'), ['/w/s2.png']);
  assert.deepEqual(bashOutputPaths(`node -e "await page.screenshot({ path: '/w/r/m.png' })"`), ['/w/r/m.png']);
  assert.deepEqual(bashOutputPaths('cp a.txt b.txt /w/dest/', '/w').sort(), ['/w/dest']);
  assert.deepEqual(bashOutputPaths('echo hi | tee -a /w/t.log'), ['/w/t.log']);
  assert.deepEqual(bashOutputPaths('ls > /dev/null; echo $X > $OUT'), [], 'no /dev/null, no variables');
  assert.deepEqual(bashOutputPaths('cd /w/p && echo x > r.txt'), ['/w/p/r.txt']);
});

test('paths and links in text', () => {
  assert.deepEqual(pathsInText('See /w/a.md, and `~/notes/b.txt`. Not /w/dir or http://x/y.png'), ['/w/a.md', `${process.env.HOME || '/root'}/notes/b.txt`]);
  assert.deepEqual(linksInText('PR: [Sample fix](https://github.com/demo/repo/pull/12). Also https://example.com/a.'),
    [{ url: 'https://github.com/demo/repo/pull/12', label: 'Sample fix' }, { url: 'https://example.com/a' }]);
});

test('a project-folder file: one agent with a call running then is theirs, two are unclear, none is unattributed', () => {
  const w = [{ agent: 'dev-demo', start: T0, end: T0 + 60_000 }, { agent: 'qa-demo', start: T0 + 50_000, end: T0 + 120_000 }];
  assert.deepEqual(ownersOf(T0 + 10_000, w), { attribution: 'agent', agents: ['dev-demo'] });
  assert.deepEqual(ownersOf(T0 + 55_000, w), { attribution: 'unclear', agents: ['dev-demo', 'qa-demo'] });
  assert.deepEqual(ownersOf(T0 + 3_600_000, w), { attribution: 'unattributed', agents: [] });
});

test("the install's own records and anything secret are never items, not even by name", () => {
  const lines = [user('go', 0),
    use('b', 'Bash', { command: 'echo n > /home/demo/.orchestra/memory/dev-demo/note.md && echo k > /home/demo/.orchestra/state/voice-calls/vc_1.json' }, 1), res('b', '', 2),
    say('Keys are in /home/demo/.orchestra/.env.secrets and /home/demo/.config/app/token.txt; notes in /home/demo/notes/plan.md', 3)];
  const { events } = parseClaudeLines(lines);
  const stat = fsOf({ '/home/demo/.orchestra/memory/dev-demo/note.md': { m: 2 }, '/home/demo/.orchestra/state/voice-calls/vc_1.json': { m: 2 },
    '/home/demo/.orchestra/.env.secrets': { m: -10 }, '/home/demo/.config/app/token.txt': { m: -10 }, '/home/demo/notes/plan.md': { m: -10 } });
  assert.deepEqual(itemsFromEvents('dev-demo', events, stat).map((i) => `${i.action}:${i.path}`), ['mentioned:/home/demo/notes/plan.md']);
});

test('files that changed inside the output folder a command named, during that call, are its output', () => {
  const out = `${P}/research/raw/run`;
  const lines = [user('render the pages', 0),
    use('b', 'Bash', { command: `OUT=${out} node render.js`, description: 'Render every page' }, 10), res('b', 'rendered 2 pages', 40)];
  const { events } = parseClaudeLines(lines);
  const files = { [`${out}/home.png`]: { m: 20, b: 20 }, [`${out}/about.png`]: { m: 30, b: 30 }, [`${out}/older.png`]: { m: -500 } };
  const items = itemsFromEvents('qa-demo', events, fsOf(files), undefined, undefined, () => Object.keys(files));
  assert.deepEqual(items.map((i) => `${i.action}:${i.path}`).sort(), [`created:${out}/about.png`, `created:${out}/home.png`], 'not the file from before the call');
});

test('a path a command only printed (its own prompt) is not its output; the Write that made it is', () => {
  const f = `${P}/notes/summary.md`;
  const lines = [user('go', 0),
    use('b', 'Bash', { command: 'cat /home/demo/prompt.md' }, 1), res('b', `Step 2: write ${f}`, 2),
    use('w', 'Write', { file_path: f }, 4), res('w', 'File created successfully at: x', 5)];
  const { events } = parseClaudeLines(lines);
  const items = itemsFromEvents('dev-demo', events, fsOf({ [f]: { m: 4.5, b: 4.5 } }));
  assert.deepEqual(items.map((i) => `${i.action}:${i.source}`), ['created:write']);
});

test("each tool call has its own window; a background command's ends at its finished notice", () => {
  const lines = [user('go', 0),
    use('a', 'Bash', { command: 'make' }, 10), res('a', 'ok', 20),
    use('bg', 'Bash', { command: 'long-job', run_in_background: true }, 30), res('bg', 'Command running in background with ID: bg1.', 31),
    say('started', 32),
    line({ type: 'attachment', timestamp: at(90), attachment: { type: 'queued_command', prompt: '<task-notification>\n<tool-use-id>bg</tool-use-id>\n<status>completed</status>\n</task-notification>', origin: { kind: 'task-notification' } } })];
  const { calls } = parseClaudeLines(lines);
  const by = Object.fromEntries(calls.map((c) => [c.id, [(c.start - T0) / 1000, (c.end - T0) / 1000]]));
  assert.deepEqual(by, { a: [10, 20], bg: [30, 90] });
});

test('a Read is a read-only call; a background command with no notice in view runs to the end of its turn', () => {
  const lines = [user('go', 0),
    use('r', 'Read', { file_path: '/w/x.md' }, 5), res('r', 'text', 50),
    use('bg', 'Bash', { command: 'render-all', run_in_background: true }, 10), res('bg', 'Command running in background with ID: b.', 11),
    say('waiting', 400)];
  const { calls } = parseClaudeLines(lines);
  assert.deepEqual(calls.map((c) => [c.id, (c.start - T0) / 1000, (c.end - T0) / 1000, !!c.readOnly]), [['r', 5, 50, true], ['bg', 10, 400, false]]);
});

test('read-only commands get no credit; with only read-only calls running, a file is unclear', () => {
  for (const c of ['ls -la /w', 'stat -c %s /w/a.png | sort', 'identify /w/a.png && du -sh /w', 'cd /w && cat a.txt | grep x',
                   'for f in /w/*.png; do file "$f"; done'.replace('for f in /w/*.png; do ', '').replace('; done', ''), 'git status', 'sleep 30']) {
    assert.equal(isReadOnlyCommand(c), true, c);
  }
  for (const c of ['cat a > b', 'node render.js', 'sed -i s/a/b/ f', 'curl -o x https://e', 'find . -delete', 'echo x | tee f', 'python3 x.py']) {
    assert.equal(isReadOnlyCommand(c), false, c);
  }
  const w = [{ agent: 'pm-demo', start: T0, end: T0 + 10_000, read_only: 1 }, { agent: 'qa-demo', start: T0 + 50_000, end: T0 + 60_000, read_only: 0 }];
  assert.deepEqual(ownersOf(T0 + 5_000, w), { attribution: 'unclear', agents: ['pm-demo'] }, 'only a read-only call ran');
  assert.deepEqual(ownersOf(T0 + 55_000, w), { attribution: 'agent', agents: ['qa-demo'] });
  assert.deepEqual(ownersOf(T0 + 55_000, [...w, { agent: 'pm-demo', start: T0 + 50_000, end: T0 + 60_000, read_only: 1 }]),
    { attribution: 'agent', agents: ['qa-demo'] }, 'a read-only call beside a writing one changes nothing');
});

test('titles: the file name or a content description; a command\'s words are the subtitle, never the title', () => {
  // a markdown note's title: front matter, else its first heading, never a line inside a code fence
  assert.equal(markdownTitle('# Sample audit\n\nBody'), 'Sample audit');
  assert.equal(markdownTitle('\n\nIntro line\n\n## Findings ##\n# Later'), 'Findings');
  assert.equal(markdownTitle('---\ntitle: "Example plan"\ndate: x\n---\n# Other'), 'Example plan');
  assert.equal(markdownTitle('---\ndate: x\n---\n# After the front matter'), 'After the front matter');
  assert.equal(markdownTitle('```sh\n# not a heading\n```\n# The **real** [title](https://example.com) of `sample_file`'), 'The real title of sample_file');
  assert.equal(markdownTitle('Sample report\n=============\n\ntext'), 'Sample report');
  assert.equal(markdownTitle('- a list item\n---\nno heading'), undefined, 'a list item over a rule is not a heading');
  assert.equal(markdownTitle('Just text, no heading.'), undefined);
  assert.equal(markdownTitle('#\n#   \n'), undefined, 'an empty heading is no title');
  const long = markdownTitle(`# ${'word '.repeat(60)}`)!;
  assert.ok(long.length <= 120 && long.endsWith('…'), 'a long heading is cut short');
  // an image's words, from a reply that shows it
  assert.deepEqual([...altTextsIn(`Here: ![The sample home page on a phone](${P}/raw/home.png) and ![](${P}/raw/x.png)`)],
    [[`${P}/raw/home.png`, 'The sample home page on a phone']]);
  assert.equal(titleFor(`${P}/raw/home.png`), 'home.png');
  assert.equal(titleFor(`${P}/raw/home.png`, 'The sample home page'), 'The sample home page');
  // a description that only repeats the name or path is no subtitle
  assert.equal(subtitleFor(`${P}/raw/home.png`, 'home.png'), undefined);
  assert.equal(subtitleFor(`${P}/raw/home.png`, 'raw/home.png'), undefined);
  assert.equal(subtitleFor(`${P}/raw/home.png`, '  Render the  home page '), 'Render the home page');
  // in a log: Bash with a description, then a reply showing the picture with words
  const lines = [user('render it', 0),
    use('b', 'Bash', { command: `node render.js > ${P}/raw/home.png`, description: 'Render the sample home page' }, 5), res('b', '', 6),
    say(`Done: ![The home page on a phone](${P}/raw/home.png)`, 8)];
  const [shot] = itemsFromEvents('qa-demo', parseClaudeLines(lines).events, fsOf({ [`${P}/raw/home.png`]: { m: 6, b: 6 } }));
  assert.equal(shot.title, 'The home page on a phone');
  assert.equal(shot.subtitle, 'Render the sample home page');
  assert.equal(shot.made, true);
});
