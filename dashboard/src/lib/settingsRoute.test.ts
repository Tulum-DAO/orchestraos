import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SETTINGS_SECTIONS, visibleSections, settingsPath, settingsSectionOf, settingsStateOf, openSettingsState, nextSettingsState,
  closeSettingsTarget, backgroundPath, isSettingsShortcut, isMacPlatform, settingsShortcutLabel, matchesQuery, SCOPE_LABEL,
} from './settingsRoute.ts';

test('phase 1 shows General, Display, Voice & calls, Agents; the later categories are listed but hidden', () => {
  assert.deepEqual(visibleSections().map((s) => s.label), ['General', 'Display', 'Voice & calls', 'Agents']);
  assert.deepEqual(SETTINGS_SECTIONS.filter((s) => !s.visible).map((s) => s.id), ['models', 'keys', 'notifications', 'people', 'system']);
});

test('paths: /settings is the list, /settings/<section> a section; unknown or hidden sections open General', () => {
  assert.equal(settingsSectionOf('/agent/x'), undefined);
  assert.equal(settingsSectionOf('/settings'), null);
  assert.equal(settingsSectionOf('/settings/'), null);
  assert.equal(settingsSectionOf('/settings/display'), 'display');
  assert.equal(settingsSectionOf('/settings/keys'), 'general');
  assert.equal(settingsSectionOf('/settings/nope'), 'general');
  assert.equal(settingsPath('voice'), '/settings/voice');
  assert.equal(settingsPath(null), '/settings');
});

test('opening keeps the page behind; each section is one Back step; closing goes back past them all', () => {
  const opened = openSettingsState({ pathname: '/agent/dev', search: '?x=1', hash: '' });
  assert.deepEqual(opened, { settingsBg: { pathname: '/agent/dev', search: '?x=1', hash: '' }, settingsDepth: 1 });
  const deeper = nextSettingsState(nextSettingsState(opened));
  assert.equal(deeper.settingsDepth, 3);
  assert.deepEqual(closeSettingsTarget(deeper), { back: 3 });
  assert.equal(backgroundPath(deeper), '/agent/dev?x=1');
  assert.deepEqual(closeSettingsTarget(settingsStateOf(null)), { to: '/' }, 'a deep link closes to the home page');
  assert.deepEqual(settingsStateOf({ settingsDepth: 'x', settingsBg: 5 }), {});
});

test('⌘, on a Mac, Ctrl+, elsewhere; nothing else opens it', () => {
  const k = (o: Partial<{ key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean }>) =>
    ({ key: ',', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...o });
  assert.equal(isSettingsShortcut(k({ metaKey: true }), true), true);
  assert.equal(isSettingsShortcut(k({ ctrlKey: true }), true), false);
  assert.equal(isSettingsShortcut(k({ ctrlKey: true }), false), true);
  assert.equal(isSettingsShortcut(k({ metaKey: true }), false), false);
  assert.equal(isSettingsShortcut(k({ ctrlKey: true, altKey: true }), false), false);
  assert.equal(isSettingsShortcut(k({ key: '.', ctrlKey: true }), false), false);
  assert.deepEqual([isMacPlatform('MacIntel'), isMacPlatform('iPhone'), isMacPlatform('Linux x86_64')], [true, true, false]);
  assert.deepEqual([settingsShortcutLabel(true), settingsShortcutLabel(false)], ['⌘,', 'Ctrl+,']);
});

test('search matches every word against the label and keywords; scope labels', () => {
  assert.equal(matchesQuery('', 'Text size', []), true);
  assert.equal(matchesQuery('text', 'Text size', []), true);
  assert.equal(matchesQuery('font big', 'Text size', ['font', 'bigger']), true);
  assert.equal(matchesQuery('voice', 'Text size', ['font']), false);
  assert.deepEqual(SCOPE_LABEL, { device: 'This device', user: 'You', everyone: 'Everyone' });
});

test('a deep link with sections clicked closes by going back to the link\'s own entry and replacing it, so Back never reopens Settings', () => {
  const clicked = nextSettingsState(settingsStateOf(null));
  assert.equal(clicked.settingsDepth, 1);
  assert.deepEqual(closeSettingsTarget(clicked), { back: 1, then: '/' });
  assert.deepEqual(closeSettingsTarget(nextSettingsState(clicked)), { back: 2, then: '/' });
});
