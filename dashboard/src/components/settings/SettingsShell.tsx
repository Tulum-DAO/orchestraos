/**
 * SettingsShell — Settings over the current page (spec §2). Desktop (48rem and up): a centred modal (880 × 640, at most
 * 90vh) over a scrim; the left column has a search box and the categories, the right the selected category. Phone: a
 * full-screen sheet, the category list first, then a category with a back chevron and Done. Esc, X, Done or the scrim
 * close it (back past every Settings history entry); Tab stays inside; focus goes back to what opened it.
 */
import { Fragment, useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { useLocation, useNavigate, type NavigateFunction } from 'react-router-dom';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { closeSettingsTarget, nextSettingsState, sectionLabel, serverGroupLabel, settingsPath, settingsStateOf, visibleSections, type SectionId, type SettingsSection } from '../../lib/settingsRoute';
import { loadServerSettings } from '../../stores/serverSettings';
import { SettingsQuery } from './settingsState';
import { GeneralSection } from './sections/GeneralSection';
import { DisplaySection } from './sections/DisplaySection';
import { VoiceSection } from './sections/VoiceSection';
import { ServerVoiceSection } from './sections/ServerVoiceSection';
import { AgentsSection } from './sections/AgentsSection';
import { NotificationsSection } from './sections/NotificationsSection';
import { ServerSection } from './sections/ServerSection';
import { PermissionsSection } from './sections/PermissionsSection';
import { RulesSection } from './sections/RulesSection';
import { GateSection } from './sections/GateSection';
import '../kit/panel.css';
import './settings.css';

const DESKTOP_QUERY = '(min-width: 48rem)';
const subscribeDesktop = (cb: () => void) => { const mq = window.matchMedia(DESKTOP_QUERY); mq.addEventListener('change', cb); return () => mq.removeEventListener('change', cb); };
const isDesktopNow = () => window.matchMedia(DESKTOP_QUERY).matches;
// What Tab reaches: a radio group's other options (tabindex -1, SettingsKit Choice) are not Tab stops.
const FOCUSABLE = ['a[href]', 'button:not([disabled])', 'input:not([disabled])', 'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]']
  .map((s) => `${s}:not([tabindex="-1"])`).join(', ');

function view(id: SectionId, desktop: boolean): ReactNode {
  switch (id) {
    case 'general': return <GeneralSection />;
    case 'display': return <DisplaySection desktop={desktop} />;
    case 'voice': return <VoiceSection />;
    case 'agents': return <AgentsSection />;
    case 'notifications': return <NotificationsSection />;
    case 'permissions': return <PermissionsSection />;
    case 'rules': return <RulesSection />;
    case 'server-voice': return <ServerVoiceSection />;
    case 'server-gate': return <GateSection />;
    case 'server': return <ServerSection />;
    default: return null;
  }
}

/** The category list with the install's own categories last, under their label (the install's name, or "Server"). */
function CategoryList({ render }: { render: (s: SettingsSection) => ReactNode }) {
  const all = visibleSections();
  return <>
    {all.map((s, i) => (
      <Fragment key={s.id}>
        {s.group && all[i - 1]?.group !== s.group && <div className="settings-nav-group" data-settings-nav-group={s.group}>{serverGroupLabel()}</div>}
        {render(s)}
      </Fragment>
    ))}
  </>;
}

/** A close that goes back to a deep link's own entry, then replaces it: the page to put there once Back has landed. */
let replaceAfterBack: string | null = null;

/** Everything outside Settings (the page, the sidebar, a panel portalled to <body>) goes inert while it is open: no
 *  click, focus or screen-reader visit reaches it. Returns the undo (only what this set). */
function inertOutside(el: HTMLElement): () => void {
  const set: Element[] = [];
  for (let node: HTMLElement | null = el; node && node !== document.body; node = node.parentElement) {
    for (const sib of node.parentElement?.children ?? []) {
      if (sib === node || sib.hasAttribute('inert') || sib.tagName === 'SCRIPT' || sib.tagName === 'STYLE') continue;
      sib.setAttribute('inert', '');
      set.push(sib);
    }
  }
  return () => { for (const s of set) s.removeAttribute('inert'); };
}

function closeWith(navigate: NavigateFunction, state: unknown): void {
  const t = closeSettingsTarget(settingsStateOf(state));
  if ('then' in t) { replaceAfterBack = t.then; navigate(-t.back); }
  else if ('back' in t) navigate(-t.back);
  else navigate(t.to, { replace: true });
}

function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return <input type="search" className="panel-input settings-search" placeholder="Search settings" aria-label="Search settings"
                value={value} onChange={(e) => onChange(e.target.value)} data-settings-search />;
}

function SearchResults({ query, desktop }: { query: string; desktop: boolean }) {
  return (
    <SettingsQuery.Provider value={query}>
      {visibleSections().map((s) => (
        <section key={s.id} className="settings-search-section" data-settings-section={s.id}>
          <h2 className="settings-search-title">{s.group ? `${serverGroupLabel()} › ${s.label}` : s.label}</h2>
          {view(s.id, desktop)}
        </section>
      ))}
      <p className="panel-empty settings-search-none">Nothing matches “{query.trim()}”.</p>
    </SettingsQuery.Provider>
  );
}

export function SettingsShell({ section }: { section: SectionId | null }) {
  const desktop = useSyncExternalStore(subscribeDesktop, isDesktopNow, () => true);
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState('');
  const [opener] = useState<HTMLElement | null>(() => (document.activeElement instanceof HTMLElement ? document.activeElement : null));
  const ref = useRef<HTMLDivElement>(null);
  const current: SectionId = section ?? 'general';
  const close = () => closeWith(navigate, location.state);
  const go = (id: SectionId | null) => navigate(settingsPath(id), { state: nextSettingsState(settingsStateOf(location.state)) });

  useEffect(() => { void loadServerSettings(); }, []);
  // Back has landed on the deep link's own entry (closeWith): replace it with the page behind, closing Settings.
  useEffect(() => {
    if (replaceAfterBack === null || settingsStateOf(location.state).settingsDepth) return;
    const to = replaceAfterBack;
    replaceAfterBack = null;
    navigate(to, { replace: true });
  }, [location.key, location.state, navigate]);
  // The page behind is inert while open; focus in on open; back to whatever opened Settings when it closes (after the
  // page is live again, or the focus would not land).
  useEffect(() => {
    const host = ref.current?.parentElement;
    const undo = host ? inertOutside(host) : () => {};
    ref.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus({ preventScroll: true });
    return () => { undo(); opener?.focus?.({ preventScroll: true }); };
  }, [opener]);
  // Esc closes; Tab cycles inside. Handled on the dialog itself (onKeyDown), and stopped there, so a panel open behind
  // Settings (the agent panel, the Agents drawer: their own window listeners) never sees the key (CommandPalette's
  // pattern). The window listener only covers focus that is somewhere else (it pulls Tab back in).
  const onKey = (e: KeyboardEvent): boolean => {
    if (e.key === 'Escape') { e.preventDefault(); closeWith(navigate, location.state); return true; }
    if (e.key !== 'Tab' || !ref.current) return false;
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (!items.length) return true;
    const first = items[0], last = items[items.length - 1];
    const inside = ref.current.contains(document.activeElement);
    if (e.shiftKey && (document.activeElement === first || !inside)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !inside)) { e.preventDefault(); first.focus(); }
    return true;
  };
  const onDialogKey = (e: ReactKeyboardEvent) => {
    if (onKey(e.nativeEvent)) { e.stopPropagation(); e.nativeEvent.stopImmediatePropagation(); }
  };
  const onKeyRef = useRef(onKey);
  useEffect(() => { onKeyRef.current = onKey; });
  useEffect(() => {
    const outside = (e: KeyboardEvent) => { if (!ref.current?.contains(document.activeElement)) onKeyRef.current(e); };
    window.addEventListener('keydown', outside);
    return () => window.removeEventListener('keydown', outside);
  }, []);

  const searching = query.trim() !== '';

  if (!desktop) {
    const atList = section === null;
    return (
      <div className="settings-host agent-panel-host">
        <div ref={ref} role="dialog" aria-modal="true" aria-label="Settings" className="settings-sheet" data-settings tabIndex={-1} onKeyDown={onDialogKey}>
          <div className="panel-bar">
            <button type="button" className="panel-icon" aria-label={atList ? 'Close' : 'Back'} data-settings-back
                    onClick={() => (atList ? close() : go(null))}>
              <ChevronLeft size={22} />
            </button>
            <h2 className="panel-title">{atList ? 'Settings' : sectionLabel(current)}</h2>
            <button type="button" className="panel-done" onClick={close}>Done</button>
          </div>
          <div className="panel-body">
            {atList ? (
              <>
                <SearchBox value={query} onChange={setQuery} />
                {searching ? <SearchResults query={query} desktop={false} /> : (
                  <div className="panel-list mt-3">
                    <CategoryList render={(s) => (
                      <button type="button" className="panel-row" data-settings-nav={s.id} onClick={() => go(s.id)}>
                        <span>{s.label}</span>
                        <ChevronRight size={18} />
                      </button>
                    )} />
                  </div>
                )}
              </>
            ) : <div data-settings-section={current}>{view(current, false)}</div>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-host agent-panel-host">
      <div className="settings-scrim" onClick={close} aria-hidden />
      <div ref={ref} role="dialog" aria-modal="true" aria-label="Settings" className="settings-modal" data-settings tabIndex={-1} onKeyDown={onDialogKey}>
        <div className="settings-nav">
          <SearchBox value={query} onChange={setQuery} />
          <nav aria-label="Settings categories" className="flex flex-col gap-0.5">
            <CategoryList render={(s) => (
              <button type="button" className="settings-nav-item" data-settings-nav={s.id}
                      aria-current={!searching && s.id === current ? 'page' : undefined}
                      onClick={() => { setQuery(''); if (s.id !== current) go(s.id); }}>
                {s.label}
              </button>
            )} />
          </nav>
        </div>
        <div className="settings-main">
          <div className="panel-bar">
            <span className="panel-icon" aria-hidden />
            <h2 className="panel-title">{searching ? 'Search' : sectionLabel(current)}</h2>
            <button type="button" className="panel-icon" aria-label="Close" onClick={close}><X size={20} /></button>
          </div>
          <div className="panel-body">
            {searching ? <SearchResults query={query} desktop /> : <div data-settings-section={current}>{view(current, true)}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
