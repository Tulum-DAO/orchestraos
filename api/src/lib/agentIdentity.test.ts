import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { decorateIdentity, projectsByPm, projectOf, fullName } from './agentIdentity.js';
import { cleanPins, readPins, writePins } from './agentPins.js';

const knowledge = {
  demo: { name: 'Demo Group', short_name: 'Demo', pm: 'pm-demo' },
  other: { name: 'Other', pm: 'pm-other' },
  nopm: { name: 'No PM yet' },
};

test('a worker inherits the project of the first PM up reports_to; a T0 and an agent with no PM are in none', () => {
  const rows = [
    { id: 'gm', tier: 'T0' }, { id: 'pm-demo', tier: 'T1', reports_to: 'gm' },
    { id: 'dev-demo', tier: 'T2', reports_to: 'pm-demo' }, { id: 'helper', tier: 'T2', reports_to: 'dev-demo' },
    { id: 'pm-other-g3', tier: 'T1', reports_to: 'gm' }, { id: 'loner', tier: 'T2' },
  ] as any[];
  decorateIdentity(rows, { knowledge, names: new Map(), lastMessage: new Map([['dev-demo', '2026-10-20T10:00:00Z']]) });
  const by = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.equal(by['gm'].project, null);
  assert.deepEqual(by['pm-demo'].project, { slug: 'demo', name: 'Demo Group', label: 'Demo' });
  assert.equal(by['dev-demo'].project.slug, 'demo');
  assert.equal(by['helper'].project.slug, 'demo');                          // two hops up
  assert.equal(by['pm-other-g3'].project.slug, 'other');                    // a generation's id is its lineage
  assert.equal(by['loner'].project, null);
  assert.equal(by['dev-demo'].last_message_at, '2026-10-20T10:00:00Z');
  assert.equal(by['loner'].last_message_at, null);
});

test('moving a worker to another PM moves its project', () => {
  const rows = [{ id: 'pm-demo', tier: 'T1' }, { id: 'pm-other', tier: 'T1' }, { id: 'dev-x', tier: 'T2', reports_to: 'pm-demo' }] as any[];
  decorateIdentity(rows, { knowledge, names: new Map(), lastMessage: new Map() });
  assert.equal(rows[2].project.slug, 'demo');
  rows[2].reports_to = 'pm-other';
  decorateIdentity(rows, { knowledge, names: new Map(), lastMessage: new Map() });
  assert.equal(rows[2].project.slug, 'other');
});

test('a renamed agent shows its full name outside its project; a never-renamed one its id', () => {
  const rows = [{ id: 'pm-demo', tier: 'T1' }, { id: 'dev-demo', tier: 'T2', reports_to: 'pm-demo' }] as any[];
  decorateIdentity(rows, { knowledge, names: new Map([['dev-demo', { name: 'dev', scope: 'demo', aliases: [] }]]), lastMessage: new Map() });
  assert.equal(rows[1].display_name, 'dev');
  assert.equal(rows[1].full_name, 'Demo · dev');
  assert.equal(rows[0].full_name, 'pm-demo');
  assert.equal(fullName('dev', 'dev-x', undefined), 'dev');
});

test('a loop in reports_to never hangs', () => {
  const byPm = projectsByPm(knowledge);
  const parents: Record<string, string> = { a: 'b', b: 'a' };
  assert.equal(projectOf('a', (id) => parents[id], () => 'T2', byPm), undefined);
});

test('pins are saved per person, cleaned, and a bad file reads as none', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pins-'));
  assert.deepEqual(readPins(dir, 'someone'), []);
  writePins(dir, 'someone', cleanPins(['dev-demo', 'dev-demo', '../x', 'pm-demo', 7])!);
  writePins(dir, 'other-person', ['gm']);
  assert.deepEqual(readPins(dir, 'someone'), ['dev-demo', 'pm-demo']);
  assert.deepEqual(readPins(dir, 'other-person'), ['gm']);
  assert.equal(cleanPins('dev-demo'), null);
  fs.writeFileSync(path.join(dir, 'state', 'agent-pins.json'), 'not json');
  assert.deepEqual(readPins(dir, 'someone'), []);
});
