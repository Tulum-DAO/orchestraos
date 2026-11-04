/**
 * Read a provider CLI's sign-in screen out of a tmux pane (`capture-pane -p`, no escapes): the link, the code, how
 * long the code lasts, and the known failures. Pure, so each parser is tested against the real screens captured on the
 * test install (fixtures.ts).
 */

/** What a sign-in screen says. Only the fields the CLI actually printed are set. */
export interface SignInScreen {
  url: string | null;
  code: string | null;
  /** The lifetime the CLI printed ("expires in 15 minutes"), in minutes. Null when it prints none. */
  expiresInMin: number | null;
  /** Codex: the account has device code login switched off. */
  deviceAuthDisabled?: boolean;
}

/** The line the sign-in command prints when the CLI ends: `__OOS_EXIT=<status>`. */
export const EXIT_MARKER = '__OOS_EXIT=';

/** The CLI's exit status, once it has ended; null while it runs. The last marker wins. */
export function exitCode(pane: string): number | null {
  const all = [...(pane || '').matchAll(/__OOS_EXIT=(\d+)/g)];
  return all.length ? Number(all[all.length - 1][1]) : null;
}

const lines = (pane: string): string[] => (pane || '').split('\n').map((l) => l.replace(/\s+$/, ''));

/** The first non-empty line after the line matching `label`, trimmed. */
function lineAfter(ls: string[], label: RegExp): string | null {
  const i = ls.findIndex((l) => label.test(l));
  if (i < 0) return null;
  for (let j = i + 1; j < ls.length; j += 1) if (ls[j].trim()) return ls[j].trim();
  return null;
}

// Codex prints e.g. QWER-7TY2U (4-5); grok AB12-CDEF (4-4). A little slack either side, never free text.
const DEVICE_CODE = /^[A-Z0-9]{4,6}-[A-Z0-9]{4,6}$/;

/** codex login --device-auth (codex-cli 0.166.0). The URL is fixed and carries no code. */
export function parseCodexPane(pane: string): SignInScreen {
  const ls = lines(pane);
  const urlLine = ls.find((l) => /https:\/\/auth\.openai\.com\/\S*device/.test(l));
  const url = urlLine ? /https:\/\/\S+/.exec(urlLine)![0] : null;
  const codeLine = lineAfter(ls, /Enter this one-time code/i);
  const code = codeLine && DEVICE_CODE.test(codeLine) ? codeLine : null;
  const exp = /expires in (\d+) minutes?/i.exec(pane || '');
  // UNVERIFIED wording: the failure was never captured. Codex names the setting ("device code login" / "device
  // code authorization") when it refuses; match that, not any error.
  const deviceAuthDisabled = /device[- ]code (login|auth\w*)[^\n]*(not (enabled|allowed)|disabled|turned off)|enable device[- ]code/i.test(pane || '');
  return { url, code, expiresInMin: exp ? Number(exp[1]) : null, deviceAuthDisabled };
}

/** grok login --device-auth (grok 1.0.70). The URL carries the code as user_code; no lifetime is printed. */
export function parseGrokPane(pane: string): SignInScreen {
  const ls = lines(pane);
  const urlLine = ls.find((l) => /https:\/\/\S*x\.ai\/\S*device/.test(l));
  const url = urlLine ? /https:\/\/\S+/.exec(urlLine)![0] : null;
  const codeLine = lineAfter(ls, /Confirm this code/i);
  let code = codeLine && DEVICE_CODE.test(codeLine) ? codeLine : null;
  if (!code && url) {
    const q = /[?&]user_code=([A-Z0-9-]+)/.exec(url);
    if (q && DEVICE_CODE.test(q[1])) code = q[1];
  }
  return { url, code, expiresInMin: null };
}

export type AgyScreen =
  | { kind: 'menu'; googleSelected: boolean }
  | { kind: 'url'; url: string }
  | { kind: 'unknown' };

const RULER = /^\s*─{10,}\s*$/;

/** The agy TUI hard-wraps its OAuth URL across several lines between two `─` rulers, each line with a one-space
 *  margin. `capture-pane -J` cannot rejoin those (they are the CLI's own breaks), so rebuild it: strip the margin and
 *  trailing blanks of every line between the rulers and concatenate. Null when the pair of rulers or the URL is not
 *  on screen yet. */
export function agyUrl(pane: string): string | null {
  const ls = lines(pane);
  // The LAST ruler pair: a re-drawn screen leaves the earlier one in the scrollback.
  for (let end = ls.length - 1; end > 0; end -= 1) {
    if (!RULER.test(ls[end])) continue;
    let start = end - 1;
    while (start >= 0 && !RULER.test(ls[start])) start -= 1;
    if (start < 0) return null;
    const joined = ls.slice(start + 1, end).map((l) => l.trim()).join('');
    return /^https:\/\/accounts\.google\.com\/\S+$/.test(joined) ? joined : null;
  }
  return null;
}

/** agy (1.5.0) with no saved sign-in: first a login-method menu, then the URL and a paste prompt. */
export function parseAgyPane(pane: string): AgyScreen {
  const url = agyUrl(pane);
  if (url) return { kind: 'url', url };
  if (/Select login method/i.test(pane || '')) {
    return { kind: 'menu', googleSelected: /^\s*>\s*1\.\s*Google OAuth/m.test(pane || '') };
  }
  return { kind: 'unknown' };
}

/** The lines under agy's paste prompt (never the URL block above it). */
function belowPrompt(pane: string): string[] {
  const parts = (pane || '').split(/paste it below:?/i);
  return parts.length > 1 ? lines(parts.slice(1).join('\n')) : [];
}

/** Log noise agy prints that is not an answer to the code: Go glog lines ("ERROR: logging before google.Init: I1104
 *  …", "E1104 07:50:42.4 …"). */
const LOG_LINE = /logging before google\.Init|^\s*[IWEF]\d{4} \d{2}:\d{2}:\d{2}/;

/** A refusal, worded conservatively: it must name the code / grant AND say it was bad, or be OAuth's own
 *  invalid_grant. A bare "error" is not enough. UNVERIFIED wording (agy's refusal was never captured). */
const REFUSAL = /\binvalid_grant\b|\b(invalid|incorrect|wrong|expired|malformed|bad)\b[^\n]*\b(code|grant|authori[sz]ation)\b|\b(code|authori[sz]ation)\b[^\n]*\b(invalid|incorrect|rejected|expired|was not accepted|failed)\b/i;

/** Did agy refuse the code just pasted? Looks only at lines under the paste prompt that were NOT on the screen when the
 *  code was sent (`before`), counted line by line so a second identical refusal still counts. The echoed code and log
 *  noise are skipped. */
export function agyCodeRejected(before: string, after: string, code: string): boolean {
  const seen = new Map<string, number>();
  for (const l of belowPrompt(before)) seen.set(l, (seen.get(l) ?? 0) + 1);
  const fresh: string[] = [];
  for (const l of belowPrompt(after)) {
    const n = seen.get(l) ?? 0;
    if (n > 0) seen.set(l, n - 1); else fresh.push(l);
  }
  return fresh.some((l) => l.trim() && !(code && l.includes(code)) && !LOG_LINE.test(l) && REFUSAL.test(l));
}

/** A Google authorization code as the antigravity.google callback page shows it (e.g. `4/0AVG7fi…`). Strict: it goes
 *  into a terminal, so nothing outside the URL-safe set, no spaces, no control characters. Returns the trimmed code or
 *  null. */
export function validAgyCode(raw: unknown): string | null {
  const code = String(raw ?? '').trim();
  return /^[A-Za-z0-9/_.~-]{10,512}$/.test(code) ? code : null;
}

/** agy's token file (the runtime probe's file-json-expiry check): signed in while token.expiry is in the future. */
export function agyTokenValid(doc: unknown, now: number): boolean {
  const expiry = (doc as { token?: { expiry?: unknown } } | null)?.token?.expiry;
  if (typeof expiry !== 'string') return false;
  const ms = Date.parse(expiry);
  return !Number.isNaN(ms) && ms > now;
}

/** "You are logged in" from `grok models` (the runtime probe's own success pattern). */
export function grokSignedIn(stdout: string): boolean {
  return /^You are logged in\b/m.test(stdout || '');
}

/** The first `x.y.z` in a `--version` line ("codex-cli 0.166.0", "grok 1.0.70 (f50904eaee6c)", "1.5.0"). */
export function versionOf(stdout: string): string | null {
  const m = /\b(\d+\.\d+\.\d+)\b/.exec(stdout || '');
  return m ? m[1] : null;
}

/** npm refusing to write a root-owned global folder: the --prefix was lost. */
export function npmPermissionError(log: string): boolean {
  return /EACCES|permission denied/i.test(log || '');
}
