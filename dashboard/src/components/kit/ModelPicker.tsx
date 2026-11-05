/**
 * ModelPicker — choose the model an agent (or Arturo, or the defaults for new agents) runs on.
 *
 * The trigger is a pill: the runtime's mark, the model's short name and, when the model has one, its effort
 * ("Opus 5 · Medium"). It opens a panel (ModelPickerPanel.tsx): a rail of EVERY provider on the left (each with a
 * usage ring from GET /api/usage and a status dot; its status, usage and CLI version are the mark's title), the
 * selected provider's name, a search box that searches every provider at once (flat, every concrete model), one row per
 * model FAMILY named by its newest version with a few words of purpose (lib/modelPurpose.ts) and, where a default is
 * being set, the runtime's "default", the older versions under "Older versions" (open by itself when the model in use
 * is one), and one footer line: a Context size control (200K | 1M) when the family in use comes in two or more sizes
 * usable here, and the Effort slider where a level would really be applied (lib/modelEffort.ts). A note under it only
 * for an unusual timing. A family row picks its newest version in the size in use when it has it, else its default /
 * standard size; a Context choice switches size at once and leaves the panel open. On a phone (narrow or a coarse
 * pointer) it is a bottom sheet with the rail as tabs on top.
 *
 * The context rule (lib/modelPickerPanel.ts): an existing agent's picker (`agentModels` given) offers only that
 * agent's runtime; the other providers say why and offer "Start a new agent with …" (the Agents page's New Agent
 * form, opened on that runtime). Settings, a new agent and Arturo's brain may pick any provider.
 *
 * Availability comes from GET /api/runtimes/available, asked again every time it opens. A provider that is not
 * installed or not signed in stays in the rail, dimmed, with its reason; selected, its banner says so with Sign in (the
 * existing Connect sheet) and its models are listed, dimmed. "+" opens the add-a-provider panel where the deployment
 * allows it.
 *
 * Keyboard: the search box keeps focus; ↑/↓ move through the rows and "Older versions" (Enter opens it), ←/→ change the provider (Tab reaches the rail,
 * where the arrows move along it), Enter picks, Esc closes; Tab stays inside the popover.
 *
 * `variant="inline"` draws the same panel in place, always open (a settings page), with no pill.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import './panel.css';
import { ChevronDown, ChevronLeft, Copy, Terminal } from 'lucide-react';
import WebTerminal from '../WebTerminal';
import { ProviderConnectModal } from '../agent/ProviderConnectModal';
import { brandUrl, providerMark } from '../avatar/brandMarks';
import { installCommand, installNote, cliFor, signinCommand } from '../../lib/providerConnect';
import { providerTileAction, showAddProvider, showNewAgent } from '../../lib/features';
import { buildProviderRows, type ProviderAvailability, type ProviderCapabilities, type ProviderModel, type ProviderRow } from '../../lib/modelSelectorFilter';
import { buildPickerModels, findCurrent, isCurrent, modelTitle, letterMark, type PickerModel, type PickerValue } from '../../lib/modelPicker';
import {
  browseRows, canPick, contextControl, contextModel, initialTab, navKey, pickableKeys, pillName, pillText, railEntries, railKey,
  scopeFor, visibleRows,
} from '../../lib/modelPickerPanel';
import { effortLabel, effortLevels as runtimeEffortLevels, type EffortLevel } from '../../lib/modelEffort';
import { fetchUsage, USAGE_QUERY_KEY, UsageHidden } from '../../lib/usageLine';
import { newAgentHref } from '../../lib/newAgent';
import ModelPickerPanel from './ModelPickerPanel';

export interface ModelPick { providerId: string; modelId: string; modelLabel: string; capabilities: ProviderCapabilities }

export interface ModelPickerProps {
  /** The model in use. A short id ("opus", "fable") matches the model it stands for. */
  value: PickerValue | null;
  onValueChange: (pick: ModelPick) => void;
  /** On an agent's page: the models that agent can switch to (the gateway's list). Its presence makes this an
   *  existing agent's picker: only its runtime can be picked. */
  agentModels?: { providerId: string; models: (ProviderModel & { subtitle?: string })[] } | null;
  /** Offer "Use the default brain" first (Arturo). */
  onPickDefault?: () => void;
  /** Whose model this chooses: an agent's (every model it may use, 1M versions included) or Arturo's brain (models
   *  only agents can use are listed dimmed). Default: an agent's. */
  chooser?: 'agent' | 'arturo';
  /** 'popover' (a pill that opens it) or 'inline' (always open, in place). */
  variant?: 'popover' | 'inline';
  /** Controlled open state (a menu item can open it); uncontrolled when left out. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** False: no pill (opened only through `open`). */
  trigger?: boolean;
  /** The pill's words while the current model is not in the list yet. */
  label?: string;
  triggerClassName?: string;
  /** The effort in use (a level id) and how to change it. The slider shows only where lib/modelEffort.ts says a level
   *  is really applied (Claude, Codex). null from onEffortChange: "CLI default", offered only with effortClearable. */
  effort?: string | null;
  onEffortChange?: (level: string | null) => void;
  /** The levels this agent can use (lib/agentModel.ts agentEffortLevels), when narrower than its runtime's. */
  effortLevels?: EffortLevel[] | null;
  /** Offer a "CLI default" toggle beside the slider (a default effort can be taken out again: Settings › Agents). */
  effortClearable?: boolean;
  /** Only an unusual timing, under the controls ("Applies from this agent's next rotation"). */
  note?: string | null;
  /** The provider tab chosen (Settings › Agents marks each runtime's own stored default on its tab). */
  onTabChange?: (providerId: string) => void;
  /** Before "Start a new agent with …" leaves the page (the Arturo pane closes itself first). */
  onBeforeNewAgent?: () => void;
  /** Where focus goes on close when the pill is not shown (below 480px the composer's overflow menu button). */
  returnFocus?: () => HTMLElement | null;
}

interface RuntimesAvailableResponse { providers: ProviderAvailability[] }

const DEFAULT_KEY = '__default__';
// Above the Arturo pane (z 76), as the old sheet was; the connect modal sits above this (z 85).
const Z = 80;
const POPOVER_W = 440;

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

/** A phone: a narrow window or a coarse pointer (touch). The picker is a bottom sheet there. */
function usePhone(): boolean {
  const q = '(max-width: 639px), (pointer: coarse)';
  const [phone, setPhone] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setPhone(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return phone;
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

/** Add a provider: each one not usable yet (not installed, not signed in) with Sign in for one that is installed (the
 *  Connect sheet), the install command for one that is not, and a terminal to run it in. */
function AddProviderPanel({ rows, onInstalled, onSignIn, canInstall }: {
  rows: ProviderRow[]; onInstalled: () => void; onSignIn: ((row: ProviderRow) => void) | null; canInstall: boolean;
}) {
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
      <p className="text-tone-300">A provider can be picked once its CLI is installed on this machine and signed in.</p>
      {missing.length > 0 && (
        <ul className="flex flex-col gap-2">
          {missing.map((r) => {
            const cmd = installCommand(r.provider.id);
            return (
              <li key={r.provider.id} className="flex flex-col gap-1">
                <span className="text-tone-300">{r.provider.label} <span className="text-tone-500">— {r.greyReason}</span></span>
                {r.provider.installed ? (onSignIn ? (
                  <button type="button" onClick={() => onSignIn(r)} data-model-add-signin={r.provider.id}
                          className="self-start min-h-[36px] px-3 rounded-lg border border-tone-700 text-tone-100 hover:bg-tone-800">
                    Sign in to {r.provider.label}
                  </button>
                ) : (
                  // No sign-in flow here (the deployment has it off): the CLI's own way, on this machine.
                  <span className="text-tone-500" data-model-add-signin-note={r.provider.id}>
                    Sign in from the CLI on this machine: <code className="px-1 rounded bg-tone-800 text-tone-200">{signinCommand(r.provider.id).replace(/\s+/g, ' ')}</code>
                  </span>
                )) : cmd ? (
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
      {canInstall && !session && (
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
      {missing.some((r) => !r.provider.installed) && <p className="text-tone-500">Install it, run the CLI once and sign in, then check again: the list asks again every time it opens.</p>}
    </div>
  );
}

export default function ModelPicker({
  value, onValueChange, agentModels, onPickDefault, chooser = 'agent', variant = 'popover', open: openProp, onOpenChange, trigger = true, label,
  triggerClassName, effort = null, onEffortChange, effortLevels: agentLevels = null, effortClearable = false, note = null, onTabChange,
  onBeforeNewAgent, returnFocus,
}: ModelPickerProps) {
  const inline = variant === 'inline';
  const [openState, setOpenState] = useState(false);
  const open = inline || (openProp ?? openState);
  const setOpen = useCallback((o: boolean) => { if (openProp === undefined) setOpenState(o); onOpenChange?.(o); }, [openProp, onOpenChange]);
  const dark = useDarkMode();
  const phone = usePhone();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>(null);
  /** "Older versions" opened or closed by hand, per provider; untouched, it follows the model in use. */
  const [olderOpen, setOlderOpen] = useState<Record<string, boolean>>({});
  const [adding, setAdding] = useState(false);
  const [connectRow, setConnectRow] = useState<ProviderRow | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const idBase = useId().replace(/:/g, '');
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

  // The usage rings: the sidebar's own reading (one query key), only while the picker is open. A caller the API
  // hides usage from (404) gets no rings, never a guess.
  const { data: usage } = useQuery({
    queryKey: USAGE_QUERY_KEY, queryFn: fetchUsage, enabled: open, staleTime: 55_000,
    retry: (n, e) => !(e instanceof UsageHidden) && n < 2,
  });

  // Asked again every time it opens: no stale list across a sign-in. Opens on the provider in use.
  useEffect(() => {
    if (!open) return;
    setQuery(''); setAdding(false); setTab(null); setOlderOpen({});
    void probe();
  }, [open, probe]);

  const scope = useMemo(() => scopeFor(agentModels), [agentModels]);
  const models = useMemo(() => buildPickerModels(rows, agentModels, { forArturo: chooser === 'arturo' }), [rows, agentModels, chooser]);
  // The rail: every provider, signed in or not, installed or not (an unusable one dimmed, with its reason and Sign in).
  const entries = useMemo(() => railEntries(rows, usage, scope), [rows, usage, scope]);
  const current = findCurrent(models, value);
  const tabs = useMemo(() => entries.map((e) => e.id), [entries]);
  const shownTab = tab && tabs.includes(tab) ? tab : initialTab(entries, scope, value?.providerId);
  const searching = !!query.trim();
  const shown = useMemo(() => visibleRows(models, query, shownTab), [models, query, shownTab]);
  const defaultId = entries.find((e) => e.id === shownTab)?.defaultModel ?? null;
  const browse = useMemo(() => browseRows(models, shownTab, {
    value, scope, defaultId, olderOpen: shownTab ? olderOpen[shownTab] ?? null : null,
  }), [models, shownTab, value, scope, defaultId, olderOpen]);
  const defaultRow = !!onPickDefault && !searching;
  const keys = useMemo(() => [...(defaultRow ? [DEFAULT_KEY] : []),
    ...(searching ? shown.filter((m) => canPick(m, scope)).map((m) => m.key) : pickableKeys(browse.rows, scope))],
  [shown, browse, scope, defaultRow, searching]);

  // The row the arrows start from: the model in use, else the first that can be picked.
  useEffect(() => {
    if (!open) return;
    setActive((a) => (a && keys.includes(a) ? a : (current && keys.includes(current.key) ? current.key : keys[0] ?? null)));
  }, [open, keys, current]);

  useEffect(() => {
    if (!open || inline) return;
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open, inline]);

  // Desktop: under the pill (above it when there is no room below); with no pill, near the top, centred.
  useLayoutEffect(() => {
    if (!open || inline || phone) { setPos(null); return; }
    const r = triggerRef.current?.getBoundingClientRect();
    const w = Math.min(POPOVER_W, window.innerWidth - 16);
    if (!r) { setPos({ left: Math.max(8, (window.innerWidth - w) / 2), top: Math.round(window.innerHeight * 0.12) }); return; }
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left));
    const below = window.innerHeight - r.bottom;
    setPos(below >= 460 || below >= r.top ? { left, top: r.bottom + 6 } : { left, bottom: window.innerHeight - r.top + 6 });
  }, [open, inline, phone]);

  const close = useCallback(() => {
    if (inline) return;
    setOpen(false);
    // Back to the pill, or, when the pill is hidden (display: none below 480px) or absent, to where it was opened from.
    setTimeout(() => {
      const t = triggerRef.current;
      if (t && t.getClientRects().length > 0) t.focus();
      else returnFocus?.()?.focus();
    }, 0);
  }, [inline, setOpen, returnFocus]);

  /** Sign in: the picker closes first, then the Connect sheet opens as the top layer (never under the picker). */
  const connect = useCallback((row: ProviderRow) => {
    if (!inline) setOpen(false);
    setConnectRow(row);
  }, [inline, setOpen]);

  // A click outside the popover closes it (the phone sheet has its own backdrop).
  useEffect(() => {
    if (!open || inline || phone || connectRow) return;
    const down = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  }, [open, inline, phone, connectRow, setOpen]);

  // The add-a-provider view replaces the list: keep focus inside, where Esc and Tab work.
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open || inline) return;
    (adding ? backRef.current : inputRef.current)?.focus();
  }, [adding, open, inline]);

  useEffect(() => {
    if (!active) return;
    document.getElementById(`${idBase}-opt-${active}`)?.scrollIntoView({ block: 'nearest' });
  }, [active, idBase]);

  const pick = (m: PickerModel) => {
    if (!canPick(m, scope)) return;
    onValueChange({ providerId: m.providerId, modelId: m.id, modelLabel: modelTitle(m), capabilities: m.capabilities });
    close();
  };
  const toggleOlder = (providerId: string) => setOlderOpen((o) => ({
    ...o, [providerId]: !(o[providerId] ?? (providerId === shownTab ? browse.olderOpen : false)),
  }));
  const pickKey = (k: string) => {
    if (k === DEFAULT_KEY) { onPickDefault?.(); close(); return; }
    if (k.startsWith('older:')) { toggleOlder(k.slice('older:'.length)); return; }
    const m = searching ? shown.find((x) => x.key === k) : browse.rows.flatMap((r) => (r.kind === 'older' || r.key !== k ? [] : [r.model]))[0];
    if (m) pick(m);
  };
  // Context: switch the model in use to its family's newest version in that size, at once; the panel stays open
  // (Settings, a new agent, Arturo), or closes in an agent's picker.
  const ctl = !searching && !adding ? contextControl(browse, value, scope) : null;
  const pickSize = (id: string) => {
    const m = contextModel(browse, id);
    if (m && canPick(m, scope) && !isCurrent(m, value)) {
      onValueChange({ providerId: m.providerId, modelId: m.id, modelLabel: modelTitle(m), capabilities: m.capabilities });
      // An agent's model reads back only after its switch: close rather than show the old size as in use.
      if (scope.kind === 'agent') close();
    }
  };
  const chooseTab = (id: string) => { setTab(id); setAdding(false); setQuery(''); onTabChange?.(id); };

  const focusTrap = (e: KeyboardEvent<HTMLElement>) => {
    const f = [...(panelRef.current?.querySelectorAll<HTMLElement>('input, button:not([disabled]):not([tabindex="-1"]), [href], [tabindex]:not([tabindex="-1"])') ?? [])];
    if (!f.length) return;
    const i = f.indexOf(document.activeElement as HTMLElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Tab') { if (!inline) focusTrap(e); return; }
    if (e.key === 'Escape' && inline) return;
    const el = e.target as HTMLElement;
    // Only from the search box: a focused button keeps its own Enter and arrows, the Effort slider (an INPUT too) its
    // ←/→ and Home/End.
    if (e.key !== 'Escape' && el !== inputRef.current) return;
    const r = navKey(e.key, { tabs, tab: shownTab, keys, active, query });
    if (!r) return;
    e.preventDefault();
    if (r.kind === 'active') setActive(r.active);
    else if (r.kind === 'tab') chooseTab(r.tab);
    else if (r.kind === 'pick') pickKey(r.key);
    else { e.stopPropagation(); close(); }
  };

  const onRailKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const next = railKey(e.key, tabs, shownTab);
    if (!next) return;
    e.preventDefault(); e.stopPropagation();
    chooseTab(next);
    setTimeout(() => document.getElementById(`${idBase}-tab-${next}`)?.focus(), 0);
  };

  // Effort: the model in use, on the provider shown, where a level is really applied (lib/modelEffort.ts).
  const where = scope.kind === 'agent' ? 'agent' : 'defaults';
  const levels = onEffortChange && current && current.providerId === shownTab
    ? (agentLevels ?? runtimeEffortLevels(current.providerId, where)) : null;
  const effortWord = runtimeEffortLevels(value?.providerId, where) ? effortLabel(value?.providerId, effort) : null;

  const layout = inline ? 'inline' : phone ? 'sheet' : 'popover';
  const panel = (
    <ModelPickerPanel
      layout={layout} idBase={idBase} rows={rows} entries={entries} shown={adding ? [] : shown} scope={scope} value={value}
      browse={adding ? null : browse} onToggleOlder={toggleOlder}
      context={ctl ? { ...ctl, onChange: pickSize } : null}
      tab={shownTab} onTab={chooseTab} query={query} onQuery={setQuery} active={active} onActive={setActive} onPick={pick}
      renderMark={(id, l, size, dim) => <ProviderMark providerId={id} label={l} dark={dark} size={size} dim={dim} />}
      defaultRow={defaultRow ? { key: DEFAULT_KEY, selected: !value, onPick: () => pickKey(DEFAULT_KEY) } : null}
      effort={levels ? { levels, value: effort, clearable: effortClearable, onChange: (id) => onEffortChange?.(id) } : null}
      note={note}
      onSignIn={providerTileAction(false) === 'connect' ? (id) => { const r = rows.find((x) => x.provider.id === id); if (r) connect(r); } : null}
      onNewAgent={scope.kind === 'agent' && showNewAgent() ? (id) => { if (!inline) setOpen(false); onBeforeNewAgent?.(); navigate(newAgentHref(id)); } : null}
      onAddProvider={showAddProvider() ? () => setAdding(true) : null}
      replaceBody={adding ? (
        <div>
          <button ref={backRef} type="button" onClick={() => setAdding(false)} className="flex items-center gap-1 px-3 py-2 text-xs text-tone-400 hover:text-tone-200">
            <ChevronLeft size={14} /> Models
          </button>
          <AddProviderPanel rows={rows} onInstalled={() => { void probe(true); }}
            onSignIn={providerTileAction(false) === 'connect' ? connect : null} canInstall={showAddProvider()} />
        </div>
      ) : undefined}
      loading={loading} error={error}
      onKeyDown={onKeyDown} onRailKeyDown={onRailKeyDown} onClose={close}
      inputRef={inputRef} panelRef={panelRef}
      style={inline ? undefined : { zIndex: Z, ...(pos ?? {}) }}
    />
  );

  const markId = value?.providerId ?? current?.providerId ?? null;
  const fullTitle = current ? modelTitle(current) : label ?? 'Choose a model';
  const pill = pillText(pillName(current, models) ?? label ?? null, effortWord);

  return (
    <>
      {!inline && trigger && (
        <button ref={triggerRef} type="button" onClick={() => setOpen(!open)} aria-haspopup="dialog" aria-expanded={open}
                aria-label={`Model: ${fullTitle}${effortWord ? `, ${effortWord} effort` : ''}. Change`} title={`Model: ${fullTitle}`} data-model-picker-trigger
                className={`inline-flex items-center gap-1.5 max-w-[240px] h-8 pl-2 pr-1.5 rounded-full border border-tone-800 bg-tone-900 text-px12 text-tone-200 hover:bg-tone-800 hover:text-tone-white transition-colors ${triggerClassName ?? ''}`}>
          {markId && <ProviderMark providerId={markId} label={current?.providerLabel ?? markId} dark={dark} size={14} />}
          <span className="truncate">{pill}</span>
          <ChevronDown size={13} className={`shrink-0 text-tone-500 transition-transform${open ? ' rotate-180' : ''}`} aria-hidden />
        </button>
      )}
      {inline ? panel : open && createPortal(
        <>
          {phone && <div className="fixed inset-0 bg-black/40" style={{ zIndex: Z }} onClick={close} aria-hidden />}
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
