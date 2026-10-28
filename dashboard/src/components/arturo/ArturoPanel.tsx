/**
 * ArturoPanel — Arturo's slide-out from the top bar's gear: a short menu about Arturo. Each row shows its
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
import { panelStatus, pathTo, factsValue, activityValue, type PanelPage } from '../../lib/arturoPanel';
import { fetchOpenAiVendor, fetchVoices, fetchCurrentVoice, putVoice, pickVoice, voiceRowValue, canChangeVoice, ADMIN_ONLY_NOTE, GPT_LIVE,
         type VoiceVendor, type VoiceRow, type CurrentVoice } from '../../lib/arturoVoice';
import { fetchJobs, elapsed, type ArturoJob } from '../../lib/arturoJobs';
import { currentThread } from '../../lib/arturoCurrent';
import { factsApi, type Fact } from '../../lib/factsApi';
import { useAgentSettings } from '../../stores/agentSettings';
import { useUser } from '../../hooks/useUser';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { LookPicker } from '../avatar/LookPicker';
import { useAgentRecord, ARTURO_ID } from '../avatar/agentState';
import { PROVIDER_LABEL } from '../../lib/agentRail';
import { providerMark } from '../avatar/brandMarks';
import { canChangeLook, shapeName, colorName } from '../../lib/avatarLooks';
import { useAssistantName, named, saveAssistantName, resetAssistantName, canRename, DEFAULT_ASSISTANT_NAME, NAME_MAX } from '../../lib/assistantName';

const TITLES: Record<PanelPage, string> = {
  voice: 'Voice', memory: 'Memory', activity: 'Activity', settings: 'Settings', brain: 'Brain', device: 'On this device',
  avatar: 'Avatar', name: 'Name', edit: 'Edit',
};
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

export function ArturoPanel({ open, onClose, returnFocusTo, brain }: {
  open: boolean;
  onClose: () => void;
  /** The gear: focus goes back to it when the panel closes. */
  returnFocusTo: RefObject<HTMLElement | null>;
  /** The composer chip's brain text ("Claude CLI"), so the status line names the same brain. */
  brain: string;
}) {
  const desktop = useSyncExternalStore(subscribeDesktop, isDesktopNow, () => true);
  const arturoRec = useAgentRecord(ARTURO_ID);
  const aname = useAssistantName();
  const canSaveLook = canChangeLook(useUser().data?.role);
  // Mounted while open or sliding out; `shown` drives the slide, a frame after mount so it animates in.
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  // The pages opened from the root, deepest last; empty is the root.
  const [stack, setStack] = useState<PanelPage[]>([]);
  // The phone's Edit page: its name draft lives here, so a trip to the look picker and back keeps it.
  const [editDraft, setEditDraft] = useState('');
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const canEditName = canRename(useUser().data);
  const [health, setHealth] = useState<ArturoHealth | null>(null);
  const [factCount, setFactCount] = useState<number | null>(null);
  const [jobs, setJobs] = useState<ArturoJob[] | null>(null);
  const [vendor, setVendor] = useState<VoiceVendor | null>(null);
  const [voice, setVoice] = useState<CurrentVoice | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(open);
  // The page just left by Back, so focus lands on the row that opened it.
  const leftPage = useRef<PanelPage | null>(null);
  const page = stack.length ? stack[stack.length - 1] : null;

  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    let raf = 0;
    if (open) {
      t = setTimeout(() => { setMounted(true); raf = requestAnimationFrame(() => setShown(true)); }, 0);
    } else {
      t = setTimeout(() => setShown(false), 0);
      const done = setTimeout(() => { setMounted(false); setStack([]); }, SLIDE_MS);
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
    void arturoHealth().then((h) => { if (alive) setHealth(h); });
    factsApi.getFacts().then((d) => { if (alive) setFactCount((d.facts || []).length); }).catch(() => {});
    void loadJobs().then((j) => { if (alive) setJobs(j); });
    void fetchOpenAiVendor().then((v) => { if (alive) setVendor(v); });
    void fetchCurrentVoice().then((v) => { if (alive) setVoice(v); });
    return () => { alive = false; };
  }, [open, atRoot]);

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
    const r = await saveAssistantName(next);
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

  const content = (
    <>
      {bar}
      <div className="arturo-panel-body" key={page ?? 'root'}>
        {page === 'voice' ? <VoicePage health={health} onPicked={setVoice} />
          : page === 'memory' ? <MemoryPage />
          : page === 'activity' ? <ActivityPage />
          : page === 'settings' ? (
            <div className="arturo-panel-list">
              <Row id="name" label="Name" value={aname} onOpen={push} />
              <Row id="brain" label="Brain" value={brain} onOpen={push} />
              <Row label="Keys" soon />
              <Row label="Notifications" soon />
              <Row id="device" label="On this device" onOpen={push} />
              <Row label="About" soon />
            </div>
          )
          : page === 'brain' ? <BrainPage health={health} brain={brain} />
          : page === 'device' ? <DevicePage />
          : page === 'name' ? <NamePage />
          : page === 'edit' ? (
            <EditPage draft={editDraft} onDraft={setEditDraft} error={editError} canName={canEditName}
                      onDone={() => void editDone()} onAvatar={() => push('avatar')} />
          )
          : page === 'avatar' ? <LookPicker agentId={ARTURO_ID} heroSize={160} canSave={canSaveLook} />
          : (
            <>
              <DrawerFace canSave={canSaveLook} canName={canEditName} desktop={desktop}
                          onOpen={() => push('avatar')} onEdit={openEdit} />
              {status.fix ? (
                <button type="button" className="arturo-panel-status is-fix" role="status"
                        onClick={() => setStack(pathTo(status.fix!))}>
                  <span>{status.text}</span>
                  <ChevronRight size={14} />
                </button>
              ) : (
                <p className="arturo-panel-status" role="status">{status.text}</p>
              )}
              <div className="arturo-panel-list">
                <Row id="avatar" label="Avatar" value={`${shapeName(arturoRec.shape)} · ${colorName(arturoRec.color)}`} onOpen={push} />
                <Row id="voice" label="Voice" value={voiceRowValue(vendor, voice, health ? handsFreeReady(health) : null)} onOpen={push} />
                <Row id="memory" label="Memory" value={factsValue(factCount)} onOpen={push} />
                <Row id="activity" label="Activity" value={activityValue(jobs)} onOpen={push} />
                <Row id="settings" label="Settings" onOpen={push} />
              </div>
            </>
          )}
      </div>
    </>
  );

  if (!desktop) {
    return (
      <div ref={panelRef} id="arturo-panel" role="dialog" aria-modal="true" aria-label={aname}
           className={`arturo-panel arturo-panel-sheet${shown ? ' is-shown' : ''}`}>
        {content}
      </div>
    );
  }
  return (
    <>
      <div className={`arturo-panel-scrim${shown ? ' is-shown' : ''}`} onClick={onClose} aria-hidden />
      <div ref={panelRef} id="arturo-panel" role="dialog" aria-modal="true" aria-label={aname}
           className={`arturo-panel arturo-panel-drawer${shown ? ' is-shown' : ''}`}>
        {content}
      </div>
    </>
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

function DrawerFace({ canSave, canName, desktop, onOpen, onEdit }: {
  canSave: boolean; canName: boolean; desktop: boolean; onOpen: () => void; onEdit: () => void;
}) {
  const rec = useAgentRecord(ARTURO_ID);
  const aname = useAssistantName();
  return (
    <div className="arturo-face">
      <button type="button" className="arturo-face-btn la-on-panel" onClick={onOpen} aria-label={`${aname}'s look`}>
        <AgentAvatar agentId={ARTURO_ID} role="drawer" size={112} selected showStatus={false} label={aname} />
        {canSave && !rec.chosen && <span className="look-pencil" aria-hidden><Pencil size={14} /></span>}
      </button>
      {desktop && canName ? <InlineName /> : <p className="arturo-face-name">{aname}</p>}
      {/* Phone: the name is edited on its own screen, opened from this pill (as Grok Bot's profile does). */}
      {!desktop && (canName || canSave) && <button type="button" className="arturo-face-edit" onClick={onEdit}>Edit</button>}
      <p className="arturo-face-status">{rec.provider ? `${brainName(rec.provider)} · ` : ''}Voice: {GPT_LIVE}</p>
    </div>
  );
}

/** Desktop: the name as plain text that is a field in disguise. Hovering fades in a textbox background and
 *  border; clicking edits it in place. Enter or leaving the field saves (the same setting as Settings → Name),
 *  Esc puts the name back. */
function InlineName() {
  const aname = useAssistantName();
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
    const r = await saveAssistantName(next);
    setBusy(false);
    if (r.ok) { setDraft(null); setError(null); } else setError(r.error);
  };
  return (
    <>
      <input ref={ref} className="arturo-face-name arturo-face-name-edit" value={text} maxLength={NAME_MAX}
             size={Math.max(4, text.length + 1)} aria-label="Assistant name" disabled={busy} data-inline-name
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

/** Phone: the Edit screen (as Grok Bot's): the avatar with a pencil badge, the Name field, then Appearance with
 *  the Avatar row that opens the look picker. Done in the bar saves the name; the back arrow leaves it. */
function EditPage({ draft, onDraft, error, canName, onDone, onAvatar }: {
  draft: string; onDraft: (v: string) => void; error: string | null; canName: boolean; onDone: () => void; onAvatar: () => void;
}) {
  const rec = useAgentRecord(ARTURO_ID);
  const aname = useAssistantName();
  return (
    <div className="arturo-edit">
      <button type="button" className="arturo-face-btn arturo-edit-face la-on-panel" onClick={onAvatar} aria-label={`Change ${aname}'s look`}>
        <AgentAvatar agentId={ARTURO_ID} role="drawer" size={96} selected showStatus={false} label={aname} />
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
function VoicePage({ health, onPicked }: { health: ArturoHealth | null; onPicked: (v: CurrentVoice) => void }) {
  const [vendor, setVendor] = useState<VoiceVendor | null | undefined>(undefined);
  const [voices, setVoices] = useState<VoiceRow[] | null | undefined>(undefined);
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editable = canChangeVoice(useUser().data?.role);
  const aname = useAssistantName();
  useEffect(() => {
    let alive = true;
    void fetchOpenAiVendor().then((v) => { if (alive) setVendor(v); });
    void fetchVoices().then((v) => { if (alive) setVoices(v); });
    void fetchCurrentVoice().then((v) => { if (alive && v) setCurrent(v.voice_id); });
    return () => { alive = false; };
  }, []);
  // The server's own key check decides; an older API without the voice routes falls back to the health check.
  const available = vendor ? vendor.available : vendor === null && health ? handsFreeReady(health) : null;
  const pick = (id: string) => {
    if (!editable || id === current) return;
    const title = voices?.find((v) => v.id === id)?.title ?? null;
    const before = current;
    void pickVoice(before, id, putVoice, (s) => { setCurrent(s.current); setError(s.error); }).then((ok) => {
      if (ok) onPicked({ voice_id: id, title });
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
          <p className="arturo-panel-note">{editable ? 'Applies from your next call.' : `${named(ADMIN_ONLY_NOTE, aname)}.`}</p>
        </>
      )}
    </>
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
  const editable = canRename(useUser().data);
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
