/**
 * Each Display setting is wired where it takes effect (Settings › Display): the variables lib/displaySettings.ts sets
 * on <html> are read by the rules that draw the chat and the sidebar.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));
export const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');

test('spacing drives the space between messages and between sidebar rows', () => {
  assert.match(read('components/arturo/arturo.css'), /\.arturo-feed\{[^}]*gap:calc\(16px \* var\(--ui-gap, 1\)\)/);
  assert.match(read('components/chat/TranscriptChatView.tsx'), /ui-feed-gap/);
  // specificity 0, like Tailwind's space-y: a row's own margin (a system row's my-1) still wins
  assert.match(read('index.css'), /@layer utilities \{ :where\(\.ui-feed-gap > :not\(:last-child\)\) \{ margin-block-end: calc\(0\.625rem \* var\(--ui-gap, 1\)\); \} \}/);
  assert.match(read('components/workbench/AgentRail.tsx'), /py-\[calc\(6px\*var\(--ui-gap,1\)\)\] min-h-8 max-md:min-h-11/);
});

test('message width drives the bubble cap and the Modern column', () => {
  assert.match(read('components/kit/kit.css'), /max-width: calc\(var\(--ui-msg-width, 560px\) - 30px\)/);
  assert.match(read('components/arturo/arturo.css'), /100% - 928px - var\(--ui-col-extra, 0px\)/);
  assert.match(read('components/chat/TranscriptChatView.tsx'), /max-w-\[calc\(860px\+var\(--ui-col-extra,0px\)\)\]/);
});

test('timestamps: every message and tool-line time carries data-msg-time, which "On hover" hides until hovered', () => {
  assert.match(read('components/chat/TranscriptCards.tsx'), /data-msg-time/);
  assert.match(read('components/kit/ToolCard.tsx'), /data-msg-time/);
  // only where something can hover: on a touch screen "On hover" shows them as "Always" does
  assert.match(read('index.css'), /@media \(hover: hover\) \{\s*html\[data-timestamps="hover"\] \[data-msg-time\] \{ opacity: 0;/);
});

test('tool call detail: the cards use this device\'s level unless a caller asks for one', () => {
  const card = read('components/kit/ToolCard.tsx');
  assert.match(card, /useDeviceSetting\('toolDetail'\)/);
  assert.match(card, /const density = densityProp \?\? deviceDensity;/);
  assert.doesNotMatch(read('components/chat/TranscriptCards.tsx'), /density = 'normal'/);
});

test('motion: one switch for the word fade, follow-scroll, the reply rise and avatars; Reduced stops CSS motion too', () => {
  assert.match(read('lib/revealStore.ts'), /export const reducedMotion = \(\) => motionReduced\(\);/);
  assert.match(read('components/kit/RevealText.tsx'), /motionReduced\(\)/);
  assert.match(read('components/avatar/AgentAvatar.tsx'), /useMotionReduced\(\)/);
  assert.match(read('components/chat/TranscriptChatView.tsx'), /motionReduced\(\)/);
  assert.doesNotMatch(read('components/chat/TranscriptChatView.tsx'), /prefers-reduced-motion/);
  assert.match(read('index.css'), /html\[data-motion="reduced"\] \*/);
});

test('avatars beside text follow the avatar rule; avatars that are controls never do', () => {
  for (const f of ['components/Sidebar.tsx', 'components/workbench/AgentRail.tsx', 'components/agent/AgentWorkingRow.tsx', 'components/chat/AgentMessage.tsx', 'pages/ArturoHome.tsx']) {
    assert.match(read(f), /useTextAvatar\(/, f);
  }
  assert.doesNotMatch(read('components/workbench/AgentRail.tsx'), /size=\{32\}|const FACE = 32/);
  for (const f of ['components/arturo/ArturoButton.tsx', 'components/call/CallBar.tsx', 'components/call/CallChip.tsx', 'components/agent/TopBar.tsx', 'components/arturo/ArturoPanel.tsx', 'components/avatar/LookPicker.tsx']) {
    assert.doesNotMatch(read(f), /useTextAvatar/, `${f} is a control: its avatar keeps its size`);
  }
});

test('sidebar width: desktop reads --sidebar-w (text-driven default or the drag), the phone drawer --drawer-w', () => {
  const s = read('components/Sidebar.tsx');
  assert.match(s, /w-\[var\(--sidebar-w,240px\)\]/);
  assert.match(s, /w-\[var\(--drawer-w,80vw\)\] max-w-\[min\(100vw,360px\)\]/, 'the drawer is capped at 360px (a tablet got ~595)');
  assert.match(s, /data-sidebar=\{variant\}/);
  assert.doesNotMatch(s, /className="w-60 /);
  const l = read('layouts/DashboardLayout.tsx');
  assert.match(l, /<Sidebar variant="desktop" \/>\s*<SidebarResizer \/>/);
  assert.match(l, /<Sidebar variant="drawer"/);
  const r = read('components/SidebarResizer.tsx');
  assert.match(r, /onDoubleClick=\{\(\) => set\(null\)\}/);
  assert.match(r, /clampSidebarWidth/);
});

test('the panel\'s Row, Info and Switch live in one place, shared with Settings', () => {
  const kit = read('components/arturo/PanelRows.tsx');
  for (const n of ['Row', 'Info', 'Switch', 'LinkRow']) assert.match(kit, new RegExp(`export function ${n}\\b`));
  assert.doesNotMatch(read('components/arturo/ArturoPanel.tsx'), /^function (Row|Info)\b/m);
  assert.doesNotMatch(read('components/arturo/RotationSettings.tsx'), /^function Switch\b/m);
});

test('settings rows: saved tick, revert with the reason, lock for non-admins, delayed-effect note, changed-by line', () => {
  const kit = read('components/settings/SettingsKit.tsx');
  for (const marker of ['data-setting-saved', 'data-setting-error', 'data-setting-locked', 'data-setting-note', 'data-setting-changed', 'data-scope']) {
    assert.match(kit, new RegExp(marker), marker);
  }
  const hook = read('components/settings/useServerSetting.ts');
  assert.match(hook, /revertRow\(useServerSettings\.getState\(\)\.rows\[key\], shown, before\)/, 'a refused save puts the old value back (unless a newer one came in)');
});

test('General: Appearance and emoji tone (This device), default chat style (You), time zone (Everyone)', () => {
  const g = read('components/settings/sections/GeneralSection.tsx');
  assert.match(g, /scope="device"/); assert.match(g, /scope="user"/); assert.match(g, /scope="everyone"/);
  for (const k of ['general.defaultChatStyle', 'general.timeZone']) assert.match(g, new RegExp(k.replace('.', '\\.')));
  assert.match(g, /useTheme\(\)/); assert.match(g, /writeTone\(/);
});

test('Display: preview first, then every row on this device; message and sidebar width only on desktop; reset confirms', () => {
  const d = read('components/settings/sections/DisplaySection.tsx');
  assert.match(d, /<DisplayPreview \/>/);
  for (const k of ['textScale', 'spacing', 'messageWidth', 'toolDetail', 'timestamps', 'motion']) assert.match(d, new RegExp(`k="${k}"`), k);
  assert.match(d, /\{desktop && <DeviceChoiceRow k="messageWidth"/);
  assert.match(d, /\{desktop && <SidebarWidthRow \/>\}/);
  assert.match(d, /<ConfirmButton[\s\S]*?resetDisplay\(\)/);
  const p = read('components/settings/sections/DisplayPreview.tsx');
  for (const m of ['BUBBLES_USER', 'BUBBLES_AGENT', 'data-msg-time', 'useTextAvatar(20)', 'useTextAvatar(32)']) assert.match(p, new RegExp(m.replace(/[()]/g, '\\$&')), m);
});

test('Voice & calls: provider, default voice, new agents take calls, daily limit (Everyone); mic, transcript, colour (This device)', () => {
  const v = read('components/settings/sections/VoiceSection.tsx');
  for (const k of ['voice.provider', 'voice.defaultVoice', 'voice.newAgentsTakeCalls', 'voice.dailyLimitMin']) assert.match(v, new RegExp(k.replace('.', '\\.')));
  for (const s of ['setMicMode', 'setTranscriptMode', 'setAccentVoice', 'Not available on this install']) assert.match(v, new RegExp(s));
});

test('Agents: default model (inline picker), auto-compact, compact at (stepper), show compaction, safety limits read-only', () => {
  const a = read('components/settings/sections/AgentsSection.tsx');
  for (const k of ['agents.defaultModel', 'agents.autoCompact', 'agents.compactAt', 'agents.showCompaction', 'agents.safetyLimits']) assert.match(a, new RegExp(k.replace('.', '\\.')));
  assert.match(a, /variant="inline"/);
  assert.match(a, /stepper/);
});

test('Settings opens over the current page from the operator menu or ⌘, / Ctrl+,; the menu has Settings, Appearance, Log out', () => {
  const app = read('App.tsx');
  assert.match(app, /<Routes location=\{pageLocation\}>/);
  // its own error boundary: a throw inside Settings shows the error there and never unmounts the app behind it
  assert.match(app, /\{inSettings && <RouteErrorBoundary label="settings"><SettingsShell section=\{section\} \/><\/RouteErrorBoundary>\}/);
  assert.match(app, /isSettingsShortcut\(e, mac\)/);
  const side = read('components/Sidebar.tsx');
  for (const m of ['data-operator-settings', 'data-operator-logout', 'appearanceRows(theme)']) assert.match(side, new RegExp(m.replace(/[()]/g, '\\$&')));
  const shell = read('components/settings/SettingsShell.tsx');
  for (const m of ['aria-modal="true"', 'data-settings', "e.key === 'Escape'", "e.key !== 'Tab'", 'opener', 'settings-scrim', 'Done']) assert.match(shell, new RegExp(m.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), m);
});

test('the agent panel keeps only the agent\'s own settings and links to Settings', () => {
  const p = read('components/arturo/ArturoPanel.tsx');
  assert.doesNotMatch(p, /On this device/);
  assert.doesNotMatch(p, /\bsoon \/>/);
  assert.doesNotMatch(p, /DevicePage/);
  assert.match(p, /<TakesCalls agentId=\{agentId\} \/>/);
  assert.match(p, /label="Server settings"/);
  assert.match(p, /openSettings\('general'\)/);
  assert.match(p, /openSettings\('voice'\)/);
  assert.doesNotMatch(read('lib/arturoPanel.ts'), /'device'/);
  const home = read('pages/ArturoHome.tsx');
  assert.doesNotMatch(home, /Open brain/);
  assert.match(home, /aria-label=\{`\$\{aname\}'s panel`\}/);
});

test('a Modern reply never grows past its column: a long code line scrolls inside its block', () => {
  // .turn-assistant is a column flex with align-items:flex-start, so a reply sized to its content took the min-content
  // width of a fenced block (phone, 150% text: 459px in a 358px column); capped at the column, the block scrolls.
  assert.match(read('components/arturo/arturo.css'), /\.turn-assistant > \*\{ max-width:100%; \}/);
});
