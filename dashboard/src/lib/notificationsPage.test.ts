/**
 * Settings › Notifications (settings spec §4, the operator's brief 2026-11-03): the page's helpers, and the page itself read
 * as source (it imports CSS, so node does not render it): the OpenBot layout (intro, then cards), the three You rows on
 * the server's keys, the per-agent rows, quiet hours in the install's zone, and This device as one note because the
 * dashboard shows no system notifications.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  LEVEL_OPTIONS, DEFAULT_QUIET, THIS_DEVICE_NOTE, addableAgents, agentChoices, changeQuiet, isLevel, overrideRows, overridesOf,
  quietOf, quietZoneLine, withOverride, withoutOverride,
} from './notificationsPage.ts';
import { visibleSections, settingsSectionOf, sectionLabel } from './settingsRoute.ts';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');
const page = () => read('components/settings/sections/NotificationsSection.tsx');
const order = (src: string, marks: string[]) => marks.map((m) => { const i = src.indexOf(m); assert.ok(i >= 0, `missing ${m}`); return i; });

test('Notifications is a visible category, after Agents, with its own page', () => {
  const ids = visibleSections().map((s) => s.id);
  assert.equal(ids.indexOf('notifications'), ids.indexOf('agents') + 1);
  assert.equal(settingsSectionOf('/settings/notifications'), 'notifications');
  assert.equal(sectionLabel('notifications'), 'Notifications');
  assert.match(read('components/settings/SettingsShell.tsx'), /case 'notifications': return <NotificationsSection \/>;/);
});

test('the page: intro, then What you hear about, Quiet hours, This device; no scope tags (each row says it in words)', () => {
  const p = page();
  assert.match(p, /<PageIntro>What reaches you, and when\.<\/PageIntro>/);
  const at = order(p, ['<Card title="What you hear about">', '<Card title="Quiet hours">', '<Card title="This device">']);
  assert.deepEqual([...at].sort((a, b) => a - b), at);
  assert.doesNotMatch(p, /<Group /);
  assert.match(p, /k="notifications\.level" label="Alerts"[\s\S]*?description="Follows you to every device\. Needs me: only cards and questions waiting on you\. Nothing: no alerts; the bell still lists them\."/);
  assert.match(p, /label="Quiet hours"[\s\S]*?description="Nothing buzzes or pushes in these hours; cards still land in the app\. Follows you to every device\."/);
  // the level row comes first, then the per-agent rows, in the same card
  const card = p.slice(p.indexOf('<Card title="What you hear about">'), p.indexOf('<Card title="Quiet hours">'));
  order(card, ['k="notifications.level"', '<AgentOverrides />']);
});

test('the three choices, in order, on the server keys the push path reads', () => {
  assert.deepEqual(LEVEL_OPTIONS.map((o) => [o.value, o.label]), [['all', 'All'], ['needs_me', 'Needs me'], ['nothing', 'Nothing']]);
  const p = page();
  for (const k of ['notifications.level', 'notifications.agentOverrides', 'notifications.quietHours']) assert.match(p, new RegExp(k.replace('.', '\\.')));
  assert.ok(isLevel('needs_me') && !isLevel('loud') && !isLevel(null));
});

test('This device: the dashboard shows no system notifications, so only a note (no Allow, Test or Sound rows to fake)', () => {
  const p = page();
  assert.match(p, /<Card title="This device">\s*<ThisDeviceNote \/>\s*<\/Card>/);
  assert.match(THIS_DEVICE_NOTE, /doesn't show pop-up alerts[\s\S]*bell[\s\S]*watch app/);
  // and that is still true of the dashboard: nothing calls the Notification API, the service worker only removes itself
  const main = read('main.tsx');
  assert.match(main, /Service worker removed/);
  assert.doesNotMatch(main + p, /Notification\.requestPermission|new Notification\(|showNotification/);
  assert.match(readFileSync(join(SRC, '..', 'public', 'sw.js'), 'utf-8'), /KILL SWITCH/);
});

test('agents for the picker: either payload shape, id + name, by name; overrides listed by name, gone agents by id', () => {
  const agents = agentChoices({ agents: [{ id: 'qa-b', name: 'QA' }, { id: 'dev-a', name: ' Dev ' }, { id: 'gm' }, { id: '' }, { id: 'gm' }, { name: 'x' }] });
  assert.deepEqual(agents, [{ id: 'dev-a', name: 'Dev' }, { id: 'gm', name: 'gm' }, { id: 'qa-b', name: 'QA' }]);
  assert.deepEqual(agentChoices([{ id: 'a' }]), [{ id: 'a', name: 'a' }]);
  assert.deepEqual(agentChoices(undefined), []);
  assert.deepEqual(agentChoices([{ id: 'unregistered:scratch' }, { id: 'mac:foo' }, { id: 'tmp', unregistered: true }, { id: 'gm' }]),
    [{ id: 'gm', name: 'gm' }]);
  const o = overridesOf({ 'qa-b': 'nothing', 'old-seat': 'all', bad: 'loud' });
  assert.deepEqual(o, { 'qa-b': 'nothing', 'old-seat': 'all' });
  assert.deepEqual(overrideRows(o, agents), [{ id: 'old-seat', name: 'old-seat', level: 'all' }, { id: 'qa-b', name: 'QA', level: 'nothing' }]);
  assert.deepEqual(addableAgents(agents, o, 'gm').map((a) => a.id), ['dev-a']);
  assert.deepEqual(overridesOf(['x']), {});
});

test('adding and removing one agent never touches the others', () => {
  const o = { a: 'all' as const, b: 'nothing' as const };
  assert.deepEqual(withOverride(o, 'c', 'needs_me'), { a: 'all', b: 'nothing', c: 'needs_me' });
  assert.deepEqual(withOverride(o, 'a', 'nothing'), { a: 'nothing', b: 'nothing' });
  assert.deepEqual(withoutOverride(o, 'a'), { b: 'nothing' });
  assert.deepEqual(o, { a: 'all', b: 'nothing' }, 'not changed in place');
  // a picked agent is saved only once a level is picked; remove of a staged one saves nothing
  const p = page();
  assert.match(p, /const set = \(id: string, level: NotifyLevel\) => \{ setChanged\(id\); if \(id === adding\) setAdding\(null\); void save\(withOverride\(overrides, id, level\)\); \};/);
  assert.match(p, /onClick=\{\(\) => \(o\.id === adding \? setAdding\(null\) : remove\(o\.id\)\)\}/);
  assert.match(p, /aria-label=\{`Remove \$\{o\.name\}`\}/);
});

test('quiet hours: off is null, on starts 22:00–07:00; a time saves only when whole and different from the other end', () => {
  assert.deepEqual(DEFAULT_QUIET, { from: '22:00', to: '07:00' });
  assert.equal(quietOf(null), null);
  assert.equal(quietOf({ from: '22:00' }), null);
  assert.equal(quietOf({ from: '9:00', to: '17:00' }), null);
  assert.deepEqual(quietOf({ from: '22:00', to: '07:00' }), { from: '22:00', to: '07:00' });
  assert.deepEqual(changeQuiet(DEFAULT_QUIET, 'to', '06:30'), { ok: true, value: { from: '22:00', to: '06:30' } });
  assert.deepEqual(changeQuiet(DEFAULT_QUIET, 'from', ''), { ok: false, error: null });
  assert.deepEqual(changeQuiet(DEFAULT_QUIET, 'from', '07:00'), { ok: false, error: 'The start and the end must differ' });
  assert.match(page(), /onToggle=\{\(\) => void save\(quiet \? null : DEFAULT_QUIET\)\}/);
});

test("quiet hours say which clock: the install's zone, or UTC while none is set (what the push path uses)", () => {
  assert.equal(quietZoneLine('America/New_York'), "Times are in the install's time zone, America/New York.");
  assert.match(quietZoneLine(''), /UTC: the install's time zone isn't set/);
  assert.match(page(), /note=\{quiet \? quietZoneLine\(zone\) : null\}/);
  assert.match(page(), /const zone = useInstallTimeZone\(\);/);
});
