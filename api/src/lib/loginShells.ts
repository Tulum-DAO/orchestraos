/**
 * The login shells the API itself started (POST /api/agents/login-shell), kept in
 * <data dir>/state/login-shells.json:
 *
 *   { "shells": [ { "session", "provider", "created_at", "expires_at", "created_by" } ] }
 *
 * The web terminal attaches only to a registered seat's session (dashboard-proxy.js). A login
 * shell has no registry row, so without this list "Open a terminal here" was refused. The proxy
 * reads this file (dashboard-login-shells.js) and lets an upgrade through for a session listed
 * here and not expired. It never trusts a name pattern: `login-x` that the API did not start is
 * refused. Only the login-shell route writes the file; entries leave when their tmux session is
 * gone (the periodic sweep and the sweep at API start) or when they expire.
 *
 * TTL 30 minutes: long enough to install a CLI and finish a device-code sign-in (the codes
 * themselves expire in about 15), short enough that a shell someone walked away from is not
 * attachable for the rest of the day. Expiry only gates new attaches; an open terminal stays,
 * and Connect again re-lists the same session with a fresh TTL.
 */
import fs from 'fs';
import path from 'path';

export const LOGIN_SHELL_TTL_MS = 30 * 60 * 1000;
/** How often the API checks that each listed shell's tmux session still exists. */
export const LOGIN_SHELL_SWEEP_MS = 30 * 1000;

export interface LoginShellEntry {
  session: string;
  provider: string | null;
  created_at: string;
  expires_at: string;
  created_by: string | null;
}

export function loginShellsFile(orchestraDir: string): string {
  return path.join(orchestraDir, 'state', 'login-shells.json');
}

function validEntry(e: unknown): e is LoginShellEntry {
  const r = e as Record<string, unknown> | null;
  return !!r && typeof r.session === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(r.session)
    && typeof r.expires_at === 'string' && !Number.isNaN(Date.parse(r.expires_at));
}

/** Every well-formed entry. An absent or unreadable file is an empty list. */
export function readLoginShells(orchestraDir: string): LoginShellEntry[] {
  try {
    const doc = JSON.parse(fs.readFileSync(loginShellsFile(orchestraDir), 'utf-8'));
    return Array.isArray(doc?.shells) ? doc.shells.filter(validEntry) : [];
  } catch { return []; }
}

function writeAll(orchestraDir: string, shells: LoginShellEntry[]): void {
  const f = loginShellsFile(orchestraDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  // Renamed into place: the proxy reads the file on every terminal upgrade and must never see half of it.
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ shells }, null, 2));
  fs.renameSync(tmp, f);
}

/** List (or re-list, with a fresh TTL) a shell the login-shell route just started or reused. */
export function recordLoginShell(orchestraDir: string, entry: { session: string; provider: string | null; createdBy: string | null },
                                 now = Date.now()): LoginShellEntry {
  const row: LoginShellEntry = {
    session: entry.session,
    provider: entry.provider,
    created_at: new Date(now).toISOString(),
    expires_at: new Date(now + LOGIN_SHELL_TTL_MS).toISOString(),
    created_by: entry.createdBy,
  };
  const rest = readLoginShells(orchestraDir).filter((e) => e.session !== entry.session);
  writeAll(orchestraDir, [...rest, row]);
  return row;
}

/** Drop every entry that has expired or whose tmux session is gone. Writes only when something
 *  left, so an idle sweep never touches the file the proxy caches by mtime. Returns what was dropped. */
export function sweepLoginShells(orchestraDir: string, sessionExists: (session: string) => boolean,
                                 now = Date.now()): string[] {
  const all = readLoginShells(orchestraDir);
  const keep: LoginShellEntry[] = [];
  const dropped: string[] = [];
  for (const e of all) {
    if (Date.parse(e.expires_at) > now && sessionExists(e.session)) keep.push(e);
    else dropped.push(e.session);
  }
  if (dropped.length) {
    try { writeAll(orchestraDir, keep); } catch (err) { console.warn(`[login-shell] sweep failed: ${err}`); return []; }
  }
  return dropped;
}

/** Sweep once now (API start) and then every LOGIN_SHELL_SWEEP_MS, never holding the process open. */
export function startLoginShellSweeper(orchestraDir: string, sessionExists: (session: string) => boolean): NodeJS.Timeout {
  sweepLoginShells(orchestraDir, sessionExists);
  const t = setInterval(() => sweepLoginShells(orchestraDir, sessionExists), LOGIN_SHELL_SWEEP_MS);
  t.unref();
  return t;
}
