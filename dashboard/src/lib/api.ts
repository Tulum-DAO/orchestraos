import { operatorUserId } from './runtimeConfig';
const BASE = '/api';

function handleAuthError(res: Response, path: string) {
  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.location.href = '/login';
  }
}

/**
 * A READ MUST NOT HANG FOREVER. Found by the LIVE port proof: with the API timing out rather than
 * refusing, a GET never settles, react-query will not start a second fetch while one is in
 * flight, and the dashboard stays wedged in STALE even after the API comes back — it only
 * recovers on a manual reload. A bounded read means the next poll can always run.
 */
async function get<T = any>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(12_000) });
  if (!res.ok) { handleAuthError(res, path); throw new Error(`API ${path}: ${res.status}`); }
  return res.json();
}

export async function post<T = any>(path: string, body?: any): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  if (!res.ok) { handleAuthError(res, path); throw new Error(`API ${path}: ${res.status}`); }
  return res.json();
}

async function patch<T = any>(path: string, body: any): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) { handleAuthError(res, path); throw new Error(`API ${path}: ${res.status}`); }
  return res.json();
}

export const updateTaskDeployment = (project: string, phaseIdx: number, taskIdx: number, state: string) =>
  patch(`/roadmaps/${project}/tasks/${phaseIdx}/${taskIdx}`, { deployment_state: state });

export const patchTask = (project: string, phaseIdx: number, taskIdx: number, updates: { status?: string; name?: string; description?: string }) =>
  patch(`/roadmaps/${project}/phases/${phaseIdx}/tasks/${taskIdx}`, updates);

export const createTask = (project: string, phaseIdx: number, task: { name: string; description?: string; status?: string }) =>
  post(`/roadmaps/${project}/phases/${phaseIdx}/tasks`, task);

export const deleteTask = (project: string, phaseIdx: number, taskIdx: number): Promise<unknown> =>
  fetch(`/api/roadmaps/${project}/phases/${phaseIdx}/tasks/${taskIdx}`, { method: 'DELETE' }).then(r => r.json());


// 2D Agents View (spec §16): the ONLY two message helpers the view may use. Both hit the
// canonical SQLite `messages` table. fetchPairCounts feeds every count and thickness on a
// connection line; fetchPairMessages feeds the conversation panel, including "load older"
// via the `before` cursor. Do not reach for /messages/thread/:conversationId or
// /messages/conversations/:agentId from this view — they read JSONL, and disagreeing with
// this table is exactly the "49 vs 40" bug the spec exists to kill.
export interface PairCountRow { a: string; b: string; count: number; last_at: string | null }
export const fetchPairCounts = (hours = 24, asof?: string | null) => {
  const q = new URLSearchParams({ hours: String(hours) });
  if (asof) q.set('asof', asof);   // omitted entirely means live
  return get<{ window_hours: number; asof: string | null; pairs: PairCountRow[] }>(
    `/messages/pair-counts?${q}`);
};

export interface PairMessage {
  id: string; conversation_id: string | null; from_agent: string; to_agent: string;
  type: string | null; subject: string | null; body: string | null; priority: string | null;
  status: string | null; created_at: string | null;
  delivered_at: string | null; acknowledged_at: string | null;
}
export interface PairPage {
  a: string; b: string; total_in_window: number; total_all_time: number; window_hours: number;
  /** Echo of the moment the page was computed at; null when live. */
  asof: string | null;
  messages: PairMessage[]; next_before: string | null; has_more: boolean;
}
export const fetchPairMessages = (
  a: string, b: string,
  opts: { limit?: number; before?: string | null; hours?: number; asof?: string | null } = {},
) => {
  const q = new URLSearchParams({ limit: String(opts.limit ?? 40), hours: String(opts.hours ?? 24) });
  if (opts.before) q.set('before', opts.before);
  if (opts.asof) q.set('asof', opts.asof);
  return get<PairPage>(`/messages/pair/${encodeURIComponent(a)}/${encodeURIComponent(b)}?${q}`);
};

/** Latest seat-to-seat mail across the fleet, newest first. Same canonical `messages` table
 *  as fetchPairCounts, so the ticker and the line counts describe the same traffic. Carries
 *  direction and type, which the per-pair counts do not — that is what lets a travelling dot
 *  mean something (spec §2: if a dot moves, a message is actually moving). */
export interface RecentMessageRow {
  id: string; conversation_id: string | null; from_agent: string; to_agent: string;
  type: string | null; subject: string | null; priority: string | null; status: string | null;
  created_at: string | null; delivered_at: string | null; acknowledged_at: string | null;
}
export const fetchRecentMessages = (limit = 50) =>
  get<{ messages: RecentMessageRow[] }>(`/messages/recent?limit=${limit}`);

export const fetchProjects = () => get('/projects');
export const fetchProject = (slug: string) => get(`/projects/${slug}`);

export const fetchMe = () => get<{ username: string; role: string; allowed_agents: string | string[] }>('/me');
export const fetchAgents = () => get('/agents');
export const fetchTasks = () => get('/tasks');
export const fetchActivity = (limit = 100) => get(`/activity?limit=${limit}`);
export const fetchSystem = () => get('/system');
export const fetchContext = () => get('/memory/context');
export const fetchFacts = (project?: string) => get(`/memory/facts${project ? `?project=${project}` : ''}`);
export const fetchHandoffs = () => get('/memory/handoffs');
export const searchMemory = (q: string, project?: string) => get<{ results: any[] }>(`/memory/search?q=${encodeURIComponent(q)}${project ? `&project=${encodeURIComponent(project)}` : ''}`);
export const updateContext = (data: any) => patch('/memory/context', data);
export const fetchRoadmaps = () => get('/roadmaps');
export const fetchVoiceAgents = () => get('/voice/agents');
export const syncVoicePrompts = () => post('/voice/sync-prompts');
export const fetchCapabilities = () => get<Record<string, boolean>>('/capabilities');

// Voice-call transcript for the [voice-call:] card. No-throw status passthrough
// so the card can render stale/unavailable states: 200 {ok,call}; 404 gone;
// 503 transient (retry). Shape of `call`: {call_id,started_at,ended_at,status,
// turns:[{role:'user'|'arturo'|'tool',...}],summary}.
export interface VoiceTurn {
  role: 'user' | 'arturo' | 'tool' | string;
  text?: string;
  ts?: number;
  tool?: string;
  input?: unknown;
  result?: string;
  status?: string;
}
export interface VoiceCall {
  call_id: string;
  started_at?: number;
  ended_at?: number;
  status?: string;
  summary?: string;
  turns?: VoiceTurn[];
}
export interface VoiceCallResult { status: number; ok?: boolean; call?: VoiceCall; error?: string }
export async function fetchVoiceCall(callId: string): Promise<VoiceCallResult> {
  const res = await fetch(`${BASE}/voice/call?call_id=${encodeURIComponent(callId)}`);
  let body: Record<string, unknown> = {};
  try { body = await res.json(); } catch { /* non-json */ }
  return { status: res.status, ...body } as VoiceCallResult;
}
export const fetchApprovals = () => get('/approvals');
export const approveAction = (id: string) => post(`/approvals/${id}/approve`);
export const denyAction = (id: string) => post(`/approvals/${id}/deny`);
export const fetchAnalytics = () => get('/analytics');
export const fetchAnalyticsDashboard = () => get('/analytics/dashboard');
export const fetchWorkflows = () => get('/workflows');
export const fetchSkills = () => get('/skills');
export const assignSkill = (skillId: string, agentId: string) => post('/skills/assign', { skill_id: skillId, agent_id: agentId });
export const unassignSkill = (skillId: string, agentId: string) => post('/skills/unassign', { skill_id: skillId, agent_id: agentId });
export const getAdaptiveAgents = (userId: string = operatorUserId()) =>
  get<{ agent_id: string; score: number; signals: Record<string, number> }[]>(`/adaptive/${userId}/agents`);

export const spawnAgent = (id: string, task?: string) => post(`/agents/${id}/spawn`, task ? { task } : undefined);
export const killAgent = (id: string) => post(`/agents/${id}/kill`);
// messageAgent() DELETED 2026-09-30. It posted to /api/agents/:id/message, which wrote a
// file into queue/inbox/ that no live agent reads, and returned {sent:true} — so the UI
// reported success while the operator's instruction vanished. Use sendToAgent() from
// lib/agentSend.ts (POST /:id/send, msg_store-backed). The endpoint now 410s.

export const getAgentOutput = (id: string, lines = 50) => get(`/agents/${id}/output?lines=${lines}`);
export const injectToAgent = (id: string, text: string) => post(`/agents/${id}/inject`, { text });

// Verified inject (gateway-backed for VPS agents): does NOT throw on 409/502 so
// the caller can surface the busy/refusal contract. 200 {injected,output}; 409
// {busy,reason,state,activity,composer_text?,stranded?}; 502 delivery unverified.
export interface InjectResult {
  status: number;
  injected?: boolean;
  busy?: boolean;
  reason?: string;
  state?: string;
  activity?: string;
  composer_text?: string;
  stranded?: { text?: string; age_s?: number };
  output?: string[];
  error?: string;
}
export async function injectAgentVerified(id: string, text: string, force = false): Promise<InjectResult> {
  const res = await fetch(`${BASE}/agents/${encodeURIComponent(id)}/inject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, force }),
  });
  let body: Record<string, unknown> = {};
  try { body = await res.json(); } catch { /* non-json */ }
  return { status: res.status, ...body } as InjectResult;
}
export const sendKeyToAgent = (id: string, key: string) => post(`/agents/${id}/key`, { key });

// Answer a pending decision menu (OptionsCard two-phase). Does NOT throw on
// 428/409/403 so the card can drive its arm/confirm/disarm UX from the status.
// 200 {ok,sent}; 428 {needs_confirm,confirm_text}; 409 {error,state} (menu gone);
// 403 {error} (key not enabled); 502 gateway unreachable.
export interface AgentKeyResult {
  status: number;
  ok?: boolean;
  sent?: string;
  needs_confirm?: boolean;
  confirm_text?: string;
  state?: string;
  error?: string;
  /** 409 reason: instance_mismatch (a different menu is up now) | instance_unknown */
  reason?: string;
}
/** The menu a card rendered: the gateway answers only while that same menu is on screen. */
export interface MenuExpect { question: string; context?: string | null }

export async function sendAgentKey(id: string, key: string, confirm: boolean,
                                   expect?: MenuExpect): Promise<AgentKeyResult> {
  const res = await fetch(`${BASE}/agents/${encodeURIComponent(id)}/agent-key`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(expect ? { key, confirm, expect } : { key, confirm }),
  });
  let body: Record<string, unknown> = {};
  try { body = await res.json(); } catch { /* non-json */ }
  return { status: res.status, ...body } as AgentKeyResult;
}

export const fetchTransitStatus = () => get('/transit/status');
export const goDark = () => post('/transit/go-dark');
export const returnFromTransit = () => post('/transit/return');
export const fetchTransitReport = () => get('/transit/report');

// GM Auth flow — works directly on GM's tmux session
export const getGmAuthState = () => get<{ state: string; url?: string; output: string }>('/system/vps-auth/gm-state');
export const sendGmLogin = () => post('/system/vps-auth/send-login');
export const selectGmOption = (key?: string) => post('/system/vps-auth/select-option', key ? { key } : undefined);
export const submitGmAuthCode = (code: string) => post('/system/vps-auth/submit-code', { code });
export const notifyTelegramAuth = (url: string) => post('/system/vps-auth/notify-telegram', { url });

export const writeHandoff = (project: string, summary: string, filesChanged?: string[], nextSteps?: string[]) =>
  post('/memory/handoff', { project, summary, files_changed: filesChanged, next_steps: nextSteps });

export const sendJarvisMessage = (message: string) =>
  post<{ response: string; approvals_pending: number; agents_summary: any }>('/system/jarvis/message', { message });

export const getJarvisHistory = (n: number = 50) =>
  get<any[]>(`/system/jarvis/history?n=${n}`);

export const getPendingInsights = async (userId: string = operatorUserId()) => {
  const data = await get<any>(`/adaptive/${userId}/insights?status=pending`);
  return Array.isArray(data) ? data : data?.insights ?? [];
};

export const getAllInsights = async (userId: string = operatorUserId()) => {
  const data = await get<any>(`/adaptive/${userId}/insights`);
  return Array.isArray(data) ? data : data?.insights ?? [];
};

export const respondToInsight = (userId: string, insightId: string, action: 'accept' | 'dismiss') =>
  post<any>(`/adaptive/${userId}/insights/${insightId}/respond`, { action });

export const getUserProfile = (userId: string = operatorUserId()) =>
  get<any>(`/adaptive/${userId}/profile`);

export const updateUserProfile = (userId: string = operatorUserId(), updates: any) =>
  patch<any>(`/adaptive/${userId}/profile`, updates);


export function connectActivitySSE(onEvent: (e: any) => void): EventSource {
  const source = new EventSource(`${BASE}/activity`);
  source.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch {} };
  return source;
}

/**
 * The rows out of a `/api/agents` response, whichever shape it arrived in.
 *
 * The endpoint is served two ways in this fleet — a bare array, and `{ agents: [...] }` —
 * and three call sites each decided for themselves which to tolerate. Feed and AgentPage
 * accepted both; AgentRail accepted only the wrapped one and rendered three EMPTY SECTIONS
 * on the other, with no error. "Empty" and "broken" then look identical, which is the one
 * thing that file's own comments say must never happen.
 *
 * One helper so the three cannot drift apart again. An unrecognised shape yields `undefined`
 * — NOT `[]` — so a caller can still tell "no agents" from "we could not read the answer".
 */
export function agentRowsFrom(data: unknown): Array<Record<string, unknown>> | undefined {
  if (Array.isArray(data)) return data as Array<Record<string, unknown>>;
  const wrapped = (data as { agents?: unknown } | null | undefined)?.agents;
  return Array.isArray(wrapped) ? (wrapped as Array<Record<string, unknown>>) : undefined;
}
