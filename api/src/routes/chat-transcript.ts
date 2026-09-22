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
import { readFileSync, existsSync, readdirSync, statSync, openSync, readSync, closeSync, readlinkSync } from 'fs';
import { join } from 'path';
import { execFileSync } from 'child_process';
import { mergeQueuedItems } from '../services/queued-merge.js';
import { homedir } from 'os';

const router = Router();

const HOME = process.env.HOME || homedir();
const ORCH_DIR = process.env.ORCHESTRA_DIR || join(HOME, 'scripts/agent-orchestra');
const CLAUDE_PROJECTS = join(HOME, '.claude', 'projects');
const GEMINI_BRAIN = join(HOME, '.gemini', 'antigravity-cli', 'brain');

function loadAgentSessions(): Record<string, any> {
  try { return JSON.parse(readFileSync(join(ORCH_DIR, 'state', 'agent-sessions.json'), 'utf-8')); }
  catch { return {}; }
}

// Live Antigravity / Gemini conversation ID extracted directly from pane process fds
function liveGeminiSid(tmuxSession: string): string | null {
  try {
    const panePid = execFileSync('tmux',
      ['list-panes', '-t', tmuxSession, '-F', '#{pane_pid}'],
      { timeout: 2000 }).toString().trim().split('\n')[0];
    if (!panePid) return null;
    let childPids: string[] = [];
    try {
      childPids = execFileSync('pgrep', ['-P', panePid], { timeout: 2000 })
        .toString().trim().split('\n').filter(Boolean);
    } catch {
      return null;
    }
    for (const pid of childPids) {
      try {
        const fds = readdirSync(`/proc/${pid}/fd`);
        for (const fd of fds) {
          try {
            const link = readlinkSync(`/proc/${pid}/fd/${fd}`);
            const m = link.match(/antigravity-cli\/(?:presence|brain)\/([0-9a-f-]{36})/);
            if (m && m[1]) return m[1];
          } catch {}
        }
      } catch {}
    }
  } catch {}
  return null;
}

// Tier-0 sid resolution: the pane's hook-event file (state-event-hook.py writes
// the LIVE session_id on every Stop/PreToolUse).
function hookEventSid(tmuxSession: string): { sid: string | null; cwd: string | null } {
  try {
    const pane = execFileSync('tmux',
      ['display-message', '-t', tmuxSession, '-p', '#{pane_id}'],
      { timeout: 3000 }).toString().trim().replace('%', '');
    if (!pane) return { sid: null, cwd: null };
    const ev = JSON.parse(readFileSync(
      join(ORCH_DIR, 'state', 'agent-events', 'panes', `${pane}.json`), 'utf-8'));
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

// Resolve an agent id to its transcript JSONL path (re-read fresh per request).
// Exported for the F1 streaming lane (transcript-stream.ts) — same resolution,
// same file, so poll and stream can never disagree on WHICH transcript.
export function resolveTranscriptPath(agentId: string): { path: string | null; sid: string | null } {
  const sessions = loadAgentSessions();
  const e = sessions[agentId] || {};
  const tmuxSession = String(e.tmux_session || agentId);

  // 0a. Live Gemini/Antigravity process check (inspect active process in tmux pane)
  const gSid = liveGeminiSid(tmuxSession);
  if (gSid) {
    const gPath = join(GEMINI_BRAIN, gSid, '.system_generated', 'logs', 'transcript.jsonl');
    if (existsSync(gPath)) return { path: gPath, sid: gSid };
  }

  // 0b. Hook event sid for live Claude process
  const hook = hookEventSid(tmuxSession);
  let sid: string | null = hook.sid;
  if (hook.sid && hook.cwd) {
    const p = join(CLAUDE_PROJECTS, hook.cwd.replace(/[/.]/g, '-'), `${hook.sid}.jsonl`);
    if (existsSync(p)) return { path: p, sid: hook.sid };
  }

  // 1. state/agents/<agentId>.json session_id (snapshot daemon updates this on rotations)
  try {
    const stateFile = join(ORCH_DIR, 'state', 'agents', `${agentId}.json`);
    if (existsSync(stateFile)) {
      const st = JSON.parse(readFileSync(stateFile, 'utf-8'));
      if (st.session_id) {
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
  if (e.conversation_path && existsSync(e.conversation_path)) {
    return { path: e.conversation_path, sid: e.session_id || null };
  }

  // 3. Direct session_id check (Claude or Gemini brain)
  if (e.session_id) {
    const gPath = join(GEMINI_BRAIN, e.session_id, '.system_generated', 'logs', 'transcript.jsonl');
    if (existsSync(gPath)) return { path: gPath, sid: e.session_id };
  }

  // 4. Resume command regex
  sid = sid || e.session_id || null;
  if (!sid && typeof e.resume_command === 'string') {
    const magy = e.resume_command.match(/--conversation\s+([0-9a-f-]{36})/);
    if (magy) {
      const gPath = join(GEMINI_BRAIN, magy[1], '.system_generated', 'logs', 'transcript.jsonl');
      if (existsSync(gPath)) return { path: gPath, sid: magy[1] };
    }
    const m = e.resume_command.match(/--resume\s+([0-9a-f-]{36})/);
    sid = m ? m[1] : null;
  }

  // 6. If agent is Gemini or name starts with gemini, search Antigravity brains by declaration
  if (agentId.startsWith('gemini') || agentId === 'agy-ops' || agentId.startsWith('agy')) {
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
      const js = readdirSync(pdir)
        .filter((f) => f.endsWith('.jsonl'))
        .map((f) => ({ f, m: statSync(join(pdir!, f)).mtimeMs }))
        .sort((a, b) => b.m - a.m);
      if (js.length) return { path: join(pdir, js[0].f), sid: js[0].f.replace('.jsonl', '') };
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
  let t = String(text ?? '');
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

function normalizeClaudeEntry(o: any): any[] {
  const t = o?.type;
  if (t !== 'user' && t !== 'assistant') return [];
  if (o.isSidechain === true || o.isCompactSummary === true) return [];
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
      let text = (o.content || '').trim();
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
      out = s(input.CommandLine || input.command || '');
      break;
    case 'Read':
    case 'view_file':
      out = shortPath(s(input.AbsolutePath || input.file_path || input.path || ''));
      break;
    case 'Edit':
    case 'replace_file_content':
    case 'Write':
    case 'write_to_file':
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
      out.id = it.id; out.ts = it.ts; out.uuid = it.uuid;
      return out;
    }
    if (it.kind === 'text' && it.role === 'user' && !firstUserSeen) {
      firstUserSeen = true;
      const text = (it.text || '').trim();
      if (text.length > 800 || /^You are\b/.test(text)) {
        return { kind: it.kind, role: it.role, text: it.text, is_system: true, ts: it.ts, uuid: it.uuid };
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
  } else {
    for (const line of lines) {
      if (!line.trim()) continue;
      try { items.push(...normalizeClaudeEntry(JSON.parse(line))); } catch { /* skip bad line */ }
    }
  }
  let windowed = enrichItems(items).slice(-limit);
  // queued-native-render (P1): merge still-pending phone/watch held turns (B1) +
  // processed-batch divs (B2) from msg_store, interleaved by ts. POLL-ONLY —
  // the SSE tailer never sets includeQueued, so its monotonic-append delta stays
  // free of the ephemeral B1 synthetics (§3e/A1). Fail-open inside mergeQueuedItems.
  if (includeQueued) windowed = mergeQueuedItems(windowed, agentId);
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
  const { path, sid } = resolveTranscriptPath(agentId);
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

export default router;
