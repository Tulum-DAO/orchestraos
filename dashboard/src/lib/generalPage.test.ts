/**
 * Settings › General, the OpenBot layout: Appearance (theme, chat style, text size with its preview,
 * emoji skin tone) and Conversations (send shortcut, agent steps, timestamps). Text size, agent steps and timestamps
 * moved here from Display; the install's time zone moved to its own category at the bottom of the list.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SETTINGS_SECTIONS, visibleSections, settingsSectionOf, sectionLabel, serverGroupLabel } from './settingsRoute.ts';
import { readDisplay, displayAttrs, DISPLAY_DEFAULTS, DISPLAY_KEYS, TIMESTAMPS } from './displaySettings.ts';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');
const general = () => read('components/settings/sections/GeneralSection.tsx');
const display = () => read('components/settings/sections/DisplaySection.tsx');
const server = () => read('components/settings/sections/ServerSection.tsx');
const order = (src: string, marks: string[]) => marks.map((m) => { const i = src.indexOf(m); assert.ok(i >= 0, `missing ${m}`); return i; });

test('General: title from the bar, the approved intro, an Appearance card then a Conversations card', () => {
  const g = general();
  assert.match(g, /<PageIntro>How OrchestraOS looks and behaves for you\.<\/PageIntro>/);
  const [a, c] = order(g, ['<Card title="Appearance">', '<Card title="Conversations">']);
  assert.ok(a < c);
  assert.equal(sectionLabel('general'), 'General');
});

test('General › Appearance: Theme, Chat style (You; directly below Theme), Text size (with the live preview), Emoji skin tone, in that order', () => {
  const g = general();
  const appearance = g.slice(g.indexOf('<Card title="Appearance">'), g.indexOf('<Card title="Conversations">'));
  const at = order(appearance, ['<ThemeRow />', 'k="general.defaultChatStyle" label="Chat style"', '<TextSizeRow />', '<SkinToneRow />']);
  // directly below Theme: nothing between them
  assert.match(appearance, /<ThemeRow \/>\s*<ServerChoiceRow k="general\.defaultChatStyle"/);
  assert.match(appearance, /description="The look new agents' chats start with\. Follows you to every device; each agent can still change its own\."/);
  assert.deepEqual([...at].sort((x, y) => x - y), at);
  assert.match(g, /<SettingRow id="display\.textScale" label="Text size"[\s\S]*?below=\{<DisplayPreview \/>\}/);
  assert.match(g, /label="Theme"/);
});

test('General › Conversations: Send shortcut, Show agent steps, Timestamps (Always / On hover / Off); Chat style is not here', () => {
  const g = general();
  const conv = g.slice(g.indexOf('<Card title="Conversations">'));
  assert.doesNotMatch(conv, /general\.defaultChatStyle/);
  const at = order(conv, ['<SendShortcutRow />', 'k="toolDetail" label="Show agent steps"', 'k="timestamps" label="Timestamps"']);
  assert.deepEqual([...at].sort((x, y) => x - y), at);
  assert.match(conv, /\{ value: 'always', label: 'Always' \}, \{ value: 'hover', label: 'On hover' \}, \{ value: 'off', label: 'Off' \}/);
  // the real semantics: only Detailed ('verbose') starts a run open (components/kit/ToolCard.tsx)
  assert.match(read('components/kit/ToolCard.tsx'), /useState\(density === 'verbose'\)/);
  assert.match(conv, /start open \(Detailed\) or folded to one line \(Summary, Normal\)/);
});

test('every General row says its scope in words; the cards carry no scope tag (one card mixes scopes)', () => {
  const g = general() + read('components/settings/DeviceRows.tsx');
  for (const m of [/label="Theme" description="[^"]*on this device\."/, /label="Text size" description="[^"]*on this device\."/,
    /label="Emoji skin tone" description="[^"]*on this device\."/, /Follows you to every device/,
    /description="Choose the keys that send a message on this device\."/, /label="Show agent steps"[\s\S]*?On this device\./,
    /label="Timestamps"[\s\S]*?on this device\./]) assert.match(g, m);
  assert.doesNotMatch(general(), /<Group /);
  assert.doesNotMatch(read('components/settings/SettingsKit.tsx').match(/export function Card[\s\S]*?\n}\n/)![0], /settings-scope/);
  // other categories keep the scope tag
  assert.match(read('components/settings/SettingsKit.tsx'), /export function Group[\s\S]*?SCOPE_LABEL\[scope\]/);
});

test('the time zone is not on General; it is the last category, under the install label, Everyone and admin-locked as before', () => {
  assert.doesNotMatch(general(), /general\.timeZone/);
  const s = server();
  assert.match(s, /<Group title="Install" scope="everyone">\s*<ServerSelectRow k="general\.timeZone"/);
  const vis = visibleSections();
  assert.equal(vis[vis.length - 1].id, 'server');
  assert.equal(vis[vis.length - 1].group, 'server');
  assert.equal(SETTINGS_SECTIONS[SETTINGS_SECTIONS.length - 1].id, 'server');
  assert.equal(serverGroupLabel(), 'Server');
  assert.equal(serverGroupLabel('  '), 'Server');
  assert.equal(serverGroupLabel('Office'), 'Office');
  assert.equal(settingsSectionOf('/settings/server'), 'server');
  const shell = read('components/settings/SettingsShell.tsx');
  assert.match(shell, /case 'server': return <ServerSection \/>;/);
  assert.match(shell, /data-settings-nav-group=\{s\.group\}>\{serverGroupLabel\(\)\}/);
  // the lock is the row's own (useServerSetting: !editable && !readOnly), the same hook as before
  assert.match(read('components/settings/ServerRows.tsx'), /export function ServerSelectRow[\s\S]*?locked=\{locked\}/);
  assert.match(read('components/settings/useServerSetting.ts'), /const locked = !!row && !row\.editable && !row\.readOnly;/);
});

test('Display: the moved rows are gone; spacing, message width, sidebar width, motion and reset stay', () => {
  const d = display();
  for (const k of ['textScale', 'toolDetail', 'timestamps']) assert.doesNotMatch(d, new RegExp(`k="${k}"`), k);
  for (const k of ['spacing', 'messageWidth', 'motion']) assert.match(d, new RegExp(`k="${k}"`), k);
  assert.match(d, /<SidebarWidthRow \/>/);
  assert.match(d, /<ResetDisplay \/>/);
});

test('deep links: /settings/display still opens Display, /settings/general General; nothing links to a moved row by Display', () => {
  assert.equal(settingsSectionOf('/settings/display'), 'display');
  assert.equal(settingsSectionOf('/settings/general'), 'general');
  for (const f of ['components/Sidebar.tsx', 'components/arturo/ArturoPanel.tsx', 'App.tsx']) assert.doesNotMatch(read(f), /openSettings\('display'\)/, f);
});

test('Timestamps Off: stored, put on <html>, and the times are left out', () => {
  assert.deepEqual([...TIMESTAMPS], ['always', 'hover', 'off']);
  const s = { getItem: (k: string) => (k === DISPLAY_KEYS.timestamps ? 'off' : null) };
  assert.equal(readDisplay(s).timestamps, 'off');
  assert.equal(displayAttrs({ ...DISPLAY_DEFAULTS, timestamps: 'off' })['data-timestamps'], 'off');
  assert.match(read('index.css'), /html\[data-timestamps="off"\] \[data-msg-time\] \{ display: none; \}/);
});

test('a row\'s description describes its control (aria-describedby) for every control kind', () => {
  const kit = read('components/settings/SettingsKit.tsx');
  assert.match(kit, /const descId = description \? descIdOf\(id\) : undefined;/);
  assert.match(kit, /<span id=\{descId\} className="settings-row-desc"/);
  assert.match(kit, /<RowDescription\.Provider value=\{descId\}>\{control\}<\/RowDescription\.Provider>/);
  assert.match(kit, /role="radiogroup" aria-labelledby=\{labelledBy\} aria-describedby=\{describedBy\}/);
  const rows = read('components/settings/ServerRows.tsx');
  assert.match(rows, /<select [^>]*aria-describedby=\{descOf\(k, description\)\}/);
  assert.match(rows, /<Switch [^>]*describedBy=\{descOf\(k, description\)\}/);
  assert.equal(rows.match(/aria-describedby=\{descOf\(k, description\)\}/g)?.length, 4, 'select, both stepper buttons and the number field');
  assert.match(read('components/arturo/PanelRows.tsx'), /aria-describedby=\{describedBy\}/);
});

test('the agent panel\'s "Server settings" opens the install\'s own category (server)', () => {
  assert.match(read('components/arturo/ArturoPanel.tsx'), /label="Server settings" data="server-settings" onClick=\{\(\) => toSettings\(\(\) => openSettings\('server'\)\)\}/);
});

test('Display\'s Reset says what it resets: only Display\'s rows', () => {
  assert.match(display(), /const RESET_WHAT = 'Spacing, message width, sidebar width and motion go back to the defaults on this device\.';/);
});
