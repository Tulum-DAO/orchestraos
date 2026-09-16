export const TIER_COLORS: Record<string, string> = {
  T0: 'bg-purple-100 text-purple-800', T1: 'bg-blue-100 text-blue-800',
  T2: 'bg-green-100 text-green-800', T3: 'bg-gray-100 text-gray-800',
};

export const PRIORITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-800', high: 'bg-orange-100 text-orange-800',
  medium: 'bg-yellow-100 text-yellow-800', low: 'bg-green-100 text-green-800',
  P0: 'bg-red-100 text-red-800', P1: 'bg-orange-100 text-orange-800',
  P2: 'bg-yellow-100 text-yellow-800', P3: 'bg-green-100 text-green-800',
};

export const STATUS_DOT: Record<string, string> = {
  running: 'bg-green-500', stopped: 'bg-red-500', spawning: 'bg-yellow-500', unknown: 'bg-gray-400',
};

export const TASK_STATUS_GROUPS: Record<string, string[]> = {
  Pending: ['pending', 'routing', 'queued'],
  'In Progress': ['in_progress', 'active', 'running', 'partial', 'review'],
  Blocked: ['blocked', 'context_wait'],
  Completed: ['completed', 'complete', 'done', 'reported'],
  Failed: ['failed', 'error'],
};

export const VOICE_AGENTS: Record<string, { label: string; description: string }> = {
  'gemini-gm': { label: 'Jarvis (Gemini GM)', description: 'Full visibility across all projects, clients, and infrastructure' },
  gm: { label: 'Jarvis', description: 'Full visibility across all projects, clients, and infrastructure' },
  'pm-products': { label: 'Products PM', description: 'Your internal products and tools' },
  'pm-clients': { label: 'Clients PM', description: 'Your client projects (e.g. Acme, Northwind)' },
  'pm-infra': { label: 'Infra PM', description: 'Agent Orchestra, Dashboard, VPS Operations' },
  // The next-gen co-worker POC (isolated soak): write-tools gated in shadow + confirm,
  // reachable by voice via the isolated :5070 bridge. Distinct from live Jarvis (gm).
  'jarvis-poc': { label: 'Jarvis · Co-Worker (POC · soak)', description: 'Next-gen co-worker — answers + proposes real work (writes shadow + confirm). Isolated soak.' },
};

// A soak/POC voice card whose ElevenLabs agent isn't wired yet carries this
// placeholder agent_id — the card renders but its call button is disabled until
// the operator sets the real agent_id in state/voice-agents.json.
export const VOICE_AGENT_PENDING_ID = 'PENDING_ELEVENLABS_AGENT_ID';

export const DEPLOYMENT_STATES = ['dev', 'testing', 'vetted', 'staged', 'production'] as const;
export const DEPLOYMENT_COLORS: Record<string, string> = {
  dev: 'bg-neutral-700 text-neutral-400',
  testing: 'bg-blue-500/15 text-blue-400',
  vetted: 'bg-amber-500/15 text-amber-400',
  staged: 'bg-purple-500/15 text-purple-400',
  production: 'bg-green-500/15 text-green-400',
};

export const APPROVAL_CATEGORY_COLORS: Record<string, string> = {
  external_comms: 'bg-purple-500/20 text-purple-300',
  deployment: 'bg-blue-500/20 text-blue-300',
  financial: 'bg-red-500/20 text-red-300',
  destructive: 'bg-red-600/20 text-red-200',
  git_push: 'bg-neutral-700 text-neutral-300',
  client_contact: 'bg-amber-500/20 text-amber-300',
};
