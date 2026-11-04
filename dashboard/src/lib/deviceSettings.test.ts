import { test } from 'node:test';
import assert from 'node:assert/strict';
import { display, setDeviceSetting, resetDisplay } from './deviceSettings.ts';
import { DISPLAY_DEFAULTS, DISPLAY_RESET_KEYS } from './displaySettings.ts';

test('a change is live at once (no storage in this runtime: the page still follows), and reset puts the defaults back', () => {
  assert.deepEqual(display(), DISPLAY_DEFAULTS);
  setDeviceSetting('spacing', 'roomy');
  setDeviceSetting('sidebarWidth', 320);
  assert.equal(display().spacing, 'roomy');
  assert.equal(display().sidebarWidth, 320);
  resetDisplay();
  assert.deepEqual(display(), DISPLAY_DEFAULTS);
});

test('Display\'s Reset puts back only the rows shown on Display; General\'s text size, agent steps, timestamps and the send keys stay', () => {
  assert.deepEqual([...DISPLAY_RESET_KEYS].sort(), ['messageWidth', 'motion', 'sidebarWidth', 'spacing']);
  setDeviceSetting('textScale', 1.5);
  setDeviceSetting('toolDetail', 'verbose');
  setDeviceSetting('timestamps', 'off');
  setDeviceSetting('sendShortcut', 'mod-enter');
  setDeviceSetting('motion', 'reduced');
  setDeviceSetting('messageWidth', 'wide');
  resetDisplay();
  const d = display();
  assert.equal(d.motion, 'system'); assert.equal(d.messageWidth, 'default');
  assert.equal(d.textScale, 1.5); assert.equal(d.toolDetail, 'verbose'); assert.equal(d.timestamps, 'off'); assert.equal(d.sendShortcut, 'mod-enter');
  for (const [k, v] of [['textScale', 1], ['toolDetail', 'normal'], ['timestamps', 'always'], ['sendShortcut', null]] as const) setDeviceSetting(k, v as never);
});
