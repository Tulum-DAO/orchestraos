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
 *   - it is a regular file (not a symlink), at most MAX_BYTES;
 *   - its text has the banner's stated length AND its collapsed first 120 chars equal the
 *     banner's head.
 * Otherwise (file gone after a reboot, or any check fails) the turn shows the head the banner
 * quoted, without the plumbing.
 */
import { lstatSync, readFileSync } from 'fs';
import { basename, dirname, resolve } from 'path';

const BANNER_RE = /^\[LONG-MSG chip-dodge\] ([\s\S]*)… — FULL TEXT \((\d+) chars\): read (\S+)$/;
const FILE_RE = /^agent-inject-\d+-[0-9a-f]{8}\.md$/;
const MAX_BYTES = 256 * 1024;

export type ReadInject = (path: string) => string | null;

function dodgeDir(): string {
  return resolve(process.env.CHIP_DODGE_TMP_DIR || '/tmp');
}

export const readInjectFile: ReadInject = (path) => {
  try {
    const st = lstatSync(path);
    if (!st.isFile() || st.isSymbolicLink() || st.size > MAX_BYTES) return null;
    return readFileSync(path, 'utf8');
  } catch {
    return null;
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
