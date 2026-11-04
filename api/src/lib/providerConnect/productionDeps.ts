/**
 * The real side effects behind ConnectManager: tmux, the installers, the signed-in checks. Everything runs as the
 * API's own user (no sudo), with execFile (no shell) except the fixed install / sign-in command lines from specs.ts.
 * Never exercised by the unit tests.
 */
import { execFile, execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { ConnectDeps, PreflightIssue } from './manager.js';
import { MIN_FREE_BYTES, signinScript, type ProviderSpec } from './specs.js';
import { agyTokenValid, grokSignedIn, versionOf } from './parsers.js';

const HOME = process.env.HOME || os.homedir();
const LOCAL_BIN = path.join(HOME, '.local', 'bin');
const AGY_TOKEN = path.join(HOME, '.gemini', 'antigravity-cli', 'antigravity-oauth-token');
const GROK_AUTH = path.join(HOME, '.grok', 'auth.json');
/** The tmux user option that marks a session as this flow's (ConnectManager.sweepOrphans). */
const MARK = '@oos_provider_connect';
const INSTALL_TIMEOUT_MS = 5 * 60_000;

/** The API's PATH with the installers' folders in front: a binary installed a second ago is found. */
const userEnv = (): NodeJS.ProcessEnv => ({
  ...process.env, HOME, PATH: `${LOCAL_BIN}:${path.join(HOME, '.grok', 'bin')}:${process.env.PATH || ''}`,
});

function run(file: string, args: string[], timeout: number, cwd = HOME): Promise<{ code: number; out: string }> {
  return new Promise((resolve) => {
    execFile(file, args, { timeout, cwd, env: userEnv(), maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      const code = err ? (typeof (err as { code?: unknown }).code === 'number' ? (err as { code: number }).code : 1) : 0;
      resolve({ code, out: `${stdout || ''}${stderr ? `\n${stderr}` : ''}` });
    });
  });
}

const executable = (p: string): boolean => { try { fs.accessSync(p, fs.constants.X_OK); return fs.statSync(p).isFile(); } catch { return false; } };

/** Where the binary is: ~/.local/bin first, then the installer's own folder (grok). */
function binPath(spec: ProviderSpec): string | null {
  const local = path.join(LOCAL_BIN, spec.cli);
  if (executable(local)) return local;
  if (spec.altBin && executable(path.join(HOME, spec.altBin))) return path.join(HOME, spec.altBin);
  return null;
}

const onPath = (cmd: string, env: NodeJS.ProcessEnv): boolean => {
  try { execFileSync('which', [cmd], { env, stdio: 'ignore', timeout: 5000 }); return true; } catch { return false; }
};

function tmux(args: string[]): Promise<{ ok: boolean; out: string }> {
  return new Promise((resolve) => {
    execFile('tmux', args, { timeout: 10_000, maxBuffer: 4 * 1024 * 1024 }, (err, stdout) => resolve({ ok: !err, out: String(stdout || '') }));
  });
}
const shq = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

/** Run an install command in its OWN process group, so a timeout kills curl / npm / the installer's children too,
 *  not only the bash in front of them. Output is kept (last 64 KB).
 *
 *  Resolves on 'close' (every pipe drained, so npm's last lines, e.g. its EACCES, are in the log), but at most
 *  `closeCapMs` after the bash has exited: a helper the installer leaves running in the background may hold the pipes
 *  open, and must not make a finished install look hung until the timeout. */
export function runInstall(command: string, opts: { timeoutMs?: number; closeCapMs?: number; cwd?: string } = {}): Promise<{ ok: boolean; log: string }> {
  const timeoutMs = opts.timeoutMs ?? INSTALL_TIMEOUT_MS;
  const closeCapMs = opts.closeCapMs ?? 2000;
  return new Promise((resolve) => {
    let log = '';
    let done = false;
    let exitCode: number | null = null;
    let timedOut = false;
    let capTimer: NodeJS.Timeout | null = null;
    const keep = (b: Buffer) => { log = (log + b.toString()).slice(-64 * 1024); };
    const child = spawn('bash', ['-lc', command], { cwd: opts.cwd ?? HOME, env: userEnv(), detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout?.on('data', keep);
    child.stderr?.on('data', keep);
    const killGroup = (sig: NodeJS.Signals) => { try { if (child.pid) process.kill(-child.pid, sig); } catch { /* gone */ } };
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (capTimer) clearTimeout(capTimer);
      const note = timedOut ? `\n(install timed out after ${Math.round(timeoutMs / 1000)} s)` : '';
      resolve({ ok: exitCode === 0 && !timedOut, log: log + note });
    };
    const timer = setTimeout(() => {
      timedOut = true;
      killGroup('SIGTERM');
      setTimeout(() => killGroup('SIGKILL'), 5000).unref();
    }, timeoutMs);
    child.on('error', (err) => { log += `\n${String(err)}`; finish(); });
    child.on('exit', (code) => {
      exitCode = code;
      capTimer = setTimeout(finish, closeCapMs);
    });
    child.on('close', (code) => { if (exitCode === null) exitCode = code; finish(); });
  });
}

/** Grok sign-in check, cheap first: `grok models` asks xAI over the network (and grok may bring up its own helper
 *  processes), so it runs only once ~/.grok/auth.json exists and is not empty. A YES is kept until that file changes;
 *  a no is never kept (a token being written or refreshed may answer no once), so the next poll asks again. */
let grokConfirmed: { mtimeMs: number } | null = null;
async function grokSignedInCheap(bin: string): Promise<boolean> {
  let st: fs.Stats;
  try { st = fs.statSync(GROK_AUTH); } catch { grokConfirmed = null; return false; }
  if (st.size === 0) return false;
  if (grokConfirmed && grokConfirmed.mtimeMs === st.mtimeMs) return true;
  const ok = grokSignedIn((await run(bin, ['models'], 20_000)).out);
  grokConfirmed = ok ? { mtimeMs: st.mtimeMs } : null;
  return ok;
}

export function productionConnectDeps(opts: { refreshRuntimes: () => void }): ConnectDeps {
  return {
    now: () => Date.now(),
    tmux: {
      // `=name` is an exact match: never another session that merely starts with the same letters.
      has: async (s) => (await tmux(['has-session', '-t', `=${s}`])).ok,
      start: async (s, cwd, script) => {
        try { fs.mkdirSync(cwd, { recursive: true }); } catch { /* tmux reports it */ }
        return (await tmux(['new-session', '-d', '-s', s, '-x', '200', '-y', '50', '-c', cwd, `bash -lc ${shq(script)}`])).ok;
      },
      capture: async (s) => (await tmux(['capture-pane', '-p', '-J', '-S', '-200', '-t', `=${s}:`])).out,
      // -l: the text is typed as is, no key names; `--`: a code starting with '-' is still text.
      sendLiteral: async (s, text) => { await tmux(['send-keys', '-t', `=${s}:`, '-l', '--', text]); },
      sendKey: async (s, key) => { await tmux(['send-keys', '-t', `=${s}:`, key]); },
      kill: async (s) => { await tmux(['kill-session', '-t', `=${s}`]); },
      // Options take the `=name:` form (a bare `=name` is "no such session" for set/show-options on tmux 3.4).
      mark: async (s) => { await tmux(['set-option', '-t', `=${s}:`, MARK, '1']); },
      isOurs: async (s) => (await tmux(['show-options', '-v', '-t', `=${s}:`, MARK])).out.trim() === '1',
    },
    signedIn: async (spec) => {
      if (spec.id === 'gemini') {
        try { return agyTokenValid(JSON.parse(fs.readFileSync(AGY_TOKEN, 'utf-8')), Date.now()); } catch { return false; }
      }
      const bin = binPath(spec);
      if (!bin) return false;
      if (spec.id === 'codex') {
        const r = await run(bin, ['login', 'status'], 15_000);
        return r.code === 0 && !/not logged in/i.test(r.out);
      }
      return grokSignedInCheap(bin);
    },
    binInstalled: (spec) => binPath(spec) !== null,
    // What the runtime probe sees: the API's own PATH, not ours.
    visible: (spec) => onPath(spec.cli, process.env),
    preflight: async (spec, { install }): Promise<PreflightIssue | null> => {
      const missing = spec.tools.filter((t) => !onPath(t, userEnv()));
      if (missing.length) return { code: 'preflight_tools', missing };
      try { fs.mkdirSync(LOCAL_BIN, { recursive: true }); fs.accessSync(LOCAL_BIN, fs.constants.W_OK); } catch { return { code: 'preflight_bin_dir' }; }
      if (!install) return null;
      try {
        const st = fs.statfsSync(HOME);
        const free = Number(st.bavail) * Number(st.bsize);
        if (free < MIN_FREE_BYTES) return { code: 'preflight_disk', freeBytes: free };
      } catch { /* can't tell: let the install say */ }
      const r = await run('curl', ['-fsS', '-o', '/dev/null', '--max-time', '8', spec.installUrl], 12_000);
      if (r.code !== 0) return { code: 'preflight_network', host: new URL(spec.installUrl).host };
      return null;
    },
    install: (spec) => runInstall(spec.install),
    ensureLink: (spec) => {
      if (!spec.altBin) return;
      const link = path.join(LOCAL_BIN, spec.cli);
      const target = path.join(HOME, spec.altBin);
      if (!fs.existsSync(link) && executable(target)) fs.symlinkSync(target, link);
    },
    version: async (spec) => {
      const bin = binPath(spec);
      if (!bin) return null;
      const r = await run(bin, ['--version'], 15_000);
      return r.code === 0 ? versionOf(r.out) : null;
    },
    signinCwd: () => path.join(HOME, '.cache', 'orchestraos-signin'),
    signinScript,
    refreshRuntimes: opts.refreshRuntimes,
  };
}
