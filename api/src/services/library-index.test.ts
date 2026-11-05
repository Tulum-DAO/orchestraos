import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, truncateSync, utimesSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { LibraryIndex, openLibraryDb } from './library-index.js';

// A real temp data dir: logs, project files with real mtimes, a real library.db.
function box() {
  const data = mkdtempSync(join(tmpdir(), 'library-'));
  mkdirSync(join(data, 'state'), { recursive: true });
  const proj = join(data, 'projects', 'sample');
  mkdirSync(join(proj, 'notes'), { recursive: true });
  return { data, proj };
}

const T0 = Date.now() - 3_600_000;                                   // an hour ago
const at = (s: number) => new Date(T0 + s * 1000).toISOString();
let n = 0;
const L = (cwd: string, o: any) => JSON.stringify({ uuid: `u${++n}`, cwd, ...o }) + '\n';
const user = (cwd: string, text: string, s: number) => L(cwd, { type: 'user', timestamp: at(s), message: { content: text } });
const use = (cwd: string, id: string, name: string, input: any, s: number) => L(cwd, { type: 'assistant', timestamp: at(s), message: { content: [{ type: 'tool_use', id, name, input }] } });
const res = (cwd: string, id: string, text: string, s: number) => L(cwd, { type: 'user', timestamp: at(s), message: { content: [{ type: 'tool_result', tool_use_id: id, content: text }] } });
const say = (cwd: string, text: string, s: number) => L(cwd, { type: 'assistant', timestamp: at(s), message: { content: [{ type: 'text', text }] } });
const touch = (p: string, s: number, body = 'x') => { writeFileSync(p, body); const t = new Date(T0 + s * 1000); utimesSync(p, t, t); };

test('a log read forward: items stored once, a growing log read again only from its last turn, counts per agent', () => {
  const { data, proj } = box();
  const log = join(data, 'dev.jsonl');
  const report = join(proj, 'notes', 'report.md');
  writeFileSync(log, user(proj, 'write the report', 0) + use(proj, 'w', 'Write', { file_path: report }, 5) + res(proj, 'w', 'File created successfully at: x', 6));
  touch(report, 6);
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's1' }] });
  ix.pass();
  ix.pass();                                                          // nothing new: nothing changes
  let s = ix.summary();
  assert.deepEqual(s.agents, [{ agent: 'dev-demo', made: 1, mentioned: 0, links: 0 }]);
  // the turn goes on: a reply naming a file it did not make, and a link
  const other = join(proj, 'notes', 'old.md');
  touch(other, -86_400);
  appendFileSync(log, say(proj, `Done: ${report}. See also ${other} and https://example.com/sample`, 20));
  ix.pass();
  s = ix.summary();
  assert.deepEqual(s.agents, [{ agent: 'dev-demo', made: 1, mentioned: 1, links: 1 }]);
  const items = ix.list({ agent: 'dev-demo' }) as any[];
  assert.equal(items.filter((i) => i.path === report).length, 1, 'stored once');
  assert.equal(items.find((i) => i.path === report).project, 'sample');
});

test('project folder: a file changed during one agent\'s running call is theirs; during two, unclear; during none, unattributed', () => {
  const { data, proj } = box();
  const a = join(data, 'a.jsonl'); const b = join(data, 'b.jsonl');
  // both agents are in a turn the whole time; only their calls differ: dev-demo 0..60 s, qa-demo 50..120 s
  writeFileSync(a, user(proj, 'go', 0) + use(proj, 'da', 'Bash', { command: 'node build.js' }, 0) + res(proj, 'da', 'ok', 60) + say(proj, 'working', 600));
  writeFileSync(b, user(proj, 'go', 0) + use(proj, 'qb', 'Bash', { command: 'node crawl.js' }, 50) + res(proj, 'qb', 'ok', 120) + say(proj, 'working', 600));
  touch(join(proj, 'notes', 'mine.md'), 30);
  touch(join(proj, 'notes', 'both.md'), 55);
  touch(join(proj, 'notes', 'nobody.md'), 300);                         // inside both turns, inside no call
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [{ id: 'dev-demo', cwd: proj }, { id: 'qa-demo', cwd: proj }],
    logsOf: (id) => [{ path: id === 'dev-demo' ? a : b, session: id }] });
  ix.pass();
  const all = ix.list({ project: 'sample' }) as any[];
  const by = (name: string) => all.find((i) => i.path.endsWith(name));
  assert.equal(by('mine.md').agent, 'dev-demo');
  assert.equal(by('mine.md').attribution, 'agent');
  assert.equal(by('both.md').attribution, 'unclear');
  assert.equal(by('both.md').agents, 'dev-demo,qa-demo');
  assert.equal(by('nobody.md').attribution, 'unattributed', 'a turn alone is not evidence');
  const s = ix.summary();
  assert.deepEqual(s.agents, [{ agent: 'dev-demo', made: 1, mentioned: 0, links: 0 }], 'unclear and unattributed count toward no agent');
});

test('retired agents are hidden unless asked for; an indexed file opens, nothing else does', () => {
  const { data, proj } = box();
  const a = join(data, 'a.jsonl');
  const f = join(proj, 'notes', 'r.md');
  writeFileSync(a, user(proj, 'go', 0) + use(proj, 'w', 'Write', { file_path: f }, 5) + res(proj, 'w', 'File created successfully', 6));
  touch(f, 6);
  let agents = [{ id: 'qa-demo', cwd: proj }];
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => agents, logsOf: () => [{ path: a, session: 's' }] });
  ix.pass();
  assert.equal((ix.list({}) as any[]).length, 1);
  agents = [];                                                          // retired
  assert.equal((ix.list({}) as any[]).length, 0);
  assert.equal((ix.list({ retired: true }) as any[]).length, 1);
  assert.deepEqual(ix.summary().agents, []);
  assert.equal(ix.summary(14, true).agents.length, 1);
  const id = (ix.list({ retired: true }) as any[])[0].id;
  assert.equal(ix.fileOf(id)?.path, f);
  assert.equal(ix.fileOf('0'.repeat(24)), null);
});

test("an agent's own log settles a file the timing gave to someone else", () => {
  const { data, proj } = box();
  const a = join(data, 'a.jsonl'); const b = join(data, 'b.jsonl');
  const f = join(proj, 'notes', 'shot.png');
  writeFileSync(b, user(proj, 'go', 0) + use(proj, 'qx', 'Bash', { command: 'node long.js' }, 0) + res(proj, 'qx', 'ok', 300));
  writeFileSync(a, user(proj, 'go', 0));
  touch(f, 50);
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [{ id: 'dev-demo', cwd: proj }, { id: 'qa-demo', cwd: proj }],
    logsOf: (id) => [{ path: id === 'dev-demo' ? a : b, session: id }] });
  ix.pass();
  const first = (ix.list({ project: 'sample' }) as any[]).find((i) => i.path === f);
  assert.equal(first.agent, 'qa-demo', 'by timing alone, only qa-demo had a call running then');
  appendFileSync(a, use(proj, 'x', 'Bash', { command: `shoot > ${f}`, description: 'Sample shot' }, 49) + res(proj, 'x', '', 51) + say(proj, 'ok', 300));
  ix.pass();
  const rows = (ix.list({ project: 'sample' }) as any[]).filter((i) => i.path === f);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].agent, 'dev-demo');
  assert.equal(rows[0].title, 'shot.png');
  assert.equal(rows[0].subtitle, 'Sample shot');
});

test('the backfill runs once and records how long it took', () => {
  const { data } = box();
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [], logsOf: () => [] });
  assert.ok(ix.backfillIfNeeded());
  assert.equal(ix.backfillIfNeeded(), null);
  assert.ok(ix.status().backfill_done);
});

// Kept copies: a scratch folder inside the temp data dir stands in for /tmp and scratchpads.
function keepBox() {
  const b = box();
  const scr = join(b.data, 'scratch');
  mkdirSync(scr, { recursive: true });
  return { ...b, scr, scratch: (p: string) => p.startsWith(scr + '/') };
}
/** A Write the agent then names in a reply: a scratch file it made and showed. */
const writeAndName = (cwd: string, id: string, p: string, s: number) =>
  use(cwd, id, 'Write', { file_path: p }, s) + res(cwd, id, 'File created successfully at: x', s + 1) + say(cwd, `Here it is: ${p}`, s + 2);

test('kept copies: a scratch file the agent made is copied into library/<agent>/; a project file and a mention are not', () => {
  const { data, proj, scr, scratch } = keepBox();
  const log = join(data, 'a.jsonl');
  const shot = join(scr, 'shot.png'); const report = join(proj, 'notes', 'report.md'); const seen = join(scr, 'seen.pdf');
  writeFileSync(log, user(proj, 'go', 0) + writeAndName(proj, 'w1', shot, 5) + use(proj, 'w2', 'Write', { file_path: report }, 10)
    + res(proj, 'w2', 'File created successfully at: x', 11) + use(proj, 'r', 'Read', { file_path: seen }, 12) + res(proj, 'r', 'ok', 13));
  touch(shot, 6, 'png-bytes'); touch(report, 11, 'report'); touch(seen, -86_400, 'pdf');
  const ix = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }] });
  ix.pass();
  const items = ix.list({ agent: 'dev-demo' }) as any[];
  const by = (p: string) => items.find((i) => i.path === p);
  assert.equal(by(shot).kept, true);
  assert.equal(by(shot).kept_size, 9);
  assert.equal(by(report).kept, false, 'a project file stays where it is');
  assert.equal(by(seen).action, 'mentioned');
  assert.equal(by(seen).kept, false, 'a mention is not its work');
  const dir = join(data, 'library', 'dev-demo');
  assert.deepEqual(readdirSync(dir), [`${by(shot).id}.png`]);
  assert.equal(readFileSync(join(dir, `${by(shot).id}.png`), 'utf-8'), 'png-bytes');
  // the file changes: its one copy is replaced
  touch(shot, 30, 'png-bytes-2');
  ix.pass();
  assert.equal(readFileSync(join(dir, `${by(shot).id}.png`), 'utf-8'), 'png-bytes-2');
  assert.equal(readdirSync(dir).length, 1);
  const u = ix.usage();
  assert.equal(u.total.bytes, 11);
  assert.equal(u.total.items, 1);
  assert.deepEqual(u.agents.map((a: any) => [a.agent, a.bytes, a.items, a.pinned, a.retired]), [['dev-demo', 11, 1, 0, false]]);
});

test('kept copies: over 25 MB an item is not kept, and says so', () => {
  const { data, proj, scr, scratch } = keepBox();
  const log = join(data, 'a.jsonl');
  const big = join(scr, 'big.mp4');
  writeFileSync(log, user(proj, 'go', 0) + writeAndName(proj, 'w', big, 5));
  writeFileSync(big, ''); truncateSync(big, 25 * 1024 * 1024 + 1);   // sparse: no real 25 MB written
  const t = new Date(T0 + 6_000); utimesSync(big, t, t);
  const ix = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }] });
  ix.pass();
  const it = (ix.list({ agent: 'dev-demo' }) as any[])[0];
  assert.equal(it.kept, false);
  assert.equal(it.keep, 'too_large');
  assert.equal(existsSync(join(data, 'library', 'dev-demo')), false);
  assert.equal(ix.usage().total.too_large, 1);
});

test('caps: the oldest unpinned copies are cleared first; a pin survives; a cleared copy is not made again', () => {
  const { data, proj, scr, scratch } = keepBox();
  const log = join(data, 'a.jsonl');
  const f = (i: number) => join(scr, `sample-${i}.txt`);
  writeFileSync(log, user(proj, 'go', 0) + writeAndName(proj, 'w1', f(1), 10) + writeAndName(proj, 'w2', f(2), 20));
  touch(f(1), 11, '0123456789'); touch(f(2), 21, '0123456789');
  const ix = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }],
                                keep: { agentCap: 25, totalCap: 1000 } });
  ix.pass();
  const id = (i: number) => (ix.list({ agent: 'dev-demo' }) as any[]).find((x) => x.path === f(i)).id;
  assert.equal(ix.usage().total.items, 2);
  assert.equal(ix.pin(id(1), true)?.pinned, true, 'the oldest, pinned');
  appendFileSync(log, writeAndName(proj, 'w3', f(3), 30));
  touch(f(3), 31, '0123456789');
  ix.pass();
  assert.equal(ix.item(id(1))?.kept, true, 'pinned: never cleared');
  assert.equal(ix.item(id(2))?.kept, false, 'the oldest unpinned went first');
  assert.equal(ix.item(id(2))?.keep, 'cleared');
  assert.equal(ix.item(id(3))?.kept, true);
  assert.equal(readdirSync(join(data, 'library', 'dev-demo')).length, 2);
  ix.pass();
  assert.equal(ix.item(id(2))?.kept, false, 'not copied again');
  assert.deepEqual((ix.list({ pinned: true }) as any[]).map((x) => x.id), [id(1)]);
  const u = ix.usage();
  assert.deepEqual([u.total.bytes, u.total.items, u.total.pinned, u.total.cleared], [20, 2, 1, 1]);
  assert.equal(u.caps.agent_cap_bytes, 25);
  // the whole library's cap, across agents: a second agent's newer copy pushes out the oldest unpinned
  const log2 = join(data, 'b.jsonl');
  writeFileSync(log2, user(proj, 'go', 0) + writeAndName(proj, 'q1', join(scr, 'qa.txt'), 40));
  touch(join(scr, 'qa.txt'), 41, '0123456789');
  const ix2 = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }, { id: 'qa-demo', cwd: proj }],
    logsOf: (a) => [{ path: a === 'dev-demo' ? log : log2, session: a }], keep: { agentCap: 25, totalCap: 25 } }, ix.db);
  ix2.pass();
  assert.equal(ix2.item(id(1))?.kept, true);
  assert.equal(ix2.item(id(3))?.kept, false);
  assert.equal(ix2.usage().total.bytes, 20);
  assert.equal(ix2.pin(id(1), false)?.pinned, false);
  assert.equal(ix2.pin('0'.repeat(24), true), null);
});

test('missing: a gone scratch file is marked and its kept copy served; a move is linked to the new item', () => {
  const { data, proj, scr, scratch } = keepBox();
  const log = join(data, 'a.jsonl');
  const shot = join(scr, 'page.png'); const moved = join(proj, 'notes', 'page.png');
  writeFileSync(log, user(proj, 'go', 0) + writeAndName(proj, 'w', shot, 5));
  touch(shot, 6, 'png-bytes');
  const ix = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }] });
  ix.pass();
  const id = (ix.list({ agent: 'dev-demo' }) as any[])[0].id;
  assert.deepEqual(ix.contentOf(id), { path: shot, original: shot, name: 'page.png', title: ix.item(id)!.title, kept: false });
  rmSync(shot);
  ix.pass();
  assert.equal(ix.item(id)?.missing, true);
  assert.ok(ix.item(id)?.missing_since);
  const c = ix.contentOf(id)!;
  assert.equal(c.kept, true);
  assert.equal(c.original, shot);
  assert.equal(c.name, 'page.png', 'its type still comes from the original name');
  assert.equal(readFileSync(c.path, 'utf-8'), 'png-bytes');
  assert.equal(ix.fileOf(id), null, 'the original is gone');
  // the agent moves it into the project: same name, same size, a newer item
  appendFileSync(log, use(proj, 'mv', 'Bash', { command: `cp ${c.path} ${moved}` }, 60) + res(proj, 'mv', '', 61));
  touch(moved, 60.5, 'png-bytes');
  ix.pass();
  const to = (ix.list({ agent: 'dev-demo' }) as any[]).find((x) => x.path === moved);
  assert.ok(to, 'the new item');
  assert.equal(ix.item(id)?.moved_to, to.id);
  // a gone file with no kept copy: nothing to serve
  rmSync(moved);
  ix.pass();
  assert.equal(ix.item(to.id)?.missing, true);
  assert.equal(ix.contentOf(to.id), null);
});

test('never copied: a refused file, or a link into a refused place, even when made in scratch', () => {
  const { data, proj, scr, scratch } = keepBox();
  const log = join(data, 'a.jsonl');
  const secret = join(scr, 'api-token.txt');
  const hidden = join(data, '.ssh'); mkdirSync(hidden);
  const key = join(hidden, 'notes.txt'); touch(key, -86_400, 'private');
  const link = join(scr, 'notes-link.txt'); symlinkSync(key, link);
  writeFileSync(log, user(proj, 'go', 0) + writeAndName(proj, 'w1', secret, 5) + writeAndName(proj, 'w2', link, 10));
  touch(secret, 6, 'token');
  const t = new Date(T0 + 11_000); utimesSync(key, t, t);
  const ix = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }] });
  ix.pass();
  const items = ix.list({ agent: 'dev-demo' }) as any[];
  assert.equal(items.find((i) => i.path === secret), undefined, 'a secret-like name is never an item');
  const l = items.find((i) => i.path === link);
  assert.ok(l, 'the link itself is an item (its name is not refused)');
  assert.equal(l.kept, false, 'but what it points at is never copied');
  assert.equal(existsSync(join(data, 'library')), false);
});

test('titles: a note\'s first heading (followed as it changes), else the file name; the command\'s words under it; old rows retitled once', () => {
  const { data, proj } = box();
  const log = join(data, 'a.jsonl');
  const note = join(proj, 'notes', 'findings.md');
  const raw = join(proj, 'notes', 'probe.txt');
  writeFileSync(log, user(proj, 'go', 0)
    + use(proj, 'b1', 'Bash', { command: `cat >> ${note} <<'X'\nmore\nX`, description: 'Append round 2 findings' }, 5) + res(proj, 'b1', '', 6)
    + use(proj, 'b2', 'Bash', { command: `curl -s https://example.com > ${raw}`, description: 'probe.txt' }, 7) + res(proj, 'b2', '', 8));
  touch(note, 6, '# Sample claim check\n\nmore\n');
  touch(raw, 8);
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }] });
  ix.pass();
  const get = (p: string) => (ix.list({ agent: 'dev-demo' }) as any[]).find((i) => i.path === p);
  assert.equal(get(note).title, 'Sample claim check');
  assert.equal(get(note).subtitle, 'Append round 2 findings');
  assert.equal(get(raw).title, 'probe.txt');
  assert.equal(get(raw).subtitle, null, 'a description that is only the name is no subtitle');
  // the heading changes: the title follows on the next read of that file
  appendFileSync(log, use(proj, 'w', 'Write', { file_path: note }, 20) + res(proj, 'w', 'The file has been updated', 21));
  touch(note, 21, 'No heading any more\n');
  ix.pass();
  assert.equal(get(note).title, 'findings.md');
  assert.equal(get(note).subtitle, 'Append round 2 findings', 'a later Write keeps the words it had');
  // a project-folder file is titled by its name, not its path
  touch(join(proj, 'notes', 'nobody.txt'), 3590);                  // after the last pass: the next one reads it
  ix.pass();
  assert.equal((ix.list({ project: 'sample' }) as any[]).find((i) => i.path.endsWith('nobody.txt')).title, 'nobody.txt');

  // an index from before: a Bash row titled with its description, a folder-pass row titled with its path
  const old = box();
  const oldNote = join(old.proj, 'notes', 'plan.md'); touch(oldNote, 0, '# Example plan\n');
  const oldShot = join(old.proj, 'notes', 'home.png'); touch(oldShot, 0);
  const db = openLibraryDb(old.data);
  db.exec("alter table items drop column subtitle");
  const ins = db.prepare(`insert into items(id, agent, agents, attribution, project, kind, action, made, title, path, source, first_seen, updated)
    values(?, 'dev-demo', 'dev-demo', 'agent', 'sample', ?, 'created', 1, ?, ?, ?, 'x', 'x')`);
  ins.run('a'.repeat(24), 'media', 'Render the home page', oldShot, 'bash');
  ins.run('b'.repeat(24), 'file', 'notes/plan.md', oldNote, 'project-window');
  db.close();
  const ix2 = new LibraryIndex({ dataDir: old.data, scratch: () => false, agents: () => [{ id: 'dev-demo', cwd: old.proj }], logsOf: () => [] });
  assert.deepEqual([ix2.item('a'.repeat(24))?.title, ix2.item('a'.repeat(24))?.subtitle], ['home.png', 'Render the home page']);
  assert.deepEqual([ix2.item('b'.repeat(24))?.title, ix2.item('b'.repeat(24))?.subtitle], ['Example plan', null]);
  // once only: a row that changes later is not retitled again on the next open
  ix2.db.prepare("update items set title = 'kept as is' where id = ?").run('a'.repeat(24));
  ix2.db.close();
  const ix3 = new LibraryIndex({ dataDir: old.data, scratch: () => false, agents: () => [], logsOf: () => [] });
  assert.equal(ix3.item('a'.repeat(24))?.title, 'kept as is');
});

test('made by, per project file: makers from their own logs, unclear with candidates, unattributed; mentioned never counts', () => {
  const { data, proj } = box();
  mkdirSync(join(proj, 'research'), { recursive: true });
  const a = join(data, 'a.jsonl'); const b = join(data, 'b.jsonl'); const c = join(data, 'c.jsonl');
  const report = join(proj, 'notes', 'report.md');       // dev-demo writes it; qa-demo names it in a reply
  const both = join(proj, 'notes', 'both-wrote.md');     // both write it themselves
  const crawl = join(proj, 'research', 'crawl.html');    // changed while two calls ran: unclear
  const stray = join(proj, 'research', 'stray.txt');     // changed while no call ran: unattributed
  const gone = join(proj, 'notes', 'gone.md');           // made, then deleted: left out
  const old = join(proj, 'notes', 'by-retired.md');      // made by an agent no longer on the roster
  writeFileSync(a, user(proj, 'go', 0)
    + use(proj, 'w1', 'Write', { file_path: report }, 5) + res(proj, 'w1', 'File created successfully', 6)
    + use(proj, 'w2', 'Write', { file_path: both }, 7) + res(proj, 'w2', 'File created successfully', 8)
    + use(proj, 'w3', 'Write', { file_path: gone }, 9) + res(proj, 'w3', 'File created successfully', 10)
    + use(proj, 'x', 'Bash', { command: 'node crawl.js' }, 100) + res(proj, 'x', 'ok', 160) + say(proj, 'ok', 900));
  writeFileSync(b, user(proj, 'go', 0)
    + use(proj, 'w4', 'Write', { file_path: both }, 12) + res(proj, 'w4', 'The file has been updated', 13)
    + use(proj, 'y', 'Bash', { command: 'node fetch.js' }, 140) + res(proj, 'y', 'ok', 200)
    + say(proj, `I read ${report}`, 300));
  writeFileSync(c, user(proj, 'go', 0) + use(proj, 'w5', 'Write', { file_path: old }, 20) + res(proj, 'w5', 'File created successfully', 21));
  touch(report, 6); touch(both, 13); touch(gone, 10); touch(old, 21);
  touch(crawl, 150); touch(stray, 600);
  let roster = [{ id: 'dev-demo', cwd: proj }, { id: 'qa-demo', cwd: proj }, { id: 'pm-demo', cwd: proj }];
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => roster,
    logsOf: (id) => [{ path: id === 'dev-demo' ? a : id === 'qa-demo' ? b : c, session: id }] });
  ix.pass();
  rmSync(gone);
  roster = roster.filter((r) => r.id !== 'pm-demo');                 // pm-demo retired
  ix.pass();
  const m = ix.madeBy('sample');
  assert.deepEqual(m.files['notes/report.md'].agents, ['dev-demo'], 'qa-demo only named it');
  assert.equal(m.files['notes/report.md'].attribution, 'agent');
  assert.deepEqual(m.files['notes/both-wrote.md'], { attribution: 'agent', agents: ['dev-demo', 'qa-demo'], at: m.files['notes/both-wrote.md'].at });
  assert.deepEqual([m.files['research/crawl.html'].attribution, m.files['research/crawl.html'].agents], ['unclear', ['dev-demo', 'qa-demo']]);
  assert.deepEqual([m.files['research/stray.txt'].attribution, m.files['research/stray.txt'].agents], ['unattributed', []]);
  assert.equal(m.files['notes/gone.md'], undefined, 'a gone file is left out');
  assert.deepEqual(m.files['notes/by-retired.md'].agents, ['pm-demo']);
  assert.deepEqual(m.makers, [{ agent: 'dev-demo', files: 2, retired: false }, { agent: 'pm-demo', files: 1, retired: true },
                              { agent: 'qa-demo', files: 1, retired: false }]);
  assert.deepEqual([m.unclear, m.unattributed], [1, 1]);
  assert.deepEqual(ix.madeBy('nothing-here'), { project: 'nothing-here', files: {}, makers: [], unclear: 0, unattributed: 0 });
});

test('lookup: the item behind each path a report names (made first, the named agent\'s first), a project file the index lacks, else null', () => {
  const { data, proj } = box();
  const a = join(data, 'a.jsonl'); const b = join(data, 'b.jsonl');
  const report = join(proj, 'notes', 'report.md');      // dev-demo made it; qa-demo named it
  const both = join(proj, 'notes', 'both.md');          // both made it
  const gone = join(proj, 'notes', 'gone.md');          // made, then deleted (not kept: a project file)
  const old = join(proj, 'notes', 'old.md');            // never in the index
  writeFileSync(a, user(proj, 'go', 0)
    + use(proj, 'w1', 'Write', { file_path: report }, 5) + res(proj, 'w1', 'File created successfully', 6)
    + use(proj, 'w2', 'Write', { file_path: both }, 7) + res(proj, 'w2', 'File created successfully', 8)
    + use(proj, 'w3', 'Write', { file_path: gone }, 9) + res(proj, 'w3', 'File created successfully', 10));
  writeFileSync(b, user(proj, 'go', 0) + use(proj, 'w4', 'Write', { file_path: both }, 12) + res(proj, 'w4', 'The file has been updated', 13)
    + say(proj, `See ${report}`, 20));
  touch(report, 6, '# Sample report\n'); touch(both, 13); touch(gone, 10); touch(old, -20 * 86_400);              // older than the library's 14 days
  const secret = join(proj, 'notes', 'api-token.txt'); touch(secret, -86_400);
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [{ id: 'dev-demo', cwd: proj }, { id: 'qa-demo', cwd: proj }],
    logsOf: (id) => [{ path: id === 'dev-demo' ? a : b, session: id }] });
  ix.pass();
  rmSync(gone);
  ix.pass();
  const r = ix.lookup([report, both, gone, old, secret, '/nowhere/x.md', 'not a path'], 'qa-demo');
  assert.equal(r[report]!.item!.agent, 'dev-demo', 'the row that made it, not qa-demo\'s mention');
  assert.equal(r[report]!.item!.title, 'Sample report');
  assert.equal(r[report]!.openable, true);
  assert.equal(r[both]!.item!.agent, 'qa-demo', 'both made it: the named agent\'s row');
  assert.equal(ix.lookup([both], 'dev-demo')[both]!.item!.agent, 'dev-demo');
  assert.equal(r[gone]!.item!.missing, true);
  assert.equal(r[gone]!.openable, false, 'gone with no kept copy: nothing to open');
  assert.deepEqual(r[old], { item: null, openable: true, project: 'sample', rel: 'notes/old.md' });
  assert.equal(r[secret], null, 'a refused name is never looked up');
  assert.equal(r['/nowhere/x.md'], null);
  assert.equal(r['not a path'], null);
  // a client sees only its own project's files
  assert.equal(ix.lookup([report, old], undefined, 'other')[report], null);
  assert.equal(ix.lookup([report, old], undefined, 'other')[old], null);
  assert.equal(ix.lookup([report], undefined, 'sample')[report]!.item!.agent, 'dev-demo');
});

test('lookup: a gone scratch file the agent made opens from its kept copy', () => {
  const { data, proj, scr, scratch } = keepBox();
  const log = join(data, 'a.jsonl');
  const shot = join(scr, 'page.png');
  writeFileSync(log, user(proj, 'go', 0) + writeAndName(proj, 'w', shot, 5));
  touch(shot, 6, 'png-bytes');
  const ix = new LibraryIndex({ dataDir: data, scratch, agents: () => [{ id: 'dev-demo', cwd: proj }], logsOf: () => [{ path: log, session: 's' }] });
  ix.pass();
  rmSync(shot);
  ix.pass();
  const r = ix.lookup([shot])[shot]!;
  assert.deepEqual([r.item!.missing, r.item!.kept, r.openable], [true, true, true]);
  assert.equal(ix.contentOf(r.item!.id)!.kept, true);
});
