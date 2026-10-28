/**
 * agentIdentity.ts — the project, name and full name the dashboard shows for each agent, and when it last messaged.
 *
 * A project is a card in state/knowledge/projects.json (the only project store); its PM is named by the PM's
 * permanent id. An agent is in the project of the first PM up its reports_to chain (a T0 is in none; an agent
 * with no PM above it is in none), so workers inherit it and moving a worker to another PM moves its project.
 * The same rule as scripts/identity_store/seat_projects.py, which the name rules and Arturo's resolver use.
 *
 * Names (scripts/identity_store/seat_names.py) are unique per project, so outside its project's header an agent
 * is shown by its full name ("Demo · dev"); an agent never renamed is shown by its id, which says enough.
 * Pure functions plus two read-only lookups (the identity DB's names, the message store's last message times).
 */
import Database from 'better-sqlite3';
import path from 'path';

const GEN_SUFFIX = /-(?:gen|g)\d+$/;
export const baseId = (id: string): string => String(id || '').replace(GEN_SUFFIX, '');

export interface ProjectRef { slug: string; label: string; name: string }
export interface SeatNames { name: string | null; scope: string; aliases: string[] }

/** {pm id: project} from projects.json. `label` is the card's short label when it has one, else its name. */
export function projectsByPm(knowledge: Record<string, any> | null | undefined): Map<string, ProjectRef> {
  const out = new Map<string, ProjectRef>();
  for (const [slug, card] of Object.entries(knowledge || {})) {
    if (!card || typeof card !== 'object' || !card.pm) continue;
    const name = String(card.name || slug);
    out.set(baseId(String(card.pm)), { slug, name, label: String(card.short_name || name) });
  }
  return out;
}

/** The project the agent is in, walking up reports_to (at most 12 hops; a T0 or a loop ends it). */
export function projectOf(id: string, parentOf: (id: string) => string | undefined, tierOf: (id: string) => string | undefined,
  byPm: Map<string, ProjectRef>): ProjectRef | undefined {
  const seen = new Set<string>();
  let cur: string | undefined = baseId(id);
  for (let i = 0; i < 12 && cur && !seen.has(cur); i++) {
    seen.add(cur);
    if (tierOf(cur) === 'T0') return undefined;
    const p = byPm.get(cur);
    if (p) return p;
    const up = parentOf(cur);
    cur = up ? baseId(up) : undefined;
  }
  return undefined;
}

/** "Demo · dev" for a renamed agent in a project; its own name otherwise (an id says enough). */
export function fullName(name: string, id: string, project?: ProjectRef): string {
  return project && name && name !== id ? `${project.label} · ${name}` : (name || id);
}

/** Every saved name, from the identity DB (read-only; empty when the table or the DB is not there yet). */
export function readSeatNames(orchestraDir: string): Map<string, SeatNames> {
  const out = new Map<string, SeatNames>();
  let db: Database.Database | null = null;
  try {
    db = new Database(path.join(orchestraDir, 'state', 'orchestra-registry.db'), { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 2000');
    const has = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seat_names'").get();
    if (!has) return out;
    const rows = db.prepare('SELECT root, name, scope, is_current FROM seat_names ORDER BY set_at, rowid').all() as
      { root: string; name: string; scope: string; is_current: number }[];
    for (const r of rows) {
      const rec = out.get(r.root) || { name: null, scope: '', aliases: [] };
      if (r.is_current) { rec.name = r.name; rec.scope = r.scope; } else rec.aliases.push(r.name);
      out.set(r.root, rec);
    }
  } catch { /* no identity DB yet: every agent is shown by its id */ }
  finally { try { db?.close(); } catch { /* closed */ } }
  return out;
}

export interface IdentityRow {
  id: string; tier?: string; reports_to?: string;
  project?: ProjectRef | null; display_name?: string; full_name?: string; last_message_at?: string | null;
}

/** Fill in project, display_name, full_name and last_message_at on each row (in place). */
export function decorateIdentity<T extends IdentityRow>(rows: T[], deps: {
  knowledge: Record<string, any> | null; names: Map<string, SeatNames>;
  lastMessage: Map<string, string>; registry?: Record<string, { reports_to?: string; tier?: string }>;
}): T[] {
  const byPm = projectsByPm(deps.knowledge);
  const meta = new Map<string, { reports_to?: string; tier?: string }>();
  for (const [k, v] of Object.entries(deps.registry || {})) meta.set(baseId(k), { reports_to: v?.reports_to, tier: v?.tier });
  for (const r of rows) meta.set(baseId(r.id), { reports_to: r.reports_to ?? meta.get(baseId(r.id))?.reports_to, tier: r.tier ?? meta.get(baseId(r.id))?.tier });
  const parentOf = (id: string) => meta.get(id)?.reports_to;
  const tierOf = (id: string) => meta.get(id)?.tier;
  for (const r of rows) {
    const root = baseId(r.id);
    const project = projectOf(root, parentOf, tierOf, byPm);
    const name = deps.names.get(root)?.name || root;
    r.project = project ?? null;
    r.display_name = name;
    r.full_name = fullName(name, root, project);
    r.last_message_at = deps.lastMessage.get(root) ?? deps.lastMessage.get(r.id) ?? null;
  }
  return rows;
}
