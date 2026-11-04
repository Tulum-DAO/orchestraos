/**
 * ModelPickerPanel — what the model picker draws, from props only (no fetching, no effects), so it renders the same
 * in the popover, the phone sheet and in place on a settings page, and can be rendered in a test.
 *
 *   ┌──────┬──────────────────────────────────────┐
 *   │ rail │ header: provider · status · usage  v │   the rail: one tab per runtime, a usage ring round its mark
 *   │      │ [ Search all models ]                │   and a status dot; on a phone it is a row of tabs on top
 *   │      │ Opus 5.5 Deep work · long tasks    ✓ │   one row per FAMILY, its newest version: name, purpose (lib/modelPurpose.ts),
 *   │      │ Sonnet 5 Everyday · fast             │   "default", check (the row in use is tinted; no radio)
 *   │      │ › Older versions                   4 │   every older model, one row each; opens by itself on an older model in use
 *   │  +   │ Context [Standard|1M] Effort [Low|…] │   Context: only when the family in use comes in more than one size;
 *   │      │ Applies from your next message       │   Effort: only where a level would really be applied (lib/modelEffort.ts)
 *   └──────┴──────────────────────────────────────┘
 *
 * The container (ModelPicker.tsx) owns the state and the keyboard; the rules are lib/modelPickerPanel.ts.
 */
import type { CSSProperties, KeyboardEvent, ReactNode, RefObject } from 'react';
import { Check, ChevronRight, Plus, Search, X } from 'lucide-react';
import { isCurrent, type PickerModel, type PickerValue } from '../../lib/modelPicker';
import { modelPurpose } from '../../lib/modelPurpose';
import type { EffortLevel } from '../../lib/modelEffort';
import {
  canPick, isRuntimeDefault, otherRuntimeNote, ringDash, rowReason, statusWords,
  type Browse, type BrowseRow, type ContextControl, type PickerScope, type RailEntry,
} from '../../lib/modelPickerPanel';
import type { ProviderRow } from '../../lib/modelSelectorFilter';

export type PanelLayout = 'popover' | 'sheet' | 'inline';
export type MarkRenderer = (providerId: string, label: string, size: number, dim?: boolean) => ReactNode;

export interface ModelPickerPanelProps {
  layout: PanelLayout;
  idBase: string;
  rows: ProviderRow[];
  entries: RailEntry[];
  /** The rows under the search box while searching: every concrete model that matches (lib/modelPickerPanel.ts
   *  visibleRows); without a search, when `browse` is absent. */
  shown: PickerModel[];
  /** Without a search: the provider's families and its older versions (lib/modelPickerPanel.ts browseRows). */
  browse?: Browse | null;
  /** Open or close a provider's "Older versions". */
  onToggleOlder?: ((providerId: string) => void) | null;
  scope: PickerScope;
  value: PickerValue | null;
  tab: string | null;
  onTab: (id: string) => void;
  query: string;
  onQuery: (q: string) => void;
  active: string | null;
  onActive: (key: string) => void;
  onPick: (m: PickerModel) => void;
  renderMark: MarkRenderer;
  /** "Use the default brain" (Arturo): its key, whether it is in use, and the pick. */
  defaultRow?: { key: string; selected: boolean; onPick: () => void } | null;
  /** The effort control, only where it would be applied. */
  effort?: { levels: EffortLevel[]; value: string | null; onChange: (id: string) => void } | null;
  /** The Context size control (Standard | 1M), only when the family in use comes in more than one size. */
  context?: (ContextControl & { onChange: (id: string) => void }) | null;
  /** When a pick takes effect ("Applies from your next message"). */
  note?: string | null;
  onSignIn?: ((providerId: string) => void) | null;
  onNewAgent?: ((providerId: string) => void) | null;
  onAddProvider?: (() => void) | null;
  /** Instead of the list (the add-a-provider panel). */
  replaceBody?: ReactNode;
  loading?: boolean;
  error?: string | null;
  onKeyDown?: (e: KeyboardEvent<HTMLElement>) => void;
  onRailKeyDown?: (e: KeyboardEvent<HTMLElement>) => void;
  onClose?: () => void;
  inputRef?: RefObject<HTMLInputElement | null>;
  panelRef?: RefObject<HTMLDivElement | null>;
  style?: CSSProperties;
}

const DOT: Record<RailEntry['status'], string> = {
  'signed-in': 'bg-emerald-500',
  'signed-out': 'bg-amber-500',
  unverified: 'bg-amber-500',
  'not-installed': 'bg-tone-600',
};

/** A runtime's mark with its usage ring (share of its limit used; no ring when unknown) and its status dot. */
export function RailMark({ entry, size, renderMark }: { entry: RailEntry; size: number; renderMark: MarkRenderer }) {
  const box = size + 14;
  const r = box / 2 - 2;
  const ring = entry.usage ? ringDash(entry.usage.pct, r) : null;
  return (
    <span className="relative inline-flex items-center justify-center shrink-0" style={{ width: box, height: box }}>
      {ring && (
        <svg width={box} height={box} viewBox={`0 0 ${box} ${box}`} className="absolute inset-0 -rotate-90" aria-hidden data-usage-ring={entry.usage!.pct}>
          <circle cx={box / 2} cy={box / 2} r={r} fill="none" strokeWidth={2} className="stroke-tone-800" />
          <circle cx={box / 2} cy={box / 2} r={r} fill="none" strokeWidth={2} strokeLinecap="round"
                  strokeDasharray={`${ring.dash} ${ring.gap}`}
                  className={entry.usage!.high && !entry.usage!.stale ? 'stroke-amber-500' : entry.usage!.stale ? 'stroke-tone-600' : 'stroke-tone-300'} />
        </svg>
      )}
      {renderMark(entry.id, entry.label, size, !entry.usable)}
      <span aria-hidden data-status={entry.status}
            className={`absolute bottom-0 right-0 w-2 h-2 rounded-full ring-2 ring-popover ${DOT[entry.status]}`} />
    </span>
  );
}

function railTitle(e: RailEntry, row: ProviderRow | undefined): string {
  const status = row ? statusWords(row) : e.status;
  return `${e.label}: ${status}${e.usage ? `, ${e.usage.text}` : ''}`;
}

export default function ModelPickerPanel({ inputRef, panelRef, ...p }: ModelPickerPanelProps) {
  const sheet = p.layout === 'sheet';
  const labelOf = (id: string) => p.entries.find((e) => e.id === id)?.label ?? id;
  const entry = p.entries.find((e) => e.id === p.tab) ?? null;
  const row = p.rows.find((r) => r.provider.id === p.tab);
  const searching = !!p.query.trim();
  const listId = `${p.idBase}-list`;
  const tabPanelId = `${p.idBase}-panel`;
  const optionId = (k: string) => `${p.idBase}-opt-${k}`;
  const target = sheet ? 'min-h-[44px]' : 'min-h-[40px]';

  const rail = (
    <div role="tablist" aria-label="Providers" aria-orientation={sheet ? 'horizontal' : 'vertical'} onKeyDown={p.onRailKeyDown}
         className={sheet ? 'flex items-center gap-1 px-3 pt-1 pb-2 overflow-x-auto border-b border-tone-800'
           : 'flex flex-col items-center gap-1 p-1.5 border-r border-tone-800 shrink-0'}>
      {p.entries.map((e) => {
        const on = e.id === p.tab && !searching;
        return (
          <button key={e.id} type="button" role="tab" id={`${p.idBase}-tab-${e.id}`} aria-selected={on} aria-controls={tabPanelId}
                  tabIndex={e.id === p.tab ? 0 : -1} title={railTitle(e, p.rows.find((r) => r.provider.id === e.id))}
                  aria-label={railTitle(e, p.rows.find((r) => r.provider.id === e.id))} data-rail={e.id}
                  onClick={() => p.onTab(e.id)}
                  className={`${sheet ? 'min-w-[44px] min-h-[44px] px-1.5 gap-1.5' : 'w-11 h-11'} inline-flex items-center justify-center rounded-lg shrink-0 ${on ? 'bg-tone-800' : 'hover:bg-tone-800/60'}${e.usable ? '' : ' opacity-60'}`}>
            <RailMark entry={e} size={18} renderMark={p.renderMark} />
            {sheet && <span className={`text-px12 ${on ? 'text-tone-100' : 'text-tone-400'}`}>{e.label}</span>}
          </button>
        );
      })}
      {p.onAddProvider && (
        <button type="button" onClick={p.onAddProvider} aria-label="Add a provider" title="Add a provider" data-model-picker-add-provider
                className={`${sheet ? 'min-w-[44px] min-h-[44px]' : 'w-11 h-11 mt-auto'} inline-flex items-center justify-center rounded-lg text-tone-400 hover:text-tone-100 hover:bg-tone-800/60 shrink-0`}>
          <Plus size={16} />
        </button>
      )}
    </div>
  );

  const header = entry && !searching && (
    <div className="flex items-baseline gap-1.5 px-3 pt-2.5 pb-1 text-px12 min-w-0" data-model-picker-header={entry.id}>
      <span className="font-medium text-tone-100 shrink-0">{entry.label}</span>
      <span className="text-tone-500 min-w-0 truncate">
        · {row ? statusWords(row) : entry.status}
        {entry.usage && <span className={entry.usage.high && !entry.usage.stale ? ' text-amber-500' : ''}> · {entry.usage.text}</span>}
      </span>
      {entry.version && <span className="ml-auto shrink-0 text-px10 text-tone-500 tabular-nums" title={`${entry.label} CLI version`}>v{entry.version}</span>}
    </div>
  );

  // What the selected provider needs before its models can be picked here.
  const otherNote = entry && !searching ? otherRuntimeNote(entry.id, entry.label, p.scope, labelOf) : null;
  const banner = entry && !searching && (!entry.usable || otherNote) && (
    <div className="mx-3 my-1.5 px-3 py-2 rounded-lg bg-tone-800/50 text-px12 text-tone-300 flex flex-col gap-2" data-model-picker-banner={entry.id}>
      {!entry.usable ? (
        <>
          <span>{entry.label} is {row ? statusWords(row) : 'not available'} on this machine.</span>
          {p.onSignIn && (
            <button type="button" onClick={() => p.onSignIn!(entry.id)} data-model-connect={entry.id}
                    className={`self-start ${target} px-3 rounded-lg border border-tone-700 text-tone-100 hover:bg-tone-800`}>
              Sign in
            </button>
          )}
        </>
      ) : (
        <>
          <span>{otherNote}</span>
          {p.onNewAgent && (
            <button type="button" onClick={() => p.onNewAgent!(entry.id)} data-model-new-agent={entry.id}
                    className={`self-start ${target} px-3 rounded-lg border border-tone-700 text-tone-100 hover:bg-tone-800`}>
              Start a new agent with {entry.label}
            </button>
          )}
        </>
      )}
    </div>
  );

  const defaultOf = (providerId: string) => p.entries.find((e) => e.id === providerId)?.defaultModel;
  /** A model's row. As a family row (`family`): ticked when the model in use is one of its newest version's sizes, the
   *  size said only when there is one (more than one: the Context control), "default" when any of them is. */
  const rowFor = (m: PickerModel, family?: Extract<BrowseRow, { kind: 'family' }>) => {
    const pickable = canPick(m, p.scope);
    const on = family ? family.current : isCurrent(m, p.value);
    const purpose = modelPurpose(m.providerId, m.name);
    const isDefault = (family ? family.family.newest : [m]).some((x) => isRuntimeDefault(x, defaultOf(x.providerId)));
    const tag = family && family.family.newest.length > 1 ? undefined : m.tag;
    const why = !pickable ? rowReason(m, p.scope, labelOf) : null;
    return (
      <div key={m.key} id={optionId(m.key)} role="option" aria-selected={on} aria-disabled={!pickable || undefined}
           data-model-option={m.key} data-model-unavailable={!pickable || undefined} title={why ?? undefined}
           onMouseEnter={pickable ? () => p.onActive(m.key) : undefined} onClick={pickable ? () => p.onPick(m) : undefined}
           className={`w-full flex items-center gap-2.5 px-3 ${target} text-left text-sm rounded-lg ${pickable ? 'cursor-pointer' : 'cursor-default text-tone-500'} ${on ? 'bg-tone-800 text-tone-white font-medium' : pickable && m.key === p.active ? 'bg-tone-800/50 text-tone-white' : pickable ? 'text-tone-200' : ''}`}>
        {searching && <span className="shrink-0 inline-flex items-center gap-1 text-px10 text-tone-500" data-provider-badge={m.providerId}>{p.renderMark(m.providerId, m.providerLabel, 12, !pickable)}{m.providerLabel}</span>}
        <span className="flex-1 min-w-0 flex flex-col">
          <span className="min-w-0 flex items-baseline gap-2">
            <span className="truncate">{m.name}{tag && <span className="text-px11 text-tone-500 tabular-nums"> · {tag}</span>}</span>
            {purpose && !searching && <span className="text-px11 text-tone-500 truncate" data-purpose>{purpose}</span>}
          </span>
          {/* Why it can't be picked here, on its own line under the name (a phone has no hover). */}
          {why && <span className="text-px11 leading-snug text-tone-500" data-model-reason>{why}</span>}
        </span>
        {isDefault && <span className="shrink-0 px-1.5 py-px rounded-full border border-tone-700 text-px10 text-tone-400" data-default-pill>default</span>}
        <span className="w-4 shrink-0">{on && <Check size={15} className={pickable ? 'text-tone-white' : 'text-tone-400'} aria-label="In use" />}</span>
      </div>
    );
  };

  // "Older versions": a row of the list (the arrows reach it, Enter opens it), never a model.
  const olderRow = (r: Extract<BrowseRow, { kind: 'older' }>) => (
    <div key={r.key} id={optionId(r.key)} role="option" aria-label={`Older versions (${r.count}), ${r.open ? 'shown' : 'hidden'}`}
         data-model-older={r.providerId} onMouseDown={(e) => e.preventDefault()} aria-selected={false}
         onMouseEnter={() => p.onActive(r.key)} onClick={() => p.onToggleOlder?.(r.providerId)}
         className={`w-full flex items-center gap-2 px-3 ${target} mt-1 text-left text-px12 rounded-lg cursor-pointer ${r.key === p.active ? 'bg-tone-800/50 text-tone-100' : 'text-tone-400 hover:text-tone-200'}`}>
      <ChevronRight size={14} aria-hidden className={`shrink-0 transition-transform${r.open ? ' rotate-90' : ''}`} />
      <span className="flex-1 min-w-0">Older versions</span>
      <span className="shrink-0 text-px11 text-tone-500 tabular-nums">{r.count}</span>
    </div>
  );
  const browsing = !searching && !!p.browse;
  const rowsShown = browsing ? p.browse!.rows.length : p.shown.length;

  const list = (
    <div id={listId} role="listbox" aria-label={searching ? 'Models from every provider' : `${entry?.label ?? ''} models`} className="flex flex-col gap-0.5 p-1.5">
      {browsing
        ? p.browse!.rows.map((r) => (r.kind === 'older' ? olderRow(r) : r.kind === 'family' ? rowFor(r.model, r) : rowFor(r.model)))
        : p.shown.map((m) => rowFor(m))}
      {searching && p.shown.length === 0 && !p.loading && <p className="px-3 py-6 text-center text-sm text-tone-500" data-model-picker-empty>No matching models.</p>}
      {!searching && rowsShown === 0 && entry && !p.loading && <p className="px-3 py-4 text-sm text-tone-500">{entry.label} lists no models.</p>}
    </div>
  );

  // Context, beside Effort in the same style: the sizes of the family in use's newest version. A size that can't be
  // picked here says why on a line under the controls (a phone has no hover).
  const contextWhyId = `${p.idBase}-context-why`;
  const contextWhy = p.context?.choices.filter((c) => c.disabled).map((c) => `${c.label}: ${c.title ?? 'Not available here'}`).join(' · ') || null;
  const context = p.context && (
    <div className="flex items-center gap-3 shrink-0" data-model-context>
      <span className="text-px12 text-tone-400 shrink-0" id={`${p.idBase}-context`}>Context</span>
      <div role="radiogroup" aria-labelledby={`${p.idBase}-context`} className="flex rounded-lg border border-tone-800 p-0.5">
        {p.context.choices.map((c) => {
          const on = c.id === p.context!.value;
          return (
            <button key={c.id} type="button" role="radio" aria-checked={on} disabled={c.disabled} title={c.title} data-context={c.id}
                    aria-describedby={c.disabled ? contextWhyId : undefined}
                    onClick={() => p.context!.onChange(c.id)}
                    className={`${target} px-2.5 rounded-md text-px12 disabled:opacity-50 disabled:cursor-default ${on ? 'bg-tone-800 text-tone-white' : 'text-tone-400 hover:text-tone-100'}`}>
              {c.label}
            </button>
          );
        })}
      </div>
    </div>
  );

  const effort = p.effort && (
    <div className="flex flex-1 min-w-[240px] items-center gap-3" data-model-effort>
      <span className="text-px12 text-tone-400 shrink-0" id={`${p.idBase}-effort`}>Effort</span>
      <div role="radiogroup" aria-labelledby={`${p.idBase}-effort`} className="flex flex-1 rounded-lg border border-tone-800 p-0.5">
        {p.effort.levels.map((l) => {
          const on = l.id === p.effort!.value;
          return (
            <button key={l.id} type="button" role="radio" aria-checked={on} onClick={() => p.effort!.onChange(l.id)} data-effort={l.id}
                    className={`flex-1 ${target} px-2 rounded-md text-px12 ${on ? 'bg-tone-800 text-tone-white' : 'text-tone-400 hover:text-tone-100'}`}>
              {l.label}
            </button>
          );
        })}
      </div>
    </div>
  );

  // "Use the default brain" (Arturo): once, above every provider's content, whatever tab is open; not while searching.
  const defaultBlock = p.defaultRow && !searching && !p.replaceBody && (
    <div role="listbox" aria-label="Default brain" className="px-1.5 py-1.5 border-b border-tone-800" data-model-default-row>
        <div id={optionId(p.defaultRow.key)} role="option" aria-selected={p.defaultRow.selected} data-model-option={p.defaultRow.key}
             onMouseEnter={() => p.onActive(p.defaultRow!.key)} onClick={p.defaultRow.onPick}
             className={`w-full flex items-center gap-2.5 px-3 ${target} text-left text-sm rounded-lg cursor-pointer ${p.defaultRow.selected ? 'bg-tone-800 text-tone-white font-medium' : p.defaultRow.key === p.active ? 'bg-tone-800/50 text-tone-white' : 'text-tone-200'}`}>
          <span className="flex-1 min-w-0">Use the default brain <span className="text-px11 text-tone-500">whatever this install is set up with</span></span>
          <span className="w-4 shrink-0">{p.defaultRow.selected && <Check size={15} className="text-tone-white" aria-label="In use" />}</span>
        </div>
    </div>
  );

  const main = (
    <div className="flex-1 min-w-0 flex flex-col min-h-0">
      {defaultBlock}
      {header}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-tone-800">
        <Search size={15} className="shrink-0 text-tone-500" aria-hidden />
        <input ref={inputRef} value={p.query} onChange={(e) => p.onQuery(e.target.value)} placeholder="Search all models" aria-label="Search all models"
               role="combobox" aria-expanded aria-controls={listId} aria-activedescendant={p.active ? optionId(p.active) : undefined}
               autoComplete="off" spellCheck={false}
               className={`flex-1 min-w-0 bg-transparent text-sm text-tone-100 placeholder:text-tone-500 outline-none ${sheet ? 'py-2.5' : 'py-1'}`} />
        {p.loading && p.rows.length > 0 && <span className="text-px10 text-tone-500 shrink-0">checking…</span>}
      </div>
      <div role="tabpanel" id={tabPanelId} aria-labelledby={entry ? `${p.idBase}-tab-${entry.id}` : undefined}
           className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
        {p.loading && p.rows.length === 0 && <p className="px-4 py-4 text-xs text-tone-500">Checking providers…</p>}
        {p.error && <p className="px-4 py-3 text-xs text-tone-red-400" role="alert">Could not load providers: {p.error}</p>}
        {p.replaceBody ?? (!p.error && (p.rows.length > 0 || !p.loading) && <>{banner}{list}</>)}
      </div>
      {!p.replaceBody && (context || effort) && (
        <div className="px-3 py-2 border-t border-tone-800 flex flex-wrap items-center gap-x-4 gap-y-2" data-model-picker-footer>
          {context}{effort}
          {contextWhy && <p id={contextWhyId} className="w-full text-px11 leading-snug text-tone-500" data-model-context-reason>{contextWhy}</p>}
        </div>
      )}
      {!p.replaceBody && p.note && <p className="px-3 py-1.5 border-t border-tone-800 text-px11 text-tone-500" data-model-picker-note>{p.note}</p>}
    </div>
  );

  return (
    <div ref={panelRef} role="dialog" aria-modal={p.layout !== 'inline'} aria-label="Choose a model" onKeyDown={p.onKeyDown}
         data-model-picker={p.layout} style={p.style}
         className={p.layout === 'inline' ? 'flex min-w-0 min-h-0'
           : sheet ? 'fixed inset-x-0 bottom-0 flex flex-col max-h-[85dvh] rounded-t-2xl border-t border-tone-800 bg-popover text-popover-foreground shadow-2xl pb-[env(safe-area-inset-bottom)]'
             : 'fixed flex w-[440px] max-w-[calc(100vw-16px)] h-[min(460px,80vh)] rounded-xl border border-tone-800 bg-popover text-popover-foreground shadow-2xl overflow-hidden'}>
      {sheet && (
        <div className="flex items-center justify-between px-4 pt-3 pb-1">
          <h2 className="text-sm font-medium text-tone-100">Choose a model</h2>
          <button type="button" onClick={p.onClose} aria-label="Close" className="w-11 h-11 -mr-2 inline-flex items-center justify-center rounded-lg hover:bg-tone-800 text-tone-400"><X size={18} /></button>
        </div>
      )}
      {rail}
      {main}
    </div>
  );
}

