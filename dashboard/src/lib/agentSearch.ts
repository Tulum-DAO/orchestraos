// Pure search/matching logic for the 2D Agents View's search box (spec §8).
//
// "prompts" is derived from the already-fetched agents array (grouped by
// system_prompt) rather than a separate fetch — no endpoint lists all prompt
// files, and every agent row already carries the one it uses. "files" is
// always empty: nothing reachable exposes a general file listing;
// /api/skills/workflows lists gstack skill workflows, a different concept,
// and using it here would be a fake fourth store (the exact §16 failure mode).
//
// Counts returned here are MATCH counts, not fleet counts — they answer a
// different question than the summary strip's "agents up out of total" and
// are not meant to reconcile with it. §8's "count matches the strip" line is
// about the status-filter counts (the inline filter in Agents.tsx), which is
// separate from this module.

export interface SearchAgent {
  id: string;
  name?: string;
  role?: string;
  tier?: string;
  system_prompt?: string;
}

export interface SearchProject {
  slug: string;
  name?: string;
  repo?: string | null;
  agents?: { id: string }[];
}

export type HitKind = 'agent' | 'repo' | 'prompt';

export interface Hit {
  id: string;
  label: string;
  kind: HitKind;
  sublabel?: string;
  agentIds?: string[];
}

export interface SearchFleetResult {
  agents: Hit[];
  repos: Hit[];
  prompts: Hit[];
  files: Hit[];
}

function includesCi(value: string | undefined | null, q: string): boolean {
  return !!value && value.toLowerCase().includes(q);
}

// An id match outranks a name match, which outranks a role/tier-only match —
// so two hits that both match don't reshuffle on ties between keystrokes.
function agentMatchRank(agent: SearchAgent, q: string): number {
  if (includesCi(agent.id, q)) return 0;
  if (includesCi(agent.name, q)) return 1;
  if (includesCi(agent.role, q)) return 2;
  return 3;
}

function searchAgents(q: string, agents: SearchAgent[]): Hit[] {
  const matches = agents
    .filter((a) => [a.id, a.name, a.role, a.tier].some((v) => includesCi(v, q)))
    .map((a) => ({ agent: a, rank: agentMatchRank(a, q) }));
  matches.sort((x, y) => x.rank - y.rank || x.agent.id.localeCompare(y.agent.id));
  return matches.map(({ agent }) => ({
    id: agent.id,
    label: agent.name || agent.id,
    kind: 'agent' as const,
    sublabel: agent.tier,
  }));
}

function searchRepos(q: string, projects: SearchProject[]): Hit[] {
  const matches = projects.filter((p) => includesCi(p.name, q) || includesCi(p.repo, q));
  matches.sort((a, b) => (a.name || a.repo || a.slug).localeCompare(b.name || b.repo || b.slug));
  return matches.map((p) => ({
    id: p.slug,
    label: p.name || p.repo || p.slug,
    kind: 'repo' as const,
    sublabel: p.repo || undefined,
    agentIds: (p.agents || []).map((a) => a.id),
  }));
}

function searchPrompts(q: string, agents: SearchAgent[]): Hit[] {
  const byPath = new Map<string, string[]>();
  for (const a of agents) {
    if (!a.system_prompt) continue;
    const ids = byPath.get(a.system_prompt) ?? [];
    ids.push(a.id);
    byPath.set(a.system_prompt, ids);
  }
  const matches = [...byPath.entries()].filter(([path]) => includesCi(path, q));
  matches.sort(([a], [b]) => a.localeCompare(b));
  return matches.map(([path, agentIds]) => ({
    id: path,
    label: path,
    kind: 'prompt' as const,
    sublabel: `${agentIds.length} agent${agentIds.length === 1 ? '' : 's'}`,
    agentIds,
  }));
}

// Same order SearchResults.tsx renders in ("files" is never rendered, so it's
// excluded here too — arrow-key nav should never land on an off-screen hit).
export function flattenVisibleHits(results: SearchFleetResult): Hit[] {
  return [...results.agents, ...results.repos, ...results.prompts];
}

export function searchFleet(
  query: string,
  data: { agents: SearchAgent[]; repos?: SearchProject[] },
): SearchFleetResult {
  const q = query.trim().toLowerCase();
  if (!q) return { agents: [], repos: [], prompts: [], files: [] };
  return {
    agents: searchAgents(q, data.agents),
    repos: searchRepos(q, data.repos || []),
    prompts: searchPrompts(q, data.agents),
    files: [],
  };
}
