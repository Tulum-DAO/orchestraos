/**
 * modelPicker.ts — the rules behind the model picker (components/kit/ModelPicker.tsx): which models it offers and
 * under what names, how search and nicknames match, how the list is grouped, and what was used recently. Pure, so
 * every rule is tested without a browser.
 *
 * Model rules:
 *   - Every picker lists the same entries in the same order: every provider's models (the probe's list, plus an
 *     agent's own when it has one), grouped by provider, newest first. An entry that can't be used here is shown
 *     dimmed with why ("Not allowed for this agent", "Not installed", "Not signed in"), never hidden.
 *   - "Opus 5.5" is claude-opus-5-5[1m]: the short `opus` id the probe lists is that model; Opus 5.5 at 200k context
 *     never appears (standing rules, CANONICAL and HIDDEN below).
 *   - A size shows only when a source states it (nameAndTag); nothing is assumed.
 *   - One model, one row: a short id and its full id under the same name ("Fable 5.1" as `fable` and as
 *     claude-fable-5-1) are one row, the full id kept and the short one remembered as an alias, so a seat already
 *     on the short id still shows as current.
 */
import type { ProviderCapabilities, ProviderModel, ProviderRow } from './modelSelectorFilter';

/** One row of the picker. `key` is unique across providers; `id` is what a pick sends. */
export interface PickerModel {
  key: string;
  id: string;
  providerId: string;
  providerLabel: string;
  /** The model's name without its context size ("Opus 5.5"). */
  name: string;
  /** Its context size when the source says it ("1M", "200K"). */
  tag?: string;
  /** Other ids that mean this model (a short id). */
  aliases: string[];
  /** Extra words search matches: family, provider names, nicknames. */
  keywords: string[];
  capabilities: ProviderCapabilities;
  /** Why it can't be picked here (shown dimmed, never hidden); absent: it can. */
  unavailable?: string;
}

export interface PickerGroup {
  /** 'recent', or the provider's id. */
  id: string;
  label: string;
  models: PickerModel[];
}

/** A provider that cannot be used yet: shown greyed at the bottom with its reason. */
export interface GreyProvider { providerId: string; label: string; reason: string }

export interface PickerValue { providerId: string; modelId: string }

/** Short ids that stand for another model. */
const CANONICAL: Record<string, Record<string, string>> = {
  claude: { opus: 'claude-opus-5-5[1m]' },
};
/** Models never offered (a standing rule, not a permission: an entry an agent may not use is dimmed instead). */
const HIDDEN: Record<string, Set<string>> = {
  claude: new Set(['claude-opus-5-5', 'claude-opus-5-5[200k]']),
};

/** The id a pick of this model sends: the short id's full model when it stands for one. */
export function canonicalId(providerId: string, id: string): string {
  return CANONICAL[providerId]?.[id] ?? id;
}

export function isHidden(providerId: string, id: string): boolean {
  return !!HIDDEN[providerId]?.has(canonicalId(providerId, id));
}

/** Family order for models of the same version (newest first is by version; then the larger model). */
const FAMILY_RANK: Record<string, number> = { opus: 0, sonnet: 1, haiku: 2, fable: 3 };

/** The version a name carries ("Opus 5.5" → [5, 5], "GPT-6 Astra" → [6]). */
function versionOf(name: string): number[] {
  const m = name.match(/(\d+(?:\.\d+)*)/);
  return m ? m[1].split('.').map(Number) : [];
}

/** Newest first: by version, then family (Opus before Sonnet before Haiku), then the standard size before 1M; the
 *  source's order otherwise. The one order both pickers show. Pure. */
export function newestFirst(a: Pick<PickerModel, 'name' | 'tag'>, b: Pick<PickerModel, 'name' | 'tag'>): number {
  const va = versionOf(a.name), vb = versionOf(b.name);
  for (let i = 0; i < Math.max(va.length, vb.length); i++) {
    const d = (vb[i] ?? 0) - (va[i] ?? 0);
    if (d) return d;
  }
  const fam = (n: string) => FAMILY_RANK[n.toLowerCase().split(/\s+/)[0]] ?? 9;
  const f = fam(a.name) - fam(b.name);
  if (f) return f;
  const size = (t?: string) => (t?.endsWith('M') ? 1 : 0);
  return size(a.tag) - size(b.tag);
}

const PROVIDER_WORDS: Record<string, string[]> = {
  claude: ['claude', 'anthropic'],
  codex: ['codex', 'openai', 'gpt', 'chatgpt'],
  gemini: ['gemini', 'google'],
  grok: ['grok', 'xai'],
};

/** Nicknames: a word people say for a kind of model, and what it matches in a model's name. */
const NICKNAMES: { word: string; matches: RegExp }[] = [
  { word: 'fast', matches: /\b(haiku|flash|fast|luna)\b/i },
  { word: 'quick', matches: /\b(haiku|flash|fast)\b/i },
  { word: 'cheap', matches: /\b(haiku|flash)\b|\(low\)/i },
  { word: 'smart', matches: /\b(opus|pro|astra)\b|\(high\)/i },
  { word: 'best', matches: /\b(opus|pro|astra)\b/i },
  { word: 'big', matches: /\b(opus|pro)\b|\b1M\b/i },
  { word: 'gpt', matches: /\bgpt\b/i },
];

/** A model's name without its context size, and that size: "Opus 5.5 (1M)" → "Opus 5.5", "1M". */
export function nameAndTag(id: string, label: string, subtitle?: string, contextWindow?: number | null): { name: string; tag?: string } {
  const m = label.match(/^(.*?)\s*\((\d+(?:\.\d+)?\s*[KkMm])\)\s*$/);
  const name = (m ? m[1] : label).trim() || id;
  let tag = m ? m[2].replace(/\s+/g, '').toUpperCase() : undefined;
  if (!tag && /\[1m\]$/i.test(id)) tag = '1M';
  if (!tag && subtitle) { const s = subtitle.match(/(\d+(?:\.\d+)?\s*[KkMm])\s*context/i); if (s) tag = s[1].replace(/\s+/g, '').toUpperCase(); }
  if (!tag && contextWindow) tag = contextWindow >= 1_000_000 ? `${+(contextWindow / 1_000_000).toFixed(1)}M` : `${Math.round(contextWindow / 1000)}K`;
  return tag ? { name, tag } : { name };
}

/** What the chip and a pick's label say: "Opus 5.5 · 1M". */
export function modelTitle(m: Pick<PickerModel, 'name' | 'tag'>): string {
  return m.tag ? `${m.name} · ${m.tag}` : m.name;
}

/** A short id ("fable") rather than a full one ("claude-fable-5-1"): kept only when nothing fuller has its name. */
const isShortId = (id: string) => !/[-.\d]/.test(id);

/** A source model as the picker needs it. The gateway's rows carry a `subtitle` ("1M context"); the probe's carry
 *  a context window. */
type SourceModel = ProviderModel & { subtitle?: string };

/** `asNamed`: take the short ids as the models they name (Arturo's brain: its `opus` is Opus 5.5 as the brain runs it,
 *  never the agents' 1M version, which it cannot use), not by the agents' rules (CANONICAL). */
export function pickerModelsFor(providerId: string, providerLabel: string, source: SourceModel[], { asNamed = false } = {}): PickerModel[] {
  const out: PickerModel[] = [];
  for (const s of source) {
    if (isHidden(providerId, s.id)) continue;
    const id = asNamed ? s.id : canonicalId(providerId, s.id);
    // THE SIZE RULE, for every entry of every picker: only what a source states (nameAndTag: the label "(1M)", a 1M id
    // `…[1m]`, the gateway's "200K context", the probe's context window). No source, no size.
    const { name, tag } = nameAndTag(id, s.label, s.subtitle, s.capabilities?.context_window ?? null);
    const same = out.find((m) => m.id === id || (m.name === name && m.tag === tag));
    if (same) {
      // One row per model: keep the full id, remember the others.
      const ids = new Set([same.id, ...same.aliases, id, s.id]);
      const keep = isShortId(same.id) && !isShortId(id) ? id : same.id;
      same.id = keep; same.key = `${providerId}:${keep}`;
      same.aliases = [...ids].filter((x) => x !== keep);
      continue;
    }
    const family = name.toLowerCase().split(/[\s\-()]+/).filter((w) => w && !/^\d/.test(w));
    const nick = NICKNAMES.filter((n) => n.matches.test(name) || n.matches.test(s.label)).map((n) => n.word);
    out.push({
      key: `${providerId}:${id}`, id, providerId, providerLabel, name, ...(tag ? { tag } : {}),
      aliases: s.id !== id ? [s.id] : [],
      keywords: [...new Set([...family, ...(PROVIDER_WORDS[providerId] ?? [providerId]), providerLabel.toLowerCase(), ...nick])],
      capabilities: s.capabilities ?? { text: true, image: false, audio: false, video: false, context_window: null },
    });
  }
  return out;
}

export const NOT_ALLOWED = 'Not allowed for this agent';
export const SEAT_ONLY = "Only for agents; Arturo can't use it";

/** Every entry every picker lists, provider by provider, newest first: each provider's models from the probe, plus an
 *  agent's own list for its provider (the models it may switch to, scripts/agent_model.py: some exist only there).
 *  Nothing is left out. An entry is dimmed with why when it can't be picked here: its provider not installed or not
 *  signed in (the probe's state, in words), on an agent's page a model the agent's list does not allow, or in
 *  Arturo's brain picker (`forArturo`) a model only agents can use (`seat_only`). */
export function buildPickerModels(rows: ProviderRow[], agentModels?: { providerId: string; models: SourceModel[] } | null,
  opts: { forArturo?: boolean } = {}): PickerModel[] {
  const out: PickerModel[] = [];
  for (const r of rows) {
    const own = agentModels && agentModels.providerId === r.provider.id && agentModels.models.length > 0 ? agentModels.models : null;
    const known = new Set(r.provider.models.map((m) => m.id));
    const source: SourceModel[] = [...r.provider.models, ...(own ?? []).filter((m) => !known.has(m.id))];
    const allowed = own ? new Set(own.map((m) => m.id)) : null;
    // A row only agents can use (`seat_only`, the 1M Opus versions): Arturo's brain picker shows it dimmed with the
    // server's reason; for an agent it is a model like any other.
    const seatOnly = new Map(source.filter((m) => m.seat_only).map((m) => [m.id, m.seat_only_reason || SEAT_ONLY]));
    const list = pickerModelsFor(r.provider.id, r.provider.label, source, { asNamed: !!opts.forArturo }).map((m) => {
      if (!r.selectable) return { ...m, unavailable: r.greyReason ?? 'Not available' };
      const seat = opts.forArturo ? [m.id, ...m.aliases].map((x) => seatOnly.get(x)).find(Boolean) : undefined;
      if (seat) return { ...m, unavailable: seat };
      if (allowed && ![m.id, ...m.aliases].some((x) => allowed.has(x))) return { ...m, unavailable: NOT_ALLOWED };
      return m;
    });
    out.push(...list.map((m, i) => ({ m, i })).sort((a, b) => newestFirst(a.m, b.m) || a.i - b.i).map((x) => x.m));
  }
  return out;
}

/** Providers that cannot be used yet, with the probe's reason, for the bottom of the list. */
export function greyProviders(rows: ProviderRow[]): GreyProvider[] {
  return rows.filter((r) => !r.selectable).map((r) => ({ providerId: r.provider.id, label: r.provider.label, reason: r.greyReason ?? 'not available' }));
}

/** Is this the model in use? Its id, one of its aliases, or a short id standing for it. */
export function isCurrent(m: PickerModel, value: PickerValue | null | undefined): boolean {
  if (!value || value.providerId !== m.providerId) return false;
  return m.id === value.modelId || m.aliases.includes(value.modelId) || canonicalId(m.providerId, value.modelId) === m.id;
}

export function findCurrent(models: PickerModel[], value: PickerValue | null | undefined): PickerModel | null {
  return models.find((m) => isCurrent(m, value)) ?? null;
}

const words = (q: string) => q.toLowerCase().split(/\s+/).map((w) => w.trim()).filter(Boolean);

/** Does a model match a search? Every word must be found in its name, id, provider or keywords (a nickname counts
 *  whole: "fast" finds Haiku). */
export function matches(m: PickerModel, query: string): boolean {
  const ws = words(query);
  if (!ws.length) return true;
  const hay = [m.name, m.tag ?? '', m.id, ...m.aliases, m.providerLabel, m.providerId].join(' ').toLowerCase();
  return ws.every((w) => hay.includes(w) || m.keywords.some((k) => k === w || k.startsWith(w)));
}

/** Search order inside a group: a name that starts with the query first, then the source order. */
function rank(m: PickerModel, query: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const n = m.name.toLowerCase();
  return n.startsWith(q) ? 0 : n.split(/\s+/).some((w) => w.startsWith(q)) ? 1 : 2;
}

/** The list: models used recently first (a Recent group, newest first, a few at most; only when not searching),
 *  then one group per provider. A recent model is not listed twice. */
export function groupModels(models: PickerModel[], opts: { query?: string; recent?: string[]; maxRecent?: number } = {}): PickerGroup[] {
  const query = opts.query ?? '';
  const hits = models.filter((m) => matches(m, query));
  const groups: PickerGroup[] = [];
  const taken = new Set<string>();
  if (!query.trim() && opts.recent?.length) {
    const recent = opts.recent.map((k) => hits.find((m) => m.key === k)).filter((m): m is PickerModel => !!m).slice(0, opts.maxRecent ?? 3);
    if (recent.length) { groups.push({ id: 'recent', label: 'Recent', models: recent }); recent.forEach((m) => taken.add(m.key)); }
  }
  const order: string[] = [];
  for (const m of hits) if (!order.includes(m.providerId)) order.push(m.providerId);
  for (const p of order) {
    const list = hits.filter((m) => m.providerId === p && !taken.has(m.key))
      .map((m, i) => ({ m, i })).sort((a, b) => rank(a.m, query) - rank(b.m, query) || a.i - b.i).map((x) => x.m);
    if (list.length) groups.push({ id: p, label: list[0].providerLabel, models: list });
  }
  return groups;
}

/** The rows keyboard arrows move through, in the order shown. */
export const flatten = (groups: PickerGroup[]): PickerModel[] => groups.flatMap((g) => g.models);

/** Arrow keys: the next row, wrapping at either end; from nothing, the first (down) or the last (up). */
export function moveActive(keys: string[], current: string | null, dir: 1 | -1): string | null {
  if (!keys.length) return null;
  const i = current ? keys.indexOf(current) : -1;
  if (i < 0) return dir === 1 ? keys[0] : keys[keys.length - 1];
  return keys[(i + dir + keys.length) % keys.length];
}

/** Grey providers shown for a search: all of them with none, else the ones whose name matches. */
export function greyFor(grey: GreyProvider[], query: string): GreyProvider[] {
  const ws = words(query);
  if (!ws.length) return grey;
  return grey.filter((g) => ws.every((w) => [g.label, g.providerId, ...(PROVIDER_WORDS[g.providerId] ?? [])].join(' ').toLowerCase().includes(w)));
}

// ── Recently used ──
export const RECENT_KEY = 'orchestra.modelPicker.recent';
const RECENT_MAX = 6;
type Store = Pick<Storage, 'getItem' | 'setItem'>;

export function readRecent(storage: Store | null): string[] {
  try {
    const v = JSON.parse(storage?.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(0, RECENT_MAX) : [];
  } catch { return []; }
}

/** A pick goes to the front; the list keeps the last few. */
export function pushRecent(list: string[], key: string): string[] {
  return [key, ...list.filter((k) => k !== key)].slice(0, RECENT_MAX);
}

export function writeRecent(storage: Store | null, list: string[]): void {
  try { storage?.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX))); } catch { /* best effort */ }
}

/** The badge for a provider: one of the proven brand marks (claude, codex via OpenAI, gemini, grok), else a
 *  neutral letter mark. */
export function letterMark(label: string): string {
  return (label.trim()[0] ?? '?').toUpperCase();
}
