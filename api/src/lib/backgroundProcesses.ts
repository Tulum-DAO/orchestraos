/**
 * backgroundProcesses.ts — Settings › Agents "Background processes": programs agents started and left running (a dev
 * server), found from /proc, and a checked Stop.
 *
 * A process is listed (a candidate) only when ALL of these hold:
 *   1. it belongs to the install user (the API's own uid) and LISTENs on a TCP port (/proc/net/tcp, tcp6);
 *   2. it is not the API itself nor any ancestor of the API;
 *   3. its own command is not on the denylist: the install's services (anything run from the code root's scripts/,
 *      services/, api/dist/, api/src/ or dashboard-proxy.js, the supervisor, the gateway, Arturo, orchestra_cli,
 *      the lineage and identity daemons), the agents' runtimes (claude, codex, gemini, grok), MCP servers, and the
 *      machine's own plumbing (sshd, tailscale, tmux, systemd, dbus, ssh/gpg agents, editor remote servers, docker);
 *   4. it does not listen on one of the install's own ports (API, dashboard, gateway, Arturo);
 *   5. it is not in a systemd service's cgroup (an installed service, the user's or the system's);
 *   6. walking its parent chain upward, an AGENT MARKER comes before anything that disqualifies it. Markers: an agent
 *      runtime (claude, codex, gemini, grok), a seat supervisor (`seat_supervisor.py run <seat>`), or a seat's tmux
 *      pane. Disqualifying: an install service or MCP server (a child of those belongs to them), sshd or the tmux server
 *      reached with no marker (a person's own terminal). A chain that reaches init or `systemd --user` with no marker
 *      and nothing disqualifying is an orphan (`nohup … &` from an agent's shell): it is listed ONLY when its own
 *      environment names a registered seat (ORCHESTRA_AGENT_ID, which spawn-agent.sh and the supervisor set). An
 *      orphan without one (a person's `nohup`, an `ssh -fN -L` tunnel) is never listed.
 *
 * Attribution (which agent): the first of a seat supervisor's argument, a seat pane's session, or ORCHESTRA_AGENT_ID
 * in the environment of a runtime on the chain (or the process's own, for an orphan); only an id that is a registered
 * seat counts. Anything else is "No agent".
 *
 * Command lines can carry secrets (`--token=…`, `postgres://user:pass@…`, `API_KEY=…`). The raw line never leaves this  // pragma: allowlist secret
 * module: a candidate carries its program's name (argv[0]'s file name, nothing more), for an admin a guess at its
 * script (programDetail) and the masked command (maskArgv), and an HMAC of the full argv under a key made at start-up
 * (commandHash). The route shows members the program's name and ports only.
 *
 * Stop re-reads the list at the moment of the request and acts only on a pid that is still a candidate with the same
 * start time and the same command hash; SIGTERM, then SIGKILL after 5 s only if that same process is still alive.
 */
import fs from 'fs';
import path from 'path';
import { execFile, execFileSync } from 'child_process';
import { createHmac, randomBytes } from 'crypto';
import { promisify } from 'util';

export interface ProcInfo {
  pid: number; ppid: number; uid: number;
  /** Start time in clock ticks since boot (/proc/<pid>/stat field 22): with the pid, what makes a process this one. */
  startTicks: number;
  /** Process state letter (/proc/<pid>/stat field 3): 'Z' is a zombie, already dead. */
  state: string;
  argv: string[];
  cwd: string | null;
  /** The last line of /proc/<pid>/cgroup, e.g. "0::/user.slice/user-1000.slice/session-5.scope". */
  cgroup: string;
  /** ORCHESTRA_AGENT_ID from its environment, when readable. */
  agentEnv: string | null;
}

export interface ProcSource {
  uid: number;
  selfPid: number;
  bootTimeSec: number;
  clkTck: number;
  /** Clock ticks since boot now (/proc/uptime): when a stop begins. */
  nowTicks(): number;
  /** pid -> the TCP ports it listens on. */
  listeners(): Map<number, number[]>;
  proc(pid: number): ProcInfo | null;
  /** tmux pane pid -> its session name. */
  tmuxPanes(): Promise<Map<number, string>>;
}

export interface Seat { id: string; tmuxSession: string }

export interface Candidate {
  pid: number; startTicks: number; startedAt: string;
  /** What anyone may see: the program's file name only ("python3", "node"). */
  program: string;
  /** Admins only: the program with its script or module when one can be told safely (programDetail). */
  detail: string;
  /** The command line with secrets masked (maskArgv): admins only. */
  command: string;
  /** HMAC of the full argv (commandHash): what Stop matches on. Admins only. */
  commandHash: string;
  ports: number[];
  agent: string | null;
  /** How the agent was found: 'supervisor', 'pane', 'runtime' (its env), 'env' (the orphan's own env), or null. */
  via: 'supervisor' | 'pane' | 'runtime' | 'env' | null;
}

export interface ListOptions { codeRoot: string; installPorts: readonly number[] }

const RUNTIMES = new Set(['claude', 'codex', 'gemini', 'grok']);
const MAX_HOPS = 64;

const base = (s: string) => s.split('/').pop() ?? s;

/** Is this an agent runtime: its program, or a node/bun script whose file is the runtime's CLI. */
export function isRuntime(argv: string[]): boolean {
  if (!argv.length) return false;
  const prog = base(argv[0]);
  if (RUNTIMES.has(prog)) return true;
  if (/^(node|bun|deno)$/.test(prog) && argv[1]) {
    const script = argv[1];
    return RUNTIMES.has(base(script)) || /@anthropic-ai\/claude-code|@openai\/codex|@google\/gemini-cli|\/grok-cli\b|\/(claude|codex|gemini|grok)\/(bin|dist|cli)/.test(script);
  }
  return false;
}

/** `seat_supervisor.py run <seat>` -> the seat. */
export function supervisorSeat(argv: string[]): string | null {
  const i = argv.findIndex((a) => base(a) === 'seat_supervisor.py');
  if (i < 0) return null;
  return argv[i + 1] === 'run' && argv[i + 2] ? argv[i + 2] : null;
}

const INSTALL_NAMES = /(^|\/)(watch_gateway\.py|arturo-proxy\.py|dashboard-proxy\.js|seat_supervisor\.py)$/;
const INSTALL_MODULES = /^(orchestra_cli|lineage_daemon(\..*)?|scripts\.identity_store(\..*)?|scripts\.[\w.]+)$/;

/** One of the install's own services: run from the code root (scripts/, services/, api/dist/, api/src/,
 *  dashboard-proxy.js), a known service file anywhere, or one of its python modules (`-m orchestra_cli`). */
export function isInstallService(p: Pick<ProcInfo, 'argv' | 'cwd'>, codeRoot: string): boolean {
  const root = codeRoot.replace(/\/+$/, '');
  const inside = ['scripts', 'services', path.join('api', 'dist'), path.join('api', 'src'), 'dashboard-proxy.js'].map((d) => path.join(root, d));
  for (let i = 0; i < p.argv.length; i++) {
    const a = p.argv[i];
    if (!a || a.startsWith('-')) {
      if (a === '-m' && p.argv[i + 1] && INSTALL_MODULES.test(p.argv[i + 1])) return true;
      continue;
    }
    if (INSTALL_NAMES.test(a)) return true;
    if (i === 0 && !a.includes('/')) continue;   // a bare program name ("python3") says nothing about where it runs from
    const abs = path.isAbsolute(a) ? path.normalize(a) : p.cwd ? path.resolve(p.cwd, a) : null;
    if (abs && inside.some((d) => abs === d || abs.startsWith(d + '/'))) return true;
  }
  return false;
}

/** MCP servers: an agent's tools. Their children (a browser with a debugging port) belong to the tool, not to a stop. */
export function isMcp(argv: string[]): boolean {
  return argv.some((a) => /(^|[/@_-])mcp([/_-]|$)|modelcontextprotocol|mcp-server|server-mcp/i.test(a));
}

/** The machine's own plumbing, never an agent's leftover. */
const PLUMBING = /^(sshd|ssh|autossh|ssh-agent|gpg-agent|tailscaled?|tmux(:.*)?|systemd(-.*)?|\(sd-pam\)|dbus-daemon|dbus-broker|init|cron|containerd(-shim.*)?|dockerd|docker-proxy|code-server|pipewire.*|wireplumber)$/;
const PLUMBING_PATH = /\/\.(vscode-server|vscode-server-insiders|cursor-server|windsurf-server)\//;

export function isPlumbing(argv: string[]): boolean {
  if (!argv.length) return false;
  const prog = base(argv[0]).replace(/:$/, '');
  return PLUMBING.test(prog) || /^sshd:/.test(argv[0]) || argv.some((a) => PLUMBING_PATH.test(a));
}

const isTmuxServer = (argv: string[]) => argv.length > 0 && /^tmux(:.*)?$/.test(base(argv[0]));
const isSshd = (argv: string[]) => argv.length > 0 && (/^sshd/.test(base(argv[0])) || /^sshd:/.test(argv[0]));
const isUserManager = (argv: string[]) => argv.length > 0 && base(argv[0]) === 'systemd';
const inServiceCgroup = (cg: string) => /\.service$/.test(cg.trim());

/** Why a process is not a candidate, or null when it is one (with its agent). Exported for the tests. */
export interface ClassifyCtx { seats: readonly Seat[]; opts: ListOptions; apiChain: ReadonlySet<number>; panes: ReadonlyMap<number, string> }

export function classify(pid: number, src: ProcSource, ctx: ClassifyCtx):
    { ok: false; why: string } | { ok: true; agent: string | null; via: Candidate['via']; info: ProcInfo } {
  const info = src.proc(pid);
  if (!info) return { ok: false, why: 'gone' };
  if (info.uid !== src.uid) return { ok: false, why: 'another user' };
  if (info.state === 'Z') return { ok: false, why: 'zombie' };
  const { seats, opts, apiChain, panes } = ctx;
  if (apiChain.has(pid)) return { ok: false, why: 'the API or its parent' };
  if (isInstallService(info, opts.codeRoot)) return { ok: false, why: 'install service' };
  if (isRuntime(info.argv)) return { ok: false, why: 'agent runtime' };
  if (isMcp(info.argv)) return { ok: false, why: 'MCP server' };
  if (isPlumbing(info.argv)) return { ok: false, why: 'system' };
  if (inServiceCgroup(info.cgroup)) return { ok: false, why: 'systemd service' };

  const known = new Set(seats.map((s) => s.id));
  const bySession = new Map(seats.map((s) => [s.tmuxSession, s.id]));
  const seatOf = (id: string | null) => (id && known.has(id) ? id : null);

  let marker = false;
  let agent: string | null = null;
  let via: Candidate['via'] = null;
  const seen = new Set<number>([pid]);
  let cur = info.ppid;
  for (let hop = 0; hop < MAX_HOPS && cur > 1 && !seen.has(cur); hop++) {
    seen.add(cur);
    const p = src.proc(cur);
    if (!p) break;
    const sup = supervisorSeat(p.argv);
    const pane = panes.get(cur);
    const paneSeat = pane !== undefined ? bySession.get(pane) ?? null : null;
    if (sup !== null) {
      marker = true;
      if (!agent && seatOf(sup)) { agent = sup; via = 'supervisor'; }
    } else if (paneSeat) {
      marker = true;
      if (!agent) { agent = paneSeat; via = 'pane'; }
    } else if (isRuntime(p.argv)) {
      marker = true;
      if (!agent && seatOf(p.agentEnv)) { agent = p.agentEnv; via = 'runtime'; }
    } else if (!marker) {
      if (isInstallService(p, opts.codeRoot)) return { ok: false, why: 'child of an install service' };
      if (isMcp(p.argv)) return { ok: false, why: 'child of an MCP server' };
      if (isSshd(p.argv)) return { ok: false, why: "a person's terminal (ssh)" };
      if (isTmuxServer(p.argv)) return { ok: false, why: "a person's terminal (tmux)" };
      if (pane !== undefined) return { ok: false, why: "a person's terminal (not an agent's pane)" };
    }
    if (agent) break;
    if (isUserManager(p.argv)) break;
    cur = p.ppid;
  }
  if (!marker) {
    // An orphan: listed only when its own environment names a registered seat.
    if (!seatOf(info.agentEnv)) return { ok: false, why: 'orphan with no agent' };
    agent = info.agentEnv; via = 'env';
  }
  return { ok: true, agent, via, info };
}

function startedAtIso(src: ProcSource, ticks: number): string {
  return new Date(Math.round((src.bootTimeSec + ticks / src.clkTck) * 1000)).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** The pids of the API and every ancestor of it (never listed). */
function apiChainOf(src: ProcSource): Set<number> {
  const out = new Set<number>();
  let cur = src.selfPid;
  for (let i = 0; i < MAX_HOPS && cur > 0 && !out.has(cur); i++) {
    out.add(cur);
    const p = src.proc(cur);
    if (!p) break;
    cur = p.ppid;
  }
  return out;
}

// ── Secrets in command lines ─────────────────────────────────────────────────────────────────────────────────────

const MASK = '***';
const SECRET_WORD = '(token|key|secret|password|passwd|pass|pwd|auth|credential|credentials|apikey)';
const SECRET_FLAG = new RegExp(`^--?[\\w.-]*${SECRET_WORD}[\\w.-]*$`, 'i');
const SECRET_FLAG_EQ = new RegExp(`^(--?[\\w.-]*${SECRET_WORD}[\\w.-]*)=.*$`, 'i');
const ENV_ASSIGN = /^([A-Za-z_][A-Za-z0-9_]*)=.+$/;
// Credentials in a URL's authority: from // to the last @ before the first / after it (redis://:pw@h, user:pa@ss@host).
const URL_CREDS = /(\/\/)[^/\s]*@/g;
// A query parameter whose name says it is a secret.
const QUERY_SECRET = /([?&;][^=&;#\s]*(?:token|key|secret|pass|auth|sig)[^=&;#\s]*=)[^&;#\s]*/gi;
// An Authorization header's whole value; a Bearer / Basic credential anywhere.
const AUTH_HEADER = /(authorization:\s*).+/i;
const AUTH_SCHEME = /\b(Bearer|Basic)\s+\S+/gi;
const HEX_LONG = /^[A-Fa-f0-9]{24,}$/;
const B64_LONG = /^[A-Za-z0-9+_-][A-Za-z0-9+/_-]{31,}={0,2}$/;
const looksLikeSecret = (a: string) => HEX_LONG.test(a) || (B64_LONG.test(a) && /\d/.test(a) && /[A-Za-z]/.test(a));

/** The argv with secret-looking values replaced by ***: `--token=…` / `--key=…` / `--password=…` (and any flag naming a
 *  token, key, secret, password or auth, with `=` or as the next argument), `-p <value>`, `user:pass@` in URLs,
 *  `NAME=value` assignments, and long hex or base64-looking arguments. */
export function maskArgv(argv: readonly string[]): string[] {
  const python = argv.length > 0 && /^python[\d.]*$/.test(base(argv[0]));
  const interp = argv.length > 0 && INTERPRETERS.test(base(argv[0]));
  const takesSecret = (prev: string) => prev === '-p' || prev === '--user' || (prev === '-u' && !python)
    || /^(bearer|basic)$/i.test(prev) || SECRET_FLAG.test(prev) || (interp && codeFlag(prev) !== null && codeFlag(prev) === prev);
  const out: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const prev = i > 0 ? argv[i - 1] : '';
    if (i > 0 && takesSecret(prev)) { out.push(MASK); continue; }
    if (interp && /^--(eval|print)=/.test(a)) { out.push(`${a.split('=')[0]}=${MASK}`); continue; }
    const code = interp ? codeFlag(a) : null;
    if (code !== null) { out.push(code === a ? a : `${code}${MASK}`); continue; }   // the flag; code attached to it (-eprint(...)) masked
    const eq = SECRET_FLAG_EQ.exec(a);
    if (eq) { out.push(`${eq[1]}=${MASK}`); continue; }
    if (/^--user=/.test(a)) { out.push(`--user=${MASK}`); continue; }
    if (/^-p.+/.test(a) && !a.startsWith('--')) { out.push(`-p${MASK}`); continue; }
    if (/^-u.+/.test(a) && !a.startsWith('--') && !python) { out.push(`-u${MASK}`); continue; }
    const env = !a.startsWith('-') ? ENV_ASSIGN.exec(a) : null;
    if (env) { out.push(`${env[1]}=${MASK}`); continue; }
    const flagEq = /^(--?[\w.-]+)=(.*)$/.exec(a);
    if (flagEq && looksLikeSecret(flagEq[2])) { out.push(`${flagEq[1]}=${MASK}`); continue; }
    if (looksLikeSecret(a)) { out.push(MASK); continue; }
    out.push(a.replace(AUTH_HEADER, `$1${MASK}`).replace(AUTH_SCHEME, `$1 ${MASK}`)
      .replace(URL_CREDS, `$1${MASK}@`).replace(QUERY_SECRET, `$1${MASK}`));
  }
  return out;
}

const INTERPRETERS = /^(node|nodejs|bun|deno|python[\d.]*|ruby|php|perl|java)$/;

/** A single-dash cluster with an inline-code letter (c, e, E or p: `-c`, `-e`, `-pe`, `-Bc`, `-E`) is a code flag; it
 *  returns the flag up to and including that letter (`-eprint(1)` -> `-e`), or null. `--eval` / `--print` too. */
export function codeFlag(a: string): string | null {
  if (/^--(eval|print)(=|$)/.test(a)) return a.split('=')[0];
  const m = /^-[A-Za-z]*?[ceEp]/.exec(a);
  if (!m || a.startsWith('--')) return null;
  return /^-[A-Za-z]+$/.test(a) && a.length <= 5 ? a : m[0];
}

// Flags that take the next argument as their value (not the script).
const VALUE_FLAGS = new Set(['-r', '--require', '--import', '--loader', '--experimental-loader', '-X', '-W', '--env-file', '-cp', '-classpath', '--class-path']);
const SCRIPT_EXT = /\.(js|mjs|cjs|ts|py|rb|php|pl|jar)$/;
const PROGRAM_MAX = 60;

/** What a MEMBER sees: the program's file name, nothing else (no script, flag or argument can carry code or a secret). */
export function programName(argv: readonly string[]): string {
  return argv.length ? base(argv[0]) || '?' : '?';
}

/** An argument taken as the script: no whitespace, ;, (, quotes or =, and a / or a script extension. */
const scriptLike = (a: string) => !/[\s;("'`=]/.test(a) && (a.includes('/') || SCRIPT_EXT.test(a));

/** An ADMIN's guess at what an interpreter runs: the module (`-m x`), the jar or script's file name when it looks like
 *  one, or the code flag alone (`perl -pe`). Anything else stops the guess. Capped, and masked as a backstop. */
export function programDetail(argv: readonly string[]): string {
  const prog = programName(argv);
  let name = prog;
  if (INTERPRETERS.test(prog)) {
    for (let i = 1; i < argv.length; i++) {
      const a = argv[i];
      if (VALUE_FLAGS.has(a)) { i++; continue; }
      const code = codeFlag(a);
      if (code !== null) { name = `${prog} ${code}`; break; }
      if (a === '-m') { if (argv[i + 1] && /^[\w.]+$/.test(argv[i + 1])) name = `${prog} -m ${argv[i + 1]}`; break; }
      if (a === '-jar') { if (argv[i + 1] && scriptLike(argv[i + 1])) name = `${prog} ${base(argv[i + 1])}`; break; }
      if (a.startsWith('-')) continue;
      if (scriptLike(a)) name = `${prog} ${base(a)}`;
      break;
    }
  }
  const masked = maskArgv(name.split(' ')).join(' ');
  return masked.length <= PROGRAM_MAX ? masked : `${masked.slice(0, PROGRAM_MAX - 1)}…`;
}

const HASH_KEY = randomBytes(32);
/** An HMAC of the full argv under a key made when the API starts: Stop matches on it, so the client never needs the raw
 *  command, and a short secret in it can't be guessed from the hash. A restart changes every hash (a Stop from a list
 *  read before it is refused, and the list is read again). */
export const commandHash = (argv: readonly string[]) => createHmac('sha256', HASH_KEY).update(argv.join('\0')).digest('hex');

export async function listCandidates(src: ProcSource, seats: readonly Seat[], opts: ListOptions): Promise<Candidate[]> {
  const install = new Set(opts.installPorts);
  const listening = [...src.listeners()].filter(([, ports]) => !ports.some((p) => install.has(p)));
  if (!listening.length) return [];
  const ctx: ClassifyCtx = { seats, opts, apiChain: apiChainOf(src), panes: await src.tmuxPanes() };
  const out: Candidate[] = [];
  for (const [pid, ports] of listening) {
    const c = classify(pid, src, ctx);
    if (!c.ok) continue;
    out.push({ pid, startTicks: c.info.startTicks, startedAt: startedAtIso(src, c.info.startTicks), program: programName(c.info.argv), detail: programDetail(c.info.argv),
      command: maskArgv(c.info.argv).join(' '), commandHash: commandHash(c.info.argv),
      ports: [...new Set(ports)].sort((a, b) => a - b), agent: c.agent, via: c.via });
  }
  return out.sort((a, b) => a.startTicks - b.startTicks || a.pid - b.pid);
}

export interface StopRequest { pid: unknown; startTicks: unknown; commandHash: unknown }
export type StopResult =
  | { ok: true; result: 'stopped' | 'killed'; candidate: Candidate; warning?: string }
  | { ok: false; status: number; error: string; candidate?: Candidate };

/** Stop one listed program: re-checked against the list as it is now (pid, start time, command hash); SIGTERM, then
 *  SIGKILL after `graceMs` only if the same process (pid and start time) is still alive. Then, after a moment, whether
 *  something is listening on its ports again (a watcher that restarted it).
 *
 *  The pid-reuse window, accepted: between the re-check and kill(), and between a poll and SIGKILL, the process could
 *  exit and the kernel hand its pid to a new process, which would get the signal. Node has no pidfd_send_signal, so
 *  this can't be closed; it is bounded by checking pid + start time right before each signal, and Linux hands pids
 *  out in order, so a reuse needs the pid space to wrap within those microseconds. */
export async function stopCandidate(src: ProcSource, seats: readonly Seat[], opts: ListOptions, req: StopRequest,
  deps: { kill: (pid: number, sig: NodeJS.Signals) => void; sleep: (ms: number) => Promise<void>; graceMs?: number; pollMs?: number; settleMs?: number }): Promise<StopResult> {
  const { pid, startTicks, commandHash: hash } = req;
  if (!Number.isInteger(pid) || (pid as number) <= 1 || !Number.isInteger(startTicks) || typeof hash !== 'string' || !hash) {
    return { ok: false, status: 400, error: 'pid, startTicks and commandHash are required' };
  }
  const now = (await listCandidates(src, seats, opts)).find((c) => c.pid === pid);
  if (!now) return { ok: false, status: 409, error: 'This program is no longer in the list. Refresh to see what is running now.' };
  if (now.startTicks !== startTicks || now.commandHash !== hash) {
    return { ok: false, status: 409, error: 'This is not the same program any more. Refresh to see what is running now.' };
  }
  // When the stop began, in ticks since boot (/proc/uptime), and never before the stopped process itself started.
  const stopTicks = Math.max(src.nowTicks(), now.startTicks + 1);
  const same = () => { const p = src.proc(now.pid); return !!p && p.startTicks === now.startTicks && p.state !== 'Z'; };
  const done = async (result: 'stopped' | 'killed'): Promise<StopResult> => {
    await deps.sleep(deps.settleMs ?? 1000);
    const warning = restartedOn(src, now, stopTicks);
    return { ok: true, result, candidate: now, ...(warning ? { warning } : {}) };
  };
  try { deps.kill(now.pid, 'SIGTERM'); }
  catch (e) { return { ok: false, status: 502, error: `Couldn't stop it: ${e instanceof Error ? e.message : String(e)}`, candidate: now }; }
  const grace = deps.graceMs ?? 5000;
  const poll = deps.pollMs ?? 250;
  for (let waited = 0; waited < grace; waited += poll) {
    await deps.sleep(poll);
    if (!same()) return done('stopped');
  }
  if (!same()) return done('stopped');
  try { deps.kill(now.pid, 'SIGKILL'); }
  catch (e) { return { ok: false, status: 502, error: `It didn't stop, and SIGKILL failed: ${e instanceof Error ? e.message : String(e)}`, candidate: now }; }
  for (let waited = 0; waited < 2000; waited += poll) {
    await deps.sleep(poll);
    if (!same()) return done('killed');
  }
  return { ok: false, status: 502, error: 'It is still running after SIGKILL.', candidate: now };
}

/** After a stop: a process that STARTED after the stop (`sinceTicks`) and listens on one of its ports means something
 *  restarted it. Names the port and the new listener's parent (program name only). */
export function restartedOn(src: ProcSource, gone: Pick<Candidate, 'pid' | 'startTicks' | 'ports'>, sinceTicks: number): string | null {
  for (const [pid, ports] of src.listeners()) {
    const port = ports.find((p) => gone.ports.includes(p));
    if (port === undefined) continue;
    const p = src.proc(pid);
    if (!p || p.startTicks < sinceTicks) continue;   // the same process, or one that was already there: not a restart
    const parent = src.proc(p.ppid);
    return `Stopped, but something restarted it on :${port}${parent ? ` (started by ${programDetail(parent.argv)})` : ''}`;
  }
  return null;
}

// ── The live source: /proc on Linux ──────────────────────────────────────────────────────────────────────────────

function readText(p: string): string | null { try { return fs.readFileSync(p, 'utf-8'); } catch { return null; } }

/** LISTEN sockets in /proc/net/tcp{,6}: inode -> port. */
export function parseNetTcp(text: string): Map<string, number> {
  const out = new Map<string, number>();
  for (const line of text.split('\n').slice(1)) {
    const f = line.trim().split(/\s+/);
    if (f.length < 10 || f[3] !== '0A') continue;
    const port = parseInt(f[1].split(':')[1] ?? '', 16);
    if (Number.isInteger(port) && f[9] !== '0') out.set(f[9], port);
  }
  return out;
}

/** /proc/<pid>/stat: the fields after the command (which may hold spaces and parens). */
export function parseStat(text: string): { state: string; ppid: number; startTicks: number } | null {
  const close = text.lastIndexOf(')');
  if (close < 0) return null;
  const f = text.slice(close + 2).split(' ');
  // f[0] is field 3 (state), f[1] field 4 (ppid), f[19] field 22 (starttime).
  const ppid = Number(f[1]), startTicks = Number(f[19]);
  return Number.isInteger(ppid) && Number.isInteger(startTicks) ? { state: f[0], ppid, startTicks } : null;
}

// Read once, when this module loads: neither changes while the machine is up.
const CLK_TCK = (() => {
  if (process.platform !== 'linux') return 100;
  try { return Number(execFileSync('getconf', ['CLK_TCK'], { encoding: 'utf-8', timeout: 2000 }).trim()) || 100; } catch { return 100; }   // Linux's usual 100
})();
const BOOT_TIME_SEC = Number(/^btime (\d+)/m.exec(readText('/proc/stat') ?? '')?.[1] ?? 0);
const execFileAsync = promisify(execFile);

export function procSource(): ProcSource {
  const uid = process.getuid ? process.getuid() : -1;
  return {
    uid, selfPid: process.pid, bootTimeSec: BOOT_TIME_SEC, clkTck: CLK_TCK,
    nowTicks() {
      const up = Number((readText('/proc/uptime') ?? '').split(' ')[0]);
      return Number.isFinite(up) && up > 0 ? Math.floor(up * CLK_TCK) : Math.floor((Date.now() / 1000 - BOOT_TIME_SEC) * CLK_TCK);
    },
    listeners() {
      const inodes = new Map<string, number>();
      for (const f of ['/proc/net/tcp', '/proc/net/tcp6']) for (const [k, v] of parseNetTcp(readText(f) ?? '')) inodes.set(k, v);
      const out = new Map<number, number[]>();
      if (!inodes.size) return out;
      let pids: string[] = [];
      try { pids = fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d)); } catch { return out; }
      for (const d of pids) {
        let fds: string[];
        try { fds = fs.readdirSync(`/proc/${d}/fd`); } catch { continue; }   // another user's: not readable, not ours
        for (const fd of fds) {
          let link: string;
          try { link = fs.readlinkSync(`/proc/${d}/fd/${fd}`); } catch { continue; }
          const m = /^socket:\[(\d+)\]$/.exec(link);
          const port = m ? inodes.get(m[1]) : undefined;
          if (port !== undefined) { const pid = Number(d); out.set(pid, [...(out.get(pid) ?? []), port]); }
        }
      }
      return out;
    },
    proc(pid) {
      const stat = parseStat(readText(`/proc/${pid}/stat`) ?? '');
      const status = readText(`/proc/${pid}/status`);
      if (!stat || !status) return null;
      const uidLine = /^Uid:\s+(\d+)/m.exec(status);
      const argv = (readText(`/proc/${pid}/cmdline`) ?? '').split('\0').filter((s, i, a) => s !== '' || i < a.length - 1);
      let cwd: string | null = null;
      try { cwd = fs.readlinkSync(`/proc/${pid}/cwd`); } catch { /* not ours */ }
      const env = readText(`/proc/${pid}/environ`);
      const agentEnv = env ? (/(?:^|\0)ORCHESTRA_AGENT_ID=([^\0]*)/.exec(env)?.[1] || null) : null;
      const cg = (readText(`/proc/${pid}/cgroup`) ?? '').trim().split('\n').pop() ?? '';
      return { pid, ppid: stat.ppid, uid: uidLine ? Number(uidLine[1]) : -1, startTicks: stat.startTicks, state: stat.state, argv, cwd, cgroup: cg, agentEnv };
    },
    async tmuxPanes() {
      const out = new Map<number, string>();
      try {
        const { stdout: txt } = await execFileAsync('tmux', ['list-panes', '-a', '-F', '#{pane_pid} #{session_name}'], { encoding: 'utf-8', timeout: 3000 });
        for (const line of txt.split('\n')) {
          const m = /^(\d+) (.+)$/.exec(line.trim());
          if (m) out.set(Number(m[1]), m[2]);
        }
      } catch { /* no tmux server: no panes */ }
      return out;
    },
  };
}

/** Whether this machine has /proc to read (Linux). Elsewhere the list is empty and says so. */
export const procSupported = () => fs.existsSync('/proc/net/tcp');
