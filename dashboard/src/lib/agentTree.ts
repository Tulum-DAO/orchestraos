/**
 * Agent tree view logic: seat -> subagent -> helper, from GET /api/agents/:id/tree (the JSON of
 * scripts/agent_tree.py). Pure, so it is unit-tested against real-shape fixtures; the component and
 * the hook only render and fetch. The same file ships in both dashboards (VPS + OrchestraOS).
 */

export type AgentTreeState = 'running' | 'waiting' | 'finished' | 'stale';

/** One node of the tree (the API contract; the last four fields are extras, all optional here). */
export interface AgentTreeNode {
  agent_id: string;
  parent_agent_id: string | null;
  depth: number;
  type: string;
  label: string;
  state: AgentTreeState;
  started_at: string | null;
  last_activity_at: string | null;
  current_tool: string | null;
  last_tool?: string | null;
  tokens_own: number;
  tokens_rollup: number;
  tokens_cache_read?: number;
  ctx_tokens?: number | null;
  elapsed_s: number | null;
  transcript_path?: string;
  children: AgentTreeNode[];
  /** Root only: the API echoes whether it honoured ?all=1. Absent = the API ignores the flag. */
  all_finished?: boolean;
}

export interface AgentTreeRow {
  id: string;
  depth: number;
  label: string;
  type: string;
  state: AgentTreeState;
  /** Tailwind classes for the state dot. */
  dot: string;
  /** Tool text, or "waiting on X" for a waiting row. */
  tool: string;
  ageS: number;
  tokensOwn: number;
  tokensRollup: number;
  tokens: string;
  hasChildren: boolean;
  collapsed: boolean;
  /** Running nodes hidden under this collapsed row. */
  liveHidden: number;
  badge?: string;
  waitingOn?: string;
}

export const DOT_CLASS: Record<AgentTreeState, string> = {
  running: 'bg-emerald-500 animate-pulse',
  waiting: 'bg-transparent ring-2 ring-amber-400 ring-inset',
  stale: 'bg-neutral-500',
  finished: 'bg-neutral-700',
};

const isLive = (n: AgentTreeNode) => n.state === 'running' || n.state === 'waiting';
const ts = (s: string | null | undefined) => (s ? Date.parse(s) || 0 : 0);

/** Latest activity anywhere in the subtree (a blocked parent's own file does not move). */
function subtreeActivity(n: AgentTreeNode): number {
  return n.children.reduce((m, c) => Math.max(m, subtreeActivity(c)), ts(n.last_activity_at));
}

function ordered(children: AgentTreeNode[]): AgentTreeNode[] {
  return [...children].sort((a, b) =>
    (Number(isLive(b)) - Number(isLive(a))) || (ts(b.last_activity_at) - ts(a.last_activity_at)));
}

/** Label of the deepest node along the most recently active live path below `node`. A waiting node
 *  always has a live descendant, so that deepest node is the one actually running. */
export function waitingOn(node: AgentTreeNode): string | null {
  let cur: AgentTreeNode | null = null;
  for (let live = node.children.filter(isLive); live.length; live = cur.children.filter(isLive)) {
    cur = live.reduce((a, b) => (subtreeActivity(b) > subtreeActivity(a) ? b : a));
  }
  return cur ? cur.label : null;
}

export function toolText(node: AgentTreeNode): string {
  if (node.current_tool) return node.current_tool;
  if (node.state === 'running' && node.last_tool) return `last: ${node.last_tool}`;
  return '';
}

function fmtN(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e5) return `${Math.round(n / 1e3)}k`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

export function fmtTokens(own: number, rollup: number): string {
  return own === rollup ? fmtN(own) : `${fmtN(own)} / ${fmtN(rollup)}`;
}

export function fmtAge(s: number): string {
  const v = Math.max(0, Math.floor(s));
  if (v < 60) return `${v}s`;
  if (v < 3600) return `${Math.floor(v / 60)}m`;
  return `${Math.floor(v / 3600)}h ${Math.floor((v % 3600) / 60)}m`;
}

function countRunning(n: AgentTreeNode): number {
  return n.children.reduce((k, c) => k + (c.state === 'running' ? 1 : 0) + countRunning(c), 0);
}

/** Visible rows, pre-order; children of a collapsed node are hidden. `now` is ms since epoch. */
export function rowsOf(tree: AgentTreeNode, collapsed: Set<string>, now: number): AgentTreeRow[] {
  const out: AgentTreeRow[] = [];
  const visit = (n: AgentTreeNode) => {
    const isCollapsed = n.children.length > 0 && collapsed.has(n.agent_id);
    const liveHidden = isCollapsed ? countRunning(n) : 0;
    const w = n.state === 'waiting' ? waitingOn(n) : null;
    const last = ts(n.last_activity_at);
    out.push({
      id: n.agent_id, depth: n.depth, label: n.label, type: n.type, state: n.state,
      dot: DOT_CLASS[n.state] ?? DOT_CLASS.stale,
      tool: w ? `waiting on ${w}` : toolText(n),
      ageS: last ? Math.max(0, Math.floor((now - last) / 1000)) : 0,
      tokensOwn: n.tokens_own, tokensRollup: n.tokens_rollup, tokens: fmtTokens(n.tokens_own, n.tokens_rollup),
      hasChildren: n.children.length > 0, collapsed: isCollapsed, liveHidden,
      ...(liveHidden ? { badge: `+${liveHidden} live` } : {}),
      ...(w ? { waitingOn: w } : {}),
    });
    if (!isCollapsed) for (const c of ordered(n.children)) visit(c);
  };
  visit(tree);
  return out;
}

/** Nodes collapsed on first render: those at depth >= maxDepth with children (phone/watch: 2). Web: none. */
export function defaultCollapsed(tree: AgentTreeNode, opts: { maxDepth?: number }): Set<string> {
  const out = new Set<string>();
  const max = opts.maxDepth;
  if (max === undefined) return out;
  const visit = (n: AgentTreeNode) => {
    if (n.depth >= max && n.children.length) out.add(n.agent_id);
    n.children.forEach(visit);
  };
  visit(tree);
  return out;
}

export function findNode(tree: AgentTreeNode | null, id: string): AgentTreeNode | null {
  if (!tree) return null;
  if (tree.agent_id === id) return tree;
  for (const c of tree.children) { const f = findNode(c, id); if (f) return f; }
  return null;
}

export interface AgentTreeView { tree: AgentTreeNode; collapsed: Set<string>; selected: string | null }

/** A new SSE frame keeps the user's collapse state and selection; a pruned selection is dropped. */
export function mergeSnapshot(prev: AgentTreeView, next: AgentTreeNode): AgentTreeView {
  return {
    tree: next,
    collapsed: prev.collapsed,
    selected: prev.selected && findNode(next, prev.selected) ? prev.selected : null,
  };
}

export function hasSubtree(tree: AgentTreeNode | null): boolean {
  return !!tree && tree.children.length > 0;
}

/** Running + waiting nodes below the seat ("3 agents working"). */
export function workingCount(tree: AgentTreeNode): number {
  return tree.children.reduce((k, c) => k + (isLive(c) ? 1 : 0) + workingCount(c), 0);
}
