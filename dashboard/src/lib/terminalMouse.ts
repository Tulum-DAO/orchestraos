/**
 * terminalMouse.ts — the Dev terminal's text selection and copy.
 *
 * Seats run tmux with `mouse on` (scripts/tmux_session_defaults.sh: a native terminal's wheel must scroll tmux's
 * scrollback, not recall another seat's prompt). An attaching client is then told to report the mouse: tmux 3.4 sends
 * each mode on its own, `ESC[?1006h ESC[?1000h ESC[?1002h`. In the browser that turned a plain drag into mouse
 * reports, so tmux made the highlight and kept the text in its own buffer (sent as OSC 52, which nothing here
 * receives): nothing reached the clipboard.
 *
 * The web terminal keeps its own wheel and touch scrolling (copy-mode keys, components/WebTerminal.tsx), so it never
 * needs mouse reports. `blockMouseModes` keeps the pane's mouse-reporting modes away from xterm.js: a plain drag is
 * then the browser's own selection, and Cmd/Ctrl+C copies it (`copyKeyAction`). Every other private mode (1049, 25,
 * 2004, …) is applied as before. A native terminal attached to the same session still gets mouse on.
 */
import type { Terminal } from '@xterm/xterm';

/** The mouse-reporting private modes: X10/normal tracking, highlight, button-event, any-event, and the UTF-8, SGR,
 *  urxvt and SGR-pixel encodings. */
export const MOUSE_MODES: ReadonlySet<number> = new Set([9, 1000, 1001, 1002, 1003, 1005, 1006, 1015, 1016]);

const first = (p: number | number[]) => (Array.isArray(p) ? p[0] : p);

/** What to do with one `ESC[?…h`: nothing to change (no mouse mode in it), swallow it whole (only mouse modes), or
 *  apply it and then turn its mouse modes back off (mixed with other modes, which must keep their place). Pure. */
export function setModeAction(params: readonly (number | number[])[]): { kind: 'pass' } | { kind: 'swallow' } | { kind: 'mixed'; mouse: number[] } {
  const modes = params.map(first);
  const mouse = modes.filter((m) => MOUSE_MODES.has(m));
  if (!mouse.length) return { kind: 'pass' };
  if (mouse.length === modes.length) return { kind: 'swallow' };
  return { kind: 'mixed', mouse };
}

/** Keep mouse reporting off in this terminal (see the top of the file). Returns the hook's disposer. A sequence of
 *  mouse modes only is dropped; one that also sets other modes is applied as it is (so they take effect exactly where
 *  they stand in the stream), and its mouse modes are reset right after. Resets (`…l`) are never touched. */
export function blockMouseModes(term: Pick<Terminal, 'parser' | 'write'>): { dispose: () => void } {
  return term.parser.registerCsiHandler({ prefix: '?', final: 'h' }, (params) => {
    const a = setModeAction(params);
    if (a.kind === 'pass') return false;            // xterm.js applies it
    if (a.kind === 'swallow') return true;
    term.write(`\x1b[?${a.mouse.join(';')}l`);      // after xterm.js applies the whole sequence
    return false;
  });
}

/** Cmd/Ctrl+C: with a selection, copy it (and send nothing to the pane); without one, the key goes to the pane as
 *  ever (Ctrl+C interrupts). Only the key press itself copies. Pure. */
export function copyKeyAction(ev: { type: string; key: string; ctrlKey: boolean; metaKey: boolean; shiftKey?: boolean; altKey?: boolean },
  hasSelection: boolean): 'copy' | 'swallow' | 'pass' {
  const isCopy = (ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === 'c';
  if (!isCopy || !hasSelection) return 'pass';
  return ev.type === 'keydown' ? 'copy' : 'swallow';
}
