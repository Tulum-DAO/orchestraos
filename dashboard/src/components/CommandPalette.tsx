/**
 * Cmd-K command palette. NAVIGATE-ONLY.
 *
 * No entry may spawn, kill, inject or message. A palette is a place people type fast without
 * reading, so the one thing it must never contain is a destructive verb.
 *
 * The decisions worth testing live in lib/commandPalette.ts; this file is the surface.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, CornerDownLeft } from 'lucide-react';
import { useAgents } from '../hooks/useAgents';
import {
  shouldOpenPalette, isPaletteChord, filterEntries, pushRecent, pruneRecents,
  RECENTS_KEY, type PaletteEntry,
} from '../lib/commandPalette';
import { navItemsFor } from '../lib/features';

/**
 * Mac or not, decided ONCE at module load.
 *
 * It only selects WHICH modifier is the palette's: Cmd-K everywhere, plus Ctrl-K only where
 * Ctrl-K is not already Cocoa's kill-to-end-of-line. Getting it wrong costs a shortcut, never
 * correctness, so a UA sniff is the right weight of tool here.
 */
const IS_MAC = typeof navigator !== 'undefined'
  && /Mac|iPhone|iPad|iPod/.test(
    (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData?.platform
    || navigator.platform || navigator.userAgent || '',
  );

/** Pages the palette can jump to. Kept here, not imported from the Sidebar, so a nav
 *  reshuffle cannot silently change what the palette offers. */
const PAGES: PaletteEntry[] = [
  { kind: 'page', id: 'command-center', label: 'Command Center', to: '/command-center' },
  { kind: 'page', id: 'overview', label: 'Overview', to: '/overview' },
  { kind: 'page', id: 'inbox', label: 'Inbox', to: '/inbox' },
  { kind: 'page', id: 'agents', label: 'Agents', to: '/agents' },
  { kind: 'page', id: 'tasks', label: 'Tasks', to: '/tasks' },
  { kind: 'page', id: 'activity', label: 'Activity', to: '/activity' },
  { kind: 'page', id: 'approvals', label: 'Approvals', to: '/approvals' },
  { kind: 'page', id: 'workflows', label: 'Workflows', to: '/workflows' },
  { kind: 'page', id: 'analytics', label: 'Analytics', to: '/analytics' },
];

function readRecents(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    const v = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
  } catch { return []; }   // private window, blocked storage, corrupt value
}
function writeRecents(ids: string[]) {
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(ids)); } catch { /* never block navigation on storage */ }
}

/**
 * Is a surface that OWNS THE KEYBOARD currently open?
 *
 * NOT `role="dialog"`. This codebase puts that on the Arturo pill's conversation pane, which
 * explicitly "does NOT dim or block the page" — keying on it made Cmd-K dead for as long as
 * Arturo was open, which I only found by running the Escape test. Nor `aria-modal` alone:
 * two of the three real modals here never set it.
 *
 * What actually makes a surface own the keyboard is a BACKDROP covering the viewport, so
 * that is what this measures. It is a property of the rendered page rather than a label
 * applied inconsistently across four components.
 */
function blockingOverlayOpen(): boolean {
  // EXCLUDE OUR OWN BACKDROP. It is `.fixed.inset-0` too, so without this the palette reports
  // itself as a blocking overlay. The `if (open) return` in the hotkey handler masks that
  // today, but a helper that is wrong whenever the thing it guards is open is a trap for the
  // next caller — found in review, fixed at the source rather than left leaning on a guard
  // somewhere else.
  const els = document.querySelectorAll<HTMLElement>(
    '[aria-modal="true"]:not([data-command-palette]), .fixed.inset-0:not([data-command-palette])',
  );
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width >= window.innerWidth * 0.9 && r.height >= window.innerHeight * 0.9) return true;
  }
  return false;
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const [recents, setRecents] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);
  const navigate = useNavigate();
  const { data } = useAgents();

  // Both response shapes, the same way AgentPage reads them. `undefined` means WE COULD NOT
  // ASK, which pruneRecents treats differently from a known-empty fleet.
  const rows = useMemo<Array<Record<string, unknown>> | undefined>(() => {
    if (Array.isArray(data)) return data as Array<Record<string, unknown>>;
    const w = (data as { agents?: unknown } | undefined)?.agents;
    return Array.isArray(w) ? (w as Array<Record<string, unknown>>) : undefined;
  }, [data]);

  const agentIds = useMemo(() => rows?.map((a) => String(a.id)), [rows]);

  const entries = useMemo<PaletteEntry[]>(() => {
    const agents: PaletteEntry[] = (rows ?? []).map((a) => {
      const id = String(a.id);
      return { kind: 'agent', id, label: String(a.name || id), to: `/agent/${encodeURIComponent(id)}` };
    });
    // Pages the deployment hides (runtime config hiddenViews) are not offered.
    return [...agents, ...navItemsFor(PAGES)];
  }, [rows]);

  // Shown when the query is EMPTY: where you were, newest first.
  const recentEntries = useMemo<PaletteEntry[]>(() => {
    const live = pruneRecents(recents, agentIds);
    return live
      .map((id) => entries.find((e) => e.kind === 'agent' && e.id === id))
      .filter((e): e is PaletteEntry => !!e)
      .map((e) => ({ ...e, kind: 'recent' as const, sublabel: 'recent' }));
  }, [recents, agentIds, entries]);

  const results = useMemo(() => {
    if (!query.trim() && recentEntries.length) {
      const seen = new Set(recentEntries.map((r) => r.id));
      return [...recentEntries, ...filterEntries(entries.filter((e) => !seen.has(e.id)), '')];
    }
    return filterEntries(entries, query);
  }, [entries, recentEntries, query]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery('');
    setCursor(0);
    // Focus RETURNS to whatever opened it, so the keyboard does not land at the top of the page.
    const el = openerRef.current as HTMLElement | null;
    if (el && typeof el.focus === 'function') el.focus();
  }, []);

  // THE HOTKEY. Capture phase, so it is decided before a page handler sees it — but it
  // refuses rather than grabs: see shouldOpenPalette for every refusal and its reason.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const chord = { key: e.key, metaKey: e.metaKey, ctrlKey: e.ctrlKey, isMac: IS_MAC };
      if (open) {
        // The chord that opened it also CLOSES it. Without this the keystroke falls through to
        // the browser while the palette is still up — on Chrome, Ctrl-K focuses the omnibox and
        // the operator is left typing into the address bar over an open palette.
        if (isPaletteChord(chord)) { e.preventDefault(); close(); }
        return;
      }
      const modalOpen = blockingOverlayOpen();
      if (!shouldOpenPalette({
        ...chord, modalOpen,
        target: e.target as unknown as { tagName?: string; isContentEditable?: boolean; closest?: (s: string) => unknown },
      })) return;
      // preventDefault ONLY on the combination we actually handle, so the browser keeps the rest.
      e.preventDefault();
      openerRef.current = document.activeElement;
      setRecents(readRecents());
      setOpen(true);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, close]);

  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  const choose = useCallback((e: PaletteEntry) => {
    if (e.kind !== 'page') {
      const next = pushRecent(readRecents(), e.id);
      writeRecents(next);
      setRecents(next);
    }
    close();
    navigate(e.to);   // the ONLY effect an entry has
  }, [close, navigate]);

  if (!open) return null;

  return (
    <div
      data-command-palette
      className="fixed inset-0 z-[100] flex items-start justify-center bg-black/50 p-4 pt-[12vh]"
      onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        data-command-palette
        aria-label="Go to"
        className="w-full max-w-[560px] overflow-hidden rounded-xl border border-neutral-700 bg-neutral-900 shadow-2xl"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            // STOP IT HERE. The Arturo pill registers its own window Escape handler while it
            // is open, so an un-stopped Escape closes BOTH — the palette the operator meant
            // and the conversation they did not.
            e.preventDefault();
            e.stopPropagation();
            (e.nativeEvent as Event).stopImmediatePropagation?.();
            close();
            return;
          }
          if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, results.length - 1)); }
          if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
          if (e.key === 'Enter' && results[cursor]) { e.preventDefault(); choose(results[cursor]); }
          if (e.key === 'Tab') {
            // Focus trap: there is one control, so Tab stays on it rather than escaping the
            // palette to the page behind.
            e.preventDefault();
            inputRef.current?.focus();
          }
        }}
      >
        <div className="flex items-center gap-2 border-b border-neutral-800 px-3">
          <Search size={15} className="shrink-0 text-neutral-500" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setCursor(0); }}
            placeholder="Go to an agent or a page"
            aria-label="Go to an agent or a page"
            className="w-full bg-transparent py-3 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none"
          />
          <kbd className="shrink-0 rounded border border-neutral-700 px-1.5 py-0.5 text-[10px] text-neutral-500">esc</kbd>
        </div>

        <ul className="max-h-[50vh] overflow-y-auto py-1" role="listbox" aria-label="Results">
          {results.length === 0 && (
            <li className="px-3 py-6 text-center text-[12px] text-neutral-500">Nothing matches “{query}”.</li>
          )}
          {results.map((e, i) => (
            <li key={`${e.kind}:${e.id}`}>
              <button
                type="button"
                role="option"
                aria-selected={i === cursor}
                onMouseEnter={() => setCursor(i)}
                onClick={() => choose(e)}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
                  i === cursor ? 'bg-neutral-800 text-neutral-100' : 'text-neutral-300 hover:bg-neutral-800/60'
                }`}
              >
                <span className="min-w-0 flex-1 truncate">{e.label}</span>
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-neutral-600">
                  {e.sublabel || e.kind}
                </span>
                {i === cursor && <CornerDownLeft size={12} className="shrink-0 text-neutral-500" aria-hidden />}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
