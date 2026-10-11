/**
 * The header bell sits IN its row, and the chat transcript scrolls back through its history.
 * Shaw, 2026-10-11: on the agent page the bell (#400) sat ~16px below the moon / siren / brain
 * icons, and in Chat mode (the agent page and the Agents-page panel) a long transcript could not
 * be scrolled up at all. Both were layout, so the rule is pinned at the source like features.test.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bellHost } from './bellPlacement.ts';
import { isNearBottom } from './liveFeedScroll.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

// ---- the bell -------------------------------------------------------------------------------

test('bellHost: desktop uses a page header slot when one is mounted, else the corner', () => {
  assert.equal(bellHost({ desktop: true, pageSlot: true, phoneSlot: true }), 'page');
  assert.equal(bellHost({ desktop: true, pageSlot: false, phoneSlot: true }), 'corner');
});

test('bellHost: on an agent page a phone uses the page header (one bar, not two stacked)', () => {
  assert.equal(bellHost({ desktop: false, pageSlot: true, phoneSlot: true, chatRoute: true }), 'page');
  assert.equal(bellHost({ desktop: false, pageSlot: false, phoneSlot: true, chatRoute: true }), 'phone');
});

test('the phone bar is hidden on agent pages, sits above page headers, and the layout uses the visible height', () => {
  const layout = src('layouts/DashboardLayout.tsx');
  assert.match(layout, /isChatRoute \? 'hidden' : 'flex'/);
  assert.match(layout, /z-\[25\]/);
  assert.match(layout, /\bh-dvh\b/);
  const top = src('components/agent/TopBar.tsx');
  assert.doesNotMatch(top, /ArturoButton|Brain|Siren|cycleTheme/, 'the agent bar is menu, seat, Chat/Dev and the bell');
  assert.match(layout, /<ArturoButton small \/>/, 'Arturo floats, small');
  assert.match(layout, /--arturo-lift/, 'and is lifted above the composer');
  assert.match(layout, /var\(--vvh, 100dvh\)/, 'the app is the visible height, so the keyboard never covers the CLI');
  assert.match(src('pages/AgentPage.tsx'), /OPEN_SIDEBAR_EVENT/, 'the agent menu button opens the one sidebar');
  assert.doesNotMatch(top, /data-header-end-slot className="hidden/, 'the bell slot shows on phones too');
  assert.doesNotMatch(src('pages/Agents.tsx'), /"sticky top-0 z-20/, 'the Agents header no longer sticks under the phone bar');
});

test('bellHost: a phone off an agent page uses the phone bar', () => {
  assert.equal(bellHost({ desktop: false, pageSlot: true, phoneSlot: true }), 'phone');
  assert.equal(bellHost({ desktop: false, pageSlot: false, phoneSlot: true }), 'phone');
  assert.equal(bellHost({ desktop: false, pageSlot: false, phoneSlot: false }), 'corner');
});

test('the layout portals its ONE bell into the header slot instead of guessing the row height', () => {
  const layout = src('layouts/DashboardLayout.tsx');
  assert.equal((layout.match(/<NotificationBell\b/g) ?? []).length, 1, 'still one bell mount');
  assert.match(layout, /createPortal\(bell, pageBellSlot\)/);
  assert.match(layout, /createPortal\(bell, phoneBellSlot\)/);
  assert.match(layout, /<HeaderEndSlotContext\.Provider value=\{setPageBellSlot\}>/);
  assert.match(layout, /ref=\{setPhoneBellSlot\}/);
  // The phone bar no longer reserves blank room for a floating bell.
  assert.doesNotMatch(layout, /ml-auto mr-12/);
});

test('the agent top bar ends with the slot, inside the icon row, and reserves no padding for a fixed bell', () => {
  const topbar = src('components/agent/TopBar.tsx');
  assert.match(topbar, /useContext\(HeaderEndSlotContext\)/);
  assert.match(topbar, /<div ref=\{registerEndSlot \?\? undefined\} data-header-end-slot className="flex items-center" \/>\s*<\/div>\s*<\/div>\s*\);/,
    'the slot is the last child of the controls row');
  assert.doesNotMatch(topbar, /md:pr-12/);
});

test('the bell is a 40px flex box like its neighbours, and its badge takes no layout space', () => {
  const bell = src('components/NotificationBell.tsx');
  assert.match(bell, /className="relative flex shrink-0 items-center" ref=\{dropdownRef\}/);
  assert.match(bell, /className="relative flex items-center justify-center p-2 text-foreground/);
  assert.match(bell, /<Bell size=\{24\} \/>/);
  assert.match(bell, /<span className="pointer-events-none absolute -top-0\.5 -right-0\.5 /);
});

// ---- the chat transcript --------------------------------------------------------------------

test('the transcript scroller does not bottom-anchor with justify-end (overflow would go off the top)', () => {
  const view = src('components/chat/TranscriptChatView.tsx');
  const scroller = view.match(/: '(flex-1 overflow-y-auto min-h-0[^']*)'/);
  assert.ok(scroller, 'non-compact scroller class found');
  assert.doesNotMatch(scroller[1], /justify-end|flex-col-reverse/, scroller[1]);
  assert.match(scroller[1], /overflow-y-auto/);
  assert.match(scroller[1], /min-h-0/);
  // ...the column bottom-anchors itself with an auto margin instead.
  assert.match(view, /"mt-auto w-full max-w-\[860px\] mx-auto px-4 pt-4 space-y-2\.5"/);
});

test('the transcript follows the tail only while the reader is at the bottom', () => {
  const view = src('components/chat/TranscriptChatView.tsx');
  assert.match(view, /atBottom\.current = isNearBottom\(el\.scrollTop, el\.scrollHeight, el\.clientHeight, 120\)/);
  assert.match(view, /if \(el && atBottom\.current\) el\.scrollTop = el\.scrollHeight;/);
  // the rule itself: 120px from the bottom still follows, a reader 121px up is left alone
  assert.equal(isNearBottom(880, 2000, 1000, 120), true);
  assert.equal(isNearBottom(879, 2000, 1000, 120), false);
  assert.equal(isNearBottom(0, 2000, 1000, 120), false);
});

test('Chat mode never mounts the Dev terminal, whose document wheel/touch hack would eat the scroll', () => {
  // WebTerminal captures wheel and touchmove on the DOCUMENT (tmux copy-mode scroll) and only
  // ignores events outside its container; it must not exist at all while Chat mode is showing.
  const cases: [string, RegExp, string][] = [
    ['pages/AgentPage.tsx', /mode === 'dev' \? \(/, '<Feed agentId'],
    ['components/AgentCard.tsx', /devMode \? \(/, '<TranscriptChatView'],
  ];
  for (const [file, devBranch, chatView] of cases) {
    const s = src(file);
    const dev = s.search(devBranch);
    const elseAt = s.indexOf(') : (', dev);
    const chat = s.indexOf(chatView, elseAt);
    assert.ok(dev > 0 && elseAt > dev && chat > elseAt, file);
    const chatBranch = s.slice(elseAt, s.indexOf('\n            )}', chat) > 0 ? s.indexOf('\n            )}', chat) : chat + 2000);
    assert.doesNotMatch(chatBranch, /<WebTerminal/, `${file}: no terminal in the chat branch`);
  }
  assert.match(src('components/WebTerminal.tsx'), /if \(!container\.contains\(e\.target as Node\)\) return;\s*e\.preventDefault\(\);/);
});
