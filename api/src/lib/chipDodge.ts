/**
 * The gateway's chip-dodge (scripts/watch_gateway.py chipsafe_text): a message longer than
 * 600 chars or with more than 2 newlines is written to /tmp/agent-inject-<time>-<hex>.md and
 * the pane receives ONE line instead:
 *
 *   [LONG-MSG chip-dodge] <first 120 chars, whitespace collapsed>… — FULL TEXT (<n> chars): read <path>
 *
 * The CLI records that line as the user's turn, so the operator's own message came back to
 * them as plumbing, with a server path and no photos (the [attached:] markers live in the
 * file). Operator report, iPhone build 263, 2026-10-08. This maps the banner back to what the
 * operator typed.
 *
 * The file is read only when ALL of these hold, so a typed look-alike banner cannot pull an
 * arbitrary file (or another message's file) into a transcript:
 *   - the whole turn is exactly one banner;
 *   - the path is <dodge dir>/agent-inject-<digits>-<8 hex>.md, nothing else;
 *   - opened without following a symlink or blocking, it is a regular file owned by this
 *     process's user, at most MAX_BYTES;
 *   - its text has the banner's stated length AND its collapsed first 120 chars equal the
 *     banner's head.
 * Otherwise (file gone after a reboot, or any check fails) the turn shows the head the banner
 * quoted, without the plumbing.
 */
import { closeSync, constants as FS, fstatSync, openSync, readSync } from 'fs';
import { basename, dirname, resolve } from 'path';

const BANNER_RE = /^\[LONG-MSG chip-dodge\] ([\s\S]*)… — FULL TEXT \((\d+) chars\): read (\S+)$/;
const FILE_RE = /^agent-inject-\d+-[0-9a-f]{8}\.md$/;
const MAX_BYTES = 256 * 1024;

export type ReadInject = (path: string) => string | null;

function dodgeDir(): string {
  return resolve(process.env.CHIP_DODGE_TMP_DIR || '/tmp');
}

/**
 * The dodge dir is usually /tmp, which every local user can write. So nothing is decided on
 * the path: open it without following a symlink and without blocking (a FIFO swapped in must
 * not hang the API), then check the OPEN handle (a regular file, ours, not too big) and read a
 * bounded amount. An lstat-then-read would leave a window to swap the file.
 */
export const readInjectFile: ReadInject = (path) => {
  let fd: number | undefined;
  try {
    fd = openSync(path, FS.O_RDONLY | FS.O_NOFOLLOW | FS.O_NONBLOCK);
    const st = fstatSync(fd);
    if (!st.isFile() || st.size > MAX_BYTES) return null;
    if (typeof process.getuid === 'function' && st.uid !== process.getuid()) return null;
    const buf = Buffer.alloc(Math.min(st.size, MAX_BYTES) + 1);
    const n = readSync(fd, buf, 0, buf.length, 0);
    if (n > MAX_BYTES) return null;            // grew after the stat
    return buf.subarray(0, n).toString('utf8');
  } catch {
    return null;
  } finally {
    if (fd !== undefined) { try { closeSync(fd); } catch { /* already closed */ } }
  }
};

const collapse = (s: string) => s.split(/\s+/).filter(Boolean).join(' ');

export function expandChipDodge(text: string, read: ReadInject = readInjectFile): string {
  const m = BANNER_RE.exec(String(text ?? '').trim());
  if (!m) return text;
  const [, head, chars, path] = m;
  const p = resolve(path);
  const fileOk = FILE_RE.test(basename(p)) && dirname(p) === dodgeDir() && p === path;
  if (fileOk) {
    const full = read(p);
    // Python counts and slices by code point; JS .length and .slice by UTF-16 unit. Use code points.
    if (full != null && [...full].length === Number(chars) && [...collapse(full)].slice(0, 120).join('') === head) {
      return full;
    }
  }
  return `${head}…`;
}
