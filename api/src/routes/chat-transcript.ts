/**
 * Transcript-driven chat endpoint — the message-isolation key.
 *
 * Renders structured conversation from both Claude Code per-session JSONL
 * (~/.claude/projects/) and Antigravity / Gemini CLI brain logs
 * (~/.gemini/antigravity-cli/brain/<cid>/.system_generated/logs/transcript.jsonl)
 * instead of scraping the tmux pane (terminal soup, ANSI, wrapping, ghost text).
 *
 * Kept in its OWN router (not agents.ts) to avoid co-edit clobbering with the
 * concurrently-maintained detector code. Mounted at /api/agents in server.ts.
 */
import { Router, type Request, type Response } from 'express';
import { readFileSync, existsSync, readdirSync, statSync, openSync, readSync, closeSync } from 'fs';
import { join } from 'path';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { LIVE_CARDS, mergeQueuedItems } from '../services/queued-merge.js';
import { homedir } from 'os';
import Database from 'better-sqlite3';
import { agentScopeParam } from '../lib/agent-scope.js';
import { expandChipDodge } from '../lib/chipDodge.js';
import { sessionOwners } from '../services/identity-store-reader.js';
import { dataDir } from '../lib/config.js';
import { paneInfo, forgetPane, childPids, procArgv, liveGeminiSid } from '../lib/paneProbe.js';
import { transcriptMarkdown, DEFAULT_LAST_TURNS } from '../lib/transcriptMarkdown.js';
import { getRegistry } from '../services/state-reader.js';
import { principal } from '../lib/principal.js';
import { readGatewayToken } from '../lib/gateway-token.js';

const router = Router();
// Every /:id route on this router is scoped to the caller's principal — the same rule GET /
// applies (lib/agent-scope.ts). Registered as a param handler so a /:id route added later is
// scoped without anyone remembering to; an out-of-scope id gets the same 404 as an unknown one.
router.param('id', agentScopeParam);

const HOME = process.env.HOME || homedir();
const ORCH_DIR = dataDir();
const CLAUDE_PROJECTS = join(HOME, '.claude', 'projects');
const GEMINI_BRAIN = join(HOME, '.gemini', 'antigravity-cli', 'brain');

function loadAgentSessions(): Record<string, any> {
  try { return JSON.parse(readFileSync(join(ORCH_DIR, 'state', 'agent-sessions.json'), 'utf-8')); }
  catch { return {}; }
}

// Live Antigravity / Gemini conversation id (pane child fds) and the pane id for the hook file both
// come from lib/paneProbe.ts: one cached tmux call per session per 10 s, /proc instead of pgrep
// (every transcript poll used to fork tmux + pgrep per seat).

// Tier-0 sid resolution: the pane's hook-event file (state-event-hook.py writes
// the LIVE session_id on every Stop/PreToolUse).
function hookEventSid(tmuxSession: string): { sid: string | null; cwd: string | null } {
  const { paneId } = paneInfo(tmuxSession);
  if (!paneId) return { sid: null, cwd: null };
  try {
    const ev = JSON.parse(readFileSync(
      join(ORCH_DIR, 'state', 'agent-events', 'panes', `${paneId}.json`), 'utf-8'));
    const sid = typeof ev.session_id === 'string' && ev.session_id.length >= 36
      ? ev.session_id : null;
    return { sid, cwd: typeof ev.cwd === 'string' ? ev.cwd : null };
  } catch { return { sid: null, cwd: null }; }
}

// Discover active Antigravity / Gemini conversation by declared identity in brain logs
function findGeminiByDeclaration(agentId: string): { path: string; sid: string } | null {
  if (!existsSync(GEMINI_BRAIN)) return null;
  try {
    const cids = readdirSync(GEMINI_BRAIN);
    const brains: { cid: string; path: string; mtime: number }[] = [];
    for (const cid of cids) {
      const tp = join(GEMINI_BRAIN, cid, '.system_generated', 'logs', 'transcript.jsonl');
      if (existsSync(tp)) {
        try {
          brains.push({ cid, path: tp, mtime: statSync(tp).mtimeMs });
        } catch {}
      }
    }
    brains.sort((a, b) => b.mtime - a.mtime);

    let exactMatch: { path: string; sid: string } | null = null;
    let siblingMatch: { path: string; sid: string } | null = null;

    const baseId = agentId.replace(/-(?:gen\d+|next|\d+)$/, '');

    for (const b of brains.slice(0, 40)) {
      try {
        const fd = openSync(b.path, 'r');
        const buf = Buffer.alloc(16384);
        const bytesRead = readSync(fd, buf, 0, 16384, 0);
        closeSync(fd);
        const text = buf.toString('utf-8', 0, bytesRead);

        const m = text.match(/You are (?:\*\*)?([A-Za-z0-9_\-]+)(?:\*\*)?/);
        if (m) {
          const decl = m[1];
          if (decl === agentId) {
            exactMatch = { path: b.path, sid: b.cid };
            break;
          }
          const declBase = decl.replace(/-(?:gen\d+|next|\d+)$/, '');
          if (declBase === baseId && !siblingMatch) {
            siblingMatch = { path: b.path, sid: b.cid };
          }
        }
      } catch {}
    }
    return exactMatch || siblingMatch;
  } catch { return null; }
}

// --- codex ------------------------------------------------------------------
// Codex writes rollouts to ~/.codex/sessions/<Y>/<M>/<D>/rollout-<ts>-<uuid>.jsonl and indexes
// them in ~/.codex/state_5.sqlite table `threads` (id, rollout_path, cwd, thread_source,
// first_user_message, updated_at_ms).
//
// Resolution is a POSITIVE seat-name declaration join on first_user_message — the same shape as
// findGeminiByDeclaration above — and NEVER a cwd match. Congruence DEC-1790239929422621: every
// seat shares repo_root by construction (orchestra_cli/seats.py register_seat does
// setdefault("cwd", str(st.repo_root))), so cwd cannot identify a seat, and a cwd/recency fallback
// would render ANOTHER project's conversation as this seat's on a shared box. A blank pane is
// correct; a wrong pane is a data exposure. chat-transcript.codex.test.ts pins that fence.
//
// DO NOT ADD A /proc TIER FOR CODEX. It works for Gemini (liveGeminiSid above) and it cannot work
// here, for a structural reason rather than an incidental one. Measured on a live codex seat in
// state "working", the complete fd list of its process was:
//     /dev/pts/N, 0, anon_inode:[eventfd], anon_inode:[eventpoll], anon_inode:[io_uring], pipes
// Zero descriptors under the home directory; zero matching codex|sqlite|rollout. Gemini holds its
// brain log OPEN, so its sid is readable off /proc. Codex opens-writes-closes, and its file I/O
// goes through io_uring, which never surfaces a persistent descriptor to read back at all. A tier
// that can never match is worse than no tier: it reads as though live seats were handled specially
// while silently always falling through to the join below.
const CODEX_STATE_DB = join(HOME, '.codex', 'state_5.sqlite');

export function findCodexByDeclaration(agentId: string, dbPath = CODEX_STATE_DB):
  { path: string; sid: string } | null {
  if (!existsSync(dbPath)) return null;
  const baseId = agentId.replace(/-(?:gen\d+|g\d+|next|\d+)$/, '');
  let db: any = null;
  try {
    db = new Database(dbPath, { readonly: true, fileMustExist: true });
    const rows = db.prepare(
      "SELECT id, rollout_path, first_user_message FROM threads "
      + "WHERE thread_source = 'user' AND first_user_message IS NOT NULL "
      + 'ORDER BY updated_at_ms DESC LIMIT 500',
    ).all() as { id: string; rollout_path: string; first_user_message: string }[];
    // Whole-name match. \b is NOT enough: a hyphen is a non-word char, so /you are gm\b/ happily
    // matches "You are gm-watcher" and would hand this seat another seat's conversation. The
    // lookahead denies a following word char OR hyphen.
    const esc = baseId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const declares = new RegExp(`^\\s*you are ${esc}(?![\\w-])`, 'i');
    for (const r of rows) {
      if (r.rollout_path && declares.test(String(r.first_user_message))) {
        return { path: r.rollout_path, sid: r.id };
      }
    }
    return null;
  } catch {
    return null;            // missing table, corrupt file, locked db — a blank pane, never a 500
  } finally {
    try { db?.close(); } catch {}
  }
}

// ---- Grok Build (`grok` CLI) -------------------------------------------------------------
// A grok seat's conversation is $GROK_HOME/sessions/<encoded-cwd>/<uuid>/updates.jsonl. The
// seat is bound to its session by PROCESS TRUTH: spawn-agent.sh pins the UUID on grok's argv
// (`-s <uuid>` new, `-r <uuid>` resume), so pane -> grok pid -> argv UUID -> the one session
// dir with that UUID. No "newest session" guess anywhere (the same rule as
// scripts/grok_context.py, which the Python side uses; this is its TypeScript twin).
const GROK_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const GROK_BASENAMES = new Set(['grok', 'grok-linux-x86_64']);

function grokSessionsRoot(): string {
  return join(process.env.GROK_HOME || join(HOME, '.grok'), 'sessions');
}

/** The session UUID a grok argv is bound to: -s/--session-id wins, else -r/--resume. A bare
 *  -r, a title, -c, or no flag is NOT an identity -> null (fail closed). */
export function grokSidFromArgv(argv: string[]): string | null {
  if (!argv.length) return null;
  const base = argv[0].split('/').pop() || '';
  if (!GROK_BASENAMES.has(base)) return null;
  const found: Record<string, string> = {};
  const args = argv.slice(1);
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    for (const [short, long, key] of [['-s', '--session-id', 's'], ['-r', '--resume', 'r']]) {
      if (a.startsWith(long + '=')) { found[key] ??= a.slice(long.length + 1); }
      else if (a === short || a === long) {
        const nxt = args[i + 1];
        if (nxt !== undefined && !nxt.startsWith('-')) found[key] ??= nxt;
      }
    }
  }
  for (const key of ['s', 'r']) {
    if (found[key] !== undefined) return GROK_UUID.test(found[key]) ? found[key] : null;
  }
  return null;
}

/** updates.jsonl for a grok session UUID: exactly one session dir, else null. */
export function grokUpdatesPath(sid: string, root = grokSessionsRoot()): string | null {
  if (!GROK_UUID.test(sid)) return null;
  let hits: string[] = [];
  try {
    hits = readdirSync(root).map((d) => join(root, d, sid)).filter((p) => {
      try { return statSync(p).isDirectory(); } catch { return false; }
    });
  } catch { return null; }
  if (hits.length !== 1) return null;
  const path = join(hits[0], 'updates.jsonl');
  return existsSync(path) ? path : null;
}

/** The pane's unique live grok process (pane shell or up to two levels below it), and the
 *  session UUID on its argv. Two grok processes, or none, -> null. */
function liveGrokSid(tmuxSession: string): { running: boolean; sid: string | null } {
  try {
    // The cached pane (lib/paneProbe) and /proc children: no tmux or pgrep fork per poll.
    const { panePid } = paneInfo(tmuxSession);
    if (!panePid) return { running: false, sid: null };
    const kids = (pid: string): string[] => childPids(pid) ?? [];
    const isGrok = (pid: string) => GROK_BASENAMES.has((procArgv(pid)[0] || '').split('/').pop() || '');
    let hits = isGrok(panePid) ? [panePid] : kids(panePid).filter(isGrok);
    if (!hits.length) for (const k of kids(panePid)) hits = hits.concat(kids(k).filter(isGrok));
    if (hits.length !== 1) return { running: hits.length > 1, sid: null };
    return { running: true, sid: grokSidFromArgv(procArgv(hits[0])) };
  } catch { return { running: false, sid: null }; }
}

/** A seat that is grok by its own records (runtime field or a grok resume command). */
function isGrokRow(e: Record<string, any>): boolean {
  return String(e?.runtime || '').toLowerCase() === 'grok'
    || /^(\S*\/)?grok\s/.test(String(e?.resume_command || ''));
}

/** Grok Build writes an ACP-style append-only stream (`method: session/update`).
 *  The lines are self-describing, so the SSE tail and this route share one sniff. */
export function looksLikeGrok(lines: string[]): boolean {
  for (const l of lines.slice(0, 40)) {
    if (!l.trim()) continue;
    try {
      const d = JSON.parse(l);
      if (d?.method === 'session/update' && typeof d?.params?.update?.sessionUpdate === 'string') {
        return true;
      }
    } catch { /* torn or foreign line */ }
  }
  return false;
}

/** Codex rollout lines are self-describing, so the SSE tail and the poll path cannot disagree
 *  about which parser to use (they call normalizeTranscript independently). */
export function looksLikeCodex(lines: string[]): boolean {
  for (const l of lines.slice(0, 40)) {
    if (!l.trim()) continue;
    try {
      const d = JSON.parse(l);
      if (d && typeof d.ordinal === 'number'
          && (d.type === 'session_meta' || d.type === 'response_item' || d.type === 'token_usage_record')) {
        return true;
      }
    } catch { /* torn or foreign line */ }
  }
  return false;
}

/** Harness envelopes codex injects as role 'user'. Observed on a live seat: environment_context,
 *  the skills/multi-agent preambles, and user_instructions. Anchored at the start so an operator
 *  quoting one of these tags in a real message is not silently reclassified. */
const CODEX_PLUMBING =
  /^\s*<(environment_context|skills_instructions|multi_agent_mode|user_instructions|system_context)\b/i;

function codexText(content: any): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content.map((c: any) => (c && typeof c.text === 'string' ? c.text : '')).join('').trim();
}

/** Codex tool arguments arrive as a JSON STRING; toolSummary()/capInput() expect an object, so an
 *  unparsed string renders a blank summary. Always hand downstream an object. */
function codexArgs(raw: any): Record<string, unknown> {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    try {
      const p = JSON.parse(raw);
      if (p && typeof p === 'object' && !Array.isArray(p)) return p as Record<string, unknown>;
      return { value: p };
    } catch { return { value: raw }; }
  }
  return {};
}

/** Map a codex rollout to the SAME flat item grammar the Claude/Gemini paths emit, so enrichItems,
 *  capInput, toolSummary and buildRenderItems are all reused untouched. */
/** Attach `ts` only when the record actually carried a timestamp.
 *
 *  `ts` is OPTIONAL in transcript.v2.schema.json and documented as "ISO-8601 timestamp", so `''`
 *  is not a legal value for it -- and this path used to coerce a missing timestamp into exactly
 *  that. The Claude path never had the bug: it passes `o.timestamp` straight through, so an absent
 *  one is `undefined` and drops out of the JSON. This makes codex agree.
 *
 *  Assigning conditionally rather than setting `ts: undefined`: an own property holding
 *  `undefined` is NOT the same as an absent property to `assert.deepStrictEqual`, which the
 *  fixture-conformance suite compares with. */
function withTs<T extends object>(it: T, ts: string | undefined): T {
  if (ts) (it as any).ts = ts;
  return it;
}

export function parseCodexRollout(lines: string[]): any[] {
  const recs: { ordinal: number; ts: string | undefined; payload: any; type: string }[] = [];
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const d = JSON.parse(line);
      if (!d || typeof d !== 'object') continue;
      recs.push({ ordinal: Number(d.ordinal ?? 0), ts: d.timestamp ? String(d.timestamp) : undefined, payload: d.payload || {}, type: String(d.type || '') });
    } catch { /* torn final line while the seat is still writing — keep everything before it */ }
  }
  // Explicit ordinal order: the file can be appended by more than one writer
  // (~/.codex/thread-writer-locks/ exists), so line order is not authoritative.
  recs.sort((a, b) => a.ordinal - b.ordinal);

  const items: any[] = [];
  for (const r of recs) {
    if (r.type !== 'response_item') continue;     // event_msg/token_usage_record/world_state/turn_context = telemetry
    const p = r.payload || {};
    const uuid = String(p.id || `${r.ordinal}`);
    switch (p.type) {
      case 'message': {
        const role = p.role === 'assistant' ? 'assistant' : 'user';
        // The gateway chip-dodges long messages for every runtime, not just claude.
        const text = role === 'user' ? expandChipDodge(codexText(p.content)) : codexText(p.content);
        if (!text) break;
        const it: any = withTs({ kind: 'text', role, text, uuid }, r.ts);
        // The operator never typed the harness's own envelopes. Same rule as the Claude path
        // (sanitizeClaudeUserText, 55134d0): internals must not render as operator speech.
        // A developer-role message is the brief; codex also injects environment/context envelopes
        // as role 'user', which would otherwise show up as a blue bubble the operator never sent.
        if (p.role === 'developer' || CODEX_PLUMBING.test(text)) it.is_system = true;
        items.push(it);
        break;
      }
      case 'agent_message': {
        const text = codexText(p.content ?? p.message);
        if (text) items.push(withTs({ kind: 'text', role: 'assistant', text, uuid }, r.ts));
        break;
      }
      case 'reasoning': {
        // encrypted_content is opaque ciphertext and is NEVER decoded or emitted. In 1570 real
        // records summary[] was populated 0 times, so this virtually always emits nothing —
        // deliberately, rather than surfacing an empty thinking bubble.
        const text = Array.isArray(p.summary)
          ? p.summary.map((s: any) => (s && typeof s.text === 'string' ? s.text : '')).join('\n').trim()
          : '';
        if (text) items.push(withTs({ kind: 'thinking', role: 'assistant', text, uuid }, r.ts));
        break;
      }
      case 'custom_tool_call':
      case 'function_call': {
        items.push(withTs({
          kind: 'tool_use', role: 'assistant',
          tool: String(p.name || ''),
          input: codexArgs(p.input ?? p.arguments),
          id: String(p.call_id || p.id || ''),     // pairs with tool_use_id below
          uuid,
        }, r.ts));
        break;
      }
      case 'custom_tool_call_output':
      case 'function_call_output': {
        items.push(withTs({
          kind: 'tool_result', role: 'user',
          text: codexText(p.output),
          tool_use_id: String(p.call_id || ''),
          is_error: !!p.is_error,
          uuid,
        }, r.ts));
        break;
      }
      default: break;                              // compacted/unknown: not conversation
    }
  }
  return items;
}

type SessionOwners = Map<string, { root: string; current: boolean }> | null;
const lineageOf = (id: string) => id.replace(/-(?:gen\d+|g\d+|next)$/, '');

/** May `agentId`'s chat show session `sid`? The state files this route reads can lag the identity
 *  store (a projector re-emitted the PREDECESSOR's session id for a seat), and a reused pane id can
 *  carry another session's hook file, so a file existing is not enough. A session the store knows
 *  is refused when it belongs to another lineage (a green's shadow root, `<seat>-g25`, counts as
 *  its seat's lineage), or, viewing the lineage's canonical id, to a generation of that root that
 *  is not its current head. A session the store does not know (a /clear, a seat outside the store,
 *  no store at all) is allowed, as before. A seat resumed on an older generation's session reads as
 *  that older generation until the store re-attributes it. `owners` is one read of the store. */
export function sessionIsThisSeats(agentId: string, sid: string | null | undefined,
                                   owners: SessionOwners): boolean {
  if (!sid || !owners) return true;
  const o = owners.get(String(sid));
  if (!o) return true;
  if (lineageOf(o.root) !== lineageOf(agentId)) return false;
  return agentId === o.root ? o.current : true;
}

// Resolve an agent id to its transcript JSONL path (re-read fresh per request).
// Exported for the F1 streaming lane (transcript-stream.ts) — same resolution,
// same file, so poll and stream can never disagree on WHICH transcript.
export function resolveTranscriptPath(agentId: string): { path: string | null; sid: string | null } {
  // The id becomes state/agents/<id>.json and a tmux target. A raw one read any .json for its
  // session_id. Safe ids only; no registry check, so a scratch tmux seat still resolves.
  if (!isSafeAgentId(agentId)) return { path: null, sid: null };
  const store = sessionOwners(ORCH_DIR);           // one read of the store for this resolve
  const owners = store?.sessions ?? null;
  const ok = (sid: string | null | undefined) => sessionIsThisSeats(agentId, sid, owners);
  const sessions = loadAgentSessions();
  const e = sessions[agentId] || {};
  const tmuxSession = String(e.tmux_session || agentId);

  // 0. Grok: a live grok process on the pane is read from ITS session (argv UUID). A seat that
  // is grok by its records but has no live process falls back to its stamped session id. A
  // grok seat never falls through onto a Claude/Codex/Gemini file that shares its cwd.
  const g = liveGrokSid(tmuxSession);
  if (g.running || isGrokRow(e)) {
    const sid = g.sid || (typeof e.session_id === 'string' && GROK_UUID.test(e.session_id) ? e.session_id : null);
    const path = sid ? grokUpdatesPath(sid) : null;
    return { path, sid };
  }

  // 0a. Live Gemini/Antigravity process check (inspect active process in tmux pane). Only a seat
  // that can be Gemini pays for it: a Gemini seat as step 6 defines one (runtime gemini/agy, or a
  // gemini-/agy- name), or a seat whose records name no runtime.
  const runtime = String(e.runtime || '').toLowerCase();
  const geminiSeat = runtime === 'gemini' || runtime === 'agy' || agentId.startsWith('gemini') || agentId.startsWith('agy');
  const gSid = !runtime || geminiSeat ? liveGeminiSid(tmuxSession) : null;
  if (gSid) {
    const gPath = join(GEMINI_BRAIN, gSid, '.system_generated', 'logs', 'transcript.jsonl');
    if (existsSync(gPath)) return { path: gPath, sid: gSid };
    forgetPane(tmuxSession);
  }

  // 0c. Codex: the seat's own declaration in ~/.codex/state_5.sqlite. Checked before the Claude
  // paths because a codex seat has no Claude hook sid and no ~/.claude/projects dir to fall into.
  // A miss falls through and ultimately returns {null,null} — never a cwd guess (see the header
  // comment on findCodexByDeclaration).
  // NOTE: there is no live-process (/proc) step here on purpose, unlike 0a for Gemini directly
  // above. A running codex holds no descriptor for its rollout — see the fd enumeration and the
  // io_uring reason in the block comment above findCodexByDeclaration before adding one.
  const cx = findCodexByDeclaration(agentId);
  if (cx && existsSync(cx.path)) return { path: cx.path, sid: cx.sid };

  // 0b. Hook event sid for live Claude process
  const hook = hookEventSid(tmuxSession);
  let sid: string | null = ok(hook.sid) ? hook.sid : null;
  if (sid && hook.cwd) {
    const p = join(CLAUDE_PROJECTS, hook.cwd.replace(/[/.]/g, '-'), `${hook.sid}.jsonl`);
    if (existsSync(p)) return { path: p, sid: hook.sid };
  }

  // 1. state/agents/<agentId>.json session_id (snapshot daemon updates this on rotations)
  try {
    const stateFile = join(ORCH_DIR, 'state', 'agents', `${agentId}.json`);
    if (existsSync(stateFile)) {
      const st = JSON.parse(readFileSync(stateFile, 'utf-8'));
      if (st.session_id && ok(st.session_id)) {
        const gPath = join(GEMINI_BRAIN, st.session_id, '.system_generated', 'logs', 'transcript.jsonl');
        if (existsSync(gPath)) return { path: gPath, sid: st.session_id };
        if (st.cwd) {
          const cp = join(CLAUDE_PROJECTS, String(st.cwd).replace(/[/.]/g, '-'), `${st.session_id}.jsonl`);
          if (existsSync(cp)) return { path: cp, sid: st.session_id };
        }
      }
    }
  } catch {}

  // 2. Explicit conversation_path in agent-sessions.json
  const eSidOk = ok(e.session_id);
  if (e.conversation_path && existsSync(e.conversation_path) && eSidOk) {
    return { path: e.conversation_path, sid: e.session_id || null };
  }

  // 3. Direct session_id check (Claude or Gemini brain)
  if (e.session_id && eSidOk) {
    const gPath = join(GEMINI_BRAIN, e.session_id, '.system_generated', 'logs', 'transcript.jsonl');
    if (existsSync(gPath)) return { path: gPath, sid: e.session_id };
  }

  // 4. Resume command regex
  sid = sid || (eSidOk ? e.session_id : null) || null;
  if (!sid && typeof e.resume_command === 'string') {
    const magy = e.resume_command.match(/--conversation\s+([0-9a-f-]{36})/);
    if (magy) {
      const gPath = join(GEMINI_BRAIN, magy[1], '.system_generated', 'logs', 'transcript.jsonl');
      if (existsSync(gPath)) return { path: gPath, sid: magy[1] };
    }
    const m = e.resume_command.match(/--resume\s+([0-9a-f-]{36})/);
    sid = m && ok(m[1]) ? m[1] : null;
  }

  // 6. A Gemini/Antigravity seat (by its recorded runtime, or a gemini-/agy- name): search the
  //    Antigravity brains by declaration
  if (geminiSeat) {
    const gHit = findGeminiByDeclaration(agentId);
    if (gHit) return gHit;
  }

  // 7. Claude project dir fallback
  let pdir: string | null = e.project_dir || null;
  if (!pdir && e.cwd) pdir = join(CLAUDE_PROJECTS, String(e.cwd).replace(/[/.]/g, '-'));
  if (pdir && sid) {
    const p = join(pdir, `${sid}.jsonl`);
    if (existsSync(p)) return { path: p, sid };
  }
  if (pdir && existsSync(pdir)) {
    try {
      // A project dir is shared by every seat with that cwd, so its newest file can be anyone's.
      // A seat the store knows (its lineage owns a session or has a canonical row, even with no
      // session yet: a service pane) takes only its OWN lineage's sessions here; any other seat keeps the old rule minus
      // sessions the store gives to someone else. Subagent sidechains (agent-*.jsonl) are never a
      // seat's chat. Lazy: stops at the first acceptable file.
      const known = !!store && [...store.roots].some((r) => lineageOf(r) === lineageOf(agentId));
      const js = readdirSync(pdir)
        .filter((f) => f.endsWith('.jsonl') && !f.startsWith('agent-'))
        .map((f) => ({ f, m: statSync(join(pdir!, f)).mtimeMs }))
        .sort((a, b) => b.m - a.m);
      const pick = js.find(({ f }) => {
        const s = f.replace('.jsonl', '');
        return known ? (!!owners!.get(s) && ok(s)) : ok(s);
      });
      if (pick) return { path: join(pdir, pick.f), sid: pick.f.replace('.jsonl', '') };
    } catch { /* ignore */ }
  }

  // 8. General Gemini brain scan fallback
  const gHit = findGeminiByDeclaration(agentId);
  if (gHit) return gHit;

  // 9. SID-glob fallback — we have a sid but no cwd-derived path matched. This
  // happens when an agent cd's into a git worktree subdir: its live hook-event
  // cwd (e.g. .../.claude/worktrees/<x>) slugifies to a project dir that does
  // NOT hold the transcript, which lives under the LAUNCH cwd's project dir.
  // A Claude session id is globally unique, so scanning every project dir for
  // <sid>.jsonl resolves it regardless of which worktree the agent wandered into.
  if (sid) {
    try {
      for (const dir of readdirSync(CLAUDE_PROJECTS)) {
        const p = join(CLAUDE_PROJECTS, dir, `${sid}.jsonl`);
        if (existsSync(p)) return { path: p, sid };
      }
    } catch { /* ignore */ }
  }

  return { path: null, sid };
}

function cleanArgs(args: any): Record<string, unknown> {
  if (!args || typeof args !== 'object') return (args as Record<string, unknown>) || {};
  const cleaned: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(args)) {
    if (typeof v === 'string') {
      try {
        cleaned[k] = JSON.parse(v);
      } catch {
        cleaned[k] = v;
      }
    } else {
      cleaned[k] = v;
    }
  }
  return cleaned;
}

// One Claude transcript line -> normalized chat items (the grammar shared with iOS).
/**
 * Claude Code writes its own plumbing into the transcript as USER messages: the caveat block
 * it prepends to a local command, the <command-name>/x</command-name> envelope, that
 * command's stdout/stderr, injected <system-reminder> blocks, and the interrupt marker. None
 * of it is something the operator said, and the chat rendered every one as a blue user bubble
 * (Shaw's screenshot of the `test` seat, 2026-09-22) — the class 55134d0 named: raw internals
 * must never reach the operator.
 *
 * A slash command IS what they typed, so it survives as `/login` (with its args); everything
 * else is dropped. Prose that merely mentions the markup is untouched, because only a
 * COMPLETE open+close pair is treated as plumbing. Returning '' drops the node: buildRenderItems
 * already skips empty text.
 */
export function sanitizeClaudeUserText(text: string): string {
  // A long message reached the pane as a one-line chip-dodge banner; show what was typed.
  let t = expandChipDodge(String(text ?? ''));
  t = t.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '');
  t = t.replace(/<local-command-caveat>[\s\S]*?<\/local-command-caveat>/g, '');
  t = t.replace(/<local-command-stdout>[\s\S]*?<\/local-command-stdout>/g, '');
  t = t.replace(/<local-command-stderr>[\s\S]*?<\/local-command-stderr>/g, '');
  const name = /<command-name>\s*([^<]*?)\s*<\/command-name>/.exec(t);
  if (name) {
    const args = /<command-args>\s*([^<]*?)\s*<\/command-args>/.exec(t);
    const typed = [name[1].trim(), (args?.[1] || '').trim()].filter(Boolean).join(' ');
    t = t.replace(/<command-(name|message|args)>[\s\S]*?<\/command-\1>/g, '');
    t = `${typed}\n${t}`;
  }
  // The harness's own interrupt marker, on its own line.
  t = t.replace(/^\s*\[Request interrupted by user[^\]]*\]\s*$/gm, '');
  return t.trim();
}

/**
 * A message the operator sent WHILE THE AGENT WAS MID-TURN.
 *
 * Claude Code does not record one as a user turn. It writes
 * `{type:'attachment', attachment:{type:'queued_command', prompt, origin, commandMode}}`,
 * so a renderer that only reads user turns never shows it. Measured on quest-orchestra's
 * transcript: 12 human prompts, and NOT ONE of them ever became a user turn — Shaw's
 * "I don't see the most recent message that I sent you". He sends most of his messages
 * mid-turn, so this hid a large share of what he says, on every surface.
 *
 * ONLY A HUMAN PROMPT IS ADMITTED, and that is an ALLOWLIST on purpose. The identical
 * shape also carries the harness's own `task-notification` envelopes and the empty
 * `queue-operation` rows; rendering those would put machine chatter in the conversation
 * as OPERATOR SPEECH, which is the same lie as hiding his message, pointed the other way.
 * A denylist would admit whatever `origin.kind` is added next.
 */
export function queuedCommandItems(o: any): any[] {
  const a = o?.attachment;
  if (!a || a.type !== 'queued_command') return [];
  const originKind = a.origin?.kind;
  // The legacy clause: builds predating `origin` label a human prompt only by commandMode.
  const isHuman = originKind === 'human' || (originKind == null && a.commandMode === 'prompt');
  if (!isHuman) return [];
  const text = sanitizeClaudeUserText(String(a.prompt || ''));
  if (!text) return [];
  // `queued` flows through buildRenderItems to the client, which can say "sent while the
  // agent was working" rather than presenting it as an ordinary turn.
  // queued:true here is a MARKER the two dedupes below find these by -- it is NOT the
  // client's "still pending" meaning, and settleIngestedQueuedCommands strips it before
  // anything is returned. See that function for why a logged queued_command is ingested.
  return [{ kind: 'text', role: 'user', text, ts: o.timestamp, uuid: o.uuid, queued: true, [FROM_QUEUED_COMMAND]: true }];
}

const FROM_QUEUED_COMMAND = '__fromQueuedCommand';

/**
 * A queued_command in the log has ALREADY been ingested -- clear its queued flag.
 *
 * The operator, 2026-10-08 (screenshots): "Queued badge never actually disappears after message
 * ingestion." The client contract for `queued` is "the server STILL holds this message"
 * (it draws a chip). But Claude Code writes a queued_command line AT INJECTION: measured
 * on live logs, it lands AFTER the tool_result it rides with (its own timestamp is the
 * earlier send time) and the agent's thinking follows at once. So once it is in the log
 * it is in the agent, and queued:true is false by construction. It was shipped forever.
 *
 * A message that is GENUINELY still pending is a msg_store held row with no delivered_at
 * (mergeQueuedItems, B1). Those never carry the marker, so their chip is untouched.
 *
 * Runs LAST, after both dedupes, because they find these items by the queued marker.
 */
export function settleIngestedQueuedCommands(items: any[]): any[] {
  return items.map((it) => {
    if (!it || it[FROM_QUEUED_COMMAND] !== true) return it;
    const { queued: _q, [FROM_QUEUED_COMMAND]: _m, ...rest } = it;
    return rest;
  });
}

/**
 * Drop a queued_command the msg_store batches ALREADY render.
 *
 * An agent-to-agent message delivered mid-turn exists TWICE: the gateway types it into the
 * pane, so Claude Code logs a `queued_command` for it, AND msg_store has the row, which
 * `mergeQueuedItems` renders as a `queued_batch`. Measured on quest-orchestra: 14 human-origin
 * queued_commands, and exactly ONE of them — a `[HUMAN-TASK apr_...]` row — is also a
 * msg_store message. Without this it shows twice.
 *
 * The BATCH wins, because it carries attribution: it knows which agent sent it and when,
 * while the queued_command has only the text the gateway typed.
 *
 * Matching is by containment on a substantial prefix, not equality, because the gateway WRAPS
 * the body when it types it in. A short text is skipped rather than matched loosely — dropping
 * one of Shaw's messages because it shares an opening with an agent's is far worse than
 * showing an agent's twice.
 */
export function dropQueuedCommandsCoveredByBatches(items: any[]): any[] {
  const bodies: string[] = [];
  // A LIVE card (queued-merge B3, in LIVE_CARDS) was bound by exact text, so it covers exactly that
  // text and nothing else: the prefix rule below would also hide an operator's own message that
  // merely opens the same way (the live store has 100+ rows sharing one 40-char opening).
  const exact = new Set<string>();
  const norm = (s: unknown) => String(s ?? '').replace(/\s+/g, ' ').trim();
  for (const it of items) {
    if (it?.kind === 'queued_batch') {
      const live = LIVE_CARDS.has(it);
      for (const e of it.entries || []) {
        const b = String(e?.body || '').trim();
        if (!b) continue;
        if (live) exact.add(norm(b));
        else bodies.push(b);
      }
    }
  }
  if (!bodies.length && !exact.size) return items;
  const MIN = 40; // below this a prefix is not distinctive enough to act on
  return items.filter((it) => {
    if (!it?.queued || it.kind !== 'text') return true;
    if (exact.has(norm(it.text))) return false;
    const t = String(it.text || '').trim();
    if (t.length < MIN) return true;
    const head = t.slice(0, MIN);
    return !bodies.some((b) => b.includes(head) || t.includes(b.slice(0, MIN)));
  });
}

/**
 * Drop a queued message that the log ALSO recorded as a real user turn.
 *
 * Whether it does is not consistent and cannot be assumed either way: on one seat the same
 * text appeared as a queue-operation row, as the attachment, AND as a user turn ~1300 lines
 * later when the turn finally consumed it; on another, 12 of 12 never appeared at all. So
 * rendering the attachment unconditionally double-shows some messages, and dropping it
 * unconditionally loses others. The real turn wins where both exist, because it carries the
 * harness's own content and position.
 */
export function dedupeQueuedCommands(items: any[]): any[] {
  const realUserText = new Set<string>();
  for (const it of items) {
    if (it.kind === 'text' && it.role === 'user' && !it.queued) {
      const t = (it.text || '').trim();
      if (t) realUserText.add(t);
    }
  }
  if (!realUserText.size) return items;
  return items.filter((it) => !(it.queued && it.kind === 'text' && realUserText.has((it.text || '').trim())));
}

function normalizeClaudeEntry(o: any): any[] {
  const t = o?.type;
  // A mid-turn message is an `attachment`, not a user turn — see queuedCommandItems.
  if (t === 'attachment') return queuedCommandItems(o);
  if (t !== 'user' && t !== 'assistant') return [];
  if (o.isSidechain === true || o.isCompactSummary === true) return [];
  // The harness writes its own notes as type:'user' with isMeta:true -- the image-size
  // note after a photo is read, stop-hook feedback, a loaded skill's body, local-command
  // caveats. The operator, 2026-10-08: rendered as user rows, they "look like messages that come
  // from the user when it's not". Measured across the 60 most recent live transcripts:
  // not one isMeta entry is human speech. Only the explicit `true` hides a turn.
  if (t === 'user' && o.isMeta === true) return [];
  const msg = o.message || {};
  const role = msg.role || t;
  const ts = o.timestamp;
  const uuid = o.uuid;
  const content = msg.content;
  const items: any[] = [];
  if (typeof content === 'string') {
    const text = role === 'user' ? sanitizeClaudeUserText(content) : content;
    if (text) items.push({ kind: 'text', role, text, ts, uuid });
    return items;
  }
  for (const b of content || []) {
    if (!b || typeof b !== 'object') continue;
    switch (b.type) {
      case 'text': {
        const text = role === 'user' ? sanitizeClaudeUserText(b.text || '') : (b.text || '');
        if (text) items.push({ kind: 'text', role, text, ts, uuid });
        break;
      }
      case 'thinking':
        items.push({ kind: 'thinking', role, text: b.thinking || '', ts, uuid });
        break;
      case 'tool_use':
        items.push({ kind: 'tool_use', role: 'assistant', tool: b.name || '', input: b.input || {}, id: b.id, ts, uuid });
        break;
      case 'tool_result': {
        let c = b.content;
        if (Array.isArray(c)) c = c.map((x: any) => (x && x.text) || '').join('');
        items.push({ kind: 'tool_result', role: 'user', text: String(c ?? ''), tool_use_id: b.tool_use_id, is_error: !!b.is_error, ts, uuid });
        break;
      }
    }
  }
  return items;
}

// Antigravity JSONL lines -> normalized chat items (the grammar shared with iOS).
function normalizeAntigravity(lines: string[]): any[] {
  const items: any[] = [];
  const pendingToolIds: string[] = [];

  for (const raw of lines) {
    if (!raw.trim()) continue;
    let o: any;
    try { o = JSON.parse(raw); } catch { continue; }

    const t = o.type;
    const ts = o.created_at || undefined;
    const step = o.step_index ?? items.length;

    if (t === 'CHECKPOINT' || t === 'CONVERSATION_HISTORY') continue;

    if (t === 'USER_INPUT') {
      let text = expandChipDodge((o.content || '').trim());
      if (text.includes('</CONTEXT_SUMMARY>')) {
        const parts = text.split('</CONTEXT_SUMMARY>');
        text = parts[parts.length - 1].trim();
      }
      text = text.replace(/<ADDITIONAL_METADATA>[\s\S]*?<\/ADDITIONAL_METADATA>/g, '').trim();
      const userReqMatch = text.match(/<USER_REQUEST>([\s\S]*?)<\/USER_REQUEST>/);
      if (userReqMatch) {
        text = userReqMatch[1].trim();
      } else {
        text = text.replace(/^<USER_REQUEST>\s*/, '').replace(/\s*<\/USER_REQUEST>$/, '').trim();
      }
      if (text) {
        items.push({
          kind: 'text',
          role: 'user',
          text,
          ts,
          uuid: `step-${step}`
        });
      }
      continue;
    }

    if (t === 'PLANNER_RESPONSE') {
      if (o.thinking && typeof o.thinking === 'string' && o.thinking.trim()) {
        items.push({
          kind: 'thinking',
          role: 'assistant',
          text: o.thinking.trim(),
          ts,
          uuid: `step-${step}-think`
        });
      }

      if (Array.isArray(o.tool_calls) && o.tool_calls.length > 0) {
        o.tool_calls.forEach((tc: any, idx: number) => {
          const callId = tc.id || tc.call_id || `call_${step}_${idx}`;
          pendingToolIds.push(callId);
          items.push({
            kind: 'tool_use',
            role: 'assistant',
            tool: tc.name || 'tool',
            input: cleanArgs(tc.args),
            id: callId,
            ts,
            uuid: `step-${step}-call-${idx}`
          });
        });
      }

      if (o.content && typeof o.content === 'string' && o.content.trim()) {
        items.push({
          kind: 'text',
          role: 'assistant',
          text: o.content.trim(),
          ts,
          uuid: `step-${step}-text`
        });
      }
      continue;
    }

    // Tool execution results
    if (pendingToolIds.length > 0) {
      const toolUseId = pendingToolIds.shift();
      let content = o.content ?? o.error ?? '';
      if (typeof content !== 'string') content = JSON.stringify(content);
      const isError = o.status === 'ERROR' || o.type === 'ERROR_MESSAGE' || (o.exit_code != null && o.exit_code !== 0);
      items.push({
        kind: 'tool_result',
        role: 'user',
        text: content,
        tool_use_id: toolUseId,
        is_error: isError,
        ts,
        uuid: `step-${step}-result`
      });
      continue;
    }
  }

  return items;
}

// ---------------------------------------------------------------------------
// GRAMMAR v2 (contract/transcript/transcript.v2.schema.json) — ADDITIVE.
// items[] keeps the v1 flat shapes; v2 adds: envelope grammar_version,
// canonical server-computed `summary` on tool_use (folds the web toolSummary()
// + iOS summarize() split into ONE place), size-capped `input` + `input_full`,
// `is_system` on the spawn brief, and server-paired `render_items[]`.
// Fixture conformance: contract/transcript/tests (TS) + watch-approval-app
// Tests/Conformance (Swift) run the SAME fixtures.
// ---------------------------------------------------------------------------

export const GRAMMAR_VERSION = 2;

const INPUT_STRING_CAP = 2000;
const TRUNC_MARK = '…[truncated]';
const SUMMARY_CAP = 120;

function shortPath(p: string): string {
  if (!p) return '';
  return p.replace(/^\/home\/[^/]+\//, '~/').replace(/^\/Users\/[^/]+\//, '~/');
}

// THE canonical one-line tool summary (schema ToolUseItem.summary). Computed
// from the FULL (pre-cap) input. Clients render it verbatim — never re-derive.
function toolSummary(tool: string, input: Record<string, unknown>): string {
  const s = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v));
  let out: string;
  switch (tool) {
    case 'Bash':
    case 'run_command':
    case 'run_terminal_command':
      out = s(input.CommandLine || input.command || '');
      break;
    case 'Read':
    case 'view_file':
    case 'read_file':
      out = shortPath(s(input.AbsolutePath || input.file_path || input.path || input.target_file || ''));
      break;
    case 'Edit':
    case 'replace_file_content':
    case 'Write':
    case 'write_to_file':
    case 'search_replace':
      out = shortPath(s(input.TargetFile || input.file_path || ''));
      break;
    case 'NotebookEdit':
      out = shortPath(s(input.notebook_path || ''));
      break;
    case 'Glob':
    case 'find_by_name':
      out = `${s(input.Pattern || input.pattern || '')}${input.SearchDirectory ? ' in ' + shortPath(s(input.SearchDirectory)) : ''}`;
      break;
    case 'Grep':
    case 'grep_search':
    case 'grep':
      out = `${s(input.Query || input.pattern || '')}${input.SearchPath || input.path ? ' in ' + shortPath(s(input.SearchPath || input.path)) : ''}`;
      break;
    case 'Task':
    case 'Agent':
    case 'invoke_subagent':
    case 'manage_subagents':
      out = s(input.Description || input.description || input.toolSummary || input.subagent_type || '');
      break;
    case 'WebFetch':
    case 'read_url_content':
    case 'WebSearch':
    case 'search_web':
      out = s(input.Url || input.url || input.Query || input.query || '');
      break;
    case 'Skill':
      out = s(input.skill || input.command || '');
      break;
    case 'send_message':
      out = `To ${s(input.Recipient || '')}: ${s(input.Message || '').slice(0, 60)}`;
      break;
    default: {
      const summary = input.toolSummary || input.toolAction;
      if (summary) { out = s(summary); break; }
      const first = Object.values(input)[0];
      out = first != null ? s(first) : '';
    }
  }
  return out.split('\n')[0].slice(0, SUMMARY_CAP);
}

// Full multi-line text of the tool args (schema ToolUseItem.input_full):
// per key in arg order — single-line values as `key: value`, multi-line values
// as a `── key ──` block. Emitted only when capInput() truncated something.
function inputFullText(input: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(input)) {
    const s = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
    if (s == null) continue;
    parts.push(s.includes('\n') ? `── ${k} ──\n${s}` : `${k}: ${s}`);
  }
  return parts.join('\n');
}

// Cap top-level string values at INPUT_STRING_CAP chars.
function capInput(input: Record<string, unknown>): { capped: Record<string, unknown>; truncated: boolean } {
  let truncated = false;
  const capped: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(input)) {
    if (typeof v === 'string' && v.length > INPUT_STRING_CAP) {
      capped[k] = v.slice(0, INPUT_STRING_CAP) + TRUNC_MARK;
      truncated = true;
    } else {
      capped[k] = v;
    }
  }
  return { capped, truncated };
}

// v2 enrichment pass over the flat v1 items (mutating a fresh copy):
// summary (from FULL input) -> cap input -> input_full, and is_system on the
// first user text (spawn brief rule shared with web buildRenderList).
function enrichItems(items: any[]): any[] {
  let firstUserSeen = false;
  return items.map((it) => {
    if (it.kind === 'tool_use') {
      const input = (it.input && typeof it.input === 'object') ? it.input : {};
      const summary = toolSummary(it.tool || '', input);
      const { capped, truncated } = capInput(input);
      const out: any = { kind: it.kind, role: it.role, tool: it.tool, input: capped, summary };
      if (truncated) out.input_full = inputFullText(input);
      // Same reason as withTs(): an own `ts` holding undefined is not an absent `ts`.
      out.id = it.id; out.uuid = it.uuid;
      withTs(out, it.ts);
      return out;
    }
    if (it.kind === 'text' && it.role === 'user' && !firstUserSeen) {
      firstUserSeen = true;
      const text = (it.text || '').trim();
      if (text.length > 800 || /^You are\b/.test(text)) {
        return withTs({ kind: it.kind, role: it.role, text: it.text, is_system: true, uuid: it.uuid }, it.ts);
      }
    }
    return it;
  });
}

// Server-side pairing (schema render_items[]): tool_result attached under its
// tool_use, empties dropped, order preserved. Replaces the duplicated web
// buildRenderList() / iOS buildRows() logic — clients do node -> view only.
export function buildRenderItems(items: any[]): any[] {
  const resultById = new Map<string, { text: string; is_error: boolean }>();
  for (const it of items) {
    if (it.kind === 'tool_result' && it.tool_use_id) {
      resultById.set(it.tool_use_id, { text: it.text || '', is_error: !!it.is_error });
    }
  }
  const nodes: any[] = [];
  let i = 0;
  for (const it of items) {
    const key = it.uuid ? `${it.uuid}:${i}` : `n${i}`;
    i++;
    if (it.kind === 'tool_result') continue; // consumed via pairing
    if (it.kind === 'text') {
      const text = (it.text || '').trim();
      if (!text) continue;
      if (it.role === 'user') {
        const node: any = { kind: 'user', text };
        if (it.is_system) node.is_system = true;
        if (it.queued) node.queued = true;
        if (it.ts !== undefined) node.ts = it.ts;
        node.key = key;
        nodes.push(node);
      } else {
        const node: any = { kind: 'assistant', text };
        if (it.ts !== undefined) node.ts = it.ts;
        node.key = key;
        nodes.push(node);
      }
      continue;
    }
    if (it.kind === 'thinking') {
      const text = (it.text || '').trim();
      if (!text) continue;
      const node: any = { kind: 'thinking', text };
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = key;
      nodes.push(node);
      continue;
    }
    if (it.kind === 'queued_batch') {
      // B2 processed-batch div (queued-native-render): passthrough as a render node.
      const node: any = { kind: 'queued_batch', count: it.count ?? (Array.isArray(it.entries) ? it.entries.length : 0), entries: it.entries || [] };
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = it.key || key;
      nodes.push(node);
      continue;
    }
    if (it.kind === 'tool_use') {
      const paired = it.id ? resultById.get(it.id) : undefined;
      const node: any = { kind: 'tool', tool: it.tool || '', summary: it.summary ?? '', input: it.input || {} };
      if (it.input_full !== undefined) node.input_full = it.input_full;
      if (paired) { node.result = paired.text; node.is_error = paired.is_error; }
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = key;
      nodes.push(node);
    }
  }
  return nodes;
}

const GROK_RESULT_CAP = 4000;
const GROK_TEXT_UPDATES = new Set(['user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk']);

function grokIso(rec: any): string | undefined {
  const ms = rec?.params?._meta?.agentTimestampMs;
  const sec = rec?.timestamp;
  const n = typeof ms === 'number' ? ms : (typeof sec === 'number' ? sec * 1000 : NaN);
  if (!Number.isFinite(n) || n <= 0) return undefined;
  const iso = new Date(n).toISOString();
  return Number.isNaN(Date.parse(iso)) ? undefined : iso;
}

/** updates.jsonl user chunks are already the operator's text. chat_history.jsonl
 *  wraps that text in harness blocks; unwrap <user_query> if a chunk ever carries it. */
function grokUserText(raw: string): { text: string; system: boolean } {
  const q = raw.match(/<user_query>\s*([\s\S]*?)\s*<\/user_query>/);
  if (q && q[1].trim()) return { text: q[1].trim(), system: false };
  const t = raw.trim();
  if (/^<(system-reminder|user_info|rules|available_skills|memory-context|dangerous_actions)\b/.test(t)) {
    return { text: t, system: true };
  }
  return { text: t, system: false };
}

function capGrokResult(text: string): string {
  const t = text.trim();
  if (t.length <= GROK_RESULT_CAP) return t;
  return t.slice(0, GROK_RESULT_CAP) + TRUNC_MARK;
}

function grokToolName(upd: any): string {
  const meta = upd?._meta?.['x.ai/tool'];
  if (meta && typeof meta.name === 'string' && meta.name) return meta.name;
  return typeof upd?.title === 'string' ? upd.title : '';
}

function grokToolInput(upd: any): Record<string, unknown> {
  const raw = upd?.rawInput;
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  return {};
}

function grokContentText(content: any): string {
  if (!Array.isArray(content)) return '';
  const parts: string[] = [];
  for (const c of content) {
    if (!c || typeof c !== 'object') continue;
    if (c.type === 'diff') {
      parts.push(typeof c.path === 'string' && c.path ? `edited ${c.path}` : 'edited');
    } else if (c.type === 'content' && c.content && typeof c.content.text === 'string') {
      parts.push(c.content.text);
    } else if (typeof c.text === 'string') {
      parts.push(c.text);
    }
  }
  return parts.join('\n');
}

/** Map Grok `updates.jsonl` onto the flat item grammar. Adjacent text chunks
 *  coalesce (token streaming). Tool progress updates are dropped; a completed
 *  update, and a later task_completed for the same id, become the tool result. */
export function parseGrokUpdates(lines: string[]): any[] {
  type Rec = { su: string; upd: any; ts: string | undefined; uuid: string };
  const recs: Rec[] = [];
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const d = JSON.parse(line);
      const upd = d?.params?.update;
      if (!upd || typeof upd.sessionUpdate !== 'string') continue;
      recs.push({
        su: upd.sessionUpdate,
        upd,
        ts: grokIso(d),
        uuid: String(d?.params?._meta?.eventId || upd.toolCallId || ''),
      });
    } catch { /* torn final line while the seat is still writing */ }
  }

  const merged: Rec[] = [];
  for (const r of recs) {
    const prev = merged[merged.length - 1];
    if (prev && GROK_TEXT_UPDATES.has(r.su) && prev.su === r.su) {
      const a = typeof prev.upd?.content?.text === 'string' ? prev.upd.content.text : '';
      const b = typeof r.upd?.content?.text === 'string' ? r.upd.content.text : '';
      prev.upd = { ...prev.upd, content: { ...(prev.upd?.content || {}), type: 'text', text: a + b } };
      continue;
    }
    merged.push(r);
  }

  const items: any[] = [];
  for (const r of merged) {
    const upd = r.upd || {};
    if (r.su === 'user_message_chunk' || r.su === 'agent_message_chunk') {
      const raw = typeof upd?.content?.text === 'string' ? upd.content.text : '';
      const role = r.su === 'user_message_chunk' ? 'user' : 'assistant';
      const parsed = role === 'user' ? grokUserText(raw) : { text: raw.trim(), system: false };
      if (!parsed.text) continue;
      const it: any = withTs({ kind: 'text', role, text: parsed.text, uuid: r.uuid }, r.ts);
      if (parsed.system) it.is_system = true;
      items.push(it);
      continue;
    }
    if (r.su === 'agent_thought_chunk') {
      const text = typeof upd?.content?.text === 'string' ? upd.content.text.trim() : '';
      if (!text) continue;
      items.push(withTs({ kind: 'thinking', role: 'assistant', text, uuid: r.uuid }, r.ts));
      continue;
    }
    if (r.su === 'tool_call') {
      const id = String(upd.toolCallId || '');
      items.push(withTs({
        kind: 'tool_use', role: 'assistant',
        tool: grokToolName(upd),
        input: grokToolInput(upd),
        id,
        uuid: r.uuid || id,
      }, r.ts));
      continue;
    }
    if (r.su === 'tool_call_update' && upd.status === 'completed') {
      const id = String(upd.toolCallId || '');
      items.push(withTs({
        kind: 'tool_result', role: 'user',
        text: capGrokResult(grokContentText(upd.content)),
        tool_use_id: id,
        is_error: false,
        uuid: r.uuid || id,
      }, r.ts));
      continue;
    }
    if (r.su === 'task_completed') {
      const snap = upd.task_snapshot || {};
      const id = String(snap.task_id || '');
      if (!id) continue;
      const output = typeof snap.output === 'string' ? snap.output : '';
      const code = snap.exit_code;
      const text = capGrokResult([code == null ? '' : `exit: ${code}`, output].filter(Boolean).join('\n'));
      items.push(withTs({
        kind: 'tool_result', role: 'user',
        text,
        tool_use_id: id,
        is_error: typeof code === 'number' && code !== 0,
        uuid: r.uuid || id,
      }, r.ts));
    }
  }
  return items;
}

// Pure v2 envelope builder — the seam the conformance fixtures exercise.
// Detection mirrors the route: 'antigravity-cli' path hint OR content sniff.
export function normalizeTranscript(
  lines: string[],
  agentId: string,
  sessionId: string | null,
  limit = 150,
  antigravityHint = false,
  includeQueued = false,
): { agent_id: string; session_id: string | null; grammar_version: number; items: any[]; render_items: any[] } {
  const isAntigravity = antigravityHint ||
    lines.some((l) => {
      try {
        const parsed = JSON.parse(l);
        return parsed.type === 'USER_INPUT' || parsed.type === 'PLANNER_RESPONSE' || parsed.source === 'MODEL';
      } catch { return false; }
    });

  let items: any[] = [];
  if (isAntigravity) {
    items = normalizeAntigravity(lines);
  } else if (looksLikeGrok(lines)) {
    items = parseGrokUpdates(lines);
  } else if (looksLikeCodex(lines)) {
    // Detected from the lines themselves, NOT from a caller-passed hint: transcript-tail.ts (SSE)
    // and this route (poll) call normalizeTranscript independently, and a hint only one of them
    // passes is how the two lanes drift apart.
    items = parseCodexRollout(lines);
  } else {
    for (const line of lines) {
      if (!line.trim()) continue;
      try { items.push(...normalizeClaudeEntry(JSON.parse(line))); } catch { /* skip bad line */ }
    }
    // Whole-file pass: a queued message the log later recorded as a real user turn must
    // show ONCE, and the real turn is the one that wins.
    items = dedupeQueuedCommands(items);
  }
  let windowed = enrichItems(items).slice(-limit);
  // queued-native-render (P1): merge still-pending phone/watch held turns (B1) +
  // processed-batch divs (B2) from msg_store, interleaved by ts. POLL-ONLY —
  // the SSE tailer never sets includeQueued, so its monotonic-append delta stays
  // free of the ephemeral B1 synthetics (§3e/A1). Fail-open inside mergeQueuedItems.
  if (includeQueued) {
    windowed = mergeQueuedItems(windowed, agentId);
    // An agent message delivered mid-turn exists as BOTH a batch row and a queued_command.
    // The batch wins: it knows who sent it.
    windowed = dropQueuedCommandsCoveredByBatches(windowed);
  }
  windowed = settleIngestedQueuedCommands(windowed);
  return {
    agent_id: agentId,
    session_id: sessionId,
    grammar_version: GRAMMAR_VERSION,
    items: windowed,
    render_items: buildRenderItems(windowed),
  };
}

// GET /api/agents/:id/transcript?limit= -> v2 envelope
// { agent_id, session_id, grammar_version, items, render_items }
router.get('/:id/transcript', (req: Request, res: Response) => {
  const agentId = String(req.params.id);
  const limit = Math.min(parseInt(String(req.query.limit)) || 150, 500);
  // Resolution is INSIDE the try (gm ruling on DEC-1790239929422621): it touches the filesystem,
  // tmux and now a sqlite index, and this route is reachable on a PUBLIC ingress — an
  // unhandled throw here is a 500, not an empty pane.
  let path: string | null = null;
  let sid: string | null = null;
  try {
    ({ path, sid } = resolveTranscriptPath(agentId));
  } catch (err) {
    res.json({ agent_id: agentId, session_id: null, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: String(err) });
    return;
  }
  if (!path) {
    res.json({ agent_id: agentId, session_id: sid, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: 'no transcript resolved' });
    return;
  }
  try {
    const raw = readFileSync(path, 'utf-8');
    const lines = raw.split('\n');
    // NOTE: match the Gemini/Antigravity brain-log path via 'antigravity-cli'
    // ONLY. A bare 'brain' substring also matches Claude agents whose project
    // dir contains "brain" (e.g. second-brain-dev, cwd ~/repos/second-brain),
    // misclassifying their Claude JSONL as Antigravity and yielding 0 items.
    // Real Gemini brain logs always live under .gemini/antigravity-cli/brain/,
    // so 'antigravity-cli' + the content sniff in normalizeTranscript cover them.
    res.json(normalizeTranscript(lines, agentId, sid, limit, path.includes('antigravity-cli'), true));
  } catch (err) {
    res.json({ agent_id: agentId, session_id: sid, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: String(err) });
  }
});

// GET /api/agents/:id/transcript.md?last=<turns>&tools=full -> the conversation as a Markdown file
// (lib/transcriptMarkdown.ts): turns with times, tool calls one line each, thinking never, every
// piece through the secret redactor. Same agent-scope check as /transcript (router.param above).
router.get('/:id/transcript.md', (req: Request, res: Response) => {
  const agentId = String(req.params.id);
  const last = Math.max(1, Math.min(parseInt(String(req.query.last)) || DEFAULT_LAST_TURNS, 500));
  const tools = req.query.tools === 'full' ? 'full' : 'summary';
  let path: string | null = null;
  let sid: string | null = null;
  try {
    ({ path, sid } = resolveTranscriptPath(agentId));
  } catch {
    path = null;
  }
  if (!path) { res.status(404).type('text/plain').send('No conversation found for this agent.'); return; }
  try {
    const lines = readFileSync(path, 'utf-8').split('\n');
    const { items } = normalizeTranscript(lines, agentId, sid, 2000, path.includes('antigravity-cli'), true);
    const reg = (getRegistry() as any)?.agents?.[agentId];
    const { markdown } = transcriptMarkdown(items as any[], {
      agent: agentId,
      model: typeof reg?.model === 'string' ? reg.model : null,
      exportedBy: principal(req)?.username ?? null,
      lastTurns: last,
      tools,
      exactSecrets: [readGatewayToken()].filter(Boolean),
    });
    const day = new Date().toISOString().slice(0, 10);
    const file = `${agentId.replace(/[^A-Za-z0-9._-]/g, '_')}-${day}.md`;
    res.status(200).type('text/markdown; charset=utf-8')
      .set('Content-Disposition', `attachment; filename="${file}"`)
      .set('Cache-Control', 'no-store')
      .send(markdown);
  } catch (err) {
    res.status(500).type('text/plain').send(`Could not export: ${String(err).slice(0, 200)}`);
  }
});

export default router;
