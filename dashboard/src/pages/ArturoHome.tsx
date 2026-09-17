/**
 * ArturoHome — the MAIN page (track T4). A standalone phone shell, not a dashboard page:
 * one thin header (drawer glyph · "Arturo · <model> ⌄" · brain glyph), black canvas with the
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
import { Mic, Plus, ArrowUp, AudioLines } from 'lucide-react';
import '../components/arturo/arturo.css';
import { BrainModal } from '../components/agent/BrainModal';
import { ModelSelectorSheet } from '../components/agent/ModelSelectorSheet';
import { arturoHealth, arturoText, runtimesAvailable, brainLabel, greeting, slugify, newConversationId,
  type ArturoHealth, type RuntimeRow } from '../lib/arturo';

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
  const [drawer, setDrawer] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const convId = useRef<string>(ls(LS_CONV) || '');
  useEffect(() => { if (!convId.current) { convId.current = newConversationId('web'); lsSet(LS_CONV, convId.current); } }, []);
  const feedRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  const startedStep = useRef<Step | null>(null);   // StrictMode double-invokes effects

  useEffect(() => { arturoHealth().then(setHealth); }, []);
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

  async function runtimeStep() {
    const id = say('', { pending: true });
    const [rows, h] = await Promise.all([runtimesAvailable(), arturoHealth()]);
    setHealth(h);
    const authed = rows.filter((r: RuntimeRow) => r.installed && r.authed === true);
    const installedOnly = rows.filter((r: RuntimeRow) => r.installed && r.authed !== true);
    const who = name ? `Nice to meet you, ${name}. ` : '';
    if (h.brain?.kind === 'none' || (authed.length === 0 && h.brain?.kind !== 'api')) {
      const hint = installedOnly.length
        ? `I can see ${installedOnly.map((r) => r.label || r.id).join(', ')} installed but not logged in. `
        : 'I do not see any agent CLI on this machine yet. ';
      patch(id, { pending: false, text: `${who}${hint}Open a terminal on the server and log in to one — \`claude\`, \`codex login\` or \`agy\` — then tap Check again. I run on the CLI you already pay for; no API key needed.`,
        decision: { options: ['Check again'], onPick: () => { setTurns((t) => t.filter((x) => x.id !== id)); void runtimeStep(); } } });
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
    const r = await arturoText(body, convId.current);
    setBusy(false);
    if (!r.ok) {
      patch(id, { pending: false, text: `I could not reach my brain: ${r.error || 'unknown'}. Is \`orchestra up\` running? Check /health on the Arturo service.` });
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
  const model = brainLabel(brain);
  const eff = brain?.kind === 'runtime' ? 'CLI' : brain?.kind === 'api' ? 'API' : '';
  const empty = turns.length === 0;

  return (
    <div className="arturo-shell">
      <header className="arturo-header">
        <button className="arturo-glyph" aria-label="Open menu" onClick={() => setDrawer(true)}>
          <svg width="20" height="16" viewBox="0 0 20 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><line x1="1" y1="2" x2="19" y2="2" /><line x1="1" y1="8" x2="19" y2="8" /><line x1="1" y1="14" x2="19" y2="14" /></svg>
        </button>
        <button className="arturo-title" onClick={() => setModelOpen(true)} aria-label="Select model">
          Arturo <span className="model">· {model}</span> <span className="chev">⌄</span>
        </button>
        <button className="arturo-glyph" aria-label="Open brain" onClick={() => setBrainOpen(true)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M9 18c-3 0-5.5-2.7-5.5-6.5S6 5 9 5s5.5 2.7 5.5 6.5" /><path d="M15 18c3 0 5.5-2.7 5.5-6.5S18 5 15 5" /><path d="M9 5c0-1.7 1.3-3 3-3s3 1.3 3 3" /><path d="M9 18v1.5a1.5 1.5 0 0 0 3 0V18" /><path d="M12 18v1.5a1.5 1.5 0 0 0 3 0V18" /><line x1="12" y1="8.5" x2="12" y2="12" /></svg>
        </button>
      </header>

      <div className="arturo-glow" />

      <div className="arturo-feed" ref={feedRef}>
        {empty ? (
          <div className="arturo-hero">
            <ArturoMark />
            <div className="greet serif">{greeting(name)}</div>
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

      <div className="arturo-composer">
        <textarea ref={taRef} rows={1} value={draft} placeholder={`Ask ${name ? 'Arturo' : 'Arturo'}`}
          onChange={(e) => { setDraft(e.target.value); grow(); }} onKeyDown={onKey} aria-label="Message Arturo" />
        <div className="ctrl-row">
          <div className="cluster">
            <button className="circle-btn" aria-label="Attach" title="Attachments come with the next build"><Plus size={18} /></button>
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
            <div className="sect">{brain ? `brain: ${brain.kind}${brain.runtime ? ' · ' + brain.runtime : ''} · ${health?.mode || ''}` : 'brain: …'}</div>
          </nav>
        </>
      )}
      <BrainModal isOpen={brainOpen} onClose={() => setBrainOpen(false)} />
      <ModelSelectorSheet open={modelOpen} onClose={() => setModelOpen(false)} />
    </div>
  );
}
