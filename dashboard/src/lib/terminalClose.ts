/**
 * terminalClose.ts — when the Dev terminal says its connection ended ("[Connection error]", "[Disconnected]").
 *
 * Its socket also closes when the close is expected: Back to chat (the page switches the seat back, and the seat's
 * terminal program ends before the page has moved on), or the terminal left from inside it (the page follows a moment
 * later). Said at once, the words flashed in the terminal on its way out. So they wait a short grace: if in that time
 * the close turns out to be expected (the page says so, or the terminal goes away), nothing is said; a real drop is
 * still said, once that grace has passed.
 */
export const CLOSE_GRACE_MS = 3000;

export interface CloseNotice {
  /** The socket reported an error. */
  error: () => void;
  /** The socket closed. */
  closed: () => void;
  /** The page expects the close (it asked for it): nothing pending or to come is said. */
  expect: () => void;
  /** The terminal is going away: nothing more is said. */
  dispose: () => void;
}

export function closeNotice(write: (text: string) => void, opts: {
  graceMs?: number; schedule?: (f: () => void, ms: number) => unknown; cancel?: (t: unknown) => void;
} = {}): CloseNotice {
  const grace = opts.graceMs ?? CLOSE_GRACE_MS;
  const schedule = opts.schedule ?? ((f, ms) => setTimeout(f, ms));
  const cancel = opts.cancel ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  let quiet = false;
  let timer: unknown = null;
  const pending: string[] = [];
  const later = (line: string) => {
    if (quiet || pending.includes(line)) return;
    pending.push(line);
    if (timer === null) {
      timer = schedule(() => {
        timer = null;
        if (!quiet) for (const l of pending) write(l);
        pending.length = 0;
      }, grace);
    }
  };
  const silence = () => {
    quiet = true;
    pending.length = 0;
    if (timer !== null) { cancel(timer); timer = null; }
  };
  return {
    error: () => later('\r\n\x1b[31m[Connection error]\x1b[0m\r\n'),
    closed: () => later('\r\n\x1b[31m[Disconnected]\x1b[0m\r\n'),
    expect: silence,
    dispose: silence,
  };
}
