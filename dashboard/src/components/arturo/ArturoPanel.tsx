/**
 * ArturoPanel — an agent's slide-out from the top bar's gear: a short menu about that agent. Arturo's page and
 * every agent page use this one panel; Arturo adds his own rows (status, brain, memory, his jobs), every other agent
 * shows its model and its subagents. Each row shows its
 * current value (the voice, how many facts, what is running), so often there is no need to tap; a tap
 * opens the row's page with a back arrow, the way an iPhone settings screen works. Settings holds a
 * second level for set-once things. Closing always starts the next opening at the top.
 *
 * Desktop: a full-height drawer from the right over the whole page, top bar and sidebar included, with the
 * scrim dimming everything behind it. Phone: a full-screen sheet. Either way it overlays the page, so
 * nothing behind it moves, and while it is open Tab stays inside it, Esc (or the scrim) closes it, and
 * focus goes back to the gear.
 *
 * Focus moves in only once the slide has finished, and without scrolling: focusing a control that is
 * still off to the right makes the browser scroll the clipped page sideways to reveal it, which reads as
 * the whole page jerking left and back.
 *
 * Only what the dashboard can already read is shown: /api/arturo/health (status, brain), /api/arturo/ptt/*
 * (the live voice: providers set up, the voices, the current pick), /api/facts
 * (Memory), a thread's ?jobs=1 (Activity), the assistant settings store (On this device). Rows with
 * nothing behind them yet are shown disabled, marked "Coming soon".
 */
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from 'react';
import { Check, ChevronLeft, ChevronRight, Pencil, X } from 'lucide-react';
import { arturoHealth, ARTURO_DISABLED_TEXT, handsFreeReady, isArturoDisabled, type ArturoHealth } from '../../lib/arturo';
import { panelStatus, pathTo, factsValue, activityValue, subagentsValue, DRAWER_SECTIONS, liveStatus, openingSection, contextUsedLabel, SECTION_LABEL,
         type PanelPage, type DrawerSection } from '../../lib/arturoPanel';
import { fetchOpenAiVendor, fetchVoices, fetchCurrentVoice, putVoice, clearVoice, pickVoice, voiceRowValue, canChangeVoice, ADMIN_ONLY_NOTE, GPT_LIVE,
         type VoiceVendor, type VoiceRow, type CurrentVoice } from '../../lib/arturoVoice';
import { styleOptions, styleLabel, chooseStyle, loadPrefs, useChatStyle, useAgentPrefsStore } from '../../lib/agentPrefs';
import { HeadlessDevNote } from './HeadlessDevNote';
import { fetchAgentModel, switchAgentModel, switchableModels, switchNotice, modelLabel, pickRefusal,
         type AgentModelInfo, type SwitchNotice } from '../../lib/agentModel';
import { renameAgent } from '../../lib/railAgents';
import './arturo.css';
import { fetchJobs, elapsed, type ArturoJob } from '../../lib/arturoJobs';
import { currentThread } from '../../lib/arturoCurrent';
import { DrawerHistory } from './DrawerHistory';
import { factsApi, type Fact } from '../../lib/factsApi';
import { useAgentSettings } from '../../stores/agentSettings';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { LookPicker } from '../avatar/LookPicker';
import { DrawerLibrary } from './DrawerLibrary';
import { useAgentRecord, ARTURO_ID } from '../avatar/agentState';
import { PROVIDER_LABEL } from '../../lib/agentRail';
import { providerMark } from '../avatar/brandMarks';
import { canChangeLook, shapeName, colorName } from '../../lib/avatarLooks';
import { useAssistantName, named, saveAssistantName, resetAssistantName, canRename, DEFAULT_ASSISTANT_NAME, NAME_MAX } from '../../lib/assistantName';

const TITLES: Record<PanelPage, string> = {
  voice: 'Voice', memory: 'Memory', activity: 'Activity', settings: 'Settings', brain: 'Brain', device: 'On this device',
  avatar: 'Avatar', name: 'Name', edit: 'Edit', style: 'Chat style', model: 'Model',
  library: 'Library', history: 'History', setup: 'Setup',
};

/** The agent the panel is about, when it is not Arturo (Arturo's comes from his own stores). */
export interface PanelAgent {
  id: string;
  /** Its short name (seat name, else its id). */
  name: string;
  /** The runtime that runs it ("claude"), for the badge under its face. */
  provider?: string;
  /** Its subagents running now, for Activity. */
  subagents?: number;
  /** How much of its context window is left (the status chip's reading), for the header's meter. */
  contextLeftPct?: number | null;
  /** Something of its is waiting on the operator: the drawer opens on History. */
  waiting?: boolean;
}

const LAST_SECTION_KEY = 'orchestra.drawer.section';
const readLast = () => { try { return localStorage.getItem(LAST_SECTION_KEY); } catch { return null; } };

type SaveName = (name: string) => Promise<{ ok: true; name: string } | { ok: false; error: string }>;
const SLIDE_MS = 200;

// Tailwind's `md` breakpoint: a drawer beside the page from here up, a full-screen sheet below it.
const DESKTOP_QUERY = '(min-width: 48rem)';
const subscribeDesktop = (cb: () => void) => {
  const mq = window.matchMedia(DESKTOP_QUERY);
  mq.addEventListener('change', cb);
  return () => mq.removeEventListener('change', cb);
};
const isDesktopNow = () => window.matchMedia(DESKTOP_QUERY).matches;

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const errText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

/** The current conversation's background jobs (none when no conversation is open). */
async function loadJobs(): Promise<ArturoJob[]> {
  const id = currentThread();
  return id ? (await fetchJobs(id)) ?? [] : [];
}

export function ArturoPanel({ open, onClose, returnFocusTo, brain = '', agent }: {
  open: boolean;
  onClose: () => void;
  /** The gear: focus goes back to it when the panel closes. */
  returnFocusTo: RefObject<HTMLElement | null>;
  /** The composer chip's brain text ("Claude CLI"), so the status line names the same brain (Arturo only). */
  brain?: string;
  /** Any agent but Arturo; absent, the panel is Arturo's. */
  agent?: PanelAgent;
}) {
  const desktop = useSyncExternalStore(subscribeDesktop, isDesktopNow, () => true);
  const agentId = agent?.id ?? ARTURO_ID;
  const isArturo = agentId === ARTURO_ID;
  const arturoRec = useAgentRecord(agentId);
  const assistantName = useAssistantName();
  const aname = isArturo ? assistantName : agent!.name;
  const saveName: SaveName = isArturo ? saveAssistantName : (n) => renameAgent(agentId, n);
  const style = useChatStyle(agentId);
  const canSaveLook = canChangeLook();
  // Mounted while open or sliding out; `shown` drives the slide, a frame after mount so it animates in.
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  // The pages opened from the root, deepest last; empty is the root.
  const [stack, setStack] = useState<PanelPage[]>([]);
  // The phone's Edit page: its name draft lives here, so a trip to the look picker and back keeps it.
  const [editDraft, setEditDraft] = useState('');
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const canEditName = canRename();
  const [health, setHealth] = useState<ArturoHealth | null>(null);
  const [factCount, setFactCount] = useState<number | null>(null);
  const [jobs, setJobs] = useState<ArturoJob[] | null>(null);
  const [vendor, setVendor] = useState<VoiceVendor | null>(null);
  const [voice, setVoice] = useState<CurrentVoice | null>(null);
  const [model, setModel] = useState<AgentModelInfo | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(open);
  // The page just left by Back, so focus lands on the row that opened it.
  const leftPage = useRef<PanelPage | null>(null);
  const page = stack.length ? stack[stack.length - 1] : null;
  // Desktop: the section shown under the name (tabs); a phone opens each as its own page instead.
  // The section picked since it opened; until then, where it opens (History when something waits or runs, else the
  // last one used). Closing clears the pick.
  const [picked, setPicked] = useState<DrawerSection | null>(null);
  const tab: DrawerSection = picked ?? openingSection(arturoRec.status, !!agent?.waiting, readLast());
  const setTab = (s: DrawerSection) => { setPicked(s); try { localStorage.setItem(LAST_SECTION_KEY, s); } catch { /* private mode */ } };
  // The live status line counts minutes: re-read the clock while open.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    const iv = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(iv);
  }, [open]);

  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    let raf = 0;
    if (open) {
      t = setTimeout(() => { setMounted(true); raf = requestAnimationFrame(() => setShown(true)); }, 0);
    } else {
      t = setTimeout(() => setShown(false), 0);
      const done = setTimeout(() => { setMounted(false); setStack([]); setPicked(null); }, SLIDE_MS);
      return () => { clearTimeout(t); clearTimeout(done); };
    }
    return () => { clearTimeout(t); cancelAnimationFrame(raf); };
  }, [open]);

  // Fresh values each time it opens, and again on the way back to the root (a fact added, a job ended):
  // the rows say what is true now, not at page load.
  const atRoot = stack.length === 0;
  useEffect(() => {
    if (!open || !atRoot) return;
    let alive = true;
    // Arturo's voice is the install's; any other agent's is its own, else the install's.
    const voiceAgent = isArturo ? undefined : agentId;
    if (isArturo) {
      void arturoHealth().then((h) => { if (alive) setHealth(h); });
      factsApi.getFacts().then((d) => { if (alive) setFactCount((d.facts || []).length); }).catch(() => {});
      void loadJobs().then((j) => { if (alive) setJobs(j); });
    } else {
      void fetchAgentModel(agentId).then((m) => { if (alive) setModel(m); });
    }
    void fetchOpenAiVendor().then((v) => { if (alive) setVendor(v); });
    void fetchCurrentVoice(voiceAgent).then((v) => { if (alive) setVoice(v); });
    void loadPrefs(agentId);
    return () => { alive = false; };
  }, [open, atRoot, isArturo, agentId]);

  // Focus in once the slide has landed; back to the gear on close.
  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    if (open && shown) {
      t = setTimeout(() => panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus({ preventScroll: true }), SLIDE_MS);
    }
    if (!open && wasOpen.current) returnFocusTo.current?.focus({ preventScroll: true });
    wasOpen.current = open;
    return () => clearTimeout(t);
  }, [open, shown, returnFocusTo]);

  // Opening a page focuses its back arrow; going back focuses the row it came from.
  const firstPageRender = useRef(true);
  useEffect(() => {
    if (firstPageRender.current) { firstPageRender.current = false; return; }
    const root = panelRef.current;
    if (!root) return;
    const back = leftPage.current;
    leftPage.current = null;
    const target = back
      ? root.querySelector<HTMLElement>(`[data-panel-row="${back}"]`)
      : root.querySelector<HTMLElement>('[data-panel-back]');
    target?.focus({ preventScroll: true });
  }, [page]);

  // Esc closes; Tab cycles inside the panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
      if (e.key !== 'Tab' || !panelRef.current) return;
      const items = [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0], last = items[items.length - 1];
      const inside = panelRef.current.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !inside)) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!mounted) return null;
  const status = panelStatus(health, brain, aname);
  const push = (p: PanelPage) => setStack((s) => [...s, p]);
  const back = () => { leftPage.current = page; setStack((s) => s.slice(0, -1)); };
  const title = page ? TITLES[page] : aname;
  const openEdit = () => { setEditDraft(aname); setEditError(null); push('edit'); };
  // Done on the Edit page saves the name (when it changed) and goes back; the back arrow leaves it unsaved.
  const editDone = async () => {
    const next = editDraft.trim();
    if (!canEditName || !next || next === aname) { back(); return; }
    setEditBusy(true); setEditError(null);
    const r = await saveName(next);
    setEditBusy(false);
    if (r.ok) back(); else setEditError(r.error);
  };

  // Desktop closes with an X at the right; the phone sheet with Done, and its chevron closes at the root.
  const bar = (
    <div className="arturo-panel-bar">
      {page ? (
        <button type="button" data-panel-back className="arturo-panel-icon" aria-label="Back" onClick={back}>
          <ChevronLeft size={22} />
        </button>
      ) : !desktop ? (
        <button type="button" className="arturo-panel-icon" aria-label="Close" onClick={onClose}>
          <ChevronLeft size={22} />
        </button>
      ) : <span className="arturo-panel-icon" aria-hidden />}
      <h2 className="arturo-panel-title">{title}</h2>
      {page === 'edit' ? (
        <button type="button" className="arturo-panel-done" onClick={() => void editDone()} disabled={editBusy}>Done</button>
      ) : desktop ? (
        <button type="button" className="arturo-panel-icon" aria-label="Close" onClick={onClose}>
          <X size={20} />
        </button>
      ) : (
        <button type="button" className="arturo-panel-done" onClick={onClose}>Done</button>
      )}
    </div>
  );

  const sections = DRAWER_SECTIONS;
  const activityText = isArturo ? activityValue(jobs) : subagentsValue(agent?.subagents);
  // A phone's drill-in rows carry a live value each, so often there is no need to open them.
  const sectionValue = (s: DrawerSection): string => s === 'history' ? (agent?.waiting ? 'Waiting for you' : activityText)
    : s === 'setup' ? (isArturo ? styleLabel(style) : modelLabel(model, model?.model) ?? '') : '';
  // Each section shows what exists today until its own step fills it, never an empty page. Dev is a chat style,
  // chosen in Setup; there is no terminal row.
  const sectionBody = (s: DrawerSection): ReactNode => s === 'history'
    ? <DrawerHistory agentId={agentId} isArturo={isArturo} name={aname} subagents={agent?.subagents} onClose={onClose} />
    : s === 'library' ? <DrawerLibrary agentId={agentId} name={aname} />
    : (
      <div className="arturo-panel-list" data-drawer-setup>
        {isArturo
          ? <Row id="memory" label="Memory" value={factsValue(factCount)} onOpen={push} />
          : <Row id="model" label="Model" value={modelLabel(model, model?.model) ?? ''} onOpen={push} />}
        <Row id="style" label="Chat style" value={styleLabel(style)} onOpen={push} />
        <Row label="Notifications" soon />
        <Row id="settings" label="Settings" onOpen={push} />
      </div>
    );

  const content = (
    <>
      {bar}
      <div className="arturo-panel-body" key={page ?? 'root'}>
        {page === 'voice' ? <VoicePage health={health} onPicked={setVoice} agentId={isArturo ? undefined : agentId} />
          : page === 'memory' ? <MemoryPage />
          : page === 'activity' ? (isArturo ? <ActivityPage /> : <SubagentsPage running={agent?.subagents} name={aname} />)
          : page === 'style' ? <StylePage agentId={agentId} />
          : page === 'model' ? <ModelPage agentId={agentId} onChanged={setModel} />
          : page === 'settings' ? (
            <div className="arturo-panel-list">
              <Row id="name" label="Name" value={aname} onOpen={push} />
              {isArturo && <Row id="brain" label="Brain" value={brain} onOpen={push} />}
              {isArturo && <Row label="Keys" soon />}
              <Row label="Notifications" soon />
              <Row id="device" label="On this device" onOpen={push} />
              {isArturo && <Row label="About" soon />}
            </div>
          )
          : page === 'brain' ? <BrainPage health={health} brain={brain} />
          : page === 'device' ? <DevicePage />
          : page === 'name' ? (isArturo ? <NamePage /> : <AgentNamePage agentId={agentId} name={aname} save={saveName} />)
          : page === 'edit' ? (
            <EditPage agentId={agentId} name={aname} draft={editDraft} onDraft={setEditDraft} error={editError} canName={canEditName}
                      onDone={() => void editDone()} onAvatar={() => push('avatar')}
                      voice={voiceRowValue(vendor, voice, health ? handsFreeReady(health) : null) + (!isArturo && voice?.scope === 'install' ? ' (default)' : '')}
                      onVoice={() => push('voice')} />
          )
          : page === 'avatar' ? <LookPicker agentId={agentId} heroSize={160} canSave={canSaveLook} />
          : page === 'library' || page === 'history' || page === 'setup' ? sectionBody(page)
          : (
            <>
              <DrawerFace agentId={agentId} name={aname} save={saveName} canSave={canSaveLook} canName={canEditName} desktop={desktop}
                          subtitle={liveStatus(arturoRec.status, arturoRec.since, now)}
                          meter={isArturo ? null : contextUsedLabel(agent?.contextLeftPct)}
                          meterPct={isArturo ? null : agent?.contextLeftPct ?? null}
                          onOpen={() => push('avatar')} onEdit={openEdit} />
              {isArturo && status.fix && (
                <button type="button" className="arturo-panel-status is-fix" role="status"
                        onClick={() => setStack(pathTo(status.fix!))}>
                  <span>{status.text}</span>
                  <ChevronRight size={14} />
                </button>
              )}
              {desktop ? (
                <>
                  <div className="arturo-panel-pills" role="tablist" aria-label={`${aname}'s drawer`}>
                    {sections.map((s) => (
                      <button key={s} type="button" role="tab" aria-selected={tab === s} data-drawer-tab={s}
                              className={`arturo-panel-pill${tab === s ? ' is-on' : ''}`} onClick={() => setTab(s)}>
                        {SECTION_LABEL[s]}
                      </button>
                    ))}
                  </div>
                  <div role="tabpanel" data-drawer-section={tab}>{sectionBody(tab)}</div>
                </>
              ) : (
                <div className="arturo-panel-list" data-drawer-rows>
                  {sections.map((s) => <Row key={s} id={s} label={SECTION_LABEL[s]} value={sectionValue(s)} onOpen={push} />)}
                </div>
              )}
            </>
          )}
      </div>
    </>
  );

  // .agent-panel-host carries the panel's colours (arturo.css), so it looks the same on an agent page as on Arturo's.
  if (!desktop) {
    return (
      <div className="agent-panel-host">
        <div ref={panelRef} id="arturo-panel" role="dialog" aria-modal="true" aria-label={aname}
             className={`arturo-panel arturo-panel-sheet${shown ? ' is-shown' : ''}`}>
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="agent-panel-host">
      <div className={`arturo-panel-scrim${shown ? ' is-shown' : ''}`} onClick={onClose} aria-hidden />
      <div ref={panelRef} id="arturo-panel" role="dialog" aria-modal="true" aria-label={aname}
           className={`arturo-panel arturo-panel-drawer${shown ? ' is-shown' : ''}`}>
        {content}
      </div>
    </div>
  );
}

/** One 52px row: the label, its current value in grey, a chevron. A row with nothing behind it yet is
 *  disabled and says so, rather than opening an empty page. */
/** Arturo's face at the top of the drawer: its picture, not its status (only the sidebar avatar shows
 *  that), with the badge of the brain that runs it; under it the name, that brain and the voice it calls
 *  with (a different vendor, so labelled as the voice). Tapping it opens the look picker; until a look is
 *  saved, a pencil on it says so. */
const MARK_NAME: Record<string, string> = { claude: 'Claude', openai: 'OpenAI', gemini: 'Gemini', grok: 'Grok' };
const brainName = (p: string) => MARK_NAME[providerMark(p) ?? ''] || PROVIDER_LABEL[p] || p;

function DrawerFace({ agentId, name, save, subtitle, meter, meterPct, canSave, canName, desktop, onOpen, onEdit }: {
  agentId: string; name: string; save: SaveName; subtitle?: string; meter?: string | null; meterPct?: number | null;
  canSave: boolean; canName: boolean; desktop: boolean; onOpen: () => void; onEdit: () => void;
}) {
  const rec = useAgentRecord(agentId);
  return (
    <div className="arturo-face">
      <button type="button" className="arturo-face-btn la-on-panel" onClick={onOpen} aria-label={`${name}'s look`}>
        <AgentAvatar agentId={agentId} role="drawer" size={112} selected showStatus={false} label={name} />
        {canSave && !rec.chosen && <span className="look-pencil" aria-hidden><Pencil size={14} /></span>}
      </button>
      {desktop && canName ? <InlineName name={name} save={save} /> : <p className="arturo-face-name">{name}</p>}
      {/* Edit opens the identity screen: name, avatar and voice, like a chat app's profile screen. */}
      <button type="button" className="arturo-face-edit" onClick={onEdit} data-drawer-edit>Edit</button>
      <p className="arturo-face-status" data-drawer-status>
        {subtitle ?? `${rec.provider ? `${brainName(rec.provider)} · ` : ''}Voice: ${GPT_LIVE}`}
      </p>
      {meter && (
        <div className="arturo-face-meter" data-drawer-context>
          <span className="arturo-face-meter-bar" aria-hidden>
            <span style={{ width: `${Math.max(0, Math.min(100, 100 - (meterPct ?? 100)))}%` }}
                  className={(meterPct ?? 100) <= 20 ? 'is-low' : ''} />
          </span>
          <span>{meter}</span>
        </div>
      )}
    </div>
  );
}

/** Desktop: the name as plain text that is a field in disguise. Hovering fades in a textbox background and
 *  border; clicking edits it in place. Enter or leaving the field saves (the same setting as Settings → Name),
 *  Esc puts the name back. */
function InlineName({ name: aname, save }: { name: string; save: SaveName }) {
  // null while not editing: the field shows the saved name (and follows a rename made elsewhere)
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const text = draft ?? aname;
  const commit = async () => {
    const next = (draft ?? '').trim();
    if (draft === null || !next || next === aname) { setDraft(null); setError(null); return; }
    setBusy(true);
    const r = await save(next);
    setBusy(false);
    if (r.ok) { setDraft(null); setError(null); } else setError(r.error);
  };
  return (
    <>
      <input ref={ref} className="arturo-face-name arturo-face-name-edit" value={text} maxLength={NAME_MAX}
             size={Math.max(4, text.length + 1)} aria-label="Name" disabled={busy} data-inline-name
             onFocus={() => setDraft((d) => d ?? aname)}
             onChange={(e) => { setDraft(e.target.value); setError(null); }}
             onBlur={() => void commit()}
             onKeyDown={(e) => {
               if (e.key === 'Enter') { e.preventDefault(); ref.current?.blur(); }
               else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setDraft(null); setError(null); requestAnimationFrame(() => ref.current?.blur()); }
             }} />
      {error && <p className="arturo-panel-error arturo-face-error" role="alert">{error}</p>}
    </>
  );
}

/** Phone: the Edit screen (like a chat app's): the avatar with a pencil badge, the Name field, then Appearance with
 *  the Avatar row that opens the look picker. Done in the bar saves the name; the back arrow leaves it. */
function EditPage({ agentId, name: aname, draft, onDraft, error, canName, onDone, onAvatar, voice, onVoice }: {
  agentId: string; name: string;
  draft: string; onDraft: (v: string) => void; error: string | null; canName: boolean; onDone: () => void; onAvatar: () => void;
  voice: string; onVoice: () => void;
}) {
  const rec = useAgentRecord(agentId);
  return (
    <div className="arturo-edit">
      <button type="button" className="arturo-face-btn arturo-edit-face la-on-panel" onClick={onAvatar} aria-label={`Change ${aname}'s look`}>
        <AgentAvatar agentId={agentId} role="drawer" size={96} selected showStatus={false} label={aname} />
        <span className="look-pencil" aria-hidden><Pencil size={14} /></span>
      </button>
      <form onSubmit={(e) => { e.preventDefault(); onDone(); }}>
        <label className="arturo-edit-label" htmlFor="arturo-edit-name">Name</label>
        <input id="arturo-edit-name" className="arturo-panel-input arturo-edit-input" value={draft} maxLength={NAME_MAX}
               readOnly={!canName} onChange={(e) => onDraft(e.target.value)} data-edit-name />
      </form>
      {error && <p className="arturo-panel-error" role="alert">{error}</p>}
      {!canName && <p className="arturo-panel-note">Only an admin can rename the assistant.</p>}
      <h3 className="arturo-panel-section">Appearance</h3>
      <div className="arturo-panel-list">
        <Row id="avatar" label="Avatar" value={`${shapeName(rec.shape)} · ${colorName(rec.color)}`} onOpen={onAvatar} />
        <Row id="voice" label="Voice" value={voice} onOpen={onVoice} />
      </div>
    </div>
  );
}

function Row({ id, label, value, onOpen, soon }: {
  id?: PanelPage; label: string; value?: string; onOpen?: (p: PanelPage) => void; soon?: boolean;
}) {
  if (soon || !id || !onOpen) {
    return (
      <button type="button" className="arturo-panel-row" disabled>
        <span>{label}</span>
        <span className="arturo-panel-rowvalue">Coming soon</span>
      </button>
    );
  }
  return (
    <button type="button" data-panel-row={id} className="arturo-panel-row" onClick={() => onOpen(id)}>
      <span>{label}</span>
      <span className="arturo-panel-rowend">
        {value ? <span className="arturo-panel-rowvalue">{value}</span> : null}
        <ChevronRight size={18} />
      </span>
    </button>
  );
}

/** A read-only row: a label and its value, no chevron. */
function Info({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="arturo-panel-field">
      <span>{label}</span>
      <span className="arturo-panel-rowvalue">{children}</span>
    </div>
  );
}

/** The live voice, the way the phone's voice picker shows it: the provider, then its voices with a check on
 *  the one the next call uses. A pick shows at once and is put back, with the server's reason, if refused.
 *  Fast replies and captions are always on, so there is nothing to switch here. Anyone may look; only an
 *  admin may change it, so for everyone else the list is read-only and says why. */
function VoicePage({ health, onPicked, agentId }: {
  health: ArturoHealth | null; onPicked: (v: CurrentVoice) => void;
  /** Any agent but Arturo: its own voice, else the install's (Arturo's voice IS the install's). */
  agentId?: string;
}) {
  const [vendor, setVendor] = useState<VoiceVendor | null | undefined>(undefined);
  const [voices, setVoices] = useState<VoiceRow[] | null | undefined>(undefined);
  const [current, setCurrent] = useState<string | null>(null);
  const [scope, setScope] = useState<CurrentVoice['scope']>(undefined);
  const [error, setError] = useState<string | null>(null);
  const editable = canChangeVoice();
  const aname = useAssistantName();
  useEffect(() => {
    let alive = true;
    void fetchOpenAiVendor().then((v) => { if (alive) setVendor(v); });
    void fetchVoices().then((v) => { if (alive) setVoices(v); });
    void fetchCurrentVoice(agentId).then((v) => { if (alive && v) { setCurrent(v.voice_id); setScope(v.scope); } });
    return () => { alive = false; };
  }, [agentId]);
  // The server's own key check decides; an older API without the voice routes falls back to the health check.
  const available = vendor ? vendor.available : vendor === null && health ? handsFreeReady(health) : null;
  const pick = (id: string) => {
    if (!editable || id === current) return;
    const title = voices?.find((v) => v.id === id)?.title ?? null;
    const before = current;
    void pickVoice(before, id, (v) => putVoice(v, agentId), (s) => { setCurrent(s.current); setError(s.error); }).then((ok) => {
      if (ok) { setScope(agentId ? 'agent' : 'install'); onPicked({ voice_id: id, title, scope: agentId ? 'agent' : 'install' }); }
    });
  };
  const useDefault = () => {
    if (!agentId) return;
    void clearVoice(agentId).then((r) => {
      if (!r.ok) { setError(`Couldn't change the voice: ${r.error || 'unknown error'}`); return; }
      void fetchCurrentVoice(agentId).then((v) => {
        if (!v) return;
        setCurrent(v.voice_id); setScope(v.scope); setError(null); onPicked(v);
      });
    });
  };
  return (
    <>
      <h3 className="arturo-panel-section">Provider</h3>
      <div className="arturo-panel-list arturo-panel-list-tight">
        <div className={`arturo-panel-field${available === false ? ' is-disabled' : ''}`}>
          <span>{GPT_LIVE} (OpenAI)</span>
          {available ? <Check size={18} className="arturo-panel-check" aria-label="Selected" /> : null}
        </div>
      </div>
      {available === false && <p className="arturo-panel-note">Add an OpenAI key to use {GPT_LIVE}.</p>}
      {available && (
        <>
          <h3 className="arturo-panel-section">Voice</h3>
          {error && <p className="arturo-panel-error" role="alert">{error}</p>}
          {voices === undefined ? <p className="arturo-panel-empty">Loading…</p>
            : voices === null ? <p className="arturo-panel-empty">Couldn't load the voices.</p>
            : (
              <div className="arturo-panel-list arturo-panel-list-tight" role="radiogroup" aria-label="Voice">
                {voices.map((v) => (
                  <button key={v.id} type="button" role="radio" aria-checked={v.id === current} data-voice={v.id}
                          disabled={!editable} className="arturo-panel-row arturo-panel-choice" onClick={() => pick(v.id)}>
                    <span>{v.title}</span>
                    {v.id === current ? <Check size={18} className="arturo-panel-check" /> : null}
                  </button>
                ))}
              </div>
            )}
          {agentId && scope === 'agent' && editable && (
            <button type="button" className="arturo-panel-link" onClick={useDefault}>Use the default voice</button>
          )}
          <p className="arturo-panel-note">
            {!editable ? `${named(ADMIN_ONLY_NOTE, aname)}.`
              : agentId && scope !== 'agent' ? 'Using the default voice. Pick one to give this agent its own. Applies from your next call.'
              : 'Applies from your next call.'}
          </p>
        </>
      )}
    </>
  );
}

/** How you see this agent: Modern, Bubbles or Dev, saved for you on the server. A style it cannot show yet is
 *  listed with why, rather than hidden. */
function StylePage({ agentId }: { agentId: string }) {
  const style = useChatStyle(agentId);
  const error = useAgentPrefsStore((s) => s.errors[agentId]);
  return (
    <>
      <div className="arturo-panel-list arturo-panel-list-tight" role="radiogroup" aria-label="Chat style">
        {styleOptions(agentId).map((o) => (
          <button key={o.id} type="button" role="radio" aria-checked={o.id === style} data-style={o.id}
                  disabled={!o.available} className="arturo-panel-row arturo-panel-choice"
                  onClick={() => { if (o.id !== style) void chooseStyle(agentId, o.id); }}>
            <span>{o.label}</span>
            {o.id === style ? <Check size={18} className="arturo-panel-check" />
              : o.reason ? <span className="arturo-panel-rowvalue">{o.reason}</span> : null}
          </button>
        ))}
      </div>
      <HeadlessDevNote agentId={agentId} />
      {error && <p className="arturo-panel-error" role="alert">{error}</p>}
      <p className="arturo-panel-note">Saved for you on every device. Dev is the agent's live terminal.</p>
    </>
  );
}

/** The model this agent runs, and the ones it can switch to (lib/agentModel.ts: live where its CLI allows, at its
 *  next rotation otherwise; a busy agent switches when its turn ends). */
function ModelPage({ agentId, onChanged }: { agentId: string; onChanged: (m: AgentModelInfo | null) => void }) {
  const [info, setInfo] = useState<AgentModelInfo | null | undefined>(undefined);
  const [notice, setNotice] = useState<SwitchNotice | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    void fetchAgentModel(agentId).then((m) => { if (alive) setInfo(m); });
    return () => { alive = false; };
  }, [agentId]);
  if (info === undefined) return <p className="arturo-panel-empty">Loading…</p>;
  if (info === null) return <p className="arturo-panel-empty">Couldn't read this agent's model.</p>;
  const models = switchableModels(info);
  const why = pickRefusal(info, { providerId: info.runtime ?? '', modelLabel: '' });
  const pick = (id: string, label: string) => {
    if (busy || id === info.model) return;
    setBusy(true);
    setNotice({ tone: 'wait', text: `Switching to ${label}…` });
    void switchAgentModel(agentId, id).then(({ status, body }) => {
      setBusy(false);
      setNotice(switchNotice(status, body, label));
      if (status === 200 && body.ok && body.switching === 'switched') {
        const next = { ...info, model: id };
        setInfo(next); onChanged(next);
      }
    });
  };
  return (
    <>
      {why ? <p className="arturo-panel-note">{why}</p> : (
        <div className="arturo-panel-list arturo-panel-list-tight" role="radiogroup" aria-label="Model">
          {models.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={m.id === info.model} data-model={m.id}
                    disabled={busy} className="arturo-panel-row arturo-panel-choice" onClick={() => pick(m.id, m.label)}>
              <span>{m.label}</span>
              {m.id === info.model ? <Check size={18} className="arturo-panel-check" /> : null}
            </button>
          ))}
        </div>
      )}
      {notice && <p className={notice.tone === 'error' ? 'arturo-panel-error' : 'arturo-panel-note'} role="status">{notice.text}</p>}
    </>
  );
}

/** What an agent has running on the side: its subagents (background tasks and subagents are its jobs). */
function SubagentsPage({ running, name }: { running?: number; name: string }) {
  return (
    <div className="arturo-panel-activity">
      <h3 className="arturo-panel-section">Running</h3>
      <p className="arturo-panel-empty">
        {running ? `${name} has ${running} subagent${running === 1 ? '' : 's'} working.` : `Nothing running beside ${name}'s own turn.`}
      </p>
    </div>
  );
}

/** An agent's short name: shown everywhere it appears. Its old names stay its own, so mail sent to them still
 *  reaches it; its id never changes. */
function AgentNamePage({ agentId, name, save }: { agentId: string; name: string; save: SaveName }) {
  const [text, setText] = useState(name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const changed = text.trim() !== name;
  const submit = () => {
    if (!changed || !text.trim()) return;
    setBusy(true); setError(null); setSaved(false);
    void save(text.trim()).then((r) => {
      setBusy(false);
      if (r.ok) { setText(r.name); setSaved(true); } else setError(r.error);
    });
  };
  return (
    <div>
      <form className="arturo-panel-add" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input value={text} onChange={(e) => { setText(e.target.value); setSaved(false); }} aria-label="Agent name"
               maxLength={40} className="arturo-panel-input" data-name-input />
        <button type="submit" className="arturo-panel-primary" disabled={!changed || !text.trim() || busy}>Save</button>
      </form>
      {error && <p className="arturo-panel-error" role="alert">{error}</p>}
      <p className="arturo-panel-note" role="status">
        {saved ? 'Saved. Its old name still reaches it.' : `Shown everywhere this agent appears. Its id stays ${agentId}.`}
      </p>
    </div>
  );
}

/** The default brain the composer's chip shows, and why it is not answering when it is not. */
function BrainPage({ health, brain }: { health: ArturoHealth | null; brain: string }) {
  const aname = useAssistantName();
  const why = !health ? null
    : isArturoDisabled(health) ? named(ARTURO_DISABLED_TEXT, aname)
    : !health.ok ? `${aname}'s brain did not answer the health check${health.error ? ` (${health.error})` : ''}. Check that the assistant service is running.`
    : health.brain?.kind === 'none' ? (health.brain.reason || 'No brain is connected. Sign in to a runtime or add a provider key on the server.')
    : null;
  return (
    <>
      <div className="arturo-panel-list">
        <Info label="Default brain">{brain || '…'}</Info>
        <Info label="Status">{panelStatus(health, brain, aname).text.split(' · ')[0]}</Info>
      </div>
      {why && <p className="arturo-panel-note">{why}</p>}
      <p className="arturo-panel-note">Change the brain for a message from the chip under the composer.</p>
    </>
  );
}

/** The assistant's name on this install: shown everywhere it appears (every device) and used in its own
 *  instructions. Only an admin renames it; anyone else sees it read-only. Saved on Save, not per keystroke,
 *  so a half-typed name never reaches the assistant. */
function NamePage() {
  const aname = useAssistantName();
  const editable = canRename();
  const [text, setText] = useState(aname);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const changed = text.trim() !== aname;
  const done = (r: Awaited<ReturnType<typeof saveAssistantName>>) => {
    setBusy(false);
    if (r.ok) { setText(r.name); setSaved(true); } else setError(r.error);
  };
  const save = () => { if (!changed || !text.trim()) return; setBusy(true); setError(null); setSaved(false); void saveAssistantName(text.trim()).then(done); };
  const reset = () => { setBusy(true); setError(null); setSaved(false); void resetAssistantName().then(done); };
  return (
    <div>
      <form className="arturo-panel-add" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <input value={text} onChange={(e) => { setText(e.target.value); setSaved(false); }} aria-label="Assistant name"
               maxLength={NAME_MAX} readOnly={!editable} className="arturo-panel-input" data-name-input />
        {editable && <button type="submit" className="arturo-panel-primary" disabled={!changed || !text.trim() || busy}>Save</button>}
      </form>
      {error && <p className="arturo-panel-error" role="alert">{error}</p>}
      {editable && aname !== DEFAULT_ASSISTANT_NAME && (
        <button type="button" className="arturo-panel-link" onClick={reset} disabled={busy}>Use the default name ({DEFAULT_ASSISTANT_NAME})</button>
      )}
      <p className="arturo-panel-note" role="status">
        {!editable ? 'Only an admin can rename the assistant.'
          : saved ? `Saved. Every device now calls it ${aname}.`
          : 'Shown on every device and used in what it says about itself. Letters, numbers and spaces.'}
      </p>
    </div>
  );
}

/** The facts Arturo recalls on its next turn: one list, to add to, correct, or confirm as still true. */
function MemoryPage() {
  const aname = useAssistantName();
  const [facts, setFacts] = useState<Fact[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newText, setNewText] = useState('');
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [busy, setBusy] = useState<number | 'new' | null>(null);

  const load = async () => {
    try { setFacts((await factsApi.getFacts()).facts || []); setError(null); }
    catch (e) { setError(errText(e, 'Could not load memory')); }
  };
  useEffect(() => { void load(); }, []);

  const run = async (key: number | 'new', fn: () => Promise<unknown>, fallback: string) => {
    setBusy(key);
    try { await fn(); await load(); return true; }
    catch (e) { setError(errText(e, fallback)); return false; }
    finally { setBusy(null); }
  };
  const add = async () => {
    const text = newText.trim();
    if (!text || busy) return;
    if (await run('new', () => factsApi.createFact(text), 'Could not add that')) setNewText('');
  };
  const save = async () => {
    if (!editing || !editing.text.trim()) return;
    const { id, text } = editing;
    if (await run(id, () => factsApi.editFact(id, text.trim()), 'Could not save that')) setEditing(null);
  };

  return (
    <div>
      <form className="arturo-panel-add" onSubmit={(e) => { e.preventDefault(); void add(); }}>
        <input value={newText} onChange={(e) => setNewText(e.target.value)} aria-label="New fact"
               placeholder={`Add something ${aname} should know`} className="arturo-panel-input" />
        <button type="submit" className="arturo-panel-primary" disabled={!newText.trim() || busy === 'new'}>Add</button>
      </form>
      {error && <p className="arturo-panel-error" role="alert">{error}</p>}
      {facts === null ? (!error && <p className="arturo-panel-empty">Loading…</p>)
        : facts.length === 0 ? <p className="arturo-panel-empty">Nothing remembered yet.</p>
        : (
          <ul className="arturo-panel-facts">
            {facts.map((f) => (
              <li key={f.id}>
                {editing?.id === f.id ? (
                  <>
                    <textarea value={editing.text} aria-label="Edit fact" className="arturo-panel-input arturo-panel-textarea"
                              onChange={(e) => setEditing({ id: f.id, text: e.target.value })} />
                    <div className="arturo-panel-actions">
                      <button type="button" className="arturo-panel-link" onClick={() => setEditing(null)}>Cancel</button>
                      <button type="button" className="arturo-panel-primary" disabled={!editing.text.trim() || busy === f.id} onClick={() => void save()}>Save</button>
                    </div>
                  </>
                ) : (
                  <>
                    <p>{f.text}</p>
                    <div className="arturo-panel-actions">
                      <span className="arturo-panel-meta">
                        {f.freshness?.age_days != null ? `${f.freshness.age_days}d old` : 'Age unknown'}
                        {f.freshness?.stale ? ' · may be out of date' : ''}
                      </span>
                      <span className="flex-1" />
                      <button type="button" className="arturo-panel-link" onClick={() => setEditing({ id: f.id, text: f.text })}>Edit</button>
                      <button type="button" className="arturo-panel-link" disabled={busy === f.id}
                              onClick={() => void run(f.id, () => factsApi.markFresh(f.id), 'Could not update that')}>Still true</button>
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

/** What Arturo is doing now: the current conversation's running and recent background jobs. */
function ActivityPage() {
  const [jobs, setJobs] = useState<ArturoJob[] | null>(null);
  const hasThread = !!currentThread();
  useEffect(() => {
    let alive = true;
    void loadJobs().then((j) => { if (alive) setJobs(j); });
    return () => { alive = false; };
  }, []);
  if (!hasThread) return <p className="arturo-panel-empty">No conversation open.</p>;
  if (jobs === null) return <p className="arturo-panel-empty">Loading…</p>;
  const running = jobs.filter((j) => j.status === 'running');
  const recent = jobs.filter((j) => j.status !== 'running');
  const list = (js: ArturoJob[]) => (
    <ul className="arturo-panel-jobs">
      {js.map((j) => (
        <li key={j.id}>
          <span className="truncate">{j.title || j.kind}</span>
          <span className="arturo-panel-meta">{j.status} · {elapsed(j)}</span>
        </li>
      ))}
    </ul>
  );
  return (
    <div className="arturo-panel-activity">
      <h3 className="arturo-panel-section">Running</h3>
      {running.length ? list(running) : <p className="arturo-panel-empty">Nothing running in this conversation.</p>}
      <h3 className="arturo-panel-section">Recent</h3>
      {recent.length ? list(recent) : <p className="arturo-panel-empty">No background jobs yet.</p>}
    </div>
  );
}

/** Settings kept in this browser only (they are not synced), one row each, saved as they change. */
function DevicePage() {
  const s = useAgentSettings();
  return (
    <div className="arturo-panel-list">
      <label className="arturo-panel-field">
        <span>Voice colour</span>
        <input type="color" value={s.accentVoice} onChange={(e) => s.setAccentVoice(e.target.value)} className="arturo-panel-swatch" />
      </label>
      <label className="arturo-panel-field">
        <span>Transcript</span>
        <select value={s.transcriptMode} onChange={(e) => s.setTranscriptMode(e.target.value === 'as-spoken' ? 'as-spoken' : 'all-at-once')} className="arturo-panel-value">
          <option value="all-at-once">All at once</option>
          <option value="as-spoken">As spoken</option>
        </select>
      </label>
      <label className="arturo-panel-field">
        <span>Microphone</span>
        <select value={s.micMode} onChange={(e) => s.setMicMode(e.target.value === 'toggle' ? 'toggle' : 'hold')} className="arturo-panel-value">
          <option value="hold">Hold to talk</option>
          <option value="toggle">Tap to talk</option>
        </select>
      </label>
    </div>
  );
}
