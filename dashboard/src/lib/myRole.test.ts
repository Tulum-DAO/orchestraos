import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { setInstallRole, installRole, isInstallAdmin, subscribeInstallRole, useInstallAdmin } from './myRole.ts';
import { canChangeVoice } from './arturoVoice.ts';
import { canRename } from './assistantName.ts';
import { canChangeLook } from './avatarLooks.ts';

test('everyone is a member (read-only) until the server says admin; only "admin" unlocks the admin-only actions', () => {
  assert.equal(installRole(), 'member', 'before /api/me answers, or when it fails, nothing admin-only is offered');
  assert.deepEqual([canChangeVoice(), canRename(), canChangeLook()], [false, false, false]);
  let heard = 0;
  const off = subscribeInstallRole(() => { heard += 1; });
  setInstallRole('admin');
  assert.equal(isInstallAdmin(), true);
  assert.deepEqual([canChangeVoice(), canRename(), canChangeLook()], [true, true, true]);
  setInstallRole('nonsense');
  assert.equal(installRole(), 'member', 'anything but "admin" is member');
  setInstallRole('admin');
  setInstallRole(undefined);
  assert.equal(installRole(), 'member', 'a missing install_role (older API) is member');
  assert.equal(heard, 4);
  off();
});

test('the role is read through a subscription wherever a component renders with it, so an admin arriving late unlocks', () => {
  assert.equal(typeof useInstallAdmin, 'function', 'useInstallAdmin(): useSyncExternalStore(subscribeInstallRole, isInstallAdmin)');
  const src = new URL('../', import.meta.url);
  const offenders: string[] = [];
  for (const f of readdirSync(src, { recursive: true }) as string[]) {
    if (!/\.tsx$/.test(f) || /\.test\./.test(f)) continue;
    const text = readFileSync(new URL(f, src), 'utf8');
    // A bare call is read once at render and never again; as the store's getSnapshot argument it is subscribed.
    for (const m of text.matchAll(/\b(canChangeLook|canRename|canChangeVoice|isInstallAdmin|installRole)\(\)/g)) offenders.push(`${f}: ${m[0]}`);
  }
  assert.deepEqual(offenders, []);
});
