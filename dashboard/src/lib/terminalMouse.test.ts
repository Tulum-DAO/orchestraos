import { test } from 'node:test';
import assert from 'node:assert/strict';
import xterm from '@xterm/xterm';
import { blockMouseModes, copyKeyAction, setModeAction } from './terminalMouse.ts';

const { Terminal } = xterm;
const write = (t: InstanceType<typeof Terminal>, s: string) => new Promise<void>((r) => t.write(s, r));
const fresh = () => { const t = new Terminal({ allowProposedApi: true }); blockMouseModes(t); return t; };

test('what tmux 3.4 sends an attaching client with mouse on: no mouse mode reaches the terminal, the rest does', async () => {
  // Captured from `tmux attach` (3.4, mouse on, TERM=xterm-256color): each mode in its own sequence.
  const t = fresh();
  await write(t, '\x1b[?1049h\x1b[?1h\x1b[?12l\x1b[?25h\x1b[?2004h\x1b[?1006h\x1b[?1000h\x1b[?1002h\x1b[?25l');
  assert.equal(t.modes.mouseTrackingMode, 'none');
  assert.equal(t.buffer.active.type, 'alternate');
  assert.equal(t.modes.bracketedPasteMode, true);
  assert.equal(t.modes.applicationCursorKeysMode, true);
  // Without the hook the same bytes do turn mouse reporting on (what made a drag go to tmux).
  const plain = new Terminal({ allowProposedApi: true });
  await write(plain, '\x1b[?1006h\x1b[?1000h\x1b[?1002h');
  assert.equal(plain.modes.mouseTrackingMode, 'drag');
});

test('a mouse mode in the same sequence as other modes: those are applied where they stand, the mouse ones not', async () => {
  const t = fresh();
  await write(t, '\x1b[?1000;1006h');                   // a pair in one sequence: mouse only
  assert.equal(t.modes.mouseTrackingMode, 'none');
  await write(t, '\x1b[?1000;1049;2004hX');
  await write(t, '');
  assert.equal(t.modes.mouseTrackingMode, 'none');
  assert.equal(t.buffer.active.type, 'alternate');
  assert.equal(t.modes.bracketedPasteMode, true);
  assert.equal(t.buffer.active.getLine(0)?.translateToString(true), 'X', 'the text after it lands on the alternate screen');
  assert.deepEqual(setModeAction([1000, 1049, 2004]), { kind: 'mixed', mouse: [1000] });
  assert.deepEqual(setModeAction([1049]), { kind: 'pass' });
  assert.deepEqual(setModeAction([1000, [1006]]), { kind: 'swallow' });
});

test('resets are left alone, and modes that are not the mouse\'s pass as before', async () => {
  const t = fresh();
  await write(t, '\x1b[?1049h\x1b[?2004h\x1b[?25l');
  assert.equal(t.buffer.active.type, 'alternate');
  await write(t, '\x1b[?1049l\x1b[?2004l\x1b[?1000l\x1b[?1006l');
  assert.equal(t.buffer.active.type, 'normal');
  assert.equal(t.modes.bracketedPasteMode, false);
  assert.equal(t.modes.mouseTrackingMode, 'none');
});

test('Cmd/Ctrl+C copies a selection; with none it goes to the pane (Ctrl+C interrupts)', () => {
  const k = (over: Partial<{ type: string; key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }>) =>
    ({ type: 'keydown', key: 'c', ctrlKey: false, metaKey: false, altKey: false, ...over });
  assert.equal(copyKeyAction(k({ ctrlKey: true }), true), 'copy');
  assert.equal(copyKeyAction(k({ metaKey: true }), true), 'copy');
  assert.equal(copyKeyAction(k({ ctrlKey: true, type: 'keyup' }), true), 'swallow');
  assert.equal(copyKeyAction(k({ ctrlKey: true }), false), 'pass');
  assert.equal(copyKeyAction(k({ ctrlKey: true, key: 'C' }), true), 'copy');
  assert.equal(copyKeyAction(k({}), true), 'pass', 'a plain c types');
  assert.equal(copyKeyAction(k({ ctrlKey: true, key: 'v' }), true), 'pass');
});
