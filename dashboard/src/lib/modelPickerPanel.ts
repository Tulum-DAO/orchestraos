/**
 * modelPickerPanel.ts — the rules of the model picker's panel (components/kit/ModelPickerPanel.tsx): the provider
 * rail (every provider; each mark's usage ring, and its status, usage and version as its label), which
 * models can be picked where, the all-provider search, the "default" mark, the pill's words and the keyboard. Pure, so each rule is tested without a browser. The model list itself (names,
 * sizes, aliases, order) stays in lib/modelPicker.ts.
 *
 * The context rule: an EXISTING agent is one session in one runtime, so its picker offers only that runtime's models;
 * every other provider explains that and offers a new agent instead. Settings defaults, a new agent and Arturo's
 * brain may pick from every provider.
 *
 * The browse list (no search): ONE ROW PER FAMILY per provider (Opus, Sonnet, GPT Astra, Gemini Flash (High), Grok…),
 * named by the family's newest version; the newest version's sizes are the Context control (Standard | 1M) beside
 * Effort; every older version, one row per concrete model, is under "Older versions". Search stays flat over every
 * concrete model.
 */
import { ALIASES, flatten, groupModels, isCurrent, moveActive, PROVIDER_WORDS, type PickerModel, type PickerValue } from './modelPicker.ts';
import type { ProviderRow } from './modelSelectorFilter.ts';
import { statusLabel } from './modelSelectorFilter.ts';
import { clock, type UsageRow } from './usageLine.ts';

// ── Where the picker is ──

/** 'agent': an existing agent's picker, locked to its runtime. 'any': defaults, a new agent, Arturo's brain. */
export type PickerScope = { kind: 'agent'; runtime: string } | { kind: 'any' };

export function scopeFor(agentModels: { providerId: string } | null | undefined): PickerScope {
  return agentModels?.providerId ? { kind: 'agent', runtime: agentModels.providerId } : { kind: 'any' };
}

/** Can this row be picked here? Never a dimmed row; in an agent's picker, only its own runtime's. */
export function canPick(m: PickerModel, scope: PickerScope): boolean {
  if (m.unavailable) return false;
  return scope.kind === 'any' || m.providerId === scope.runtime;
}

/** Why another provider's models can't be picked in this agent's picker; null when they can. */
export function otherRuntimeNote(providerId: string, providerLabel: string, scope: PickerScope, labelOf: (id: string) => string): string | null {
  if (scope.kind !== 'agent' || providerId === scope.runtime) return null;
  return `This agent runs on ${labelOf(scope.runtime)}. Each agent is one session in one runtime, so ${providerLabel} needs a new agent.`;
}

/** The runtime's "default" mark on a row: only where the picker SETS a default (Settings › Agents' default model,
 *  Arturo's brain), never in an existing agent's picker, where it says nothing about that agent. */
export function showsDefaultMark(scope: PickerScope): boolean {
  return scope.kind === 'any';
}

/** A dimmed row's reason, shown under its name: why the model can't be used (not signed in, not allowed for this
 *  agent…), else, in an agent's picker, that it needs a new agent on its own provider. Null: it can be picked. */
export function rowReason(m: PickerModel, scope: PickerScope, labelOf: (id: string) => string): string | null {
  if (m.unavailable) return m.unavailable;
  if (scope.kind === 'agent' && m.providerId !== scope.runtime) return `Needs a new agent on ${labelOf(m.providerId) || m.providerLabel}`;
  return null;
}

// ── The rail ──

export type ProviderStatus = 'signed-in' | 'signed-out' | 'unverified' | 'not-installed';

export function providerStatus(r: ProviderRow): ProviderStatus {
  if (!r.provider.installed) return 'not-installed';
  if (r.provider.authed === true) return 'signed-in';
  if (r.provider.authed === 'unverified') return 'unverified';
  return 'signed-out';
}

/** The status in the header's words: "signed in", else the probe's reason as every surface says it. */
export function statusWords(r: ProviderRow): string {
  return providerStatus(r) === 'signed-in' ? 'signed in' : (r.greyReason || statusLabel(r.provider) || 'not available').toLowerCase();
}

export interface UsageSummary {
  /** Share of the limit used, 0..100, rounded. */
  pct: number;
  /** "74% of weekly used" (with "as of <time>" when the reading is old). */
  text: string;
  stale: boolean;
  high: boolean;
}

const WINDOWS: Record<string, string> = { '5h': '5 h', weekly: 'weekly' };
const isPct = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100;

/** A provider's usage from GET /api/usage's rows: the limit nearest to running out (the highest share used across
 *  its windows and logins). Null when it reports no number: no ring, never a guess. */
export function usageFor(rows: unknown, providerId: string, now: Date = new Date()): UsageSummary | null {
  if (!Array.isArray(rows)) return null;
  const mine = (rows as UsageRow[]).filter((r) => r && r.provider === providerId && isPct(r.used_pct));
  if (!mine.length) return null;
  const top = mine.reduce((a, b) => ((b.used_pct as number) > (a.used_pct as number) ? b : a));
  const pct = Math.round(top.used_pct as number);
  const win = (top.window && WINDOWS[top.window]) || top.window || 'its limit';
  const stale = !!top.stale;
  const asOf = stale && typeof top.as_of === 'number' ? ` (as of ${clock(top.as_of, now)})` : '';
  return { pct, text: `${pct}% of ${win} used${asOf}`, stale, high: pct >= 80 };
}

/** The usage ring's stroke: a circle of radius `r` drawn `pct`% of the way round. */
export function ringDash(pct: number, r: number): { circumference: number; dash: number; gap: number } {
  const circumference = 2 * Math.PI * r;
  const share = Math.min(100, Math.max(0, Number.isFinite(pct) ? pct : 0)) / 100;
  const dash = circumference * share;
  return { circumference, dash, gap: circumference - dash };
}

export interface RailEntry {
  id: string;
  label: string;
  status: ProviderStatus;
  /** Its models can be used on this machine (installed and signed in). */
  usable: boolean;
  /** In an agent's picker, a provider other than the agent's runtime. */
  otherRuntime: boolean;
  /** Its status in words ("signed in", "not installed"). */
  statusText: string;
  version?: string;
  defaultModel?: string;
  usage: UsageSummary | null;
}

export function railEntries(rows: ProviderRow[], usage: unknown, scope: PickerScope, now: Date = new Date()): RailEntry[] {
  return rows.map((r) => ({
    id: r.provider.id,
    label: r.provider.label,
    status: providerStatus(r),
    usable: r.selectable,
    otherRuntime: scope.kind === 'agent' && r.provider.id !== scope.runtime,
    statusText: statusWords(r),
    ...(r.provider.version ? { version: r.provider.version } : {}),
    ...(r.provider.model_catalog_default ? { defaultModel: r.provider.model_catalog_default } : {}),
    usage: usageFor(usage, r.provider.id, now),
  }));
}

/** A rail mark's title and aria-label: everything the header used to say, the reading's age included ("Claude: signed
 *  in, 74% of weekly used (as of 09:12), CLI v2.1.295"). */
export function railLabel(e: RailEntry): string {
  return `${e.label}: ${e.statusText}${e.usage ? `, ${e.usage.text}` : ''}${e.version ? `, CLI v${e.version}` : ''}`;
}

/** The provider the panel opens on: the agent's runtime, else the model in use's, else the first usable one. */
export function initialTab(entries: RailEntry[], scope: PickerScope, valueProvider: string | null | undefined): string | null {
  const has = (id: string | null | undefined) => !!id && entries.some((e) => e.id === id);
  if (scope.kind === 'agent' && has(scope.runtime)) return scope.runtime;
  if (has(valueProvider)) return valueProvider as string;
  return entries.find((e) => e.usable)?.id ?? entries[0]?.id ?? null;
}

// ── Rows ──

/** Is this the runtime's own default (what an agent runs when no model is named)? Its id, an alias, or the model a
 *  CLI alias stands for (lib/modelPicker.ts ALIASES: claude-opus-5-5 is the row named "Opus 5.5 · 200K"). */
export function isRuntimeDefault(m: PickerModel, defaultId: string | null | undefined): boolean {
  if (!defaultId) return false;
  if (m.id === defaultId || m.aliases.includes(defaultId)) return true;
  const a = ALIASES[m.providerId]?.[defaultId];
  return !!a && a.name === m.name && a.tag === m.tag;
}

/** The rows under the search box: with a query, matches from EVERY provider (each with its badge), signed in or not;
 *  without one, the selected provider's models. */
export function visibleRows(models: PickerModel[], query: string, tab: string | null): PickerModel[] {
  if (query.trim()) return flatten(groupModels(models, { query }));
  return models.filter((m) => m.providerId === tab);
}

// ── Families ──

/** A context size in tokens, for ordering ("200K" < "1M"); no size: 0 (the standard one). */
function sizeOf(tag: string | undefined): number {
  const m = tag?.match(/^(\d+(?:\.\d+)?)([KM])$/i);
  return m ? Number(m[1]) * (m[2].toUpperCase() === 'M' ? 1e6 : 1e3) : 0;
}

/** Newest first by version ([5, 5] before [5]). */
function byVersion(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (b[i] ?? 0) - (a[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

const VERSION_TOKEN = /^v?\d{1,4}(?:\.\d{1,4})*$/;
const SIZE_TOKEN = /^\d+(?:\.\d+)?[km]$/;
/** After the version, a number led by a zero or of 4+ digits is a date stamp ("grok-4-0709", "gpt-4o-2024-08-06"). */
const STAMP_TOKEN = /^(?:0\d+|\d{4,})$/;
/** Words a label adds that name no family ("Opus 5 with 1M context"). */
const FILLER = new Set(['with', 'context']);

/**
 * THE FAMILY RULE: a model's family is its name (or id) with the version numbers, context sizes ("1M", "[1m]"), date
 * stamps, filler ("with", "context"), a bracketed id repeating the model ("Sonnet (claude-sonnet-5)") and the
 * provider's own words in front (lib/modelPicker.ts PROVIDER_WORDS) taken out; what is left, lower-cased and each word
 * once, is the family ("Opus 5.5" and claude-opus-5-5[1m] → "opus"; "GPT-5.6 Terra" → "terra"; gpt-5.1-codex-mini →
 * "codex mini", a provider word inside the name staying; "Gemini 3.8 Flash (High)" → "flash high", its level being part
 * of the model; "Grok 4.7" → "grok", the provider word kept when it is all there is). The version is the numbers, in
 * order ("5-5" and "5.5" → [5, 5]; "4o" → [4]), never a date stamp after it; none in the name: the bracketed id's.
 * Nothing but numbers: no family (null), and the model is its own row. No vendor list: a new family or version needs
 * no edit.
 */
export function familyOf(providerId: string, nameOrId: string): { family: string | null; version: number[] } {
  const ids: string[] = [];
  const text = nameOrId.toLowerCase().replace(/\(([a-z0-9]+(?:[-.][a-z0-9]+)+(?:\[[^\]]*\])?)\)/g, (_, id: string) => { ids.push(id); return ' '; });
  const tokens = text.replace(/\[[^\]]*\]/g, ' ').replace(/[()]/g, ' ').split(/[\s\-_/:]+/).filter(Boolean);
  const own = new Set(PROVIDER_WORDS[providerId] ?? [providerId]);
  const version: number[] = [];
  const words: string[] = [];
  /** Provider words in front, before the version and any other word ("claude", "gpt", "gemini"). */
  const prefix: string[] = [];
  for (const t of tokens) {
    if (SIZE_TOKEN.test(t) || (version.length && STAMP_TOKEN.test(t))) continue;
    if (VERSION_TOKEN.test(t)) { version.push(...t.replace(/^v/, '').split('.').map(Number)); continue; }
    const lead = t.match(/^(\d{1,3})[a-z]+$/);
    if (lead && !version.length) version.push(Number(lead[1]));
    if (/^\d+$/.test(t) || FILLER.has(t) || words.includes(t) || prefix.includes(t)) continue;
    if (!version.length && !words.length && own.has(t)) prefix.push(t);
    else words.push(t);
  }
  if (!version.length && ids.length) version.push(...familyOf(providerId, ids[0]).version);
  const family = (words.length ? words : prefix).join(' ');
  return { family: family || null, version };
}

/** A model's family and version: from its name, else from its id. */
function familyOfModel(m: PickerModel): { family: string | null; version: number[] } {
  const byName = familyOf(m.providerId, m.name);
  if (byName.family && byName.version.length) return byName;
  const byId = familyOf(m.providerId, m.id);
  return { family: byName.family ?? byId.family, version: byName.version.length ? byName.version : byId.version };
}

export interface Family {
  key: string;
  providerId: string;
  /** The family's newest version in every size it is offered in, the standard size first: the newest one that can be
   *  used here (an agent whose list excludes Opus 5.5 gets Opus 5), else the newest. */
  newest: PickerModel[];
  /** Every other version, newest first (a newer one that can't be used here among them, dimmed with why). */
  older: PickerModel[];
}

/** A provider's families, in the order the list shows models (newest first): the first model seen opens a family. */
export function familiesFor(models: PickerModel[], providerId: string): Family[] {
  const groups = new Map<string, { m: PickerModel; v: number[] }[]>();
  for (const m of models) {
    if (m.providerId !== providerId) continue;
    const f = familyOfModel(m);
    const key = f.family ? `${providerId}:${f.family}` : m.key;
    groups.set(key, [...(groups.get(key) ?? []), { m, v: f.version }]);
  }
  return [...groups].map(([key, members]) => {
    const usable = members.filter((x) => !x.m.unavailable);
    const top = (usable.length ? usable : members).reduce((a, b) => (byVersion(b.v, a.v) < 0 ? b : a)).v;
    const newest = members.filter((x) => byVersion(x.v, top) === 0).map((x) => x.m)
      .map((m, i) => ({ m, i })).sort((a, b) => sizeOf(a.m.tag) - sizeOf(b.m.tag) || a.i - b.i).map((x) => x.m);
    return { key, providerId, newest, older: members.filter((x) => byVersion(x.v, top) !== 0).map((x) => x.m) };
  });
}

export interface ContextChoice { tag: string; label: string; model: PickerModel }

/** The sizes of a family's newest version, one choice per size, smallest first, each by its size as the rows and the
 *  pill say it ("200K | 1M"); a size no source states is "Standard". */
export function contextChoices(f: Family): ContextChoice[] {
  const out: ContextChoice[] = [];
  for (const m of f.newest) {
    const tag = m.tag ?? '';
    if (!out.some((c) => c.tag === tag)) out.push({ tag, label: tag || 'Standard', model: m });
  }
  return out;
}

/** The concrete model a family row picks: its newest version in the size in use when it has that size, else the
 *  runtime's default size, else its standard; never one that can't be picked here while another can. */
export function resolveFamily(f: Family, size: string | null | undefined, scope: PickerScope, defaultId?: string | null): PickerModel {
  const ok = (m: PickerModel) => canPick(m, scope);
  return (size != null ? f.newest.find((m) => (m.tag ?? '') === size && ok(m)) : undefined)
    ?? f.newest.find((m) => ok(m) && isRuntimeDefault(m, defaultId))
    ?? f.newest.find(ok)
    ?? f.newest.find((m) => isRuntimeDefault(m, defaultId))
    ?? f.newest[0];
}

export type BrowseRow =
  /** A family: the concrete model a pick sends (its key is the row's), and whether the model in use is one of its
   *  newest version's sizes (the row is ticked). */
  | { kind: 'family'; key: string; model: PickerModel; family: Family; current: boolean }
  /** The "Older versions" disclosure. */
  | { kind: 'older'; key: string; providerId: string; open: boolean }
  /** An older version, one row per concrete model. */
  | { kind: 'model'; key: string; model: PickerModel };

export interface Browse {
  rows: BrowseRow[];
  /** The model in use, when it is this provider's. */
  current: PickerModel | null;
  /** Its family, when it is that family's newest version (the Context control's family). */
  currentFamily: Family | null;
  olderOpen: boolean;
}

export const olderKey = (providerId: string) => `older:${providerId}`;

/**
 * A provider's list without a search: its families, then "Older versions" (when there are any) and, open, every older
 * model. AN OLDER MODEL IN USE: the disclosure opens by itself on it, so the model in use is always on screen, ticked,
 * in its real place; no family row is ticked for it (a family row is the newest version). `olderOpen` is the user's
 * own open/close for this provider, which wins.
 */
export function browseRows(models: PickerModel[], tab: string | null,
  opts: { value: PickerValue | null | undefined; scope: PickerScope; defaultId?: string | null; olderOpen?: boolean | null }): Browse {
  if (!tab) return { rows: [], current: null, currentFamily: null, olderOpen: false };
  const families = familiesFor(models, tab);
  const current = models.find((m) => m.providerId === tab && isCurrent(m, opts.value)) ?? null;
  const currentFamily = (current && families.find((f) => f.newest.includes(current))) ?? null;
  const older = families.flatMap((f) => f.older);
  const open = opts.olderOpen ?? (!!current && older.includes(current));
  // The size a family pick keeps: the size of the model in use (any provider's).
  const size = current?.tag ?? models.find((m) => isCurrent(m, opts.value))?.tag ?? null;
  const rows: BrowseRow[] = families.map((f) => {
    const on = f === currentFamily;
    const model = on && current ? current : resolveFamily(f, size, opts.scope, opts.defaultId);
    return { kind: 'family', key: model.key, model, family: f, current: on };
  });
  if (older.length) rows.push({ kind: 'older', key: olderKey(tab), providerId: tab, open });
  if (open) rows.push(...older.map((m): BrowseRow => ({ kind: 'model', key: m.key, model: m })));
  return { rows, current, currentFamily, olderOpen: open };
}

/** The keys the arrows move through: every row that can be picked, and the disclosure. */
export function pickableKeys(rows: BrowseRow[], scope: PickerScope): string[] {
  return rows.filter((r) => r.kind === 'older' || canPick(r.model, scope)).map((r) => r.key);
}

export interface ContextControl {
  choices: { id: string; label: string }[];
  /** The size in use (a choice's id). */
  value: string;
}

/** The Context control: only for the model in use's family, offering only the sizes of its newest version (the one
 *  in use) that can be picked here; fewer than two of those, no control. A size that can't be used is never offered. */
export function contextControl(b: Browse, value: PickerValue | null | undefined, scope: PickerScope): ContextControl | null {
  const cur = b.current && isCurrent(b.current, value) ? b.current : null;
  if (!cur || !b.currentFamily) return null;
  const choices = contextChoices(b.currentFamily).filter((c) => canPick(c.model, scope));
  if (choices.length < 2 || !choices.some((c) => c.tag === (cur.tag ?? ''))) return null;
  return { choices: choices.map((c) => ({ id: c.tag, label: c.label })), value: cur.tag ?? '' };
}

/** The model a Context choice stands for (the current family's newest version in that size). */
export function contextModel(b: Browse, id: string): PickerModel | null {
  return b.currentFamily?.newest.find((m) => (m.tag ?? '') === id) ?? null;
}

// ── The pill ──

/** The model's name for the pill: its short name, with its size only when the runtime offers that model in more than
 *  one size ("Opus 5.5 · 1M" beside an "Opus 5.5 · 200K"); null with no model. */
export function pillName(m: PickerModel | null | undefined, models: PickerModel[]): string | null {
  if (!m) return null;
  const sizes = new Set(models.filter((x) => x.providerId === m.providerId && x.name === m.name).map((x) => x.tag ?? ''));
  return m.tag && sizes.size > 1 ? `${m.name} · ${m.tag}` : m.name;
}

/** The pill's words: the model's short name, then its effort when it has one ("Opus 5 · Medium"). */
export function pillText(name: string | null | undefined, effort: string | null | undefined, fallback = 'Choose a model'): string {
  const n = name || fallback;
  return effort && name ? `${n} · ${effort}` : n;
}

// ── Keyboard ──

export interface NavState {
  tabs: string[];
  tab: string | null;
  /** The pickable rows, in the order shown (keys). */
  keys: string[];
  active: string | null;
  query: string;
}

export type NavResult =
  | { kind: 'active'; active: string | null }
  | { kind: 'tab'; tab: string }
  | { kind: 'pick'; key: string }
  | { kind: 'close' };

/** What a key does while focus is in the search box (or a row): ↑/↓ move through the pickable rows, ←/→ change the
 *  provider (only with an empty search: with text they move the caret), Enter picks, Esc closes. Null: not ours. */
export function navKey(key: string, s: NavState): NavResult | null {
  if (key === 'ArrowDown' || key === 'ArrowUp') return { kind: 'active', active: moveActive(s.keys, s.active, key === 'ArrowDown' ? 1 : -1) };
  if ((key === 'ArrowLeft' || key === 'ArrowRight') && !s.query && s.tabs.length > 1) {
    const i = Math.max(0, s.tab ? s.tabs.indexOf(s.tab) : 0);
    const d = key === 'ArrowRight' ? 1 : -1;
    return { kind: 'tab', tab: s.tabs[(i + d + s.tabs.length) % s.tabs.length] };
  }
  if (key === 'Enter') return s.active && s.keys.includes(s.active) ? { kind: 'pick', key: s.active } : null;
  if (key === 'Escape') return { kind: 'close' };
  return null;
}

/** Keys inside the rail (a tablist): arrows along it, Home/End to its ends. Null: not ours. */
export function railKey(key: string, tabs: string[], tab: string | null): string | null {
  if (!tabs.length) return null;
  const i = Math.max(0, tab ? tabs.indexOf(tab) : 0);
  if (key === 'ArrowDown' || key === 'ArrowRight') return tabs[(i + 1) % tabs.length];
  if (key === 'ArrowUp' || key === 'ArrowLeft') return tabs[(i - 1 + tabs.length) % tabs.length];
  if (key === 'Home') return tabs[0];
  if (key === 'End') return tabs[tabs.length - 1];
  return null;
}
