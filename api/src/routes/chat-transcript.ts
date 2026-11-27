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
import { readFileSync, existsSync, readdirSync, statSync, openSync, readSync, closeSync, realpathSync } from 'fs';
import { join, basename, relative, sep } from 'path';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { LIVE_CARDS, mergeQueuedItems, queuedStateSig } from '../services/queued-merge.js';
import { tagAudience, tagSynthetics, openerKind, wakeKind, mailWake, isRouterMail, promoteWake, promoteEnvelopeRest, NEWEST_DRAWN } from '../lib/audience.js';
import { hasReactionWake, stripReactionWake, silentReply } from '../lib/reaction-wake-text.js';
import { refable, partAt, messageRef } from '../lib/messageRef.js';
import { storeRules, applyStoreRules, compactionNotices, mergeNotices, storePath } from '../services/conversation-store.js';
import { listAgentCalls, mergeCallMarkers, readHandoffs, showHandoff, CALL_ID_RE } from '../lib/agentCalls.js';
import { buildCallContext, readJournals, clampBudget, shawTz, operatorName, redact } from '../lib/callContext.js';
import { isLoopback } from '../lib/access-log.js';
import { redactSecrets } from '../lib/redactSecrets.js';
import { readSeatSettings } from '../lib/seatSettings.js';
import { homedir } from 'os';
import Database from 'better-sqlite3';
import { agentScopeParam, canPrincipalSeeAgent, defaultResolveTags } from '../lib/agent-scope.js';
import { expandChipDodge } from '../lib/chipDodge.js';
import { usageFromClaude, usageFromCodex, addUsage, type TokenUsage } from '../lib/tokenUsage.js';
import { sessionOwners, predecessorOf, lineageChainOf, getCanonicalAgents, identityStoreSig } from '../services/identity-store-reader.js';
import { cachedLog, cachedLogTail, logRange, type LogParser } from '../services/transcript-log-cache.js';
import { firstPage } from '../services/transcript-first-page-cache.js';
import { callsDir } from '../lib/agentCalls.js';
import { dataDir } from '../lib/config.js';
import { paneInfo, forgetPane, childPids, procArgv, liveGeminiSid } from '../lib/paneProbe.js';
import { transcriptMarkdown, DEFAULT_LAST_TURNS } from '../lib/transcriptMarkdown.js';
import { getRegistry } from '../services/state-reader.js';
import { principal } from '../lib/principal.js';
import { readGatewayToken } from '../lib/gateway-token.js';
import { findImagePaths, imagePathsInToolUse } from '../lib/imageRefs.js';
import { defaultImageRefsFor, windowFor, type RefsFor } from '../lib/mediaStore.js';
import { stampReplyTo } from '../lib/reply-to-store.js';

const router = Router();
// Every /:id route on this router is scoped to the caller's principal — the same rule GET /
// applies (lib/agent-scope.ts). Registered as a param handler so a /:id route added later is
// scoped without anyone remembering to; an out-of-scope id gets the same 404 as an unknown one.
router.param('id', agentScopeParam);

const HOME = process.env.HOME || homedir();
const ORCH_DIR = dataDir();
const CLAUDE_PROJECTS = join(HOME, '.claude', 'projects');
const GEMINI_BRAIN = join(HOME, '.gemini', 'antigravity-cli', 'brain');

// agent-sessions.json is read on every transcript request and is close to 1 MB on a busy install:
// parsing it cost ~5 ms per request, a third of a cache hit. The parsed document is kept while the
// file's inode, size and mtime are what they were; a rewrite is seen on the very next request.
let agentSessionsKept: { sig: string; doc: Record<string, any> } | null = null;
function loadAgentSessions(): Record<string, any> {
  const p = join(ORCH_DIR, 'state', 'agent-sessions.json');
  let sig: string;
  try { const st = statSync(p); sig = `${st.ino}:${st.size}:${st.mtimeMs}`; } catch { agentSessionsKept = null; return {}; }
  if (agentSessionsKept && agentSessionsKept.sig === sig) return agentSessionsKept.doc;
  try {
    const doc = JSON.parse(readFileSync(p, 'utf-8'));
    agentSessionsKept = { sig, doc };
    return doc;
  } catch { agentSessionsKept = null; return {}; }
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
// Resolution is TIERED and NEVER a cwd match. Tier 1 is the identity store: the canonical
// generation's session_id (services/identity-store-reader.ts) is the thread id spawn launched,
// joined to threads.id. Tier 2 is a POSITIVE seat-name declaration join on first_user_message —
// the same shape as findGeminiByDeclaration above — accepting the seat's own -gN alias. Congruence DEC-1790239929422621: every
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

/** How a codex seat was bound to its rollout: 'canonical' = the registry's canonical generation
 *  session_id joined to threads.id (the seat's own declaration, as spawn wrote it); 'declaration'
 *  = the first_user_message "You are <seat>" join. */
export type CodexTier = 'canonical' | 'declaration';

// Tier 1. The registry names the seat's live head: canonical.generation_id -> generations.session_id
// IS the codex thread id (spawn-agent.sh stamps the sid it launched with). That row needs no
// text match at all, so it is immune to how the rotation engine phrases the brief. A seat whose
// id is itself a canonical root (codex-arturo-g8 is one, beside codex-arturo) is read under its
// own id ONLY: its base lineage is another root, and falling back to it would hand g8 the g7
// rollout as 'canonical'. A sid the thread index does not know (rollout pruned) falls through to
// tier 2, where the exact-id regex still prefers the seat's own brief.
function findCodexByCanonical(agentId: string, baseId: string, db: any, orch?: string):
  { path: string; sid: string; tier: CodexTier } | null {
  const canon = getCanonicalAgents(orch);
  if (!canon) return null;
  const bySid = db.prepare('SELECT id, rollout_path FROM threads WHERE id = ? AND rollout_path IS NOT NULL');
  const root = canon[agentId] ? agentId : baseId;
  const sid = canon[root]?.session_id;
  if (!sid) return null;
  const r = bySid.get(sid) as { id: string; rollout_path: string } | undefined;
  return r?.rollout_path ? { path: r.rollout_path, sid: r.id, tier: 'canonical' } : null;
}

export function findCodexByDeclaration(agentId: string, dbPath = CODEX_STATE_DB, orch?: string):
  { path: string; sid: string; tier: CodexTier } | null {
  if (!existsSync(dbPath)) return null;
  const baseId = agentId.replace(/-(?:gen\d+|g\d+|next|\d+)$/, '');
  let db: any = null;
  try {
    db = new Database(dbPath, { readonly: true, fileMustExist: true });
    const canonical = findCodexByCanonical(agentId, baseId, db, orch);
    if (canonical) return canonical;

    // Tier 2: the declaration join. SQL prefilters on the seat's base name (LIKE is a prefilter
    // only; the regex below is the match) and there is NO LIMIT: the live index holds 570 user
    // threads and a seat whose brief sat past row 500 was unreachable.
    const rows = db.prepare(
      "SELECT id, rollout_path, first_user_message FROM threads "
      + "WHERE thread_source = 'user' AND first_user_message IS NOT NULL "
      + 'AND first_user_message LIKE ? '
      + 'ORDER BY updated_at_ms DESC',
    ).all(`%You are ${baseId}%`) as { id: string; rollout_path: string; first_user_message: string }[];
    // Whole-name match. \b is NOT enough: a hyphen is a non-word char, so /you are gm\b/ happily
    // matches "You are gm-watcher" and would hand this seat another seat's conversation. The
    // lookahead denies a following word char OR hyphen. The one hyphenated form that IS this seat
    // is its own generation alias: the rotation engine briefs every generation as
    // "You are codex-gm-g11. Read /tmp/agent-init-codex-gm-g11.md", so -gN / -genN is accepted
    // before the lookahead. "codex-gm-watcher" is still another seat and still rejected.
    const esc = baseId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const declares = new RegExp(`^\\s*you are ${esc}(?:-(?:g|gen)\\d+)?(?![\\w-])`, 'i');
    // A seat id that carries its own suffix ("codex-arturo-g8") prefers the brief that names it
    // exactly over a sibling generation's brief; a bare base id takes the newest alias.
    const escFull = agentId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const declaresExact = agentId === baseId ? null : new RegExp(`^\\s*you are ${escFull}(?![\\w-])`, 'i');
    let alias: { path: string; sid: string; tier: CodexTier } | null = null;
    for (const r of rows) {
      if (!r.rollout_path) continue;
      const msg = String(r.first_user_message);
      if (declaresExact?.test(msg)) return { path: r.rollout_path, sid: r.id, tier: 'declaration' };
      if (!alias && declares.test(msg)) {
        alias = { path: r.rollout_path, sid: r.id, tier: 'declaration' };
        if (!declaresExact) return alias;
      }
    }
    return alias;
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
/** Positive evidence of the Claude Code grammar: at least one well-formed user/assistant/attachment/system line with a
 *  uuid. Used only to decide whether a parse may be RESUMED (transcript-log-cache.ts); detection of the other grammars
 *  stays as it is. A log that so far holds only lines without a uuid (summary carries leafUuid; file-history-snapshot,
 *  queue-operation) stays on a full parse per change until its first user line: slower for those reads, never wrong. */
export function looksLikeClaude(lines: string[]): boolean {
  for (const l of lines) {
    if (!l.trim()) continue;
    try {
      const d = JSON.parse(l);
      if (d && (d.type === 'user' || d.type === 'assistant' || d.type === 'attachment' || d.type === 'system')
        && typeof d.uuid === 'string') return true;
    } catch { /* torn or foreign line */ }
  }
  return false;
}

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
  // turn_context carries the model each turn ran on; assistant items take the latest one. A
  // turn_context with no model makes it unknown again (absent), never the previous turn's.
  let codexModel: string | undefined;
  // token_count (event_msg) follows each model response: its last_token_usage goes on that
  // response's last agent item. A repeat with the same running total (Codex re-sends it with rate
  // limits) is not counted again. A call that drew nothing (encrypted reasoning only) carries its
  // tokens to the next agent item, so a sum over items stays the true total.
  let usedFrom = 0, lastTotal: number | undefined, carried: TokenUsage | undefined;
  for (const r of recs) {
    if (r.type === 'event_msg' && r.payload?.type === 'token_count') {
      const info = r.payload.info;
      const total = info?.total_token_usage?.total_tokens;
      const usage = usageFromCodex(info?.last_token_usage);
      if (!usage || (typeof total === 'number' && total === lastTotal)) continue;
      if (typeof total === 'number') lastTotal = total;
      let at = -1;
      for (let i = items.length - 1; i >= usedFrom; i--) if (items[i].role === 'assistant') { at = i; break; }
      if (at >= 0) {
        let u = addUsage(items[at].usage, usage);
        if (carried) u = addUsage(u, carried);
        items[at].usage = u;
        carried = undefined;
      } else carried = addUsage(carried, usage);
      usedFrom = items.length;
      continue;
    }
    if (r.type === 'turn_context') {
      const m = r.payload?.model;
      codexModel = typeof m === 'string' && m ? m : undefined;
      continue;
    }
    if (r.type !== 'response_item') continue;     // event_msg/token_usage_record/world_state = telemetry
    const before = items.length;
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
    if (codexModel) for (let i = before; i < items.length; i++) if (items[i].role === 'assistant') items[i].model = codexModel;
    // Tokens carried from a call that drew nothing go on the next agent item as soon as it exists
    // (its own count, if one follows, adds to them), so they never wait on a count that may not come.
    if (carried) {
      const i = items.findIndex((it, k) => k >= before && it.role === 'assistant');
      if (i >= 0) { items[i].usage = addUsage(items[i].usage, carried); carried = undefined; }
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
/** The tmux session a seat's pane lives in (its sessions record, else its id): what the live stream asks paneProbe
 *  about to tell a gone window from one that has not written its transcript yet. Null for an unsafe id. */
export function seatTmuxSession(agentId: string): string | null {
  if (!isSafeAgentId(agentId)) return null;
  const e = loadAgentSessions()[agentId] || {};
  return String(e.tmux_session || agentId);
}

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

  // 0c. Codex: the registry's canonical sid, else the seat's own declaration, in
  // ~/.codex/state_5.sqlite (findCodexByDeclaration, two tiers). Checked before the Claude
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

/** The stitched chat: what the poll route puts above a
 *  seat's current conversation, so a rotation keeps the thread. Only when `sid` is the current head
 *  of `agentId`'s lineage and the generation it took over from has a file this box can read:
 *  Claude by the lineage's project dir (else the row's conversation_path), Codex by its thread's
 *  rollout, Gemini by its brain log; a path a row names only when it is that session's own file
 *  (ownFile). Anything else: null, and the chat is the current file alone. */
export interface PreviousTranscript { path: string; antigravity: boolean; divider: Record<string, unknown> }

const SESSION_ID = /^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$/;   // a DB value going into a path: no separators
const CODEX_SESSIONS = join(HOME, '.codex', 'sessions');

/** Every candidate file is used only when, symlinks resolved, it is that session's OWN file under
 *  its runtime's own directory (`isOwn` sees the path relative to it), so a wrong or hostile row, or
 *  a link planted where the file should be, cannot hand this public route another seat's
 *  conversation, or any other file. */
function ownFile(p: string | null, root: string, isOwn: (rel: string) => boolean): string | null {
  if (!p) return null;
  try {
    const real = realpathSync(p);
    const base = realpathSync(root);
    return real.startsWith(base + sep) && isOwn(relative(base, real)) ? real : null;
  } catch {
    return null;
  }
}

function codexRolloutFor(sid: string, dbPath = CODEX_STATE_DB): string | null {
  if (!existsSync(dbPath)) return null;
  let db: any = null;
  try {
    db = new Database(dbPath, { readonly: true, fileMustExist: true });
    const r = db.prepare('SELECT rollout_path FROM threads WHERE id = ?').get(sid) as { rollout_path?: string } | undefined;
    return r && typeof r.rollout_path === 'string' && r.rollout_path ? r.rollout_path : null;
  } catch {
    return null;
  } finally {
    try { db?.close(); } catch {}
  }
}

/** `<projects>/<any dir>/<sid>.jsonl`, the session's own file (ownFile), newest first if a copy
 *  sits in more than one project dir. */
let projectScans = 0;
/** How many any-project-dir scans ran (tests: the poll path must not scan when it never reads). */
export function claudeProjectScans(): number { return projectScans; }

function claudeFileAnywhere(sid: string, isOwn: (rel: string) => boolean): string | null {
  projectScans += 1;
  let dirs: string[];
  try { dirs = readdirSync(CLAUDE_PROJECTS); } catch { return null; }
  const hits: { p: string; m: number }[] = [];
  for (const d of dirs) {
    const own = ownFile(join(CLAUDE_PROJECTS, d, `${sid}.jsonl`), CLAUDE_PROJECTS, isOwn);
    if (own) { try { hits.push({ p: own, m: statSync(own).mtimeMs }); } catch { /* gone */ } }
  }
  hits.sort((a, b) => b.m - a.m);
  return hits.length ? hits[0].p : null;
}

interface Predecessor { p: NonNullable<ReturnType<typeof predecessorOf>>; divider: Record<string, unknown> }

/** The cheap half (one registry read): whether `sid` took over from a generation of this agent's
 *  lineage, and the divider for it. No file is looked for here. */
function predecessorFor(agentId: string, sid: string | null): Predecessor | null {
  if (!sid) return null;
  const p = predecessorOf(sid);
  if (!p || lineageOf(p.root) !== lineageOf(agentId) || !SESSION_ID.test(p.from_session)) return null;
  return {
    p,
    divider: {
      kind: 'rotation', role: 'system', uuid: `rot:${sid}`, ts: p.promoted_at,
      from_generation: p.from_generation, to_generation: p.to_generation,
      from_session: p.from_session, to_session: sid,
    },
  };
}

/** The predecessor's own log file, or null. */
function previousFilePath({ p }: Predecessor): string | null {
  return sessionFilePath(p.from_session, p.cwd, p.conversation_path);
}

/** A past generation's own log file, or null. */
function sessionFilePath(from: string, cwd: string | null, conversationPath: string | null): string | null {
  const claudeOwn = (rel: string) => basename(rel) === `${from}.jsonl`;
  const geminiLog = join(from, '.system_generated', 'logs', 'transcript.jsonl');
  // Tried in order, each only when the ones before found nothing (the directory scan is the last
  // Claude resort): the lineage's project dir, the path the row names, then ANY project dir (a
  // generation may have run in another directory than the lineage's, and its row may name no
  // path), then Codex's rollout and Gemini's brain log.
  const candidates: (() => string | null)[] = [
    () => cwd ? ownFile(join(CLAUDE_PROJECTS, String(cwd).replace(/[/.]/g, '-'), `${from}.jsonl`), CLAUDE_PROJECTS, claudeOwn) : null,
    () => ownFile(conversationPath, CLAUDE_PROJECTS, claudeOwn),
    () => claudeFileAnywhere(from, claudeOwn),
    () => ownFile(codexRolloutFor(from), CODEX_SESSIONS, (rel) => /^rollout-.*\.jsonl$/.test(basename(rel)) && basename(rel).endsWith(`-${from}.jsonl`)),
    () => ownFile(join(GEMINI_BRAIN, geminiLog), GEMINI_BRAIN, (rel) => rel === geminiLog),
  ];
  for (const c of candidates) { const path = c(); if (path) return path; }
  return null;
}

export function resolvePreviousTranscript(agentId: string, sid: string | null): PreviousTranscript | null {
  const pred = predecessorFor(agentId, sid);
  const path = pred ? previousFilePath(pred) : null;
  return pred && path ? { path, antigravity: path.includes('antigravity-cli'), divider: pred.divider } : null;
}

// ---- The whole lineage in one chat --------------------------------------------------------------
// A seat's chat is every generation of its lineage whose log this box can read, oldest first,
// with ONE quiet marker item (kind 'rotation') where one generation took over from the one before.
// The newest page is the poll route's window; older pages are read with `before=<cursor>`. Files
// are found and parsed only as far back as a page reaches, and each parse is kept per file
// (services/transcript-log-cache.ts) until the file changes.

/** One generation of the chat. `file` is looked for on first use only. */
interface Segment {
  session: string;                      // '' for a current log with no known session
  generation: number | null;
  promotedAt: string | null;
  root?: string | null;                 // the lineage root and generations row id: the key of the owed verdict
  generationId?: number | null;
  file: () => string | null;
  /** A past generation: look its file up again, past this segment's own memo (the kept first page
   *  asks, so a file that was missing when the page was built is seen when it appears). */
  refind?: () => string | null;
}

// Where each past generation's file is, so a page further back does not search the disk again
// for every generation it passes (a file not found is not looked for again for a while either).
const PAST_FILE_TTL_MS = 5 * 60_000;
const pastFiles = new Map<string, { path: string | null; at: number }>();

function pastFile(sid: string, cwd: string | null, conversationPath: string | null): string | null {
  const hit = pastFiles.get(sid);
  if (hit && Date.now() - hit.at < PAST_FILE_TTL_MS) {
    // A found file is used only while it still passes the same check (moved or replaced: look again).
    if (hit.path === null) return null;
    try { if (realpathSync(hit.path) === hit.path) return hit.path; } catch { /* gone: look again */ }
  }
  const path = sessionFilePath(sid, cwd, conversationPath);
  pastFiles.set(sid, { path, at: Date.now() });
  return path;
}

/** Tests: forget where past generations' files were. */
export function forgetPastFiles(): void { pastFiles.clear(); }

/** The chat's generations, oldest first, the current log last. Past generations come only from
 *  this seat's own lineage, by session ids that are plain ids, each through ownFile. */
export function chatSegments(agentId: string, sid: string | null, currentPath: string): Segment[] {
  let chain: ReturnType<typeof lineageChainOf> = null;
  if (sid) { try { chain = lineageChainOf(sid); } catch { chain = null; } }
  const head = chain && lineageOf(chain.root) === lineageOf(agentId) ? chain : null;
  const last = head?.links[head.links.length - 1];
  const current: Segment = {
    session: sid ?? '', generation: last?.generation ?? null, promotedAt: last?.promoted_at ?? null,
    root: head?.root ?? null, generationId: last?.id ?? null,
    file: () => currentPath,
  };
  if (!head) return [current];
  const past: Segment[] = [];
  for (const l of head.links.slice(0, -1)) {
    const from = l.session_id;
    if (!from || !SESSION_ID.test(from) || from === sid) continue;
    let found: string | null | undefined;
    past.push({
      session: from, generation: l.generation, promotedAt: l.promoted_at, root: head.root, generationId: l.id,
      file: () => {
        if (found === undefined) {
          found = pastFile(from, head.cwd, l.conversation_path);
          if (found === currentPath) found = null;
        }
        return found;
      },
      refind: () => pastFile(from, head.cwd, l.conversation_path),
    });
  }
  return [...past, current];
}

// The warm-up a rotation runs in the incoming generation before it takes over (the handoff and the
// model's first answer) is not part of the conversation. The supervisor marks it in
// <data dir>/state/headless/<seat>/warmup.json:
//   { "sessions": { "<session id>": { "uuids": ["<log uuid>", ...], "through": "<log uuid>" } } }
// `uuids`: every item with one of these uuids, or belonging to the exchange one of them opened, is
// left out. `through`: everything in that session's log up to and including the last item with
// this uuid is left out. Either may be given alone. Written before the generation takes over, so
// nothing has been shown yet. Fail-open: no file, a bad file, nothing left out.
export interface WarmupMark { uuids: Set<string>; through: string | null }

export function warmupMarkPath(agentId: string, root?: string): string | null {
  if (!isSafeAgentId(agentId)) return null;
  try {
    return join(root ?? dataDir(), 'state', 'headless', agentId, 'warmup.json');
  } catch {
    return null;
  }
}

const warmupCache = new Map<string, { sig: string; doc: any }>();

function warmupDoc(agentId: string): { sig: string; doc: any } {
  const p = warmupMarkPath(agentId);
  if (!p) return { sig: '', doc: null };
  let sig = '';
  try { const st = statSync(p); sig = `${st.size}:${st.mtimeMs}`; } catch { return { sig: '', doc: null }; }
  const hit = warmupCache.get(p);
  if (hit && hit.sig === sig) return hit;
  let doc: any = null;
  try { doc = JSON.parse(readFileSync(p, 'utf-8')); } catch { doc = null; }
  const got = { sig, doc };
  warmupCache.set(p, got);
  return got;
}

/** The mark for one session of `agentId`, with a signature that changes when the file does. */
export function warmupMarkFor(agentId: string, session: string | null): { sig: string; mark: WarmupMark | null } {
  if (!session) return { sig: '', mark: null };
  const { sig, doc } = warmupDoc(agentId);
  const s = doc && typeof doc === 'object' && doc.sessions && typeof doc.sessions === 'object' ? doc.sessions[session] : null;
  if (!s || typeof s !== 'object') return { sig: '', mark: null };
  const uuids = new Set<string>(Array.isArray(s.uuids) ? s.uuids.filter((u: unknown) => typeof u === 'string' && u) : []);
  const through = typeof s.through === 'string' && s.through ? s.through : null;
  if (!uuids.size && !through) return { sig: '', mark: null };
  return { sig, mark: { uuids, through } };
}

export function dropWarmup(items: readonly any[], mark: WarmupMark | null): any[] {
  if (!mark) return items as any[];
  let start = 0;
  if (mark.through) {
    for (let i = items.length - 1; i >= 0; i--) if (items[i]?.uuid === mark.through) { start = i + 1; break; }
  }
  const out: any[] = [];
  for (let i = start; i < items.length; i++) {
    const it = items[i];
    if (mark.uuids.size && ((it?.uuid && mark.uuids.has(String(it.uuid))) || (it?.exchange && mark.uuids.has(String(it.exchange))))) continue;
    out.push(it);
  }
  return out;
}

/** A generation's items as the chat shows them, plus the first position of each uuid in them, and the text of each
 *  user message by uuid (what carriedOver compares). `start`/`end`: the bytes of the log this view was read from
 *  (start 0 and end null = the whole file; a tail read has start > 0; a cursor page's middle read has both). */
interface SegmentView {
  items: readonly any[]; firstAt: Map<string, number>; userTexts: Map<string, string>;
  start: number; end: number | null;
  /** The view reaches the file's end (a tail or whole read always does; a middle read may not). */
  eof: boolean;
}

// ---- Tail-first pages ---------------------------------------------------------------------------
// A page costs the bytes of the page: the newest page reads the last TAIL_WANT0 bytes of the log (a line start at or
// under it), an older page reads a window around its cursor, and either is widened until it holds the page AND a
// PERSON turn opener older than the page's oldest item (the audience pass carries the person's exchange across the
// machine turns after it, and only a person turn makes the newest reply fold) AND, for a cursor page, a person opener
// newer than the cursor that is a real turn and not the real turn of a queued copy (isDrainedOpener: the turn the
// cursor is in ends there, with its closing reply, and every queued copy before the cursor has its real turn, written
// after it, inside the window to be deduped against). So the whole-array passes (audience, queued dedupe, wake
// replies) see the same turns the whole-file build does. A widening is sized from the lines read so far (bytesFor:
// the rest of the page, or one more opener's worth) and the cache parses only the lines it adds, so a log of 100 KiB
// lines costs about the bytes of its page too. The cache keeps the newest page's chunk and grows it by appends or by
// widening; a cursor page's window is kept once and grown either way. Never on the whole file unless the file is that
// small (or has no person turn), a cursor is in an old form, or the grammar cannot be read from a chunk.
let TAIL_WANT0 = 256 * 1024;
/** Bytes read past a cursor's line: the turn after it, so a queued copy and the real turn that landed after it meet. */
const TAIL_LOOKAHEAD = 64 * 1024;
/** Tests: TAIL_WANT0 = Infinity is the whole-file build (the oracle every tail-first page must equal). */
export function tailFirstForTests(o: { want0?: number }): void {
  if (o.want0 !== undefined) TAIL_WANT0 = o.want0;
}

/** What of a log a view reads: the last `want` bytes, a middle window, or the whole file. */
type Range = { want: number } | { lo: number; hi: number } | 'whole';

const isPartial = (v: SegmentView | null | undefined): boolean => !!v && v.start > 0;

/** The same range reaching further back (never past the file's start): by `need` more bytes (at least TAIL_WANT0)
 *  when the caller can size the rest of its page from the lines it has seen, else twice as far. The cache parses
 *  only the lines a widening adds (transcript-log-cache.ts prefixed/extended), so a small step costs its own bytes. */
function widened(r: Range, need = 0): Range {
  if (r === 'whole') return r;
  if ('want' in r) {
    if (!Number.isFinite(r.want)) return r;
    return { want: need > 0 ? r.want + Math.max(need, TAIL_WANT0) : r.want * 2 };
  }
  const back = need > 0 ? Math.max(need, TAIL_WANT0) : Math.max(r.hi - r.lo, TAIL_WANT0);
  return { lo: Math.max(0, r.lo - back), hi: r.hi };
}

/** The bytes a view still needs for `missing` more of what it holds `have` of (items, or person openers), from the
 *  bytes per one in the view, and a quarter over. 0 when there is nothing to size from (the caller doubles). */
function bytesFor(r: Range, have: number, missing: number): number {
  if (r === 'whole' || have <= 0 || missing <= 0) return 0;
  const bytes = 'want' in r ? r.want : r.hi - r.lo;
  if (!Number.isFinite(bytes) || bytes <= 0) return 0;
  return Math.ceil((bytes / have) * missing * 1.25);
}

/** A middle range reaching twice as far forward. */
function widenedForward(r: Range): Range {
  if (r === 'whole' || 'want' in r) return r;
  return { lo: r.lo, hi: r.hi + Math.max(r.hi - r.lo, TAIL_LOOKAHEAD) };
}

const isUserText = (it: any): boolean => it?.kind === 'text' && it.role === 'user' && !!it.uuid;
const isTurnOpener = (it: any): boolean => it?.kind === 'text' && it.role === 'user' && !it.mid_turn;
/** A turn the operator opened (lib/audience.ts): what the audience pass anchors an exchange on. */
const isPersonOpener = (it: any): boolean => isTurnOpener(it) && openerKind(it) === 'person';
/** On a real user turn: a queued copy of it was dropped (dedupeQueuedCommands). Not enumerable, so no copy or
 *  comparison of the item carries it. */
const CONSUMED_QUEUED = Symbol('consumedQueued');
/** A person opener past which the queue the operator filled mid-turn is drained: a real turn (not a queued copy,
 *  which the real turn written after it dedupes), and not one such real turn (CONSUMED_QUEUED: more may follow it,
 *  one per copy). The harness writes every queued message as a real turn before any message typed after them, so the
 *  first opener that is neither ends the turn a cursor is in, with every copy before the cursor matched. */
const isDrainedOpener = (it: any): boolean => isPersonOpener(it) && !it.queued && it[CONSUMED_QUEUED] !== true;
/** A turn opener the audience pass opened an exchange on (its own exchange): what a chunk needs below a page for its
 *  tags to be the whole log's. Read from the tag, not the text: a wake's line is emptied after tagging (hideWakes), and
 *  an emptied queue digest would read as a person's words. */
const anchorsExchange = (it: any): boolean => isTurnOpener(it) && !!it.uuid && it.exchange === String(it.uuid);

function segmentView(agentId: string, seg: Segment, range: Range = 'whole'): SegmentView | null {
  const path = seg.file();
  if (!path) return null;
  const parser = logParser(path.includes('antigravity-cli'));
  let log: { items: readonly any[]; memo: Map<string, unknown>; start: number; end?: number; eof?: boolean };
  try {
    if (range === 'whole') log = cachedLog(path, parser);
    else if ('want' in range) log = cachedLogTail(path, parser, range.want);
    else log = logRange(path, parser, range.lo, range.hi);
  } catch { return null; }
  const end = log.end === undefined ? null : log.end;
  const { sig, mark } = warmupMarkFor(agentId, seg.session || null);
  // What a session only saw (the earlier-conversation block, seed rows) is never shown (services/conversation-store.ts).
  const rules = storeRules(seg.session || null);
  const prep = (xs: readonly any[]) => applyStoreRules(dropWarmup(xs, mark), rules);
  // Was anything owed to the operator when this generation was promoted? The capsule's verdict for this generation
  // (owedVerdict), read only when this log has a promote wake to settle. Never the previous log.
  const open = hasPromoteWake(log.items) ? owedVerdict(seg.root ?? null, seg.generationId ?? null, dataDir(), seg.promotedAt) : null;
  // From the generation's promotion on (liveFrom): its hold phase as a green is never shown, on any page. A chunk of the
  // log is cut at the takeover's line, found once on the log's head (liveFromFile).
  const cutSig = `${sig}:${rules.sig}`;
  const file = { path, parser, start: log.start, eof: log.eof ?? true };
  const cut = cutOfView(file, seg.promotedAt, prep, cutSig);
  const key = `view:${seg.session}:${sig}:${rules.sig}:${log.start}:${end ?? ''}:${seg.promotedAt ?? ''}:${open}:${cutKey(cut)}`;
  const memo = log.memo.get(key) as SegmentView | undefined;
  if (memo) return memo;
  const items = liveFromFile(prep(log.items), file, cut, seg.promotedAt, open, prep, cutSig);
  const firstAt = new Map<string, number>();
  const userTexts = new Map<string, string>();
  items.forEach((it, i) => {
    if (it?.uuid && !firstAt.has(String(it.uuid))) firstAt.set(String(it.uuid), i);
    if (isUserText(it) && !userTexts.has(String(it.uuid))) userTexts.set(String(it.uuid), String(it.text ?? ''));
  });
  // A chunk that starts at or before the takeover holds the whole chat of this generation: nothing older is shown, so
  // the view starts at the log's start (a page never widens below it).
  const whole = cut === 'none' || (typeof cut === 'object' && cut.off >= log.start);
  const view: SegmentView = { items, firstAt, userTexts, start: whole ? 0 : log.start, end, eof: log.eof ?? true };
  // One view per file: an older warm-up signature is never asked for again.
  for (const k of [...log.memo.keys()]) if (k.startsWith('view:')) log.memo.delete(k);
  log.memo.set(key, view);
  return view;
}

function rotationMarker(older: Segment, newer: Segment): Record<string, unknown> {
  const m: Record<string, unknown> = {
    kind: 'rotation', role: 'system', uuid: `rot:${newer.session}`,
    from_generation: older.generation, to_generation: newer.generation,
    from_session: older.session, to_session: newer.session, audience: 'user',
  };
  if (newer.promotedAt) m.ts = newer.promotedAt;
  return m;
}

/** One chat position, newest to oldest: an item of a generation, or the marker above one. */
interface Unit { item: any; seg: number; at: number }   // at = -1: the marker above segment `seg`

/** The views of one window build: each generation's view over its range, made as the walk reaches it, and the WHOLE
 *  view of a generation when a pass needs every item of it (carriedOver). */
class Views {
  readonly ranges: Range[];
  private readonly made: (SegmentView | null | undefined)[];
  private readonly wholes: (SegmentView | null | undefined)[];
  constructor(private readonly agentId: string, readonly segs: Segment[], range: Range) {
    this.ranges = segs.map(() => range);
    this.made = segs.map(() => undefined);
    this.wholes = segs.map(() => undefined);
  }
  get(i: number): SegmentView | null {
    if (this.made[i] === undefined) this.made[i] = segmentView(this.agentId, this.segs[i], this.ranges[i]);
    return this.made[i] ?? null;
  }
  /** Every item of generation `i`: the whole file (a retired generation's is parsed once and kept). */
  whole(i: number): SegmentView | null {
    if (this.ranges[i] === 'whole') return this.get(i);
    if (this.wholes[i] === undefined) this.wholes[i] = segmentView(this.agentId, this.segs[i], 'whole');
    return this.wholes[i] ?? null;
  }
  /** The view of `i` read again over `range` (a widened one). */
  reset(i: number, range: Range): void {
    this.ranges[i] = range;
    this.made[i] = undefined;
  }
}

/** The positions in generation `s` that a newer generation carried over: a user message that was being answered when
 *  the CLI went down is written again, with the same uuid and text, into the rebuilt conversation, and answered there.
 *  The chat shows it once, at the newer copy. Only user messages, and only on the same text too, because some logs
 *  number their ids per file (a codex ordinal, an antigravity step) and those repeat between generations by chance.
 *  Positions keep their index, so a cursor naming a hidden entry still resolves. The newer generations are read
 *  WHOLE here: the carried copy sits at the start of the rebuilt conversation, which a tail read does not reach. */
function carriedOver(s: number, views: Views): Set<number> {
  const out = new Set<number>();
  const v = views.get(s);
  const count = views.segs.length;
  if (!v || !v.userTexts.size || s >= count - 1) return out;
  const newer: SegmentView[] = [];
  for (let j = s + 1; j < count; j++) { const w = views.whole(j); if (w?.userTexts.size) newer.push(w); }
  if (!newer.length) return out;
  v.items.forEach((it, k) => {
    if (!isUserText(it)) return;
    const id = String(it.uuid);
    const text = String(it.text ?? '');
    if (newer.some((w) => w.userTexts.get(id) === text)) out.add(k);
  });
  return out;
}

/** Everything older than position (seg, at), newest first; markers only between two generations
 *  that both show something. Views are made only as the walk reaches them. The walk STOPS at the start of a
 *  partial view (a tail or middle read): what is older is in the same file, below the chunk, and the caller widens. */
function* olderThan(views: Views, seg: number, at: number, dividers = false): Generator<Unit> {
  const segs = views.segs;
  const hiddenMemo = new Map<number, Set<number>>();
  const hidden = (i: number) => {
    let h = hiddenMemo.get(i);
    if (!h) { h = carriedOver(i, views); hiddenMemo.set(i, h); }
    return h;
  };
  // A generation shows something when its view has an item the newer generations did not carry over. A PARTIAL view
  // that shows nothing (a retired log ending in sidechain rows, or more seed than the chunk) is widened until it shows
  // something or reaches the file's start: a chunk that happens to hold nothing is not an empty generation.
  const shows = (i: number) => {
    for (let round = 0; round < 64; round++) {
      const v = views.get(i);
      if (!v) return false;
      if (v.items.length > hidden(i).size) return true;
      if (!isPartial(v)) return false;
      hiddenMemo.delete(i);
      views.reset(i, widened(views.ranges[i]));
    }
    return false;
  };
  let s = seg;
  let i = at;
  for (;;) {
    const v = views.get(s);
    if (i > 0 && v) {
      const h = hidden(s);
      for (let k = Math.min(i, v.items.length) - 1; k >= 0; k--) if (!h.has(k)) yield { item: v.items[k], seg: s, at: k };
    }
    if (isPartial(v)) return;                   // the rest of this generation is below the chunk
    // Above the start of segment s: the next older generation that shows anything, if any.
    let t = s - 1;
    while (t >= 0 && !shows(t)) t--;
    if (t < 0) return;
    // The marker only when asked for (operators: ?dividers=1): the chat reads as one conversation.
    if (dividers && i >= 0 && shows(s)) yield { item: rotationMarker(segs[t], segs[s]), seg: s, at: -1 };
    s = t;
    i = Number.POSITIVE_INFINITY;
  }
}

/** The cursor for a unit: the marker's own uuid; `<session>~@<line offset>~<uuid>` (or `~#<part in line>` for an item
 *  with no uuid) for an item whose line the cache located; `<session>~<uuid>` / `<session>~#<index>` otherwise (a
 *  whole-file grammar). Sessions are plain ids, so '~' never appears in one. */
function cursorOf(u: Unit, segs: Segment[]): string {
  if (u.at < 0) return String(u.item.uuid);
  const s = segs[u.seg].session;
  if (typeof u.item?.line_off === 'number') {
    return u.item?.uuid ? `${s}~@${u.item.line_off}~${u.item.uuid}` : `${s}~@${u.item.line_off}~#${u.item.line_part}`;
  }
  return u.item?.uuid ? `${s}~${u.item.uuid}` : `${s}~#${CURSOR_CUT}.${u.at}`;
}

/** The version of a position cursor (`~#<version>.<index>`): an index counts places in a generation's view, and the
 *  head cut (liveFrom) moved them, so a cursor from an older server is "unknown cursor", never the wrong items. A line
 *  cursor (`~@<offset>~...`) names its log line and is checked against the item there, so it needs no version: one
 *  that names an item the cut left out is not found. */
const CURSOR_CUT = 'h1';

/** A unit's item as a shallow copy (the cache keeps the originals), with the server's message ref
 *  `<session>~<uuid>#<part>` (lib/messageRef.ts) when it can carry one: a log item with a real uuid, in a generation
 *  with a known session. part is counted from the uuid's first position in that generation's view, NOT in the window,
 *  so a block has the same ref in the newest window, an older page and every re-read (a chunk never starts inside one
 *  uuid's lines). The line fields are the cache's: the envelope's items never carry them. */
function withRef(u: Unit, views: Views): any {
  const { line_off: _off, line_part: _part, ...copy } = u.item;
  const v = u.at >= 0 ? views.get(u.seg) : null;
  const session = views.segs[u.seg]?.session;
  if (v && session && refable(u.item)) {
    const from = v.firstAt.get(String(u.item.uuid)) ?? 0;
    copy.ref = messageRef(session, String(u.item.uuid), partAt(v.items, u.at, from));
  }
  return copy;
}

const OFFSET_CURSOR = /^@(\d+)~(.*)$/;

/** Where a cursor points: (segment, index), or null for one this chat does not have. An `@<offset>` cursor is looked
 *  for in a window just under its line (the view's range is set here; the caller widens it); the older forms read the
 *  whole file (cached), as they did; a bare uuid is looked for from the newest generation back. */
function cursorAt(views: Views, cursor: string): { seg: number; at: number } | null {
  const segs = views.segs;
  if (cursor.startsWith('rot:')) {
    const s = segs.findIndex((g) => g.session && `rot:${g.session}` === cursor);
    return s > 0 ? { seg: s, at: -1 } : null;
  }
  const tilde = cursor.indexOf('~');
  if (tilde >= 0) {
    const s = segs.findIndex((g) => g.session === cursor.slice(0, tilde));
    if (s < 0) return null;
    const rest = cursor.slice(tilde + 1);
    const m = OFFSET_CURSOR.exec(rest);
    if (m) {
      const off = Number(m[1]);
      if (!Number.isSafeInteger(off)) return null;
      const r = views.ranges[s];
      if (r === 'whole' || 'want' in r) views.reset(s, { lo: Math.max(0, off - TAIL_WANT0), hi: off + TAIL_LOOKAHEAD });
      const v = views.get(s);
      if (!v) return null;
      const want = m[2];
      const at = v.items.findIndex((it) => it?.line_off === off
        && (want.startsWith('#') ? it.line_part === Number(want.slice(1)) : String(it.uuid) === want));
      return at < 0 ? null : { seg: s, at };
    }
    if (views.ranges[s] !== 'whole') views.reset(s, 'whole');
    const v = views.get(s);
    if (!v) return null;
    // A position cursor names a place in the view as cut (CURSOR_CUT): one from before the head cut is unknown.
    if (rest.startsWith('#')) {
      const pos = new RegExp(`^#${CURSOR_CUT}\\.(\\d+)$`).exec(rest);
      if (!pos) return null;
      const n = Number(pos[1]);
      return n < v.items.length ? { seg: s, at: n } : null;
    }
    const at = v.firstAt.get(rest);
    return at === undefined ? null : { seg: s, at };
  }
  for (let s = segs.length - 1; s >= 0; s--) {
    if (views.ranges[s] !== 'whole') views.reset(s, 'whole');
    const at = views.get(s)?.firstAt.get(cursor);
    if (at !== undefined) return { seg: s, at };
  }
  return null;
}

/** Throws when this generation's own log cannot be opened (the route answers an error envelope, outside the fail-open
 *  views). A probe, not a parse: the page reads the log tail-first. */
function readableLog(path: string): void {
  closeSync(openSync(path, 'r'));
}

/** A window of the stitched chat: the newest `limit` positions (no cursor), or the `limit`
 *  positions just older than `before`. A page never starts inside one log line's items, so the
 *  next page, read from its `oldest_id`, neither repeats nor skips any. Read tail-first: see TAIL_WANT0. */
export function chatWindow(agentId: string, segs: Segment[], limit: number, before: string | null,
                           opts: { dividers?: boolean } = {}):
    { items: any[]; has_older: boolean; oldest_id: string | null; cursor_ts: string | null } | null {
  const views = new Views(agentId, segs, { want: TAIL_WANT0 });
  const last = segs.length - 1;
  // Each round reads over the ranges so far; a round that ran out of chunk, or whose page has no turn opener below
  // it in its chunk, widens that generation's range and builds again. Ranges only widen, so this ends.
  for (let round = 0; round <= 64; round++) {
    // The rounds ran out (sized steps too small for the gap to the next opener): the last round reads every
    // generation whole, which always ends.
    if (round === 64) for (let i = 0; i <= last; i++) if (views.ranges[i] !== 'whole') views.reset(i, 'whole');
    let from = { seg: last, at: Number.POSITIVE_INFINITY };
    let cursorTs: string | null = null;
    if (before !== null) {
      const at = cursorAt(views, before);
      if (!at) return null;
      from = at;
      const v = views.get(at.seg);
      cursorTs = at.at >= 0 && v ? (v.items[at.at]?.ts ?? null) : (segs[at.seg].promotedAt ?? null);
      // The turn the cursor is in must end inside the window (a person opener after the cursor, or the file's end):
      // its closing reply is the last assistant text of the turn, and a queued copy before the cursor is deduped
      // against the real turn written after it. A copy after the cursor is not that opener, and neither is the real
      // turn of a copy (isDrainedOpener): two copies with the page break between them would otherwise leave the
      // older one on the page (its real turn past the window) while the newer one closed the search.
      if (v && at.at >= 0 && !v.eof) {
        let closed = false;
        for (let k = at.at + 1; k < v.items.length && !closed; k++) if (isDrainedOpener(v.items[k])) closed = true;
        if (!closed) {
          views.reset(at.seg, widenedForward(views.ranges[at.seg]));
          continue;
        }
      }
    }
    const walk = olderThan(views, from.seg, from.at, opts.dividers === true);
    const units: Unit[] = [];
    let done = false;
    // Never one step past `limit` without a cursor: a window the current log fills must not look
    // for, or read, an older generation's file.
    while (units.length < limit) {
      const got = walk.next();
      if (got.done) { done = true; break; }
      units.push(got.value);
    }
    const stopSeg = units.length ? units[units.length - 1].seg : from.seg;
    if (done && isPartial(views.get(stopSeg))) {
      // The chunk ran out before the page was full: what is older is in the same file, below it. Sized from the
      // lines read so far: the rest of the page, one more line, and a quarter over.
      const r = views.ranges[stopSeg];
      const need = bytesFor(r, views.get(stopSeg)?.items.length ?? 0, limit - units.length + 2);
      views.reset(stopSeg, widened(r, need));
      continue;
    }
    let hasOlder = false;
    if (before === null) {
      // Without reading further (an older generation's file above all): the window is full and its
      // oldest position has log lines below it in its own generation (or below its chunk), or a
      // generation above it in the chain. The chain's file may turn out unreadable; the next page then says
      // has_older false. Keep one line's items together here too: a window that landed inside a line (an
      // assistant's text and its tool call share a uuid) takes the rest of that line, so oldest_id names a
      // whole line and the next page continues from it without losing an item. Only while the edge has
      // lines below it in its own segment (edge.at > 0): no step into an older generation's file.
      let edge = units.length >= limit ? units[units.length - 1] : undefined;
      while (edge && edge.at > 0 && edge.item?.uuid) {
        const next = walk.next();
        if (next.done) break;
        if (next.value.seg !== edge.seg || next.value.item?.uuid !== edge.item.uuid) break;
        units.push(next.value);
        edge = next.value;
      }
      hasOlder = !!edge && (edge.at > 0 || isPartial(views.get(edge.seg)) || edge.seg > 0);
    } else {
      let next = walk.next();
      // Keep one line's items together: take the rest of the oldest item's line too.
      const edge = units[units.length - 1];
      while (edge && edge.at >= 0 && edge.item?.uuid && !next.done
             && next.value.seg === edge.seg && next.value.item?.uuid === edge.item.uuid) {
        units.push(next.value);
        next = walk.next();
      }
      hasOlder = !next.done || (!!edge && edge.at >= 0 && isPartial(views.get(edge.seg)));
    }
    // The whole-array passes saw the turn the page starts in only if a PERSON opener lies below the page's oldest
    // item inside its chunk (or the chunk is the file's start). Otherwise: wider.
    const oldest = units[units.length - 1];
    if (oldest && oldest.at >= 0) {
      const v = views.get(oldest.seg);
      if (isPartial(v)) {
        let opened = false;
        for (let k = oldest.at - 1; k >= 0 && !opened; k--) if (anchorsExchange(v!.items[k])) opened = true;
        if (!opened) {
          // Sized by the person openers the chunk holds: about one more opener's worth of lines (or doubled).
          const r = views.ranges[oldest.seg];
          const need = bytesFor(r, v!.items.filter(anchorsExchange).length, 1);
          views.reset(oldest.seg, widened(r, need));
          continue;
        }
      }
    }
    units.reverse();
    return {
      items: units.map((u) => withRef(u, views)),  // shallow copies (the cache keeps the originals), with refs
      has_older: hasOlder,
      oldest_id: units.length ? cursorOf(units[0], segs) : null,
      cursor_ts: cursorTs,
    };
  }
  return null;
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
// The quiet page line the send route adds to what the agent receives (agent-send.ts pageContextLine): the agent reads
// it, the person never sees it as part of their message.
const PAGE_CONTEXT_TAG = /\s*<page-context>[\s\S]*?<\/page-context>/g;
// The same for the live-call line (agent-send.ts VOICE_CALL_LIVE_TAG): typed during a voice call.
const VOICE_CALL_LIVE_TAG_RE = /\s*<voice-call-(live|said)>[\s\S]*?<\/voice-call-\1>/g;
// A request the operator SAID on a voice call, handed to the agent's session (services/arturo/agent_call.py
// VOICE_SAID_TAG): the call's card holds it, so the chat does not show it as a typed message; the reply stays.
const VOICE_CALL_SAID = '<voice-call-said>';

export function stripPageContext(text: string): string {
  // The reaction wake (lib/reaction-wake-text.ts, services/reaction-wake.ts): the agent reads it, nobody said it.
  if (hasReactionWake(text)) text = stripReactionWake(text);
  if (text.includes('<voice-call-')) text = text.replace(VOICE_CALL_LIVE_TAG_RE, '').trim();
  return text.includes('<page-context>') ? text.replace(PAGE_CONTEXT_TAG, '').trim() : text;
}

export function sanitizeClaudeUserText(text: string): string {
  // A long message reached the pane as a one-line chip-dodge banner; show what was typed.
  let t = expandChipDodge(String(text ?? ''));
  t = t.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '');
  t = t.replace(/<local-command-caveat>[\s\S]*?<\/local-command-caveat>/g, '');
  t = t.replace(/<local-command-stdout>[\s\S]*?<\/local-command-stdout>/g, '');
  t = t.replace(/<local-command-stderr>[\s\S]*?<\/local-command-stderr>/g, '');
  const name = /<command-name>\s*([^<]*?)\s*<\/command-name>/.exec(t);
  // A model switch (`/model <id>`, typed by the dashboard's model picker or by hand) is not part of the conversation:
  // the thread already marks where the model changed ("Model changed", from the assistant entries' model).
  if (name && name[1].trim() === '/model') return '';
  // Leaving the TUI (`/exit`, or its alias `/quit`) is not conversation either: a headless seat's supervisor types it
  // on every Back to chat, a switch-on leaves one behind, and on a headless seat typed by hand it IS Back to chat.
  // Its reply ("Goodbye!") is local-command output, already dropped above.
  if (name && /^\/(exit|quit)$/.test(name[1].trim())) return '';
  // A person's /compact is not conversation either: our rotation runs it (the supervisor never passes it to the CLI)
  // and the chat shows the compaction notice instead. Only the command itself, as the whole message: a sentence that
  // mentions /compact is what someone said and stays.
  if (name && name[1].trim() === '/compact') return '';
  if (/^\/compact(?:[ \t][^\n]*)?$/.test(t.trim())) return '';
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
  // A background job that finished while the agent was mid-turn: kept as the harness's own line
  // (machine `task`, never operator speech), so clients draw it as a finished-work card instead of
  // losing it. `mid_turn` keeps it from opening a turn of its own (audience.ts), so the fold of the
  // turn it landed in is unchanged.
  if (originKind === 'task-notification') {
    const text = String(a.prompt || '').trim();
    if (!/^<task-notification>/.test(text)) return [];
    return [{ kind: 'text', role: 'user', text, ts: o.timestamp, uuid: o.uuid, machine: 'task', mid_turn: true }];
  }
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
 * Render a machine message ONCE when it reached the pane as a real turn.
 *
 * The approval loop delivers a card's answer twice by design: a durable msg_store row (the audit
 * and watchdog backstop) AND a typed line into the agent's pane. When the agent is idle that line
 * is a real user turn in the log, not a queued_command, so dropQueuedCommandsCoveredByBatches (which
 * only drops queued items) leaves it, and the row came back as a "1 queued message processed"
 * batch beside it: the same [DECISION ANSWERED ...] drawn twice. Measured on the live log: an answer
 * at 04:00:02 as a user turn, and the same body as a batch entry 13 s later.
 *
 * The TURN wins here, the reverse of the queued case: it is where the agent actually read the
 * message, and it carries the server's `machine` tag, so every client draws it as that machine
 * card. Only machine-tagged turns take part (never the operator's own words), bodies under 40
 * characters are not matched (not distinctive enough), and a batch left empty is dropped.
 */
export function dropBatchEntriesCoveredByMachineTurns(items: any[]): any[] {
  const MIN = 40;
  const norm = (s: unknown) => String(s ?? '').replace(/\s+/g, ' ').trim();
  const turns: string[] = [];
  for (const it of items) {
    if (it?.kind === 'text' && it.role === 'user' && !it.queued && it.machine) {
      const t = norm(it.text);
      if (t.length >= MIN) turns.push(t);
    }
  }
  if (!turns.length) return items;
  const covered = (body: unknown) => {
    const b = norm(body);
    if (b.length < MIN) return false;
    const head = b.slice(0, MIN);
    return turns.some((t) => t === b || t.includes(b) || b.includes(t) || t.startsWith(head));
  };
  const out: any[] = [];
  for (const it of items) {
    if (it?.kind !== 'queued_batch' || !Array.isArray(it.entries)) { out.push(it); continue; }
    const kept = it.entries.filter((e: any) => !covered(e?.body));
    if (kept.length === it.entries.length) { out.push(it); continue; }
    if (!kept.length) continue;
    const copy = { ...it, entries: kept, count: kept.length };
    if (LIVE_CARDS.has(it)) LIVE_CARDS.add(copy);      // keep its identity as a live card
    out.push(copy);
  }
  return out;
}

/** A processed batch's machine wakes (lib/audience.ts wakeKind) left out, and a batch left empty with them. */
export function dropWakeBatchEntries(items: any[]): any[] {
  const out: any[] = [];
  for (const it of items) {
    if (it?.kind !== 'queued_batch' || !Array.isArray(it.entries)) { out.push(it); continue; }
    const kept = it.entries.filter((e: any) => !wakeKind(e?.body) && !mailWake(e?.agent, e?.body));
    if (kept.length === it.entries.length) { out.push(it); continue; }
    if (!kept.length) continue;
    const copy = { ...it, entries: kept, count: kept.length };
    if (LIVE_CARDS.has(it)) LIVE_CARDS.add(copy);      // keep its identity as a live card
    out.push(copy);
  }
  return out;
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
  // POSITIONAL: a copy is dropped for a real turn with its words written AFTER it (the harness writes the queued
  // message as a real turn when the turn ends), never for one before it (the same words sent twice are two
  // messages). So a chunk that holds the copy and what follows it decides the same as the whole file.
  const isUser = (it: any) => it?.kind === 'text' && it.role === 'user';
  const isCopy = (it: any) => isUser(it) && (it.queued || it.mid_turn);
  const isReal = (it: any) => isUser(it) && !it.queued && !it.mid_turn;
  const words = (it: any) => String(it.text || '').trim();
  if (!items.some(isCopy)) return items;
  // The real turn a copy is dropped for is marked (not enumerable: never sent, never compared): a tail-first page
  // whose cursor is before the copy must read past that turn to the ones after it (chatWindow isDrainedOpener).
  const pending = new Set<string>();
  for (const it of items) {
    if (isCopy(it)) { const t = words(it); if (t) pending.add(t); }
    else if (isReal(it) && pending.has(words(it))) {
      Object.defineProperty(it, CONSUMED_QUEUED, { value: true, enumerable: false, configurable: true });
    }
  }
  const after = new Set<string>();
  const drop = new Set<any>();
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (isReal(it)) { const t = words(it); if (t) after.add(t); }
    else if (isCopy(it) && after.has(words(it))) drop.add(it);
  }
  return drop.size ? items.filter((it) => !drop.has(it)) : items;
}

// keepSidechain: a subagent's own log (<sid>/subagents/agent-<id>.jsonl) is sidechain on every row; its
// view keeps them. The seat's chat never does.
function normalizeClaudeEntry(o: any, keepSidechain = false): any[] {
  const t = o?.type;
  // A mid-turn message is an `attachment`, not a user turn — see queuedCommandItems.
  if (t === 'attachment') return queuedCommandItems(o);
  if (t !== 'user' && t !== 'assistant') return [];
  if ((o.isSidechain === true && !keepSidechain) || o.isCompactSummary === true) return [];
  // The harness writes its own notes as type:'user' with isMeta:true -- the image-size
  // note after a photo is read, stop-hook feedback, a loaded skill's body, local-command
  // caveats. The operator, 2026-10-08: rendered as user rows, they "look like messages that come
  // from the user when it's not". Measured across the 60 most recent live transcripts:
  // not one isMeta entry is human speech. Only the explicit `true` hides a turn.
  if (t === 'user' && o.isMeta === true) {
    // Except the promote wake in its Stop-hook shape (lib/audience.ts promoteWake): it opens the promoted seat's first
    // turn, which settlePromoteTurns keeps or leaves out whole. Dropped like the rest, its turn's work and reply would
    // run on inside whatever turn came before (a hydrate digest's, or a person's).
    const c = o.message?.content;
    const text = typeof c === 'string' ? c
      : Array.isArray(c) ? c.filter((b: any) => b?.type === 'text').map((b: any) => String(b.text ?? '')).join('\n') : '';
    if (promoteWake(text)) return [{ kind: 'text', role: 'user', text, ts: o.timestamp, uuid: o.uuid }];
    // A Stop-hook continuation (review v3 F2) leaves a marker, never drawn and gone before anything is sent (liveFrom
    // drops it): the promote turn's final reply is the one BEFORE it, as the capsule reads a hook continuation.
    if (/^Stop hook feedback:/.test(text.trimStart())) return [{ kind: HOOK_MARKER, ts: o.timestamp, uuid: o.uuid }];
    return [];
  }
  const msg = o.message || {};
  const role = msg.role || t;
  const ts = o.timestamp;
  const uuid = o.uuid;
  const content = msg.content;
  const items: any[] = [];
  // Which model wrote this assistant entry (Claude Code records it on every one; "<synthetic>" is
  // its own placeholder for harness-made turns, not a model). Clients draw "Model changed" where it
  // changes. Additive: absent = unknown.
  const model = role === 'assistant' && typeof msg.model === 'string' && msg.model && msg.model !== '<synthetic>'
    ? msg.model : undefined;
  const stamp = (it: any) => { if (model && it.role === 'assistant') it.model = model; return it; };
  if (typeof content === 'string') {
    const text = role === 'user' ? sanitizeClaudeUserText(content) : content;
    if (text) items.push(stamp({ kind: 'text', role, text, ts, uuid }));
    return items;
  }
  for (const b of content || []) {
    if (!b || typeof b !== 'object') continue;
    switch (b.type) {
      case 'text': {
        const text = role === 'user' ? sanitizeClaudeUserText(b.text || '') : (b.text || '');
        if (text) items.push(stamp({ kind: 'text', role, text, ts, uuid }));
        break;
      }
      case 'thinking':
        items.push(stamp({ kind: 'thinking', role, text: b.thinking || '', ts, uuid }));
        break;
      case 'tool_use':
        items.push(stamp({ kind: 'tool_use', role: 'assistant', tool: b.name || '', input: b.input || {}, id: b.id, ts, uuid }));
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
/** The line fields a tail read stamps (services/transcript-log-cache.ts), carried onto a rebuilt item. */
function withLine(out: any, it: any): any {
  if (typeof it?.line_off === 'number') { out.line_off = it.line_off; out.line_part = it.line_part; }
  return out;
}

function enrichItems(items: any[], atStart = true): any[] {
  // The log's first user text can be the spawn brief: only when these items start at the file's first line.
  let firstUserSeen = !atStart;
  return items.map((it) => {
    if (it.kind === 'tool_use') {
      const input = (it.input && typeof it.input === 'object') ? it.input : {};
      const summary = toolSummary(it.tool || '', input);
      const { capped, truncated } = capInput(input);
      const out: any = { kind: it.kind, role: it.role, tool: it.tool, input: capped, summary };
      if (truncated) out.input_full = inputFullText(input);
      // Same reason as withTs(): an own `ts` holding undefined is not an absent `ts`.
      out.id = it.id; out.uuid = it.uuid;
      if (it.model) out.model = it.model;      // the model that made the call (additive)
      if (it.usage) out.usage = it.usage;      // tokens its reply spent, OTel terms (additive)
      withTs(out, it.ts);
      return withLine(out, it);
    }
    if (it.kind === 'text' && it.role === 'user' && !firstUserSeen) {
      firstUserSeen = true;
      const text = (it.text || '').trim();
      if (text.length > 800 || /^You are\b/.test(text)) {
        const out = withLine(withTs({ kind: it.kind, role: it.role, text: it.text, is_system: true, uuid: it.uuid }, it.ts), it);
        // Marked on its length alone: a guess (a long first message may be the operator's), never a reason to cut it.
        if (!/^You are\b/.test(text)) BRIEF_GUESS.add(out);
        return out;
      }
    }
    return it;
  });
}

/** First user texts enrichItems marked is_system on their LENGTH only (not a "You are ..." brief): drawn as before, but
 *  liveFrom reads them by their words, so an operator's long first message in a rotated log is never cut with the
 *  harness's lines (review item 3). Identity-keyed: a copy made later is the old, cautious reading. */
const BRIEF_GUESS = new WeakSet<object>();


// Server-side pairing (schema render_items[]): tool_result attached under its
// tool_use, empties dropped, order preserved. Replaces the duplicated web
// buildRenderList() / iOS buildRows() logic — clients do node -> view only.
/** audience/exchange ride along onto the render node built from an item. */
function withAudience(node: any, it: any): any {
  if (it?.audience !== undefined) node.audience = it.audience;
  if (it?.exchange !== undefined) node.exchange = it.exchange;
  return node;
}

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
        if (it.voice_said) continue;      // said on a voice call: the call's card holds it
        const node: any = { kind: 'user', text };
        if (it.is_system) node.is_system = true;
        if (it.queued) node.queued = true;
        if (it.machine) node.machine = it.machine;
        if (it.ts !== undefined) node.ts = it.ts;
        node.key = key;
        nodes.push(withAudience(node, it));
      } else {
        const node: any = { kind: 'assistant', text };
        if (Array.isArray(it.images) && it.images.length) node.images = it.images;   // images in the chat (additive)
        if (it.ts !== undefined) node.ts = it.ts;
        node.key = key;
        nodes.push(withAudience(node, it));
      }
      continue;
    }
    if (it.kind === 'thinking') {
      const text = (it.text || '').trim();
      if (!text) continue;
      const node: any = { kind: 'thinking', text };
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = key;
      nodes.push(withAudience(node, it));
      continue;
    }
    if (it.kind === 'rotation') {
      // The stitched chat's divider: drawn like a date line between two generations.
      const node: any = {
        kind: 'rotation', from_generation: it.from_generation, to_generation: it.to_generation,
        from_session: it.from_session, to_session: it.to_session,
      };
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = it.uuid || key;
      nodes.push(withAudience(node, it));
      continue;
    }
    if (it.kind === 'compaction') {
      // The compaction notice: one node per notice, keyed by its id so it updates in place.
      const node: any = {
        kind: 'compaction', id: it.id, state: it.state, trigger: it.trigger ?? 'auto', finished_at: it.finished_at ?? null,
        before_tokens: it.before_tokens ?? null, after_tokens: it.after_tokens ?? null,
      };
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = it.id || key;
      nodes.push(withAudience(node, it));
      continue;
    }
    if (it.kind === 'queued_batch') {
      // B2 processed-batch div (queued-native-render): passthrough as a render node.
      const node: any = { kind: 'queued_batch', count: it.count ?? (Array.isArray(it.entries) ? it.entries.length : 0), entries: it.entries || [] };
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = it.key || key;
      nodes.push(withAudience(node, it));
      continue;
    }
    if (it.kind === 'tool_use') {
      const paired = it.id ? resultById.get(it.id) : undefined;
      const node: any = { kind: 'tool', tool: it.tool || '', summary: it.summary ?? '', input: it.input || {} };
      if (it.input_full !== undefined) node.input_full = it.input_full;
      if (Array.isArray(it.images) && it.images.length) node.images = it.images;   // the image a Write made (additive)
      if (paired) { node.result = paired.text; node.is_error = paired.is_error; }
      if (it.ts !== undefined) node.ts = it.ts;
      node.key = key;
      nodes.push(withAudience(node, it));
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

/** What the Claude grammar carries from one line to the next, so a log read in pieces (services/transcript-log-cache.ts:
 *  the whole file once, then only the appended lines) parses exactly as the whole file would. */
export interface ClaudeParseState {
  /** Replies (message.id) whose usage already went on an item. */
  counted: Set<string>;
  /** A reply whose lines drew nothing so far: its tokens wait for its own or the next reply's first drawn item. */
  waiting?: { id: string; usage: TokenUsage };
  /** Tokens of replies that drew nothing, to go on the next drawn assistant item. */
  carried?: TokenUsage;
}

/** Claude Code lines -> items, continuing `state`. Claude Code writes each block of one API reply as its own line,
 *  every line repeating the reply's usage: it goes ONCE per reply (message.id), on the reply's first drawn item. First,
 *  not last: the SSE tailer only appends, and an item already sent never moves its usage. A reply none of whose lines
 *  drew anything carries its tokens to the next reply's first item. */
function parseClaudeLines(lines: string[], keepSidechain: boolean, state: ClaudeParseState): any[] {
  const items: any[] = [];
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    if (!line.trim()) continue;
    try {
      const o = JSON.parse(line);
      const got = normalizeClaudeEntry(o, keepSidechain);
      for (const it of got) it.li = li;   // which line: the cache turns it into the line's byte offset (line_off/line_part)
      const msg = o?.message;
      const id = o?.type === 'assistant' && typeof msg?.id === 'string' ? msg.id : '';
      const first = got.find((it) => it.role === 'assistant');
      // A new assistant line from another reply ends the waiting reply: its tokens are carried.
      if (state.waiting && o?.type === 'assistant' && state.waiting.id !== id) {
        state.carried = addUsage(state.carried, state.waiting.usage);
        state.waiting = undefined;
      }
      const usage = id && !state.counted.has(id) ? usageFromClaude(msg.usage) : undefined;
      if (first && (usage || state.carried)) {
        first.usage = usage ? addUsage(state.carried, usage) : state.carried;
        state.carried = undefined;
        if (usage) { state.counted.add(id); state.waiting = undefined; }
      } else if (usage) state.waiting = { id, usage };
      items.push(...got);
    } catch { /* skip bad line */ }
  }
  return items;
}

/** One log's grammar for the parsed-log cache (services/transcript-log-cache.ts): `full` reads whole lines by the
 *  grammar they show and hands back the state only the Claude grammar can resume from (`append` parses just the new
 *  lines with it; the other grammars are whole-file parsers, so the cache re-parses them in full on every change);
 *  `finish` is every whole-array pass, run over all raw items each time. The grammar is detected from the lines
 *  themselves, NOT from a caller-passed hint: transcript-tail.ts (SSE) and this route (poll) read independently, and
 *  a hint only one of them passes is how the two lanes drift apart. */
export function logParser(antigravityHint: boolean, keepSidechain = false): LogParser<ClaudeParseState> {
  return {
    // the cache keeps one entry per (key, file): both flags change the items a file yields
    key: `${antigravityHint ? 'antigravity' : 'auto'}:${keepSidechain ? 'sidechain' : 'main'}`,
    full(lines) {
      const isAntigravity = antigravityHint ||
        lines.some((l) => {
          try {
            const parsed = JSON.parse(l);
            return parsed.type === 'USER_INPUT' || parsed.type === 'PLANNER_RESPONSE' || parsed.source === 'MODEL';
          } catch { return false; }
        });
      if (isAntigravity) return { items: normalizeAntigravity(lines), state: null };
      if (looksLikeGrok(lines)) return { items: parseGrokUpdates(lines), state: null };
      if (looksLikeCodex(lines)) return { items: parseCodexRollout(lines), state: null };
      const state: ClaudeParseState = { counted: new Set<string>() };
      const items = parseClaudeLines(lines, keepSidechain, state);
      // A resumable state only on POSITIVE Claude evidence. An empty or torn first read (a session file the tailer saw
      // the second it was created; a codex session_meta line half-written) shows no grammar at all: handing back a
      // Claude state then would lock the file to the Claude parser, and every later codex/grok/antigravity line would
      // be appended into nothing until the API restarted. Without evidence the cache re-detects on every change.
      return { items, state: looksLikeClaude(lines) ? state : null };
    },
    append(lines, state) {
      return { items: parseClaudeLines(lines, keepSidechain, state), state };
    },
    // Nothing waits to ride on a later item: the lines after this point parse the same from a fresh state (counted ids
    // never span a chunk edge: a reply's lines are kept together, services/transcript-log-cache.ts chunkStartAt).
    clean: (state) => !state.waiting && !state.carried,
    finish(raw, state, atStart) {
      // On COPIES of the raw items: the whole-array passes (tagAudience above all) stamp audience/exchange in place and
      // never clear them, and `raw` is kept by the cache for the next append. Tagging raw itself left a reply with the
      // exchange of a queued opener that dedupeQueuedCommands dropped on a later pass (append != whole-file parse).
      // `li` (which line) is the cache's to turn into line_off/line_part; a whole-array parse outside the cache drops it.
      const items = raw.map(({ li: _li, ...it }) => it);
      // Whole-file pass (Claude): a queued message the log later recorded as a real user turn must
      // show ONCE, and the real turn is the one that wins (dedupeQueuedCommands, inside finishLogItems after the
      // text rewrites: the mark it leaves must sit on the item the page sees).
      return finishLogItems(items, atStart, !!state);
    },
  };
}

// One log's items: parsed by the grammar its lines show, whole-file passes applied, then tagged
// (audience/exchange) on the WHOLE log.
export function logItems(lines: string[], antigravityHint: boolean, keepSidechain = false): any[] {
  const parser = logParser(antigravityHint, keepSidechain);
  const got = parser.full(lines);
  return parser.finish(got.items, got.state, true);
}

/** The whole-array passes every grammar's raw items get, in order, then the audience/exchange tags. `atStart` false
 *  for a tail chunk: its first user text is not the log's first message (never the spawn brief by position). */
function finishLogItems(items: any[], atStart = true, dedupeQueued = false): any[] {
  // The quiet page line, on every runtime's user turns: what the agent read, never what the person said.
  // Kept as the turn's opener (audience/exchange tagging and the interrupted-turn merge need it), flagged, words only;
  // drawing skips it (buildRenderItems here, buildRenderList in the dashboard).
  items = items.map((it) => (it?.kind === 'text' && it.role === 'user' && typeof it.text === 'string'
    && it.text.includes(VOICE_CALL_SAID) ? { ...it, text: stripPageContext(it.text), voice_said: true } : it));
  // A reaction wake turn: kept (it opens the agent's turn), emptied (never drawn: both renderers skip empty user
  // text) and flagged, so the dashboard does not fold the agent's emoji answer to it into the operator's message
  // before (dashboard lib/transcript.ts afterReaction, view.ts withReactions).
  items = items.map((it) => (it?.kind === 'text' && it.role === 'user' && typeof it.text === 'string'
    && hasReactionWake(it.text) ? { ...it, text: stripPageContext(it.text), reaction_wake: true } : it));
  items = items.map((it) => (it?.kind === 'text' && it.role === 'user' && typeof it.text === 'string'
    && (it.text.includes('<page-context>') || it.text.includes('<voice-call-live>'))
    ? { ...it, text: stripPageContext(it.text) } : it));
  // A queued copy against the real turn written after it (Claude): on the rewritten items, so the mark the dedupe
  // leaves on that real turn (CONSUMED_QUEUED, not enumerable: a spread drops it) is on the item the page sees.
  if (dedupeQueued) items = dedupeQueuedCommands(items);
  // The agent's answer to that wake when it is not a message (NO_REPLY, "(no reply needed)", one emoji): marked here,
  // dropped from what the chat is sent (finishWindow, the tailer after its refs). chatItemsFor keeps them marked: the
  // wake watcher (services/reaction-wake.ts) turns an emoji answer into a real reaction.
  items = markWakeReplies(items);
  // A call handed to the agent (lib/agentCalls.ts handoffMessage): the agent read the transcript, the chat shows the
  // call's card on this line, where the call ended.
  items = items.map((it) => {
    if (it?.kind !== 'text' || it.role !== 'user' || typeof it.text !== 'string' || !it.text.includes('<voice-call-transcript')) return it;
    let shown: ReturnType<typeof showHandoff> = null;
    try { shown = showHandoff(it.text, dataDir()); } catch { shown = null; }
    return shown ? { ...it, text: shown.text, call_id: shown.call_id } : it;
  });
  // audience/exchange (the chat fold): tagged on the WHOLE log, so a turn whose opener falls
  // outside the window keeps its tags. Then the machine wakes are taken out (hideWakes), on the whole log too.
  return hideWakes(tagAudience(enrichItems(splitPromoteEnvelopes(items), atStart)));
}

/** A router-injected promote wake with an operator's words after it in the same prompt (lib/audience.ts
 *  promoteEnvelopeRest): the turn is his, so the line is his words alone, before the turn is tagged. */
function splitPromoteEnvelopes(items: any[]): any[] {
  return items.map((it) => {
    if (it?.kind !== 'text' || it.role !== 'user' || it.mid_turn || it.is_system) return it;
    const rest = promoteEnvelopeRest(it.text);
    return rest ? { ...it, text: rest, promote_envelope: true } : it;
  });
}

/** The machine wakes left out of a seat's chat (lib/audience.ts wakeKind): a `rotation` wake's whole turn (a hydrate
 *  digest and the agent's answer to it, a promote notice, the retire notice), and a `beat` wake's own line, emptied and
 *  flagged `wake` (never drawn: both renderers skip empty user text), so the turn it opened keeps its place. */
const HOOK_MARKER = 'hook_continuation';
const STOP_HOOK = /^Stop hook feedback:/;
/** A beat wake's emptied line; a Stop-hook line is flagged `hook` (it continues the turn before it, review v3 F2). */
const beatLine = (it: any): any => ({ ...it, text: '', wake: 'beat', ...(STOP_HOOK.test(String(it.text ?? '').trimStart()) ? { hook: true } : {}) });

export function hideWakes(items: any[]): any[] {
  let any = false;
  for (const it of items) if (isTurnOpener(it) && !it.is_system && wakeKind(it.text)) { any = true; break; }
  if (!any) return items;
  const out: any[] = [];
  let dropping = false;
  for (const it of items) {
    if (isTurnOpener(it)) {
      const k = it.is_system ? null : wakeKind(it.text);
      // A wake typed in while the agent was mid-turn (a queued_command) did not start what follows it: the turn it
      // landed in goes on. Only its own line is taken out.
      if (it.queued) {
        dropping = false;
        if (k === 'rotation') continue;
        out.push(k === 'beat' ? beatLine(it) : it);
        continue;
      }
      // The promote wake alone, in either shape: its line is hidden here and its turn kept for settlePromoteTurns,
      // which knows the promotion time and whether an operator message was still open (liveFrom).
      if (!it.is_system && promoteWake(it.text)) {
        dropping = false;
        out.push({ ...it, text: '', wake: 'promote' });
        continue;
      }
      dropping = k === 'rotation';
      if (dropping) continue;
      out.push(k === 'beat' ? beatLine(it) : it);
      continue;
    }
    if (!dropping) out.push(it);
  }
  return out;
}

const BOOT_LINE = /^You are [\w.-]+\. Read \/tmp\/agent-init-[\w.-]+\.md\b/;
/** A turn that is not part of the conversation: a wake, the harness's own line (the spawn brief, a stop hook), the
 *  spawn boot line. After the promotion a first message marked system on its length alone (BRIEF_GUESS) is read by
 *  its words instead: it is the operator's first input in a rotated log, not a brief. */
const notConversation = (it: any, afterPromotion = false): boolean =>
  (!!it?.wake && it?.owed === undefined) || (it?.is_system === true && !(afterPromotion && BRIEF_GUESS.has(it)))
  || BOOT_LINE.test(String(it?.text ?? '').trimStart());

/** A turn an operator (a person) opened: not a wake, not the harness or the boot line, and not machine-written
 *  (mail, forwarded mail, a card answer, a hook, a job's notice: lib/audience.ts openerKind). Before the promotion a
 *  length-only system mark stands: a green's long brief is the likelier reading there. */
const personTurn = (it: any): boolean => !notConversation(it) && !isRouterMail(it?.text)
  && !/^\[fwd from /.test(String(it?.text ?? '').trimStart()) && openerKind(it) === 'person';

/** A background job's notice (<task-notification> ... <tool-use-id>X</tool-use-id>) for a job launched by a tool call
 *  in `launched`: the job was started before the promotion, so its notice is the hold phase's too. */
const TASK_TOOL_ID = /^<task-notification>[\s\S]*?<tool-use-id>([^<\s]+)<\/tool-use-id>/;
const heldJobNotice = (it: any, launched: Set<string>): boolean => {
  const m = TASK_TOOL_ID.exec(String(it?.text ?? '').trimStart());
  return !!m && launched.has(m[1]);
};

/** An operator's own input: a turn he opened, or a message he sent while the agent was working (queued). Not a wake,
 *  the harness, mail, a job's notice, a reaction wake or a voice call's page line. */
const personInput = (it: any): boolean => it?.kind === 'text' && it.role === 'user' && !it.mid_turn
  && !it.reaction_wake && !it.voice_said && String(it.text ?? '').trim() !== '' && personTurn(it);

const hasPromoteWake = (items: readonly any[]): boolean => items.some((it) => it?.wake === 'promote');

/** A card or questionnaire answer the approval loop relayed (review v3 F3): his input too, though machine-written. */
const CARD_ANSWER = /^\[(?:APPROVAL RESOLVED|DECISION ANSWERED|MULTI-PART DECISION ANSWERED|HUMAN-TASK|questionnaire-answers|questionnaire-discarded) /;
const operatorInput = (it: any): boolean => personInput(it)
  || (it?.kind === 'text' && it.role === 'user' && CARD_ANSWER.test(String(it.text ?? '').trimStart()));

/** A Stop-hook continuation: the marker of a dropped isMeta hook line, or an emptied hook line. */
const hookContinuation = (it: any): boolean => it?.kind === HOOK_MARKER || (it?.wake === 'beat' && it?.hook === true);

/** The turn opened at `k`: up to the next opener that is not a message queued while the agent worked. */
function turnEnd(items: readonly any[], k: number): number {
  let end = k + 1;
  while (end < items.length && !(isTurnOpener(items[end]) && !items[end].queued && !hookContinuation(items[end]))) end++;
  return end;
}

/** The owed verdict the capsule wrote for a promoted generation (agent-orchestra lineage_daemon/wal/
 *  conversation_capsule.py write_owed_verdict: <data dir>/state/agent-handoffs/owed/<root>.<generation id>.json, at the
 *  promote wake). THE one source of "owed to the operator" (review v2): the API never re-derives it. true / false from
 *  the file; null when there is no file, it cannot be read, or it is not this generation's (the promote turn is then
 *  kept: it is left out only on positive evidence). Read on each view build: it is a few hundred bytes. */
export function owedVerdict(root: string | null, generationId: number | null, orch: string = dataDir(),
                            promotedAt?: string | null): boolean | null {
  // The registry's root alphabet, the same as conversation_capsule.py _ROOT_OK (ASCII; no dots; 1-64).
  if (!root || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(root) || !Number.isInteger(generationId)) return null;
  try {
    const v = JSON.parse(readFileSync(join(orch, 'state', 'agent-handoffs', 'owed', `${root}.${generationId}.json`), 'utf-8'));
    if (!v || v.root !== root || v.generation_id !== generationId || typeof v.owed !== 'boolean') return null;
    // A generation promoted twice has one verdict file (review v4 B-2): it speaks only for the promotion it was
    // computed for. A different (or missing) promoted_at, when this promotion's is known, is no verdict: keep.
    if (promotedAt !== undefined && promotedAt !== null) {
      const a = Date.parse(String(v.promoted_at ?? '')), b = Date.parse(String(promotedAt));
      if (Number.isNaN(a) || Number.isNaN(b) || a !== b) return null;
    }
    return v.owed;
  } catch {
    return null;
  }
}

/**
 * The promote wake's turn (rulings 2026-11-21, item 2; hideWakes marks its opener wake:'promote'). The first one at or
 * after `promotedAt` is LEFT OUT, the seat's reply included ("I am now canonical, I checked the registry ..."), only
 * when nothing is owed: `owed` is false (the capsule's verdict, owedVerdict). When something is owed, or that is not
 * known, the turn stays: the wake line stays hidden, the turn counts as conversation (owed) and its FINAL reply (the
 * last assistant text with no tool call after it in the turn; none if the turn ends in a call) is for the operator. A turn that also
 * holds an operator message (sent while the wake turn ran) stays whatever the rest. Any other promote turn (a second
 * wake, one before the promotion, a log with no promotion time) is left out. `owed` may be a thunk: read once, and
 * only when such a turn exists. `firstOff`, for a chunk that starts after the promotion (a tail-first read): the log
 * line of the log's first promote turn at or after it (null: none), which the chunk alone cannot see.
 */
export function settlePromoteTurns(items: readonly any[], promotedAt: string | null | undefined,
                                   open: boolean | null | (() => boolean | null) = null,
                                   firstOff?: number | null): any[] {
  if (!hasPromoteWake(items)) return items as any[];
  const t0 = promotedAt ? Date.parse(String(promotedAt)) : NaN;
  let known: { v: boolean | null } | null = null;
  const isOpen = (): boolean | null => {
    if (!known) known = { v: typeof open === 'function' ? open() : open };
    return known.v;
  };
  const out: any[] = [];
  let firstTaken = false;
  for (let k = 0; k < items.length; k++) {
    const it = items[k];
    if (it?.wake !== 'promote' || !isTurnOpener(it)) { out.push(it); continue; }
    // The turn runs to the next opener that is neither a message queued while it worked nor a Stop-hook continuation.
    const end = turnEnd(items, k);
    const t = Date.parse(String(it.ts ?? ''));
    // No promotion time (the registry lookup failed): not known, so the first promote turn is treated as the one to
    // settle, kept unless a verdict says nothing was owed (review v3 F4).
    const first = !firstTaken && (firstOff !== undefined ? firstOff !== null && it.line_off === firstOff
      : Number.isNaN(t0) || (!Number.isNaN(t) && t >= t0));
    if (first) firstTaken = true;
    let op = -1;
    for (let j = k + 1; j < end; j++) if (operatorInput(items[j])) { op = j; break; }
    const withOperator = op >= 0;
    // owed:false (review v4 B-1, gm): the turn is not deleted. Its wake line stays hidden, its tool calls stay in the
    // show-work strip, and no reply of the wake's own work is emitted (review v5 A-1): nothing is drawn for the operator.
    // Any other promote turn (a second wake, one before the promotion) is left out, as before.
    const verdict = first && !withOperator ? isOpen() : null;
    const quiet = first && !withOperator && verdict === false;
    const keep = withOperator || first;
    if (keep) {
      // The final reply only (review v2 P2-3): the last assistant text before the first Stop-hook continuation with no
      // tool call after it; a turn that ends in a call has none.
      let hookAt = end;
      for (let j = k + 1; j < end; j++) if (hookContinuation(items[j])) { hookAt = j; break; }
      let closing = -1;
      if (!withOperator && !quiet) {
        for (let j = hookAt - 1; j > k; j--) {
          if (items[j]?.kind === 'tool_use') break;
          if (items[j]?.kind === 'text' && items[j].role === 'assistant') { closing = j; break; }
        }
      }
      // Every other reply of the wake's own work, and its thinking, is NOT EMITTED (review v5 A-1): no client grouping
      // can then draw it (the web draws every text whatever its audience tag, and an edge thinking block as a "💭" line;
      // the apps force the newest text to 'user'). Its tool calls and results stay: the work in the show-work strip.
      // The raw session file keeps everything. From an operator message on, his exchange keeps its items and tags.
      const quietTo = withOperator ? op : end;
      out.push({ ...it, owed: !quiet });
      for (let j = k + 1; j < end; j++) {
        const x = items[j];
        if (j === closing) { out.push({ ...x, audience: 'user' }); continue; }
        if (j < quietTo && ((x?.kind === 'text' && x.role === 'assistant') || x?.kind === 'thinking')) continue;
        out.push(x);
      }
    }
    k = end - 1;
  }
  return out;
}

/** A generation's chat from its takeover on: a green lives 30-60 minutes before it
 *  takes over (reads its init file, takes in the baton, answers hydrate digests, verifies its promote notice, writes
 *  an "I am now canonical" recap), and none of that is the conversation. The takeover is the EARLIER of
 *   - the first turn a person opened before `promotedAt` (an operator writing to the green, or a late-stamped old
 *     generation that was in fact working with him: the conversation had started), and
 *   - the first turn opened at or after `promotedAt` that is not a machine wake, the harness, the spawn boot line, or
 *     the notice of a background job launched before `promotedAt`.
 *  Everything before the takeover is left out; everything from it on stays (machine wakes are already out: hideWakes).
 *  No promotion time, or a log with no times at all: the log whole, as before (nothing to cut on). No takeover yet:
 *  nothing to show. */
export function liveFrom(items: readonly any[], promotedAt: string | null | undefined,
                         open: boolean | null | (() => boolean | null) = null): any[] {
  return dropHookMarkers(cutAtTakeover(settlePromoteTurns(items, promotedAt, open), promotedAt));
}

/** The Stop-hook markers exist only for settlePromoteTurns: nothing downstream ever sees one. */
export const dropHookMarkers = (items: readonly any[]): any[] =>
  (items.some((it) => it?.kind === HOOK_MARKER) ? items.filter((it) => it?.kind !== HOOK_MARKER) : items as any[]);

function cutAtTakeover(items: readonly any[], promotedAt: string | null | undefined): any[] {
  const at = takeoverAt(items, promotedAt, true);
  if (at === 'whole' || at === 0) return items as any[];
  if (at === 'none' || at === 'more') return [];
  return items.slice(at);
}

/** The index of the takeover in `items` (a log, or the head of one: `eof` says whether it runs to the log's end):
 *  'whole' when there is nothing to cut on (no promotion time, no times at all), 'none' when there is no takeover yet,
 *  'more' when the head ends before the takeover is known. */
function takeoverAt(items: readonly any[], promotedAt: string | null | undefined, eof: boolean): number | 'whole' | 'none' | 'more' {
  const t0 = promotedAt ? Date.parse(String(promotedAt)) : NaN;
  if (Number.isNaN(t0)) return 'whole';
  let i = -1;
  let timed = false;
  for (let k = 0; k < items.length; k++) {
    const t = Date.parse(String(items[k]?.ts ?? ''));
    if (Number.isNaN(t)) continue;
    timed = true;
    if (t >= t0 && isTurnOpener(items[k])) { i = k; break; }
  }
  if (!timed) return eof ? 'whole' : 'more';
  // A person's turn before the promotion: the takeover.
  const cut = i < 0 ? items.length : i;
  for (let k = 0; k < cut; k++) {
    if (isTurnOpener(items[k]) && personTurn(items[k])) return k;
  }
  if (i < 0) return eof ? 'none' : 'more';
  // Jobs the hold phase launched: their notices after the promotion are still its own.
  const launched = new Set<string>();
  for (let k = 0; k < items.length; k++) {
    const it = items[k];
    if (it?.kind === 'tool_use' && it.id && Date.parse(String(it.ts ?? '')) < t0) launched.add(String(it.id));
  }
  while (i < items.length && (notConversation(items[i], true) || heldJobNotice(items[i], launched))) {
    let j = i + 1;
    while (j < items.length && !isTurnOpener(items[j])) j++;
    i = j;
  }
  if (i < items.length) return i;
  return eof ? 'none' : 'more';
}

/** Where a generation's chat starts, as a log line: 'whole' (nothing cut), 'none' (nothing shown yet), or the byte
 *  offset of the takeover's line. A tail-first view (a chunk of the log, services/transcript-log-cache.ts) cannot see
 *  the hold phase it may start in, so the takeover is found once on the log's HEAD, read forward from its first line
 *  until the takeover is known (a hold phase is the first half hour or so of a green's log), and every chunk is cut at
 *  that line. A found takeover never moves (the log only grows); no takeover yet is read again when the log grows. */
type Takeover = 'whole' | 'none' | { off: number; firstPromote: number | undefined; exchange: string | undefined };
const takeovers = new Map<string, { t: Takeover; size: number }>();
const TAKEOVERS_MAX = 256;
const HEAD_WANT0 = 32 * 1024;

/** A log file as a cache key part (its inode: an atomic replace is another file) and its size. */
function logStat(path: string): { id: string; size: number } {
  try { const st = statSync(path); return { id: `${st.dev}:${st.ino}`, size: st.size }; } catch { return { id: '', size: -1 }; }
}

const takeoverKey = (path: string, id: string, promotedAt: string | null | undefined, sig: string): string =>
  `${path}|${id}|${promotedAt ?? ''}|${sig}`;

function headTakeover(path: string, parser: LogParser<any>, promotedAt: string | null | undefined,
                      prep: (items: readonly any[]) => any[], sig: string): Takeover {
  const t0 = promotedAt ? Date.parse(String(promotedAt)) : NaN;
  if (Number.isNaN(t0)) return 'whole';
  const { id, size } = logStat(path);
  const key = takeoverKey(path, id, promotedAt, sig);
  const kept = takeovers.get(key);
  // A found takeover never moves while the file only grows; a file cut shorter is read again.
  if (kept && (typeof kept.t === 'object' ? size >= kept.size : kept.size === size)) return kept.t;
  let t: Takeover = 'whole';
  // From a small head up, doubled: the takeover is usually in the first lines (a person turn, or a short hold phase).
  // No takeover yet (the last read ran to the end): the head is the whole log, read through the whole-file cache, which
  // parses only the lines appended since (a range read of a grown file starts over).
  let hi = kept?.t === 'none' ? Number.POSITIVE_INFINITY : Math.min(TAIL_WANT0, HEAD_WANT0);
  for (let round = 0; round < 64; round++) {
    let log: ReturnType<typeof logRange>;
    try {
      log = Number.isFinite(hi) ? logRange(path, parser, 0, hi) : { ...cachedLog(path, parser), end: Math.max(size, 0), eof: true };
    } catch { return 'whole'; }
    const items = settlePromoteTurns(prep(log.items), promotedAt, null);
    const at = takeoverAt(items, promotedAt, log.eof);
    if (at === 'more' && !log.eof) { hi *= 2; continue; }
    if (at === 'more' || at === 'none') t = 'none';
    else if (at === 'whole') t = 'whole';
    else if (typeof items[at]?.line_off === 'number') {
      const promote = items.find((it) => it?.wake === 'promote' && isTurnOpener(it) && Date.parse(String(it.ts ?? '')) >= t0);
      // The log's first promote turn after the promotion, when the head holds it (the head is the log's start); else
      // found when a chunk needs it (firstPromoteOf: one may still land).
      // The exchange the takeover's turn is in, as the whole log tags it (the head is the log's start): the hold phase
      // can leave one open (withHeadTags).
      t = { off: items[at].line_off, firstPromote: typeof promote?.line_off === 'number' ? promote.line_off : undefined,
        exchange: items[at].exchange === undefined ? undefined : String(items[at].exchange) };
    }
    break;
  }
  if (takeovers.size >= TAKEOVERS_MAX) takeovers.delete(takeovers.keys().next().value as string);
  takeovers.set(key, { t, size });
  return t;
}

/** The log line of the promote turn settlePromoteTurns takes as the promotion's on the whole log: its first at or after
 *  `promotedAt` (its first, with no promotion time). Read on the whole log, only for a chunk below the head that holds a
 *  promote wake (a second wake, or one that landed late), which is rare. A found one is kept (the log only grows); none
 *  yet holds only while the file does not grow (a wake may land). */
const firstPromotes = new Map<string, { off: number | null; size: number }>();
function firstPromoteOf(path: string, parser: LogParser<any>, promotedAt: string | null | undefined,
                           prep: (items: readonly any[]) => any[], sig: string): number | null {
  const { id, size } = logStat(path);
  const key = takeoverKey(path, id, promotedAt, sig);
  const kept = firstPromotes.get(key);
  if (kept && (kept.off !== null ? size >= kept.size : kept.size === size)) return kept.off;
  const t0 = promotedAt ? Date.parse(String(promotedAt)) : NaN;
  let off: number | null = null;
  try {
    const promote = prep(cachedLog(path, parser).items).find((it) => it?.wake === 'promote' && isTurnOpener(it)
      && (Number.isNaN(t0) || Date.parse(String(it.ts ?? '')) >= t0));
    off = typeof promote?.line_off === 'number' ? promote.line_off : null;
  } catch { return null; }
  if (firstPromotes.size >= TAKEOVERS_MAX) firstPromotes.delete(firstPromotes.keys().next().value as string);
  firstPromotes.set(key, { off, size });
  return off;
}

/** Where a view of a log FILE is cut (cheap: the head's takeover is kept): 'log' when the view is the whole log (cut
 *  as liveFrom cuts it), else the log's takeover (headTakeover). `prep`: the store's rules and the warm-up. */
function cutOfView(view: { path: string; parser: LogParser<any>; start: number; eof: boolean },
                   promotedAt: string | null | undefined, prep: (items: readonly any[]) => any[], sig: string): 'log' | Takeover {
  if (view.start === 0 && view.eof) return 'log';
  return headTakeover(view.path, view.parser, promotedAt, prep, sig);
}

/** A cut as a memo key part. */
const cutKey = (c: 'log' | Takeover): string => (typeof c === 'string' ? c : `@${c.off}`);

/** liveFrom for a view of a log FILE (`items`: its items with `prep` applied) cut as cutOfView says: a whole log as
 *  liveFrom cuts it; a chunk (a tail or a middle read) at the takeover's line. */
function liveFromFile(items: readonly any[], view: { path: string; parser: LogParser<any>; start: number; eof: boolean },
                      cut: 'log' | Takeover, promotedAt: string | null | undefined,
                      open: boolean | null | (() => boolean | null), prep: (items: readonly any[]) => any[], sig: string): any[] {
  if (cut === 'log') return liveFrom(items, promotedAt, open);
  if (cut === 'whole') {
    // Nothing to cut at, but a chunk below the head may hold a promote turn that is not the log's first.
    const first = view.start > 0 && hasPromoteWake(items) ? firstPromoteOf(view.path, view.parser, promotedAt, prep, sig) : undefined;
    return dropHookMarkers(settlePromoteTurns(items, promotedAt, open, first));
  }
  if (cut === 'none') return [];
  // A chunk below the head may hold a promote turn that is not the log's first one: which one is first is read on the
  // log (the head's, or firstPromoteOf), never guessed from the chunk's own times (a line queued mid-turn keeps the
  // time it was typed).
  const first = view.start > 0 && hasPromoteWake(items)
    ? (cut.firstPromote ?? firstPromoteOf(view.path, view.parser, promotedAt, prep, sig)) : undefined;
  const settled = dropHookMarkers(settlePromoteTurns(items, promotedAt, open, first));
  let k = 0;
  while (k < settled.length && typeof settled[k]?.line_off === 'number' && settled[k].line_off < cut.off) k++;
  const kept = k === 0 ? settled : settled.slice(k);
  return view.start > 0 && cut.off >= view.start ? withHeadTags(kept, cut.exchange) : kept;
}

/** A chunk that holds the takeover but not the log's head was tagged from its own first line (lib/audience.ts
 *  tagAudience), without the hold phase before it. A person's turn there (a forwarded mail, a typed digest request: the
 *  audience pass reads them as a person's, though they are never the takeover) leaves its exchange open over the turns
 *  after the takeover, up to the next person's turn, and means the whole log has a person's turn, so no reply is drawn
 *  for want of one (NEWEST_DRAWN). `exchange`: that open exchange, read on the head (headTakeover). */
function withHeadTags(items: any[], exchange: string | undefined): any[] {
  if (exchange === undefined) return items;
  let out = items;
  const copy = (i: number): any => {
    if (out === items) out = items.slice();
    if (out[i] === items[i]) out[i] = copyItem(items[i]);
    return out[i];
  };
  for (let i = 0; i < items.length && items[i] && items[i].exchange === undefined; i++) {
    // Where the audience pass puts it (right after `audience`), so the item reads as the whole-file build's, key for key.
    const c = copy(i);
    const keys = Object.keys(c);
    const at = keys.indexOf('audience');
    c.exchange = exchange;
    if (at >= 0) for (const k of keys.slice(at + 1)) { const v = c[k]; delete c[k]; c[k] = v; }
  }
  for (let i = 0; i < items.length; i++) {
    if (items[i] && Object.prototype.hasOwnProperty.call(items[i], NEWEST_DRAWN)) copy(i).audience = items[i][NEWEST_DRAWN];
  }
  return out;
}

/** A shallow copy of an item that keeps its marks (the non-enumerable symbols, the brief guess): the cache keeps the
 *  original. */
function copyItem(it: any): any {
  const c = { ...it };
  for (const sym of Object.getOwnPropertySymbols(it)) {
    if (sym !== NEWEST_DRAWN) Object.defineProperty(c, sym, Object.getOwnPropertyDescriptor(it, sym)!);
  }
  if (BRIEF_GUESS.has(it)) BRIEF_GUESS.add(c);
  return c;
}

/** `sid`'s generation as the head of `agentId`'s lineage: its promotion time, root and generations row id. */
function headOf(agentId: string, sid: string | null): { promotedAt: string | null; root: string; generationId: number | null } | null {
  if (!sid || !SESSION_ID.test(sid)) return null;
  try {
    const chain = lineageChainOf(sid);
    if (!chain || lineageOf(chain.root) !== lineageOf(agentId)) return null;
    const last = chain.links[chain.links.length - 1];
    return { promotedAt: last?.promoted_at ?? null, root: chain.root, generationId: last?.id ?? null };
  } catch {
    return null;
  }
}

/** The assistant text answering a reaction wake (after a reaction_wake turn, before the next user text) that is not a
 *  message: copies marked wake_reply 'silent' (NO_REPLY, a "no reply" phrase) or 'emoji' (a reaction written as text).
 *  Real words, and the same words answering a real message, are left alone. Read-only: nothing is recorded. */
export function markWakeReplies(items: any[]): any[] {
  let inWake = false;
  return items.map((it) => {
    if (it?.kind === 'text' && it.role === 'user') { inWake = it.reaction_wake === true; return it; }
    if (!inWake || it?.kind !== 'text' || it.role !== 'assistant') return it;
    const quiet = silentReply(it.text);
    return quiet ? { ...it, wake_reply: quiet } : it;
  });
}

/** What the chat is sent: the marked wake answers left out (markWakeReplies). */
export function dropWakeReplies(items: any[]): any[] {
  return items.some((it) => it?.wake_reply) ? items.filter((it) => !it?.wake_reply) : items;
}

// A headless seat's cut turns (scripts/seat_supervisor.py): a message whose turn did not finish
// (the process ended mid-turn, or an interrupt cut it) is never resent, so the chat says so. The
// supervisor lists them in <data dir>/state/headless/<seat>/interrupted.json; each one becomes a
// machine line placed after the turn its message opened. Only for a message that is in the window,
// and once per message. Fail-open: no file, nothing added.
export const INTERRUPTED_PREFIX = '[TURN INTERRUPTED]';

export interface InterruptedTurn { id: string; text: string; at?: string; reason?: string }

/** Where the supervisor lists a seat's cut turns; null for an id that is not a safe path segment. */
export function interruptedTurnsPath(agentId: string, root?: string): string | null {
  if (!isSafeAgentId(agentId)) return null;
  try {
    return join(root ?? dataDir(), 'state', 'headless', agentId, 'interrupted.json');
  } catch {
    return null;                                  // no data dir configured: no file to read
  }
}

/** A cheap change signature for that file (size and mtime), '' when it is missing. The live stream
 *  re-reads the transcript when it changes, as a cut turn is listed without the log growing. */
export function interruptedTurnsSig(agentId: string, root?: string): string {
  const p = interruptedTurnsPath(agentId, root);
  if (!p) return '';
  try {
    const st = statSync(p);
    return `${st.size}:${st.mtimeMs}`;
  } catch {
    return '';
  }
}

export function readInterruptedTurns(agentId: string, root?: string): InterruptedTurn[] {
  const p = interruptedTurnsPath(agentId, root);
  if (!p) return [];
  try {
    const doc = JSON.parse(readFileSync(p, 'utf-8'));
    const items = doc && Array.isArray(doc.items) ? doc.items : [];
    // The supervisor keeps the text as delivered: without the quiet page line, as the chat shows the message.
    // Filtered after stripping too: a cut reaction wake strips to '' and is no message (an empty text would also
    // match every user turn in mergeInterruptedTurns' startsWith).
    return items.filter((x: any) => x && typeof x.id === 'string' && typeof x.text === 'string' && x.text.trim())
      .map((x: any) => ({ ...x, text: stripPageContext(x.text) }))
      .filter((x: any) => x.text);
  } catch {
    return [];
  }
}

const isOpenerItem = (it: any) => it?.kind === 'text' && it.role === 'user' && !it.mid_turn && !it.is_system;

export function interruptedLine(t: InterruptedTurn): string {
  const words = t.text.replace(/\s+/g, ' ').trim();
  const head = words.length > 160 ? `${words.slice(0, 159)}…` : words;
  return `${INTERRUPTED_PREFIX} The agent's process stopped before it finished answering this message, `
    + `and the message was not sent again. Message: "${head}"`;
}

export function mergeInterruptedTurns(items: any[], turns: InterruptedTurn[]): any[] {
  if (!turns.length) return items;
  const out = [...items];
  for (const t of turns) {
    const uuid = `interrupted:${t.id}`;
    if (out.some((it) => it?.uuid === uuid)) continue;
    const want = t.text.trim();
    let at = -1;
    for (let i = out.length - 1; i >= 0; i--) {
      const it = out[i];
      if (it?.kind === 'text' && it.role === 'user' && !it.is_system && typeof it.text === 'string'
          && (it.text.trim() === want || it.text.trim().startsWith(want.slice(0, 200)))) { at = i; break; }
    }
    if (at < 0) continue;                           // its message is not in this window
    let end = at + 1;
    while (end < out.length && !isOpenerItem(out[end])) end++;
    out.splice(end, 0, {
      kind: 'text', role: 'user', text: interruptedLine(t), ...(t.at ? { ts: t.at } : {}), uuid,
      machine: 'system', mid_turn: true, audience: 'user', interrupted_turn: true,
    });
  }
  return out;
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
  previous: { lines: () => string[]; antigravity: boolean; divider: Record<string, unknown> } | null = null,
  keepWakeReplies = false,
  keepSidechain = false,
): { agent_id: string; session_id: string | null; grammar_version: number; items: any[]; render_items: any[] } {
  return windowFromLog(logItems(lines, antigravityHint, keepSidechain), agentId, sessionId, limit, includeQueued, previous, keepWakeReplies, keepSidechain);
}

/** normalizeTranscript for a log FILE, through the parsed-log cache (services/transcript-log-cache.ts): the file is
 *  parsed once, then only its appended lines; the poll route's windows and the SSE tailer share that one parse. The
 *  previous-generation stitch is the poll route's (chatWindow), not this path's. */
export function normalizeTranscriptFile(
  path: string,
  agentId: string,
  sessionId: string | null,
  limit = 150,
  antigravityHint = false,
  includeQueued = false,
  keepWakeReplies = false,
  keepSidechain = false,
  tailFirst = false,
): { agent_id: string; session_id: string | null; grammar_version: number; items: any[]; render_items: any[] } {
  const parser = logParser(antigravityHint, keepSidechain);
  if (!tailFirst) {
    const log = cachedLog(path, parser);
    return windowFromLog(log.items, agentId, sessionId, limit, includeQueued, null, keepWakeReplies, keepSidechain,
      { path, parser, start: log.start, eof: true });
  }
  // Tail-first (the SSE tailer's first read of a log): the last TAIL_WANT0 bytes, doubled until they hold `limit` shown
  // items with a PERSON turn opener below the oldest of them (the same rule as the poll route's pages: the audience pass
  // sees the turn the window starts in), or the generation's whole chat (the chunk holds its takeover), or the whole
  // file. Counted on the chat as cut (liveFromFile: no hold phase, hook markers or settled promote turn): a window
  // shorter than `limit` tells the tailer it is the whole log, and numbers by its count. Later ticks hit the kept chunk
  // and ride its appends.
  let want = TAIL_WANT0;
  for (let round = 0; round < 64; round++) {
    const log = cachedLogTail(path, parser, want);
    if (log.start === 0) break;
    const file = { path, parser, start: log.start, eof: true };
    const live = fileLiveItems(log.items, agentId, sessionId, keepSidechain, file);
    const oldest = live.items.length - limit;
    let opened = false;
    for (let k = oldest - 1; k >= 0 && !opened; k--) if (anchorsExchange(live.items[k])) opened = true;
    if (live.whole || (live.items.length >= limit && opened)) {
      return windowFromLog(log.items, agentId, sessionId, limit, includeQueued, null, keepWakeReplies, keepSidechain,
        file, live.items);
    }
    want *= 2;
  }
  const log = cachedLog(path, parser);
  return windowFromLog(log.items, agentId, sessionId, limit, includeQueued, null, keepWakeReplies, keepSidechain,
    { path, parser, start: log.start, eof: true });
}

/** The store's rules and the warm-up a seat's window applies. A subagent's own log (keepSidechain) shares the seat's
 *  session id but none of the seat's window rules: the store's seen-only/seed rows and the warmup mark name the seat's
 *  entries, so they never apply to it. */
function prepFor(agentId: string, sessionId: string | null, keepSidechain: boolean):
    { prep: (xs: readonly any[]) => any[]; sig: string } {
  const ruleSession = keepSidechain ? null : sessionId;
  const warm = warmupMarkFor(agentId, ruleSession);
  const rules = storeRules(ruleSession);
  return { prep: (xs) => applyStoreRules(dropWarmup(xs, warm.mark), rules), sig: `${warm.sig}:${rules.sig}` };
}

/** Whether anything was owed to the operator at this generation's promotion (owedVerdict), read when asked. */
function openFor(agentId: string, sessionId: string | null, keepSidechain: boolean): () => boolean | null {
  const head = headOf(agentId, keepSidechain ? null : sessionId);
  return () => owedVerdict(head?.root ?? null, head?.generationId ?? null, dataDir(), head?.promotedAt ?? null);
}

/** A log file's items (or a chunk's) as the chat shows them: from this generation's promotion on, as the poll route's
 *  segments cut it (liveFrom; a chunk at the takeover's line). `whole`: they are the generation's whole chat (the chunk
 *  reaches the file's start, or holds the takeover, or there is no takeover yet). */
function fileLiveItems(logged: readonly any[], agentId: string, sessionId: string | null, keepSidechain: boolean,
                       file: { path: string; parser: LogParser<any>; start: number; eof: boolean }): { items: any[]; whole: boolean } {
  const { prep, sig } = prepFor(agentId, sessionId, keepSidechain);
  const head = headOf(agentId, keepSidechain ? null : sessionId);
  const promotedAt = head?.promotedAt ?? null;
  const open = () => owedVerdict(head?.root ?? null, head?.generationId ?? null, dataDir(), promotedAt);
  const cut = cutOfView(file, promotedAt, prep, sig);
  const items = liveFromFile(prep(logged), file, cut, promotedAt, open, prep, sig);
  const whole = file.start === 0 || cut === 'none' || (typeof cut === 'object' && cut.off >= file.start);
  return { items, whole };
}

function windowFromLog(
  logged: readonly any[],
  agentId: string,
  sessionId: string | null,
  limit: number,
  includeQueued: boolean,
  previous: { lines: () => string[]; antigravity: boolean; divider: Record<string, unknown> } | null,
  keepWakeReplies: boolean,
  keepSidechain: boolean,
  file: { path: string; parser: LogParser<any>; start: number; eof: boolean } | null = null,
  live: any[] | null = null,
): { agent_id: string; session_id: string | null; grammar_version: number; items: any[]; render_items: any[] } {
  let all = live ?? (file ? fileLiveItems(logged, agentId, sessionId, keepSidechain, file).items
    : liveFrom(prepFor(agentId, sessionId, keepSidechain).prep(logged), headOf(agentId, keepSidechain ? null : sessionId)?.promotedAt ?? null,
      openFor(agentId, sessionId, keepSidechain)));
  // The stitched chat: after a rotation, the previous generation's tail, ONE divider, then this
  // generation, with `limit` across the joined list. Each log is parsed and tagged on its own (its
  // own spawn brief, its own exchanges); the divider is drawn in the conversation, never inside a
  // strip. The previous log is read only when this one alone does not fill the window.
  if (previous && all.length < limit) {
    let before: any[] | null = null;
    try {
      before = dropHookMarkers(settlePromoteTurns(applyStoreRules(logItems(previous.lines(), previous.antigravity), storeRules(String(previous.divider.from_session ?? '') || null)), null, false));
    } catch { before = null; }  // unreadable: this log alone
    if (before) all = [...before, { ...previous.divider, audience: 'user' }, ...all];
  }
  return {
    agent_id: agentId,
    session_id: sessionId,
    grammar_version: GRAMMAR_VERSION,
    // keepWakeReplies: the tailer stamps refs first (parts numbered as the poll route numbers them), then drops them.
    // The line fields are the cache's (what a byte cursor names): the window's items never carry them.
    ...finishWindow(windowItems(all, limit).map((it) => (typeof it?.line_off === 'number' ? (({ line_off: _o, line_part: _p, ...rest }) => rest)(it) : it)),
      agentId, includeQueued, null, keepWakeReplies),
  };
}

/** The newest `limit` items, extended back to the start of the oldest one's log line (an assistant's text and its tool
 *  call share a uuid): a window never starts inside a line, so its refs are numbered from the line as the poll route's
 *  pages number them (chatWindow), whatever the window's size. */
function windowItems(all: readonly any[], limit: number): any[] {
  let from = Math.max(0, all.length - limit);
  while (from > 0 && all[from]?.uuid && all[from - 1]?.uuid === all[from].uuid) from--;
  return all.slice(from);
}

/** The merges and tags every window gets once its log items are chosen. `olderPage` is set for a
 *  page read with a cursor: msg_store's synthetics then stay only where they belong to that page
 *  (no still-pending turn, and nothing after its last log item that a newer page draws). */
function finishWindow(windowed: any[], agentId: string, includeQueued: boolean,
                      olderPage: { cursorMs: number | null } | null = null,
                      keepWakeReplies = false): { items: any[]; render_items: any[] } {
  // A wake's silent or emoji answer is never sent (markWakeReplies); refs were stamped with it in place (chatWindow).
  if (!keepWakeReplies) windowed = dropWakeReplies(windowed);
  // queued-native-render (P1): merge still-pending phone/watch held turns (B1) +
  // processed-batch divs (B2) from msg_store, at their delivery positions (never by ts:
  // the log's own order is the delivery order). POLL-ONLY —
  // the SSE tailer never sets includeQueued, so its monotonic-append delta stays
  // free of the ephemeral B1 synthetics (§3e/A1). Fail-open inside mergeQueuedItems.
  if (includeQueued) {
    const logged = new Set(windowed);
    windowed = mergeQueuedItems(windowed, agentId);
    if (olderPage) windowed = keepOlderPageSynthetics(windowed, logged, olderPage.cursorMs);
    // An agent message delivered mid-turn exists as BOTH a batch row and a queued_command.
    // The batch wins: it knows who sent it.
    windowed = dropQueuedCommandsCoveredByBatches(windowed);
    // ...and a machine message the pane recorded as a real turn shows as that turn, not ALSO as a batch.
    windowed = dropBatchEntriesCoveredByMachineTurns(windowed);
    // ...and a machine wake that came in through the queue is no more shown than one typed into the pane (hideWakes).
    windowed = dropWakeBatchEntries(windowed);
    // A headless seat's cut turns, shown where they happened (poll only, like the merges above).
    windowed = mergeInterruptedTurns(windowed, readInterruptedTurns(agentId));
  }
  windowed = stampImages(tagSynthetics(settleIngestedQueuedCommands(windowed)), agentId);
  // reply_to (reply threads P1): the operator's message whose words are a recorded send carries the uuid and excerpt
  // of the message it answered (lib/reply-to-store.ts). The SSE tailer stamps its own items the same way. Fail-open.
  windowed = stampReplyTo(windowed, agentId);
  return { items: windowed, render_items: buildRenderItems(windowed) };
}

/**
 * Images in the chat: every image or video the agent REFERENCED (lib/imageRefs.ts: an absolute path
 * in its reply, the target of a Write) that is really there, under the agent's own roots and an
 * image or a video by its bytes (lib/mediaStore.ts imageRefsFor) gets an opaque id and rides on the
 * item as `images` (ADDITIVE v2; a video with `kind: 'video'`). The poll route and the SSE tailer
 * both come through finishWindow, so both lanes carry it. The text is untouched, so the path stays
 * readable. The whole window is read first (stampWindow): the table keeps every id this window
 * shows, so ids stay the same from one stamp to the next however many images the window names; only
 * paths that can be shown count toward the cap, and each carries its item's time, so paging to an
 * older window never takes the newest window's ids.
 * Fail-open: any trouble here leaves the items as they were.
 */
export function stampImages(items: any[], agentId: string, refsFor: RefsFor = defaultImageRefsFor(ORCH_DIR)): any[] {
  const perItem: string[][] = items.map((it) => {
    try {
      if (it?.kind === 'text' && it.role === 'assistant') return findImagePaths(String(it.text || ''));
      if (it?.kind === 'tool_use') return imagePathsInToolUse(String(it.tool || ''), it.input);
    } catch { /* no paths */ }
    return [];
  });
  let window: ReadonlySet<string> | undefined;
  try { window = windowFor(refsFor, agentId, perItem, items.map((it) => it?.ts)); } catch { window = undefined; }
  const out: any[] = [];
  items.forEach((it, i) => {
    try {
      const paths = perItem[i];
      if (!paths.length) { out.push(it); return; }
      const images = refsFor(agentId, paths, window);
      out.push(images.length ? { ...it, images } : it);
    } catch { out.push(it); }
  });
  return out;
}

/** On an older page: a still-pending turn is the live end's, and a batch placed after the page's
 *  last log item is drawn by the newer page unless it is older than that page's first item. */
function keepOlderPageSynthetics(items: any[], logged: Set<any>, cursorMs: number | null): any[] {
  let lastLogged = -1;
  items.forEach((it, i) => { if (logged.has(it)) lastLogged = i; });
  return items.filter((it, i) => {
    if (logged.has(it)) return true;
    if (it?.queued && typeof it.uuid === 'string' && it.uuid.startsWith('queued:')) return false;
    if (i < lastLogged) return true;
    const t = Date.parse(String(it?.ts ?? ''));
    return it?.kind === 'queued_batch' && cursorMs !== null && !Number.isNaN(t) && t < cursorMs;
  });
}

/** The seat's compaction notices placed in a window by time (services/conversation-store.ts), unless the seat's
 *  `show_compaction` is off. The newest window holds the notices from its first item on (all of them when it is the
 *  whole chat); an older page holds those from its first item (or from the start, when nothing is older) up to its
 *  cursor, so pages chained by oldest_id carry each notice once. Fail-open: the window as it was. */
function withNotices(agentId: string, win: { items: any[]; has_older: boolean; cursor_ts: string | null }, limit: number,
                     before: string | null): any[] {
  try {
    if (!readSeatSettings(dataDir(), agentId).show_compaction) return win.items;
    const notices = compactionNotices(agentId);
    if (!notices.length) return win.items;
    const firstTs = win.items.find((it) => it?.ts)?.ts ?? null;
    const whole = before === null ? win.items.length < limit : !win.has_older;
    return mergeNotices(win.items, notices, { from: whole ? null : firstTs, to: before === null ? null : win.cursor_ts });
  } catch {
    return win.items;
  }
}

/** The calls made on this agent's page, each where it started, as the chat's call card (lib/agentCalls.ts). The
 *  same window bounds as the compaction notices. Fail-open: no journals, or a bad one, and the chat is unchanged. */
function withCalls(agentId: string, win: { items: any[]; has_older: boolean; cursor_ts: string | null }, limit: number,
                   before: string | null, body: { items: any[]; render_items: any[] }): { items: any[]; render_items: any[] } {
  try {
    // A handed-off call's card is its hand-off line (logItems): never also where it started.
    const handed = readHandoffs(dataDir());
    const calls = listAgentCalls(dataDir(), agentId).filter((c) => !handed[c.call_id]);
    if (!calls.length) return body;
    const firstTs = win.items.find((it) => it?.ts)?.ts ?? null;
    const whole = before === null ? win.items.length < limit : !win.has_older;
    return mergeCallMarkers(body, calls, dataDir(), { from: whole ? null : firstTs, to: before === null ? null : win.cursor_ts });
  } catch {
    return body;
  }
}

/** This agent's chat items as the chat shows them (every generation this box can read, the newest `limit`), for the
 *  readers that need them outside a page: a call's work anchors. Empty when nothing resolves. */
export function chatItemsFor(agentId: string, limit = 2000): any[] {
  try {
    const { path, sid } = resolveTranscriptPath(agentId);
    if (!path) return [];
    const current = path;
    let segs: Segment[];
    try { segs = chatSegments(agentId, sid, current); } catch { segs = [{ session: sid ?? '', generation: null, promotedAt: null, file: () => current }]; }
    return chatWindow(agentId, segs, limit, null)?.items ?? [];
  } catch { return []; }
}

/** The items the chat page shows for this agent's newest `limit`: the same assembly as GET /:id/transcript with no
 *  cursor (held operator messages merged, wake replies dropped, notices and call markers placed), so the call
 *  context's Python twin, which reads that route, sees the same chat. [] when nothing resolves. */
export function pageItemsFor(agentId: string, limit = 300): any[] {
  try {
    const { path, sid } = resolveTranscriptPath(agentId);
    if (!path) return [];
    const current = path;
    readableLog(current);
    let segs: Segment[];
    try { segs = chatSegments(agentId, sid, current); } catch { segs = [{ session: sid ?? '', generation: null, promotedAt: null, file: () => current }]; }
    const win = chatWindow(agentId, segs, limit, null);
    if (!win) return [];
    return withCalls(agentId, win, limit, null, finishWindow(withNotices(agentId, win, limit, null), agentId, true)).items;
  } catch { return []; }
}

// GET /api/agents/:id/transcript?limit= -> v2 envelope, the newest page
// { agent_id, session_id, grammar_version, items, render_items, has_older, oldest_id }
// GET /api/agents/:id/transcript?before=<cursor>&limit= -> the page just older than the cursor (a
// page's own `oldest_id`, or an item's uuid), the same envelope.
// The first page defaults to FIRST_PAGE_LIMIT items (was 150: a seat switch loads one page per
// visible seat, and 150 items of a busy seat were 230 KB-1 MB each); has_older/oldest_id on it
// tell the client where the rest is. A cursor page still defaults to PAGE_LIMIT.
const PAGE_LIMIT = 100;
export const FIRST_PAGE_LIMIT = 40;

/** `size:mtime` of a file, '' when it is not there. */
function fileSig(p: string): string {
  try { const st = statSync(p); return `${st.size}:${st.mtimeMs}`; } catch { return ''; }
}

/** The call journals as one string: the directory's mtime (a journal added or removed) and every
 *  journal's size and mtime (one rewritten in place: a call that ended, a rating), since the page
 *  shows each call's state. Several hundred stats, 4-5 ms on a busy install, so the answer is kept
 *  for CALLS_SIG_TTL_MS: a seat switch's 20 requests pay once, and a call's state is at most a
 *  second late on the page. '' with no directory. */
export const CALLS_SIG_TTL_MS = 1000;
let callsSigKept: { dir: string; at: number; sig: string } | null = null;
function callsSig(dir: string): string {
  const now = Date.now();
  if (callsSigKept && callsSigKept.dir === dir && now - callsSigKept.at < CALLS_SIG_TTL_MS) return callsSigKept.sig;
  const sig = callsSigNow(dir);
  callsSigKept = { dir, at: now, sig };
  return sig;
}
function callsSigNow(dir: string): string {
  try {
    const d = statSync(dir);
    let n = 0; let size = 0; let newest = 0;
    for (const f of readdirSync(dir)) {
      if (!f.startsWith('vc_') || !f.endsWith('.json')) continue;
      try { const st = statSync(join(dir, f)); n += 1; size += st.size; if (st.mtimeMs > newest) newest = st.mtimeMs; } catch { /* gone between readdir and stat */ }
    }
    return `${d.mtimeMs}:${n}:${size}:${newest}`;
  } catch { return ''; }
}

/** Everything the first page is made of, as one string: the log's identity and bytes, the
 *  lineage store, the seat's queued rows, its cut turns and warm-up mark, its settings, the
 *  compaction store and its call journals. The kept page (services/transcript-first-page-cache.ts)
 *  is served while this is the string it was built under. Not covered: a referenced image
 *  appearing on disk later (its id is stable; it shows with the next change). */
function firstPageValidity(agentId: string, sid: string | null, current: string): string {
  const st = statSync(current);
  const dir = dataDir();
  const store = storePath(dir);
  return [
    current, `${st.ino}:${st.size}:${st.mtimeMs}`, sid ?? '',
    identityStoreSig(),
    queuedStateSig(agentId),
    interruptedTurnsSig(agentId),
    warmupMarkFor(agentId, sid).sig,
    fileSig(join(dir, 'state', 'seat-settings.json')), fileSig(join(dir, 'state', 'seat-settings-default.json')),
    fileSig(store), fileSig(`${store}-wal`),
    callsSig(callsDir(dir)), fileSig(join(dir, 'state', 'voice-call-handoffs.json')), fileSig(join(dir, 'state', 'voice-call-ratings.json')),
  ].join('|');
}

/** `Server-Timing: read;dur=<ms>` (and `cache;desc=hit|miss` for a first page): the server's own
 *  time on the request, resolution to body, so a slow seat switch can be read from the browser's
 *  network panel without guessing at the network's share. */
function serverTiming(res: Response, startedAt: number, cache?: 'hit' | 'miss'): void {
  const dur = (performance.now() - startedAt).toFixed(1);
  res.set('Server-Timing', cache ? `read;dur=${dur}, cache;desc=${cache}` : `read;dur=${dur}`);
}

router.get('/:id/transcript', (req: Request, res: Response) => {
  const startedAt = performance.now();
  const agentId = String(req.params.id);
  const before = typeof req.query.before === 'string' && req.query.before ? req.query.before.slice(0, 300) : null;
  const limit = Math.min(parseInt(String(req.query.limit)) || (before === null ? FIRST_PAGE_LIMIT : PAGE_LIMIT), 500);
  const paged = (body: Record<string, unknown>) => before === null ? body : { has_older: false, oldest_id: null, ...body };
  // Resolution is INSIDE the try (gm ruling on DEC-1790239929422621): it touches the filesystem,
  // tmux and now a sqlite index, and this route is reachable on a PUBLIC ingress — an
  // unhandled throw here is a 500, not an empty pane.
  let path: string | null = null;
  let sid: string | null = null;
  try {
    ({ path, sid } = resolveTranscriptPath(agentId));
  } catch (err) {
    res.json(paged({ agent_id: agentId, session_id: null, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: String(err) }));
    return;
  }
  if (!path) {
    res.json(paged({ agent_id: agentId, session_id: sid, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: 'no transcript resolved' }));
    return;
  }
  try {
    // NOTE: match the Gemini/Antigravity brain-log path via 'antigravity-cli'
    // ONLY. A bare 'brain' substring also matches Claude agents whose project
    // dir contains "brain" (e.g. second-brain-dev, cwd ~/repos/second-brain),
    // misclassifying their Claude JSONL as Antigravity and yielding 0 items.
    // Real Gemini brain logs always live under .gemini/antigravity-cli/brain/,
    // so 'antigravity-cli' + the content sniff in logItems cover them (segmentView).
    // The stitched chat: every earlier generation of the lineage this box can read, above this
    // one. Fail-open: anything wrong with the chain and the chat is this file alone, as before.
    // This generation's own log first, outside the fail-open views: unreadable is an error here.
    const current = path;
    // `read`: every earlier generation's file the window reached into (the kept first page is
    // valid only while those are unchanged too; the current log is covered by firstPageValidity).
    // `missing`: the past segments whose file was not there when the page was built; the kept page
    // asks for them again on every hit, so one that appears is seen.
    // The divider between two generations is for operators only (?dividers=1); the chat is one conversation.
    const dividers = req.query.dividers === '1' || req.query.dividers === 'true';
    const read = new Set<string>();
    const missing: Segment[] = [];
    const build = (): Record<string, unknown> | null => {
      readableLog(current);
      let segs: Segment[];
      try { segs = chatSegments(agentId, sid, current); } catch { segs = [{ session: sid ?? '', generation: null, promotedAt: null, file: () => current }]; }
      segs = segs.map((s) => ({ ...s, file: () => { const f = s.file(); if (f && f !== current) read.add(f); else if (!f && s.refind) missing.push(s); return f; } }));
      const win = chatWindow(agentId, segs, limit, before, { dividers });
      if (!win) return null;
      const cursorMs = win.cursor_ts ? Date.parse(String(win.cursor_ts)) : NaN;
      const body = withCalls(agentId, win, limit, before,
        finishWindow(withNotices(agentId, win, limit, before), agentId, true, before === null ? null : { cursorMs: Number.isNaN(cursorMs) ? null : cursorMs }));
      return { agent_id: agentId, session_id: sid, grammar_version: GRAMMAR_VERSION, ...body, has_older: win.has_older, oldest_id: win.oldest_id };
    };
    if (before !== null) {
      const env = build();
      serverTiming(res, startedAt);
      if (!env) {
        res.json(paged({ agent_id: agentId, session_id: sid, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: 'unknown cursor' }));
        return;
      }
      res.json(env);
      return;
    }
    // The first page: kept serialized (and gzipped) per seat while nothing it is made of changed,
    // so a seat switch that loads every visible seat's page again costs a stat and a send each.
    // A cursor page is not kept: it is read once, scrolling back.
    let built = false;
    const page = firstPage(agentId, limit, `${firstPageValidity(agentId, sid, current)}|dividers=${dividers}`, () => {
      built = true;
      const body = build() ?? {};
      return { body, deps: [...read], probes: missing.map((s) => () => s.refind?.() ?? '') };
    });
    serverTiming(res, startedAt, built ? 'miss' : 'hit');
    res.set('Content-Type', 'application/json; charset=utf-8');
    res.set('Vary', 'Accept-Encoding');
    if (page.gzip && req.acceptsEncodings('gzip')) {
      res.set('Content-Encoding', 'gzip');
      res.set('ETag', page.etag.replace(/"$/, '-gz"'));
      res.send(page.gzip);
    } else {
      res.set('ETag', page.etag);
      res.send(page.json);
    }
  } catch (err) {
    res.json(paged({ agent_id: agentId, session_id: sid, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: String(err) }));
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

// GET /api/agents/:id/call-context?budget=<tokens>&calls=<owner|arturo>&exclude=<call id> -> what a call with this agent
// knows at start (lib/callContext.ts; the Python twin services/arturo/thread_context.py, same bytes): the agent's chat
// plus the owner's calls, oldest first, every line stamped, newest verbatim within the budget, older summarized.
// Loopback only (a voice engine on this box), never through a proxy; scoped like transcript.md (router.param above);
// every piece through the same secret redactor. `calls` defaults to this agent; "arturo" = Arturo's own calls.
const FORWARDED = ['x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'forwarded', 'x-real-ip',
  'tailscale-funnel-request', 'tailscale-user-login', 'x-orchestra-peer'];
/** Whose calls may merge in: an owner is scoped like :id (router.param), so an out-of-scope owner is the same 404.
 *  Arturo's own calls (owner null) are the operator's: only a principal that sees every agent reads them. */
export function callOwnerVisible(p: ReturnType<typeof principal>, owner: string | null,
  resolveTags: (id: string) => unknown = defaultResolveTags): boolean {
  if (owner === null) return !p?.clientScope && p?.allowedAgents === '*';
  return canPrincipalSeeAgent(p, { id: owner, tags: resolveTags(owner) });
}

/** A request from a process on this box, not relayed by any proxy (exported for its test). */
export function directLoopback(req: { socket?: { remoteAddress?: string }; headers: Record<string, unknown> }): boolean {
  return isLoopback(req.socket?.remoteAddress) && !FORWARDED.some((h) => req.headers[h] !== undefined);
}
router.get('/:id/call-context', (req: Request, res: Response) => {
  if (!directLoopback(req)) {
    res.status(404).type('text/plain').send('Not found.');
    return;
  }
  const agentId = String(req.params.id);
  const callsQ = typeof req.query.calls === 'string' ? req.query.calls : '';
  if (callsQ && callsQ !== 'arturo' && !isSafeAgentId(callsQ)) { res.status(400).type('text/plain').send('bad calls'); return; }
  const owner = callsQ === 'arturo' ? null : (callsQ || agentId);
  if (!callOwnerVisible(principal(req), owner)) { res.status(404).json({ error: `Agent '${owner ?? 'arturo'}' not found` }); return; }
  const exQ = typeof req.query.exclude === 'string' ? req.query.exclude : '';
  if (exQ && !CALL_ID_RE.test(exQ)) { res.status(400).type('text/plain').send('bad exclude'); return; }
  try {
    const items = pageItemsFor(agentId, 300);            // the chat exactly as GET /:id/transcript shows it
    const [tz, tzLabel] = shawTz(dataDir());
    const exact = [readGatewayToken()].filter((v) => v.length >= 8);   // the 8-char floor of the twin and redactSecrets
    // ONE redaction, inside the builder, on each piece BEFORE it is clipped (a secret cut at the clip boundary would
    // otherwise ship as a near-complete prefix): this install's token by exact value FIRST (a token containing "sk-"
    // must not be half-eaten by a shape), then the twin's shapes, then the full redactor. The full redactor is a
    // superset of the twin's, so on secret-shaped text the route's bytes differ from the twin's, by design.
    const text = buildCallContext({
      chatAgent: agentId, callOwner: owner, items, journals: readJournals(callsDir(dataDir()), Date.now() / 1000),
      exclude: exQ || null, budgetTokens: clampBudget(req.query.budget), tz, tzLabel, operatorLabel: operatorName(dataDir()),
      redact: (t: string) => redactSecrets(redact(exact.reduce((x, v) => x.split(v).join('[redacted]'), t)), exact).text,
    });
    res.status(200).type('text/plain; charset=utf-8').set('Cache-Control', 'no-store').send(text);
  } catch (err) {
    console.error('[call-context]', String(err).slice(0, 200));
    res.status(500).type('text/plain').send('Could not build the call context.');
  }
});

// A subagent id as Claude writes it in agent-<id>.jsonl (every depth sits flat in <sid>/subagents/).
export const SAFE_SUBAGENT_ID = /^[a-f0-9]{6,40}$/;

/** One subagent's own log under the seat's current Claude session; path null when the seat has no
 *  Claude session here or no such subagent. Both ids are checked before any path is built. */
export function resolveSubagentTranscriptPath(agentId: string, subagentId: string,
  resolveSeat: (id: string) => { path: string | null; sid: string | null } = resolveTranscriptPath,
): { path: string | null; sid: string | null } {
  if (!isSafeAgentId(agentId) || !SAFE_SUBAGENT_ID.test(subagentId)) return { path: null, sid: null };
  const { path, sid } = resolveSeat(agentId);
  if (!path || !path.endsWith('.jsonl')) return { path: null, sid };
  const sub = join(path.slice(0, -'.jsonl'.length), 'subagents', `agent-${subagentId}.jsonl`);
  return { path: existsSync(sub) ? sub : null, sid };
}

// GET /api/agents/:id/subagents/:aid/transcript?limit= -> v2 envelope (+ subagent_id) of one subagent's own
// log, any depth (the agent-tree drill-in, read-only). 404 when the seat has no such subagent on this box.
// Scoped exactly like /:id/transcript: router.param('id') above runs first.
export function handleSubagentTranscript(req: Request, res: Response,
  resolve: (agentId: string, subagentId: string) => { path: string | null; sid: string | null } = resolveSubagentTranscriptPath): void {
  const agentId = String(req.params.id);
  const subagentId = String(req.params.aid);
  if (!isSafeAgentId(agentId) || !SAFE_SUBAGENT_ID.test(subagentId)) {
    res.status(400).json({ error: 'bad agent or subagent id' });
    return;
  }
  const limit = Math.min(parseInt(String(req.query.limit)) || 150, 500);
  let found: { path: string | null; sid: string | null };
  try { found = resolve(agentId, subagentId); } catch { found = { path: null, sid: null }; }
  const { path, sid } = found;
  if (!path) { res.status(404).json({ error: 'no such subagent for this seat on this box' }); return; }
  try {
    const lines = readFileSync(path, 'utf-8').split('\n');
    res.json({ ...normalizeTranscript(lines, agentId, sid, limit, false, false, null, false, true), subagent_id: subagentId });
  } catch (err) {
    // Never the exception text to the client: a read error names the absolute log path.
    console.error(`[subagent-transcript] ${agentId}/${subagentId}:`, err);
    res.status(500).json({ agent_id: agentId, session_id: sid, subagent_id: subagentId, grammar_version: GRAMMAR_VERSION, items: [], render_items: [], error: 'transcript unreadable' });
  }
}
router.get('/:id/subagents/:aid/transcript', (req: Request, res: Response) => handleSubagentTranscript(req, res));

export default router;
