/**
 * ModelPicker — choose the model an agent (or Arturo) runs: a compact chip, then a popover (a bottom sheet on a
 * phone) with a search box and the models grouped by provider. Rules (names, search, nicknames, grouping, recent)
 * in lib/modelPicker.ts.
 *
 * Availability comes from GET /api/runtimes/available, asked again every time it opens, so a provider signed in
 * a moment ago shows. Providers that are not installed or not signed in sit greyed at the bottom with the probe's
 * reason and a Connect action (the same connect modal the old sheet used); "Add a provider" shows what it takes
 * and opens an install terminal where the deployment allows it.
 *
 * Keyboard: the search box keeps focus; arrows move through the models, Enter picks, Esc closes, Tab stays inside.
 * Logos are the proven brand marks only (components/avatar/brandMarks.ts), drawn unmodified; a provider without
 * one gets a neutral letter.
 *
 * `variant="inline"` draws the same list in place, always open (a settings page), with no chip.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, ChevronLeft, Copy, Plus, Search, Terminal, X } from 'lucide-react';
import WebTerminal from '../WebTerminal';
import { ProviderConnectModal } from '../agent/ProviderConnectModal';
import { brandUrl, providerMark } from '../avatar/brandMarks';
import { installCommand, installNote, cliFor } from '../../lib/providerConnect';
import { providerTileAction, showAddProvider } from '../../lib/features';
import { buildProviderRows, type ProviderAvailability, type ProviderCapabilities, type ProviderModel, type ProviderRow } from '../../lib/modelSelectorFilter';
import {
  buildPickerModels, groupModels, flatten, findCurrent, isCurrent, modelTitle, moveActive,
  letterMark, type PickerModel, type PickerValue,
} from '../../lib/modelPicker';

export interface ModelPick { providerId: string; modelId: string; modelLabel: string; capabilities: ProviderCapabilities }

export interface ModelPickerProps {
  /** The model in use. A short id ("opus", "fable") matches the model it stands for. */
  value: PickerValue | null;
  onValueChange: (pick: ModelPick) => void;
  /** On an agent's page: the models that agent can switch to (the gateway's list), shown for its own provider. */
  agentModels?: { providerId: string; models: (ProviderModel & { subtitle?: string })[] } | null;
  /** Offer "Use the default brain" first (Arturo). */
  onPickDefault?: () => void;
  /** Whose model this chooses: an agent's (every model it may use, 1M versions included) or Arturo's brain (models
   *  only agents can use are listed dimmed). Default: an agent's. */
  chooser?: 'agent' | 'arturo';
  /** 'popover' (a chip that opens it) or 'inline' (always open, in place). */
  variant?: 'popover' | 'inline';
  /** Controlled open state (a menu item can open it); uncontrolled when left out. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** False: no chip (opened only through `open`). */
  trigger?: boolean;
  /** The chip's words while the current model is not in the list yet. */
  label?: string;
  triggerClassName?: string;
}

interface RuntimesAvailableResponse { providers: ProviderAvailability[] }

const DEFAULT_KEY = '__default__';
// Above the Arturo pane (z 76), as the old sheet was; the connect modal sits above this (z 85).
const Z = 80;

/** The page's theme as drawn (the `dark` class on <html>), followed live. */
function useDarkMode(): boolean {
  const read = () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark');
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const mo = new MutationObserver(() => setDark(read()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => mo.disconnect();
  }, []);
  return dark;
}

function useNarrow(): boolean {
  const q = '(max-width: 639px)';
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return narrow;
}

/** A provider's badge: its proven brand mark, unmodified, or a neutral letter. Greyed providers are dimmed, never
 *  recoloured. */
export function ProviderMark({ providerId, label, dark, size = 16, dim = false }: { providerId: string; label: string; dark: boolean; size?: number; dim?: boolean }) {
  const mark = providerMark(providerId);
  const style = { width: size, height: size };
  if (mark) return <img src={brandUrl(mark, dark ? 'dark' : 'light')} alt="" aria-hidden style={style} className={`shrink-0 object-contain${dim ? ' opacity-50' : ''}`} />;
  return (
    <span aria-hidden style={{ ...style, fontSize: Math.round(size * 0.6) }}
          className={`shrink-0 inline-flex items-center justify-center rounded-full bg-tone-800 text-tone-300 font-semibold leading-none${dim ? ' opacity-50' : ''}`}>
      {letterMark(label)}
    </span>
  );
}

/** What it takes for a provider to appear, with each missing one's install command and a terminal to run it in. */
function AddProviderPanel({ rows, onInstalled }: { rows: ProviderRow[]; onInstalled: () => void }) {
  const missing = rows.filter((r) => !r.selectable);
  const [session, setSession] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  async function openTerminal() {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const res = await fetch('/api/agents/login-shell', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ purpose: 'install' }) });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || !json.ok) setError(json.reason || json.error || `HTTP ${res.status}`);
      else setSession(json.session as string);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'network');
    } finally { setBusy(false); }
  }
  return (
    <div className="text-xs text-tone-400 leading-relaxed px-3 py-2 flex flex-col gap-2" data-model-picker-add>
      <p className="text-tone-300">A provider shows up here once its CLI is installed on this machine and signed in.</p>
      {missing.length > 0 && (
        <ul className="flex flex-col gap-2">
          {missing.map((r) => {
            const cmd = installCommand(r.provider.id);
            return (
              <li key={r.provider.id} className="flex flex-col gap-1">
                <span className="text-tone-300">{r.provider.label} <span className="text-tone-500">— {r.greyReason}</span></span>
                {cmd ? (
                  <div className="flex items-start gap-1">
                    <code className="flex-1 px-2 py-1 rounded bg-tone-800 text-tone-200 break-all">{cmd}</code>
                    <button type="button" aria-label={`Copy the ${cliFor(r.provider.id)} install command`} className="p-1 rounded hover:bg-tone-800 shrink-0"
                            onClick={() => {
                              void navigator.clipboard?.writeText(cmd);
                              setCopied(r.provider.id);
                              setTimeout(() => setCopied((c) => (c === r.provider.id ? null : c)), 1500);
                            }}>
                      {copied === r.provider.id ? <span className="text-px10">copied</span> : <Copy size={14} />}
                    </button>
                  </div>
                ) : <span className="text-tone-500">{installNote(r.provider.id)}</span>}
              </li>
            );
          })}
        </ul>
      )}
      {!session && (
        <button type="button" onClick={openTerminal} disabled={busy}
                className="self-start inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-tone-700 hover:bg-tone-800 text-tone-200 disabled:opacity-60">
          <Terminal size={14} /> {busy ? 'Opening a terminal…' : 'Open a terminal here'}
        </button>
      )}
      {error && <p className="text-tone-red-400">Could not open a terminal: {error}</p>}
      {session && (
        <>
          <div className="rounded-lg overflow-hidden border border-tone-700 h-[40vh] min-h-[260px]"><WebTerminal session={session} machine="vps" /></div>
          <button type="button" onClick={onInstalled} className="self-start underline underline-offset-2 text-tone-400 hover:text-tone-200">check again</button>
        </>
      )}
      <p className="text-tone-500">Install it, run the CLI once and sign in, then check again: the list asks again every time it opens.</p>
    </div>
  );
}

export default function ModelPicker({
  value, onValueChange, agentModels, onPickDefault, chooser = 'agent', variant = 'popover', open: openProp, onOpenChange, trigger = true, label, triggerClassName,
}: ModelPickerProps) {
  const inline = variant === 'inline';
  const [openState, setOpenState] = useState(false);
  const open = inline || (openProp ?? openState);
  const setOpen = useCallback((o: boolean) => { if (openProp === undefined) setOpenState(o); onOpenChange?.(o); }, [openProp, onOpenChange]);
  const dark = useDarkMode();
  const narrow = useNarrow();
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [connectRow, setConnectRow] = useState<ProviderRow | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number } | null>(null);

  const probe = useCallback(async (fresh = false) => {
    setLoading(true); setError(null);
    try {
      const res = await fetch(fresh ? '/api/runtimes/available/refresh' : '/api/runtimes/available', fresh ? { method: 'POST' } : undefined);
      if (!res.ok) throw new Error(`GET /api/runtimes/available -> ${res.status}`);
      const next = buildProviderRows(((await res.json()) as RuntimesAvailableResponse).providers ?? []);
      setRows(next);
      setConnectRow((cur) => (cur ? next.find((r) => r.provider.id === cur.provider.id) ?? cur : cur));
    } catch (e) {
      setError((e as Error).message);
    } finally { setLoading(false); }
  }, []);

  // Asked again every time it opens: no stale list across a sign-in.
  useEffect(() => {
    if (!open) return;
    setQuery(''); setAdding(false);
    void probe();
  }, [open, probe]);

  const models = useMemo(() => buildPickerModels(rows, agentModels, { forArturo: chooser === 'arturo' }), [rows, agentModels, chooser]);
  const current = findCurrent(models, value);
  // One order in every picker: by provider, newest first (lib/modelPicker.ts newestFirst); no Recent group above it.
  const groups = useMemo(() => groupModels(models, { query }), [models, query]);
  const showDefault = !!onPickDefault && !query.trim();
  // The arrows move through what can be picked; a dimmed entry is shown, not chosen.
  const keys = useMemo(() => [...(showDefault ? [DEFAULT_KEY] : []), ...flatten(groups).filter((m) => !m.unavailable).map((m) => m.key)], [groups, showDefault]);

  // The row the arrows start from: the model in use, else the first.
  useEffect(() => {
    if (!open) return;
    setActive((a) => (a && keys.includes(a) ? a : (current && keys.includes(current.key) ? current.key : keys[0] ?? null)));
  }, [open, keys, current]);

  useEffect(() => {
    if (!open || inline) return;
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open, inline]);

  // Desktop: under the chip (above it when there is no room below); with no chip, near the top, centred.
  useLayoutEffect(() => {
    if (!open || inline || narrow) { setPos(null); return; }
    const r = triggerRef.current?.getBoundingClientRect();
    const w = 360;
    if (!r) { setPos({ left: Math.max(8, (window.innerWidth - w) / 2), top: Math.round(window.innerHeight * 0.12) }); return; }
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left));
    const below = window.innerHeight - r.bottom;
    setPos(below >= 360 || below >= r.top ? { left, top: r.bottom + 6 } : { left, bottom: window.innerHeight - r.top + 6 });
  }, [open, inline, narrow]);

  const close = useCallback(() => {
    if (inline) return;
    setOpen(false);
    setTimeout(() => triggerRef.current?.focus(), 0);
  }, [inline, setOpen]);

  /** Connect: the picker closes first, then the Connect sheet opens as the top layer (never under the picker). */
  const connect = useCallback((row: ProviderRow) => {
    if (!inline) setOpen(false);
    setConnectRow(row);
  }, [inline, setOpen]);

  // A click outside the popover closes it (the phone sheet has its own backdrop).
  useEffect(() => {
    if (!open || inline || narrow || connectRow) return;
    const down = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  }, [open, inline, narrow, connectRow, setOpen]);

  // The view under the search box changes (models / add a provider): keep focus inside, where Esc and Tab work.
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open || inline) return;
    (adding ? backRef.current : inputRef.current)?.focus();
  }, [adding, open, inline]);

  useEffect(() => {
    if (!active) return;
    document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: 'nearest' });
  }, [active, listId]);

  const pick = (m: PickerModel) => {
    onValueChange({ providerId: m.providerId, modelId: m.id, modelLabel: modelTitle(m), capabilities: m.capabilities });
    close();
  };
  const pickKey = (k: string | null) => {
    if (!k) return;
    if (k === DEFAULT_KEY) { onPickDefault?.(); close(); return; }
    const m = flatten(groups).find((x) => x.key === k);
    if (m) pick(m);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => moveActive(keys, a, e.key === 'ArrowDown' ? 1 : -1));
    } else if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') {
      e.preventDefault(); pickKey(active);
    } else if (e.key === 'Escape' && !inline) {
      e.preventDefault(); e.stopPropagation(); close();
    } else if (e.key === 'Tab' && !inline) {
      // Tab stays inside: from the last control back to the first, and the other way.
      const f = [...(panelRef.current?.querySelectorAll<HTMLElement>('input, button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])') ?? [])];
      if (!f.length) return;
      const i = f.indexOf(document.activeElement as HTMLElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    }
  };

  const optionClass = (k: string) => `w-full flex items-center gap-2.5 px-3 py-2 text-left text-sm rounded-lg ${k === active ? 'bg-tone-800 text-tone-white' : 'text-tone-200'}`;
  const anyModel = groups.length > 0;
  const firstLoad = loading && rows.length === 0;

  const list: ReactNode = adding ? (
    <div>
      <button ref={backRef} type="button" onClick={() => setAdding(false)} className="flex items-center gap-1 px-3 py-2 text-xs text-tone-400 hover:text-tone-200">
        <ChevronLeft size={14} /> Models
      </button>
      <AddProviderPanel rows={rows} onInstalled={() => { void probe(true); }} />
    </div>
  ) : (
    <div id={listId} role="listbox" aria-label="Models" className="flex flex-col gap-0.5 p-1.5">
      {showDefault && (
        <div id={`${listId}-${DEFAULT_KEY}`} role="option" aria-selected={!value} data-model-option={DEFAULT_KEY}
             className={`${optionClass(DEFAULT_KEY)} cursor-pointer`} onMouseEnter={() => setActive(DEFAULT_KEY)} onClick={() => pickKey(DEFAULT_KEY)}>
          <span className="flex-1 min-w-0">Use the default brain <span className="text-px11 text-tone-500">whatever this install is set up with</span></span>
          {!value && <Check size={15} className="shrink-0 text-tone-white" aria-hidden />}
        </div>
      )}
      {groups.map((g) => {
        // A provider that can't be used yet: its heading says why and offers Connect; its models are listed dimmed.
        const row = rows.find((r) => r.provider.id === g.id);
        const off = row && !row.selectable ? row : null;
        return (
        <div key={g.id} role="group" aria-label={g.label} data-model-group={g.id}>
          <div className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-px11 font-medium text-tone-500 uppercase tracking-wide">
            {g.id !== 'recent' && <ProviderMark providerId={g.id} label={g.label} dark={dark} size={12} dim={!!off} />}
            <span className="flex-1 min-w-0 truncate">{g.label}{off && <span className="normal-case tracking-normal font-normal"> · {off.greyReason}</span>}</span>
            {off && providerTileAction(false) === 'connect' && (
              <button type="button" onClick={() => connect(off)} data-model-connect={g.id}
                      className="shrink-0 normal-case tracking-normal px-2 py-0.5 rounded-md border border-tone-700 text-px12 font-normal text-tone-300 hover:bg-tone-800 hover:text-tone-white">
                Connect
              </button>
            )}
          </div>
          {g.models.map((m) => {
            const on = isCurrent(m, value);
            if (m.unavailable) {
              return (
                <div key={m.key} role="option" aria-selected={on} aria-disabled data-model-option={m.key} data-model-unavailable
                     title={m.unavailable} className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-sm rounded-lg text-tone-500 cursor-default">
                  <ProviderMark providerId={m.providerId} label={m.providerLabel} dark={dark} size={16} dim />
                  {/* The name first, whole; the reason under it (a long reason never squeezes the name away). */}
                  <span className="flex-1 min-w-0">
                    <span className="block truncate">{m.name}{m.tag && <span className="text-px11 tabular-nums"> · {m.tag}</span>}</span>
                    {!off && <span className="block text-px11 leading-snug">{m.unavailable}</span>}
                  </span>
                  <span className="w-4 shrink-0">{on && <Check size={15} className="text-tone-400" aria-label="In use" />}</span>
                </div>
              );
            }
            return (
              <div key={m.key} id={`${listId}-${m.key}`} role="option" aria-selected={on} data-model-option={m.key}
                   className={`${optionClass(m.key)} cursor-pointer`} onMouseEnter={() => setActive(m.key)} onClick={() => pick(m)}>
                <ProviderMark providerId={m.providerId} label={m.providerLabel} dark={dark} size={16} />
                <span className="flex-1 min-w-0 truncate">{m.name}</span>
                {m.tag && <span className="shrink-0 text-px11 text-tone-500 tabular-nums">{m.tag}</span>}
                {m.capabilities.video && <span className="shrink-0 text-px10 text-tone-500">video</span>}
                <span className="w-4 shrink-0">{on && <Check size={15} className="text-tone-white" aria-label="In use" />}</span>
              </div>
            );
          })}
        </div>
        );
      })}
      {!firstLoad && !error && !anyModel && query.trim() && <p className="px-3 py-6 text-center text-sm text-tone-500" data-model-picker-empty>No matching models.</p>}
      {showAddProvider() && !query.trim() && (
        <button type="button" onClick={() => setAdding(true)} data-model-picker-add-provider
                className="flex items-center gap-2.5 px-3 py-2 text-sm text-tone-400 hover:text-tone-200 hover:bg-tone-800 rounded-lg text-left">
          <span className="w-4 h-4 shrink-0 inline-flex items-center justify-center"><Plus size={15} /></span> Add a provider
        </button>
      )}
    </div>
  );

  const panel = (
    <div ref={panelRef} role="dialog" aria-modal={!inline} aria-label="Choose a model" onKeyDown={onKeyDown} data-model-picker={inline ? 'inline' : narrow ? 'sheet' : 'popover'}
         className={inline ? 'flex flex-col min-w-0'
           : narrow ? 'fixed inset-x-0 bottom-0 flex flex-col max-h-[85dvh] rounded-t-2xl border-t border-tone-800 bg-popover text-popover-foreground shadow-2xl pb-[env(safe-area-inset-bottom)]'
             : 'fixed flex flex-col w-[360px] max-h-[min(480px,80vh)] rounded-xl border border-tone-800 bg-popover text-popover-foreground shadow-2xl'}
         style={inline ? undefined : { zIndex: Z, ...(pos ?? {}) }}>
      {narrow && !inline && (
        <div className="flex items-center justify-between px-4 pt-3 pb-1">
          <h2 className="text-sm font-medium text-tone-100">Choose a model</h2>
          <button type="button" onClick={close} aria-label="Close" className="p-1 rounded hover:bg-tone-800 text-tone-400"><X size={18} /></button>
        </div>
      )}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-tone-800">
        <Search size={15} className="shrink-0 text-tone-500" aria-hidden />
        <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search models" aria-label="Search models"
               role="combobox" aria-expanded aria-controls={listId} aria-activedescendant={active ? `${listId}-${active}` : undefined} autoComplete="off" spellCheck={false}
               className="flex-1 min-w-0 bg-transparent text-sm text-tone-100 placeholder:text-tone-500 outline-none py-1" />
        {loading && rows.length > 0 && <span className="text-px10 text-tone-500 shrink-0">checking…</span>}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
        {firstLoad && <p className="px-4 py-4 text-xs text-tone-500">Checking providers…</p>}
        {error && <p className="px-4 py-3 text-xs text-tone-red-400" role="alert">Could not load providers: {error}</p>}
        {(!firstLoad || rows.length > 0) && !error && list}
      </div>
    </div>
  );

  const markId = value?.providerId ?? current?.providerId ?? null;
  const chipText = current ? modelTitle(current) : label ?? 'Choose a model';

  return (
    <>
      {!inline && trigger && (
        <button ref={triggerRef} type="button" onClick={() => setOpen(!open)} aria-haspopup="dialog" aria-expanded={open}
                aria-label={`Model: ${chipText}. Change`} title={`Model: ${chipText}`} data-model-picker-trigger
                className={`inline-flex items-center gap-1.5 max-w-[220px] h-8 pl-2 pr-1.5 rounded-full border border-tone-800 bg-tone-900 text-px12 text-tone-200 hover:bg-tone-800 hover:text-tone-white transition-colors ${triggerClassName ?? ''}`}>
          {markId && <ProviderMark providerId={markId} label={current?.providerLabel ?? markId} dark={dark} size={14} />}
          <span className="truncate">{chipText}</span>
          <ChevronDown size={13} className="shrink-0 text-tone-500" aria-hidden />
        </button>
      )}
      {inline ? panel : open && createPortal(
        <>
          {narrow && <div className="fixed inset-0 bg-black/40" style={{ zIndex: Z }} onClick={close} aria-hidden />}
          {panel}
        </>, document.body)}
      {/* The Connect sheet is the top layer, in the page's body: the picker has closed under it (connect), so nothing
          sits over the sheet and its dimmed backdrop covers everything else. Back reopens the picker. */}
      {connectRow && createPortal(
        <ProviderConnectModal
          provider={{ id: connectRow.provider.id, label: connectRow.provider.label, installed: connectRow.provider.installed,
                      authed: connectRow.provider.authed, auth_reason: connectRow.provider.auth_reason ?? connectRow.greyReason }}
          onBack={inline ? () => { setConnectRow(null); void probe(true); } : () => { setConnectRow(null); setOpen(true); }}
          onClose={() => setConnectRow(null)}
          onRecheck={() => probe(true)}
        />, document.body)}
    </>
  );
}
