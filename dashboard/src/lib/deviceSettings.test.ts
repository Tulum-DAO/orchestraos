import { test } from 'node:test';
import assert from 'node:assert/strict';
import { display, setDeviceSetting, resetDisplay } from './deviceSettings.ts';
import { DISPLAY_DEFAULTS } from './displaySettings.ts';

test('a change is live at once (no storage in this runtime: the page still follows), and reset puts the defaults back', () => {
  assert.deepEqual(display(), DISPLAY_DEFAULTS);
  setDeviceSetting('textScale', 1.5);
  setDeviceSetting('sidebarWidth', 320);
  assert.equal(display().textScale, 1.5);
  assert.equal(display().sidebarWidth, 320);
  resetDisplay();
  assert.deepEqual(display(), DISPLAY_DEFAULTS);
});
