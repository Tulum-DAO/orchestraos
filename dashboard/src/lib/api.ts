const BASE = '/api';

function handleAuthError(res: Response, path: string) {
  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.location.href = '/login';
  }
}

async function get<T = any>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
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

export const fetchClients = () => get('/clients');
export const fetchClientEcosystem = (id: string) => get(`/clients/${id}/ecosystem`);

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
export const fetchExperiments = () => get('/experiments');
export const createExperiment = (data: any) => post('/experiments', data);
export const fetchSkills = () => get('/skills');
export const assignSkill = (skillId: string, agentId: string) => post('/skills/assign', { skill_id: skillId, agent_id: agentId });
export const unassignSkill = (skillId: string, agentId: string) => post('/skills/unassign', { skill_id: skillId, agent_id: agentId });
export const getAdaptiveAgents = (userId: string = 'operator') =>
  get<{ agent_id: string; score: number; signals: Record<string, number> }[]>(`/adaptive/${userId}/agents`);

export const spawnAgent = (id: string, task?: string) => post(`/agents/${id}/spawn`, task ? { task } : undefined);
export const killAgent = (id: string) => post(`/agents/${id}/kill`);
export const messageAgent = (id: string, message: string) => post(`/agents/${id}/message`, { message });

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
}
export async function sendAgentKey(id: string, key: string, confirm: boolean): Promise<AgentKeyResult> {
  const res = await fetch(`${BASE}/agents/${encodeURIComponent(id)}/agent-key`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, confirm }),
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

export const getPendingInsights = async (userId: string = 'operator') => {
  const data = await get<any>(`/adaptive/${userId}/insights?status=pending`);
  return Array.isArray(data) ? data : data?.insights ?? [];
};

export const getAllInsights = async (userId: string = 'operator') => {
  const data = await get<any>(`/adaptive/${userId}/insights`);
  return Array.isArray(data) ? data : data?.insights ?? [];
};

export const respondToInsight = (userId: string, insightId: string, action: 'accept' | 'dismiss') =>
  post<any>(`/adaptive/${userId}/insights/${insightId}/respond`, { action });

export const getUserProfile = (userId: string = 'operator') =>
  get<any>(`/adaptive/${userId}/profile`);

export const updateUserProfile = (userId: string = 'operator', updates: any) =>
  patch<any>(`/adaptive/${userId}/profile`, updates);

export const fetchCampaigns = () => get('/campaigns');
export const fetchClientCampaigns = (clientSlug: string) => get(`/campaigns/${clientSlug}`);

export function connectActivitySSE(onEvent: (e: any) => void): EventSource {
  const source = new EventSource(`${BASE}/activity`);
  source.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch {} };
  return source;
}
