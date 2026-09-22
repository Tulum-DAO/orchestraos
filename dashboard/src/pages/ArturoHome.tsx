/**
 * ArturoHome — the MAIN page (track T4). A standalone phone shell, not a dashboard page:
 * one thin header (settings gear · "Arturo · <model> ⌄" · brain), icons from the
 * shared lucide set so the web pair matches the iOS pair (brain + gearshape) instead of
 * a one-off hand-drawn glyph, black canvas with the
 * accent glow behind the composer, empty state = mark + serif greeting, one two-row composer.
 *
 * Onboarding is Arturo's FIRST THREAD, not a form: name → runtime detect (catalog probe +
 * /api/arturo/health) → optional voice → "what should your first agent do?" → spawn, all through
 * the conversation. Once a seat exists (spawn_agent ran) the thread is ordinary chat.
 *
 * Every turn goes through POST /api/arturo/text (gateway → :5071/text), which runs the full
 * tool-enabled turn on whichever brain the install has (api key / authed CLI / none).
 */
import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Mic, Plus, ArrowUp, AudioLines, Paperclip } from 'lucide-react';
import '../components/arturo/arturo.css';
import { BrainModal } from '../components/agent/BrainModal';
import { ModelSelectorSheet } from '../components/agent/ModelSelectorSheet';
import { arturoHealth, arturoText, runtimesAvailable, brainLabel, greeting, slugify, newConversationId,
  isStarting, waitForArturo, STARTING_TEXT,
  type ArturoHealth, type RuntimeRow } from '../lib/arturo';
import { listThreads, loadThread, type ThreadSummary } from '../lib/arturoThreads';
import WebTerminal from '../components/WebTerminal';
import { installCommand } from '../lib/providerConnect';
import { uploadAttachment, attachmentPreamble, describeAttachment, type Attachment } from '../lib/arturoUpload';
import { Brain, Settings } from 'lucide-react';

type Turn = { id: number; role: 'user' | 'arturo'; text: string; tools?: string[]; pending?: boolean;
  decision?: { options: string[]; onPick: (v: string) => void } };
type Step = 'name' | 'runtime' | 'voice' | 'first' | 'done';

const LS_NAME = 'orchestra.arturo.name';
const LS_ONBOARDED = 'orchestra.arturo.onboarded';
const LS_CONV = 'orchestra.arturo.conversation';

function ls(key: string): string | null { try { return localStorage.getItem(key); } catch { return null; } }
function lsSet(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* private mode */ } }

export function ArturoMark({ className = 'arturo-mark' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
      <g transform="translate(24,24)">
        <line x1="0" y1="-19" x2="0" y2="-7" /><line x1="0" y1="19" x2="0" y2="7" />
        <line x1="-19" y1="0" x2="-7" y2="0" /><line x1="19" y1="0" x2="7" y2="0" />
        <line x1="-13.4" y1="-13.4" x2="-5" y2="-5" /><line x1="13.4" y1="13.4" x2="5" y2="5" />
        <line x1="13.4" y1="-13.4" x2="5" y2="-5" /><line x1="-13.4" y1="13.4" x2="-5" y2="5" />
      </g>
    </svg>
  );
}

const DRAWER = [
  ['Inbox', '/inbox'], ['Approvals', '/approvals'], ['Agents', '/agents'], ['Projects', '/projects'],
  ['Tasks', '/tasks'], ['Activity', '/activity'], ['Overview', '/overview'], ['Command Center', '/command-center'],
];

function renderText(t: string) {
  // `code` spans only — the thread stays plain text otherwise (no markdown engine).
  const parts = t.split(/(`[^`]+`)/g);
  return parts.map((p, i) => p.startsWith('`') && p.endsWith('`') ? <code key={i}>{p.slice(1, -1)}</code> : <span key={i}>{p}</span>);
}

export default function ArturoHome() {
  const [name, setName] = useState<string>(ls(LS_NAME) || '');
  const [step, setStep] = useState<Step>(ls(LS_ONBOARDED) === '1' ? 'done' : (ls(LS_NAME) ? 'runtime' : 'name'));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<ArturoHealth | null>(null);
  const [starting, setStarting] = useState(false);   // G15: proxy not up yet (orchestra up boot window)
  const [drawer, setDrawer] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  // G20: the home and the pill read ONE server-side thread space, so a conversation started
  // in either place is reachable from the other. The drawer is where you go back to one.
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  // The onboarding terminal. A first-time installer has NO CLI yet, so this is where they
  // install one. POST /api/agents/login-shell works with nothing installed, so it is
  // reachable BEFORE there is a brain to talk to — no chicken-and-egg.
  const [onboardShell, setOnboardShell] = useState<string | null>(null);
  const [shellError, setShellError] = useState<string | null>(null);
  // Attachments the operator has added but not yet sent. POST /api/uploads already existed
  // and worked; the + button was simply never wired to it.
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const convId = useRef<string>(ls(LS_CONV) || '');
  useEffect(() => { if (!convId.current) { convId.current = newConversationId('web'); lsSet(LS_CONV, convId.current); } }, []);
  useEffect(() => { if (drawer) void listThreads().then(setThreads); }, [drawer]);

  /** Resume a previous conversation: its turns come from the server, so "pick up right where
   *  we left off" holds across a reload, another surface, and a service restart. */
  async function resumeThread(id: string) {
    const t = await loadThread(id);
    if (!t) return;
    convId.current = id; lsSet(LS_CONV, id);
    setTurns(t.turns.map((x) => ({ id: nextId.current++, role: x.role === 'user' ? 'user' : 'arturo', text: x.content })));
    setStep('done'); lsSet(LS_ONBOARDED, '1');
    setDrawer(false);
  }

  /** New thread — the one you leave stays in the list rather than becoming unreachable. */
  function startNewThread() {
    convId.current = newConversationId('web'); lsSet(LS_CONV, convId.current);
    setTurns([]);
    setDrawer(false);
  }

  const feedRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  const startedStep = useRef<Step | null>(null);   // StrictMode double-invokes effects

  useEffect(() => {
    let alive = true;
    (async () => {
      const h = await arturoHealth();
      if (!alive) return;
      if (h.ok || !isStarting(h)) { setHealth(h); return; }
      setStarting(true);
      const ready = await waitForArturo({ onTick: (last) => { if (alive) setHealth(last); } });
      if (!alive) return;
      setHealth(ready); setStarting(!ready.ok && isStarting(ready));
    })();
    return () => { alive = false; };
  }, []);
  useEffect(() => { feedRef.current?.scrollTo({ top: 1e9, behavior: 'smooth' }); }, [turns]);

  const say = (text: string, extra: Partial<Turn> = {}) => {
    const id = nextId.current++;
    setTurns((t) => [...t, { id, role: 'arturo', text, ...extra }]);
    return id;
  };
  const patch = (id: number, extra: Partial<Turn>) => setTurns((t) => t.map((x) => x.id === id ? { ...x, ...extra } : x));

  // --- onboarding thread -------------------------------------------------------------
  useEffect(() => {
    if (startedStep.current === step) return;
    startedStep.current = step;
    if (step === 'name' && turns.length === 0) {
      say("Hi, I'm Arturo — the voice and text front door of this OrchestraOS. What should I call you?");
    }
    if (step === 'runtime') void runtimeStep();
    if (step === 'first') say(`What should your first agent do? Describe the job in a sentence — I'll spawn a seat on the runtime you're logged in to and hand it the task.`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  async function runtimeStep(fresh = false) {
    const id = say('', { pending: true });
    // fresh: the user just logged in and tapped "Check again" — re-probe, never the cache
    let h = await arturoHealth();
    if (!h.ok && isStarting(h)) {          // G15: booting is not "no CLI"
      patch(id, { pending: false, text: STARTING_TEXT });
      h = await waitForArturo();
      patch(id, { pending: true, text: '' });
    }
    const rows = await runtimesAvailable(fresh);
    setHealth(h);
    const authed = rows.filter((r: RuntimeRow) => r.installed && r.authed === true);
    const installedOnly = rows.filter((r: RuntimeRow) => r.installed && r.authed !== true);
    const who = name ? `Nice to meet you, ${name}. ` : '';
    if (h.brain?.kind === 'none' || (authed.length === 0 && h.brain?.kind !== 'api')) {
      const hint = installedOnly.length
        ? `I can see ${installedOnly.map((r) => r.label || r.id).join(', ')} installed but not logged in. `
        : 'I do not see any agent CLI on this machine yet. ';
      // "Open a terminal ON THE SERVER" was an instruction to LEAVE the product, given to
      // the one person who cannot act on it: someone who just installed this and has no CLI
      // and possibly no shell of their own. The terminal is offered HERE instead, first.
      const nothingInstalled = installedOnly.length === 0;
      patch(id, { pending: false,
        text: `${who}${hint}${nothingInstalled
          ? `You need one on this machine before I can think. Open a terminal right here and install one — \`${installCommand('claude')}\` — then run \`claude\` and sign in.`
          : 'Open a terminal right here and sign in — `claude`, `codex login` or `agy`.'} I run on the CLI you already pay for; no API key needed.`,
        decision: {
          options: ['Open a terminal', 'Check again'],
          onPick: (choice: string) => {
            if (choice === 'Check again') {
              setTurns((t) => t.filter((x) => x.id !== id));
              void runtimeStep(true);
              return;
            }
            setShellError(null);
            void (async () => {
              try {
                const res = await fetch('/api/agents/login-shell', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
                });
                const j = await res.json().catch(() => ({}));
                if (!res.ok || !j.ok || !j.session) { setShellError(j.reason || j.error || `HTTP ${res.status}`); return; }
                setOnboardShell(j.session);
              } catch (e) {
                setShellError(e instanceof Error ? e.message : 'could not open a terminal');
              }
            })();
          },
        } });
      return;
    }
    const brain = h.brain?.kind === 'api' ? `an API key (${brainLabel(h.brain)})` : `${brainLabel(h.brain)} through your logged-in CLI`;
    const list = authed.length ? authed.map((r) => r.label || r.id).join(', ') : 'none';
    patch(id, { pending: false, text: `${who}I can see ${authed.length || 'no'} runtime${authed.length === 1 ? '' : 's'} authenticated: ${list}. My brain is running on ${brain}.${h.mode === 'text-only' ? ' Voice needs a vendor key; text is free and on.' : ' Voice is on.'}` });
    if (h.mode === 'text-only') {
      say('Want voice now, or is text fine for today?', {
        decision: { options: ['Text is fine', 'I will add a voice key'], onPick: (v) => {
          user(v);
          if (v.startsWith('Text')) say('Text it is. You can add ELEVENLABS_API_KEY later and restart Arturo — nothing else changes.');
          else say('Put ELEVENLABS_API_KEY (or CARTESIA_API_KEY) in the environment `orchestra up` runs under, restart, and /health will say mode: voice.');
          setStep('first');
        } },
      });
    } else {
      setStep('first');
    }
  }

  const user = (text: string) => setTurns((t) => [...t, { id: nextId.current++, role: 'user', text }]);

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    user(text);
    if (step === 'name') {
      const n = text.replace(/^(i am|i'm|call me|my name is)\s+/i, '').replace(/[.!]+$/, '').trim().split(/\s+/)[0];
      const clean = n.charAt(0).toUpperCase() + n.slice(1);
      setName(clean); lsSet(LS_NAME, clean);
      setStep('runtime');
      return;
    }
    const isFirst = step === 'first';
    const body = isFirst
      ? `Commission a new agent named "${slugify(text)}" on this machine (machine: vps) and give it this task: ${text}. Use the spawn_agent tool, then tell me the seat name in one sentence.`
      : text;
    setBusy(true);
    const id = say('', { pending: true });
    // The path rides in front of the message: Arturo runs on this machine with tool
    // access, so a path is openable — a filename alone would be decoration.
    const pre = attachmentPreamble(attachments);
    const sent = pre ? `${pre}\n\n${body}` : body;
    setAttachments([]);
    let r = await arturoText(sent, convId.current);
    if (!r.ok && isStarting(r)) {          // G15: still booting -> say so, wait for health, retry once
      patch(id, { pending: false, text: STARTING_TEXT });
      const ready = await waitForArturo();
      setHealth(ready);
      if (ready.ok) { patch(id, { pending: true, text: '' }); r = await arturoText(sent, convId.current); }
    }
    setBusy(false);
    if (!r.ok) {
      patch(id, { pending: false, text: isStarting(r)
        ? 'I am still starting up and could not answer yet — give `orchestra up` a moment and send that again.'
        : `I could not reach my brain: ${r.error || 'unknown'}. Is \`orchestra up\` running? Check /health on the Arturo service.` });
      return;
    }
    patch(id, { pending: false, text: r.reply_text || '(no reply)', tools: r.tools_called });
    if (isFirst && (r.tools_called || []).includes('spawn_agent')) {
      lsSet(LS_ONBOARDED, '1'); setStep('done');
      say('Your first seat is up. From here on, this thread is the front door: ask for status, commission more agents, or open the drawer for the rest of the OS.');
    }
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !(e.nativeEvent as KeyboardEvent).isComposing) { e.preventDefault(); void send(); }
  };
  const grow = () => { const el = taRef.current; if (!el) return; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 140) + 'px'; };

  const brain = health?.brain;
  const model = starting ? 'starting…' : brainLabel(brain);
  const eff = brain?.kind === 'runtime' ? 'CLI' : brain?.kind === 'api' ? 'API' : '';
  const empty = turns.length === 0;

  return (
    <div className="arturo-shell">
      <header className="arturo-header">
        <button className="arturo-glyph" aria-label="Open settings" onClick={() => setDrawer(true)}>
          <Settings size={22} strokeWidth={2.2} absoluteStrokeWidth />
        </button>
        <button className="arturo-title" onClick={() => setModelOpen(true)} aria-label="Select model">
          Arturo <span className="model">· {model}</span> <span className="chev">⌄</span>
        </button>
        <button className="arturo-glyph" aria-label="Open brain" onClick={() => setBrainOpen(true)}>
          <Brain size={22} strokeWidth={2.2} absoluteStrokeWidth />
        </button>
      </header>

      <div className="arturo-glow" />

      <div className="arturo-feed" ref={feedRef}>
        {empty ? (
          <div className="arturo-hero">
            <ArturoMark />
            <div className="greet serif">{greeting(name)}</div>
            {starting && <div className="tools" style={{ color: 'rgba(255,255,255,0.45)', fontSize: 13 }}>{STARTING_TEXT}</div>}
          </div>
        ) : turns.map((t) => t.role === 'user' ? (
          <div key={t.id} className="turn-user"><div className="bubble-user">{t.text}</div></div>
        ) : (
          <div key={t.id} className="turn-assistant">
            <ArturoMark className="mark-sm" />
            {t.pending ? <span className="thinking" aria-label="thinking" /> : <div className="txt">{renderText(t.text)}</div>}
            {t.tools && t.tools.length > 0 && <div className="tools">ran {t.tools.join(', ')}</div>}
            {t.decision && (
              <div className="decision-card">
                {t.decision.options.map((o) => (
                  <button key={o} className="decision-opt" onClick={() => { patch(t.id, { decision: undefined }); t.decision!.onPick(o); }}>
                    {o}<span className="r" />
                  </button>
                ))}
                <div className="decision-freetext">✎ Or type your own answer below…</div>
              </div>
            )}
          </div>
        ))}
      </div>

      {(onboardShell || shellError) && (
        <div className="arturo-onboard-shell">
          {shellError ? (
            <p className="shell-error">Could not open a terminal: {shellError}</p>
          ) : (
            <>
              <div className="shell-head">
                <span>Terminal on this machine · {onboardShell}</span>
                <button onClick={() => { setOnboardShell(null); void runtimeStep(true); }}>Done — check again</button>
              </div>
              <WebTerminal session={onboardShell!} machine="vps" />
            </>
          )}
        </div>
      )}

      <div className="arturo-composer">
        <input ref={fileInput} type="file" multiple hidden aria-hidden="true"
               onChange={async (e) => {
                 const picked = Array.from(e.target.files || []);
                 e.target.value = '';                 // re-picking the same file must work
                 if (picked.length === 0) return;
                 setUploading(true); setUploadError(null);
                 for (const f of picked) {
                   const r = await uploadAttachment(f);
                   if (r.ok) setAttachments((prev) => [...prev, { name: r.name, path: r.path, size: r.size }]);
                   else setUploadError(r.error);     // refusals are shown, never swallowed
                 }
                 setUploading(false);
               }} />
        {(attachments.length > 0 || uploading || uploadError) && (
          <div className="arturo-attachments">
            {attachments.map((a, i) => (
              <span key={i} className="attach-chip">
                <Paperclip size={12} /> {describeAttachment(a)}
                <button aria-label={`Remove ${a.name}`}
                        onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}>×</button>
              </span>
            ))}
            {uploading && <span className="attach-note">Uploading…</span>}
            {uploadError && <span className="attach-error">{uploadError}</span>}
          </div>
        )}
        <textarea ref={taRef} rows={1} value={draft} placeholder={`Ask ${name ? 'Arturo' : 'Arturo'}`}
          onChange={(e) => { setDraft(e.target.value); grow(); }} onKeyDown={onKey} aria-label="Message Arturo" />
        <div className="ctrl-row">
          <div className="cluster">
            <button className="circle-btn" aria-label="Attach a file" title="Attach a file"
                    onClick={() => fileInput.current?.click()} disabled={uploading}><Plus size={18} /></button>
            <button className="model-chip" onClick={() => setModelOpen(true)}><b>{model}</b>{eff && <span className="eff">{eff}</span>}</button>
          </div>
          <div className="cluster">
            <button className="circle-btn" aria-label="Voice" title={health?.voice ? 'Voice' : 'Voice needs a vendor key (text-only mode)'} disabled={!health?.voice}><Mic size={16} /></button>
            {draft.trim() ? (
              <button className="circle-btn white" aria-label="Send" onClick={() => void send()} disabled={busy}><ArrowUp size={18} /></button>
            ) : (
              <button className="circle-btn white" aria-label="Voice mode" disabled={!health?.voice}><AudioLines size={16} /></button>
            )}
          </div>
        </div>
      </div>

      {drawer && (
        <>
          <div className="arturo-drawer-back" onClick={() => setDrawer(false)} />
          <nav className="arturo-drawer">
            <div className="brand">OrchestraOS</div>
            {DRAWER.map(([label, to]) => <NavLink key={to} to={to} onClick={() => setDrawer(false)}>{label}</NavLink>)}
            <div className="sect sect-head">
              <span>Conversations</span>
              <button className="drawer-new" onClick={startNewThread}>New</button>
            </div>
            <div className="drawer-threads">
              {threads.length === 0 && <p className="empty">No earlier conversations yet.</p>}
              {threads.map((t) => (
                <button key={t.id} className={t.id === convId.current ? 'thread-row current' : 'thread-row'}
                        onClick={() => void resumeThread(t.id)}>
                  <span className="t-title">{t.title || 'Untitled'}</span>
                  <span className="t-meta">{Math.ceil((t.turns || 0) / 2)}</span>
                </button>
              ))}
            </div>
            <div className="sect">{brain ? `brain: ${brain.kind}${brain.runtime ? ' · ' + brain.runtime : ''} · ${health?.mode || ''}` : 'brain: …'}</div>
          </nav>
        </>
      )}
      <BrainModal isOpen={brainOpen} onClose={() => setBrainOpen(false)} />
      <ModelSelectorSheet open={modelOpen} onClose={() => setModelOpen(false)} />
    </div>
  );
}
