/**
 * special-keys — the ONE map from harness key names to tmux send-keys tokens.
 *
 * RED ALERT (docs/RED_ALERT.md): on 2026-09-17 the bottom-bar "^Z" button
 * sent C-z to the gm pane and suspended the CLI (process STAT T, alive, never running).
 * ctrl-z is FORBIDDEN here so no client can do that again; ctrl-u (clear input) takes
 * its place on the bar. Keep every key policy in this file, not in the route.
 */

export const FORBIDDEN_KEYS: readonly string[] = ['ctrl-z', 'c-z', '^z', ''];

const KEY_MAP: Record<string, string[]> = {
  'escape': ['Escape'],
  'esc': ['Escape'],
  'enter': ['Enter'],
  'tab': ['Tab'],
  'backspace': ['BSpace'],
  'up': ['Up'],
  'down': ['Down'],
  'left': ['Left'],
  'right': ['Right'],
  'ctrl-c': ['C-c'],
  'ctrl-d': ['C-d'],
  'ctrl-u': ['C-u'],
  'ctrl-o': ['C-o'],
  'ctrl-k': ['C-k'],
  'shift-tab': ['BTab'],
  'ctrl-l': ['C-l'],
  'ctrl-r': ['C-r'],
  'ctrl-a': ['C-a'],
  'ctrl-e': ['C-e'],
  'space': ['Space'],
  '1': ['1'], '2': ['2'], '3': ['3'], '4': ['4'], '5': ['5'],
  '6': ['6'], '7': ['7'], '8': ['8'], '9': ['9'], '0': ['0'],
};

export type KeyResolution = { ok: true; tmux: string[] } | { ok: false; reason: string };

export function resolveSpecialKey(key: string): KeyResolution {
  const k = String(key);
  if (FORBIDDEN_KEYS.includes(k.toLowerCase())) {
    return {
      ok: false,
      reason: 'ctrl-z would suspend the agent CLI (RED ALERT); refused. Use ctrl-u to clear input.',
    };
  }
  return { ok: true, tmux: KEY_MAP[k.toLowerCase()] || [k] };
}
