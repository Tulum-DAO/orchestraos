import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  groupOf, groupItems, groupQuery, unclearProjects, mergePage, bucketOf, byBucket, whenLabel, itemTitle, itemSubtitle,
  shortPath, hostOf, isWebLink, unclearLine, candidatesOf, librarySource, rosterOf, PAGE, makerOptions, madeByMatches, madeByLabel,
  lookupQuery, fileTarget, isGone,
  type LibraryItem, type ProjectMadeBy, type MakerChoice,
} from './drawerLibrary.ts';

const NOW = new Date(2026, 9, 30, 15, 0, 0).getTime();   // a Friday, 15:00 local
const at = (daysAgo: number, hour = 10) => { const d = new Date(NOW); d.setDate(d.getDate() - daysAgo); d.setHours(hour, 0, 0, 0); return d.toISOString(); };

let n = 0;
const item = (over: Partial<LibraryItem> = {}): LibraryItem => ({
  id: (++n).toString(16).padStart(24, '0'), agent: 'qa-demo', agents: 'qa-demo', attribution: 'agent', project: 'example',
  kind: 'file', action: 'created', made: 1, title: 'notes.md', path: '/home/u/.orchestra/projects/example/notes/notes.md',
  url: null, source: 'write', at: at(0), size: 2048, ...over,
});

test('groups: made files and media, links, mentioned apart, unclear only for the agents it names', () => {
  const file = item();
  const media = item({ kind: 'media', path: '/p/projects/example/raw/shot.png', title: 'shot.png' });
  const edited = item({ action: 'edited' });
  const link = item({ kind: 'link', action: 'posted', made: 0, url: 'https://example.com/a', path: null, title: 'Example page' });
  const shown = item({ action: 'mentioned', made: 0, kind: 'media' });
  const unclear = item({ agent: '', agents: 'dev-demo,qa-demo', attribution: 'unclear' });
  const nobody = item({ agent: '', agents: '', attribution: 'unattributed' });
  const other = item({ agent: 'pm-demo', agents: 'pm-demo' });
  assert.equal(groupOf(file, 'qa-demo'), 'files');
  assert.equal(groupOf(edited, 'qa-demo'), 'files');
  assert.equal(groupOf(media, 'qa-demo'), 'media');
  assert.equal(groupOf(link, 'qa-demo'), 'links');
  assert.equal(groupOf(shown, 'qa-demo'), 'mentioned');          // a picture it showed is not one it made
  assert.equal(groupOf(unclear, 'qa-demo'), 'unclear');
  assert.equal(groupOf(unclear, 'dev-demo'), 'unclear');
  assert.equal(groupOf(unclear, 'pm-demo'), null);
  assert.equal(groupOf(nobody, 'qa-demo'), null);
  assert.equal(groupOf(other, 'qa-demo'), null);
  const g = groupItems([file, media, edited, link, shown, unclear, nobody, other], 'qa-demo');
  assert.deepEqual(Object.fromEntries(Object.entries(g).map(([k, v]) => [k, v.length])), { files: 2, media: 1, links: 1, mentioned: 1, unclear: 1 });
});

test('candidates come from the comma list, blanks dropped', () => {
  assert.deepEqual(candidatesOf({ agents: 'dev-demo, qa-demo,' }), ['dev-demo', 'qa-demo']);
  assert.deepEqual(candidatesOf({ agents: '' }), []);
});

test('each group asks the index for only its own items, 50 at a time', () => {
  assert.equal(groupQuery('files', 'qa-demo'), 'agent=qa-demo&kind=file&made=1&limit=50');
  assert.equal(groupQuery('media', 'qa-demo', 50), 'agent=qa-demo&kind=media&made=1&limit=50&offset=50');
  assert.equal(groupQuery('links', 'qa-demo', 0, 6), 'agent=qa-demo&kind=link&limit=6');
  assert.equal(groupQuery('mentioned', 'qa-demo'), 'agent=qa-demo&action=mentioned&limit=50');
});

test('unclear files are looked up in the projects the summary says have some', () => {
  assert.deepEqual(unclearProjects({ shared_or_unattributed: [
    { project: 'example', attribution: 'unattributed', n: 18 }, { project: 'example', attribution: 'unclear', n: 53 },
    { project: 'sample', attribution: 'unclear', n: 0 }, { project: null, attribution: 'unclear', n: 2 },
  ] }), ['example']);
  assert.deepEqual(unclearProjects(null), []);
});

test('paging: a page is appended without repeats; a full page means there may be more', () => {
  const a = item(), b = item(), c = item();
  const first = mergePage([], [a, b], 2);
  assert.deepEqual(first.items.map((i) => i.id), [a.id, b.id]);
  assert.equal(first.more, true);
  const second = mergePage(first.items, [b, c], 50);
  assert.deepEqual(second.items.map((i) => i.id), [a.id, b.id, c.id]);
  assert.equal(second.more, false);
  assert.equal(PAGE, 50);
});

test('Today, This week (the six days before), Older; no time is Older', () => {
  assert.equal(bucketOf(at(0, 1), NOW), 'today');
  assert.equal(bucketOf(at(1, 23), NOW), 'week');
  assert.equal(bucketOf(at(6, 0), NOW), 'week');
  assert.equal(bucketOf(at(7, 23), NOW), 'older');
  assert.equal(bucketOf(null, NOW), 'older');
  assert.equal(bucketOf('not a date', NOW), 'older');
  const old = item({ at: at(20) }), week = item({ at: at(3) }), today = item({ at: at(0, 9) }), later = item({ at: at(0, 14) });
  const b = byBucket([old, today, week, later], NOW);
  assert.deepEqual(b.map(([k]) => k), ['today', 'week', 'older']);
  assert.deepEqual(b[0][1].map((i) => i.id), [later.id, today.id]);   // newest first
  assert.deepEqual(byBucket([old], NOW).map(([k]) => k), ['older']);  // empty headings left out
});

test('the row date: a time today, a weekday this week, a day before that', () => {
  assert.match(whenLabel(at(0, 9), NOW), /9/);
  assert.equal(whenLabel(at(2), NOW), new Date(at(2)).toLocaleDateString([], { weekday: 'short' }));
  assert.equal(whenLabel(at(30), NOW), new Date(at(30)).toLocaleDateString([], { month: 'short', day: 'numeric' }));
  assert.equal(whenLabel(null, NOW), '');
});

test('titles: the file name or a description of its content; a command\'s words go under it, never as the title', () => {
  // the index as it is now: title and subtitle as given
  assert.equal(itemTitle(item({ title: 'Sample claim check', subtitle: 'Append round 2 findings', source: 'bash', path: '/p/notes/claims.md' })), 'Sample claim check');
  assert.equal(itemSubtitle(item({ title: 'Sample claim check', subtitle: 'Append round 2 findings', source: 'bash', path: '/p/notes/claims.md' })), 'Append round 2 findings');
  assert.equal(itemTitle(item({ title: 'home.png', subtitle: 'Render the home page', source: 'bash', path: '/p/raw/home.png' })), 'home.png');
  assert.equal(itemSubtitle(item({ title: 'home.png', subtitle: null, source: 'write', path: '/p/raw/home.png' })), '');
  // a path for a title shows the file's name
  assert.equal(itemTitle(item({ title: 'research/raw/qa/render-faqs-mobile.png', subtitle: null, path: '/p/projects/example/research/raw/qa/render-faqs-mobile.png' })), 'render-faqs-mobile.png');
  assert.equal(itemTitle(item({ title: '', path: '/p/sample_report.md' })), 'sample_report.md');
  // an index from before (no subtitle field): a Bash-made file's title was the command's words; they move under it
  const old = item({ title: 'Append final restatement', source: 'bash', path: '/p/notes/qa-notes.md' });
  delete old.subtitle;
  assert.equal(itemTitle(old), 'qa-notes.md');
  assert.equal(itemSubtitle(old), 'Append final restatement');
  const oldWrite = item({ title: 'Sample heading', source: 'write', path: '/p/notes/a.md' });
  delete oldWrite.subtitle;
  assert.equal(itemTitle(oldWrite), 'Sample heading', 'only a command\'s words are moved');
  assert.equal(itemSubtitle(oldWrite), '');
  // links
  assert.equal(itemTitle(item({ kind: 'link', title: 'Example docs', url: 'https://example.com/docs', path: null })), 'Example docs');
  assert.equal(itemTitle(item({ kind: 'link', title: '', url: 'https://example.com/docs/a', path: null })), 'example.com/docs/a');
  assert.equal(itemTitle(item({ kind: 'link', title: 'https://example.com/', url: 'https://example.com/', path: null })), 'example.com');
  assert.equal(itemSubtitle(item({ kind: 'link', title: 'Example docs', source: 'bash', url: 'https://example.com/docs', path: null })), '');
});

test('made by: one choice per agent that made files (most first), Unclear and Unattributed when any; made only', () => {
  const m: ProjectMadeBy = {
    project: 'example',
    files: {
      'notes/a.md': { attribution: 'agent', agents: ['dev-demo'], at: null },
      'notes/b.md': { attribution: 'agent', agents: ['dev-demo', 'qa-demo'], at: null },
      'notes/c.md': { attribution: 'agent', agents: ['pm-demo-old'], at: null },
      'raw/d.html': { attribution: 'unclear', agents: ['dev-demo', 'pm-demo', 'qa-demo'], at: null },
      'raw/e.txt': { attribution: 'unattributed', agents: [], at: null },
      'raw/gone.txt': { attribution: 'agent', agents: ['qa-demo'], at: null },
    },
    makers: [{ agent: 'dev-demo', files: 2, retired: false }, { agent: 'pm-demo-old', files: 1, retired: true }, { agent: 'qa-demo', files: 2, retired: false }],
    unclear: 1, unattributed: 1,
  };
  const listed = ['PROJECT.md', 'notes/a.md', 'notes/b.md', 'notes/c.md', 'raw/d.html', 'raw/e.txt'];   // raw/gone.txt is not listed any more
  assert.deepEqual(makerOptions(listed, m).map((o) => `${o.label}:${o.count}`),
    ['Everyone:6', 'dev-demo:2', 'pm-demo-old (retired):1', 'qa-demo:1', 'Unclear:1', 'Unattributed:1']);
  assert.deepEqual(makerOptions(listed, null).map((o) => o.value), ['everyone'], 'no index: no filter');
  const pick = (c: MakerChoice) => listed.filter((p) => madeByMatches(m.files[p], c));
  assert.deepEqual(pick('everyone'), listed);
  assert.deepEqual(pick('agent:dev-demo'), ['notes/a.md', 'notes/b.md']);
  assert.deepEqual(pick('agent:qa-demo'), ['notes/b.md'], 'an unclear file it may have made is not one it made');
  assert.deepEqual(pick('unclear'), ['raw/d.html']);
  assert.deepEqual(pick('unattributed'), ['raw/e.txt'], 'a file with no entry is under Everyone only');
  assert.equal(madeByLabel(m.files['notes/b.md']), 'dev-demo, qa-demo');
  assert.equal(madeByLabel(m.files['raw/d.html']), 'Unclear: dev-demo, pm-demo or qa-demo');
  assert.equal(madeByLabel(m.files['raw/e.txt']), 'Unattributed');
  assert.equal(madeByLabel(undefined), '');
  // with no unclear or unattributed files those choices are left out
  assert.deepEqual(makerOptions(['notes/a.md'], m).map((o) => o.value), ['everyone', 'agent:dev-demo']);
});

test('paths are shown inside their project; links by host; only web links open', () => {
  assert.equal(shortPath({ path: '/home/u/.orchestra/projects/example/research/raw/a.png', project: 'example' }), 'research/raw/a.png');
  assert.equal(shortPath({ path: '/tmp/a.png', project: null }), '/tmp/a.png');
  assert.equal(hostOf('https://docs.example.com/x'), 'docs.example.com');
  assert.equal(hostOf('nonsense'), '');
  assert.equal(isWebLink('https://example.com'), true);
  assert.equal(isWebLink('javascript:alert(1)'), false);
  assert.equal(isWebLink(null), false);
});

test('unclear: the other candidates, this agent last; retired agents counted unless shown', () => {
  const roster = new Set(['dev-demo', 'qa-demo', 'pm-demo']);
  assert.equal(unclearLine({ agents: 'dev-demo,qa-demo' }, 'qa-demo', roster, false), 'Could be dev-demo or this agent');
  assert.equal(unclearLine({ agents: 'dev-demo,pm-demo,qa-demo' }, 'qa-demo', roster, false), 'Could be dev-demo, pm-demo or this agent');
  assert.equal(unclearLine({ agents: 'qa-demo,qa-demo-old' }, 'qa-demo', roster, false), 'Could be 1 retired agent or this agent');
  assert.equal(unclearLine({ agents: 'qa-demo,qa-demo-old' }, 'qa-demo', roster, true), 'Could be qa-demo-old (retired) or this agent');
  // Before the roster loads nobody is called retired.
  assert.equal(unclearLine({ agents: 'qa-demo,qa-demo-old' }, 'qa-demo', null, false), 'Could be qa-demo-old or this agent');
});

test('a library item opens through its own content route', () => {
  const s = librarySource({ id: 'abc123abc123abc123abc123', path: '/p/notes.md' });
  assert.deepEqual(s.key, ['library-file', 'abc123abc123abc123abc123']);
  assert.equal(s.path, '/p/notes.md');
  assert.equal(s.rawUrl, '/api/library/items/abc123abc123abc123abc123/content?raw=1');
});

test('the roster: the agents route list (wrapped or bare) plus the always-current ones', () => {
  assert.deepEqual([...rosterOf({ agents: [{ id: 'dev-demo' }, { id: 'qa-demo' }, {}] }, ['assistant-demo'])].sort(), ['assistant-demo', 'dev-demo', 'qa-demo']);
  assert.deepEqual([...rosterOf([{ id: 'pm-demo' }])], ['pm-demo']);
  assert.deepEqual([...rosterOf(null)], []);
});

test('result card files: one lookup per card (each path once, 20 at most); each opens from its item, a project file, or not at all', () => {
  assert.equal(lookupQuery(['/p/a.md', '/p/b.png', '/p/a.md'], 'qa-demo'), 'path=%2Fp%2Fa.md&path=%2Fp%2Fb.png&agent=qa-demo');
  assert.equal(lookupQuery(['/p/a.md'], 'Sample Agent (display name)'), 'path=%2Fp%2Fa.md', 'a display name is not an agent id');
  assert.equal(new URLSearchParams(lookupQuery(Array.from({ length: 30 }, (_, i) => `/p/${i}.md`))).getAll('path').length, 20);
  const made = item({ title: 'Sample report', path: '/p/projects/example/notes/report.md' });
  assert.deepEqual(fileTarget({ item: made, openable: true }), { kind: 'item', item: made, title: 'Sample report' });
  const kept = item({ path: '/tmp/x/shot.png', title: 'shot.png', project: null });
  assert.equal(fileTarget({ item: { ...kept, missing: true, kept: true }, openable: true })?.kind, 'item', 'gone, served from its kept copy');
  const gone = { item: { ...kept, missing: true, kept: false }, openable: false };
  assert.equal(fileTarget(gone), null);
  assert.equal(isGone(gone), true);
  assert.deepEqual(fileTarget({ item: null, openable: true, project: 'example', rel: 'notes/old.md' }), { kind: 'project', slug: 'example', rel: 'notes/old.md', title: 'old.md' });
  assert.equal(fileTarget(null), null);
  assert.equal(fileTarget(undefined), null, 'before the lookup answers (or when it fails): the plain path');
  assert.equal(isGone(undefined), false);
});
