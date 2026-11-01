// The agent page's top-bar seat pill is centred (the dock row stays at the left), and Export chat is a button in the top-right
// corner of the agent's slideout, not the top bar. Read from the source, like lib/headerLayout.test.ts.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('the dock row and its job chips sit at the left; the top bar\'s seat pill is the centred one', () => {
  const css = src('components/agent/agentWorking.css');
  assert.doesNotMatch(css, /margin-inline:auto/);
  assert.doesNotMatch(css, /justify-content:center/);
  // the row is not a bar the floating Arturo button lifts over (it would drop when the row goes: a shift); it keeps
  // clear of the button's corner on narrow screens instead
  assert.doesNotMatch(src('components/agent/AgentWorkingRow.tsx'), /data-arturo-avoid/);
  assert.match(src('components/agent/agentWorking.css'), /@media \(max-width: 900px\)\{ \.agent-working\{ padding-right:56px; \} \}/);
  // three columns, equal ends that never shrink below their content, the pill truncating in the middle
  const top = src('components/agent/TopBar.tsx');
  assert.match(top, /grid grid-cols-\[minmax\(max-content,1fr\)_minmax\(0,auto\)_minmax\(max-content,1fr\)\]/);
  assert.match(top, /className="flex items-center justify-center gap-2 min-w-0" data-seat-header/);
  assert.doesNotMatch(top, /md:absolute/);
});

test('Export chat: a corner button in the agent slideout (no row), opening a popover with the same download and copy', () => {
  assert.equal(existsSync(new URL('../components/agent/ExportMenu.tsx', import.meta.url)), false);
  assert.equal(existsSync(new URL('../components/agent/ExportChatRow.tsx', import.meta.url)), false);
  assert.doesNotMatch(src('components/agent/TopBar.tsx'), /Export/);
  const panel = src('components/arturo/ArturoPanel.tsx');
  assert.doesNotMatch(panel, /data-panel-export|ExportChatRow/);
  assert.equal((panel.match(/<ExportChatButton /g) ?? []).length, 1);
  // first in the root page's body, right under the bar's Done / X, before the identity block; agents only
  assert.match(panel, /\{!isArturo && <ExportChatButton agentId=\{agentId\} \/>\}\n\s*<DrawerFace /);
  const btn = src('components/agent/ExportChatButton.tsx');
  assert.match(btn, /fetch\(exportUrl\(agentId\)\)/);
  assert.match(btn, /exportFileName\(r\.headers\.get\('Content-Disposition'\), agentId\)/);
  assert.match(btn, /role="menu"/);
  assert.match(btn, /e\.key === 'Escape'/);
  assert.match(btn, /'pointerdown'/);
  for (const k of ['Export chat', 'Download .md', 'Copy as Markdown']) assert.ok(btn.includes(k), k);
  // it takes no room: a zero-height anchor; the popover floats, anchored to the button's right edge
  const css = src('components/agent/exportChat.css');
  assert.match(css, /\.export-chat-anchor\{ position:relative; height:0;/);
  assert.match(css, /\.export-chat-menu\{ position:absolute; top:100%; right:0;/);
});
