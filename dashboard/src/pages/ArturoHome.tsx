/**
 * ArturoHome — the MAIN page (track T4). A standalone phone shell, not a dashboard page:
 * one thin header (settings gear · "Arturo · <model> ⌄" · brain), icons from the
 * shared lucide set so the web pair matches the iOS pair (brain + gearshape) instead of
 * a one-off hand-drawn glyph, black canvas with the
 * accent glow behind the composer, empty state = mark + serif greeting, one two-row composer.
 *
 * Onboarding is Arturo's FIRST THREAD, run by its BRAIN, not scripted here (the operator, 2026-10-08:
 * "I don't want hardcoded arturo questions, just instructions to the llm that powers it"). The page
 * only checks that a brain exists (catalog probe + /api/arturo/health; with none it offers a terminal
 * to log one in), then sends one invisible opener and marks each turn until the SERVER says onboarding
 * is done. The questions, the cards (ask_choices) and the pairing codes (pair_device) all come from
 * the brain's tools; this page renders them and parses nothing.
 * The operator's name is SERVER state (/api/arturo/health .operator.name); localStorage only
 * caches it for the first paint.
 *
 * Every turn goes through POST /api/arturo/text (gateway → :5071/text), which runs the full
 * tool-enabled turn on whichever brain the install has (api key / authed CLI / none).
 */
import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Mic, Plus, ArrowUp, AudioLines, Paperclip, X, PhoneOff } from 'lucide-react';
import { useHandsFreeCall } from '../hooks/useHandsFreeCall';
import '../components/arturo/arturo.css';
import { BrainModal } from '../components/agent/BrainModal';
import { ModelSelectorSheet } from '../components/agent/ModelSelectorSheet';
import { useArturoBrain } from '../stores/arturoBrain';
import { arturoTurn, arturoPrewarm } from '../lib/arturoStream';
import { brainFromThread, describeTurnError, toWireBrain } from '../lib/arturoBrain';
import { arturoHealth, arturoText, runtimesAvailable, brainLabel, greeting, newConversationId,
  isStarting, waitForArturo, STARTING_TEXT, firstStep, onboardingTurn, onboardingDone,
  HANDS_FREE, handsFreeTitle, handsFreeReady, dictateLocked, dictateTitle, ONBOARDING_OPENER, sendStateLabel,
  type ChoiceCard, type PairCard,
  type ArturoHealth, type RuntimeRow, type SendState } from '../lib/arturo';
import { listThreads, loadThread, type ThreadSummary } from '../lib/arturoThreads';
import { hydrateTurns, onboardingConversation, carriesOnboardingMarker, mergeResumeReply, isBusy } from '../lib/arturoResume';
import WebTerminal from '../components/WebTerminal';
import { installCommand } from '../lib/providerConnect';
import { uploadAttachment, attachmentPreamble, describeAttachment, type Attachment } from '../lib/arturoUpload';
import { useDictation } from '../components/arturo/useDictation.ts';
import SpawnedAgentCard from '../components/arturo/SpawnedAgentCard';
import ChoicesCard from '../components/arturo/ChoicesCard';
import PairCodeCard from '../components/arturo/PairCodeCard';
import ToolRun from '../components/arturo/ToolRun';
import { applyTextDelta, applyToolCall, applyToolResult, groupParts, hasToolParts, type TurnPart } from '../lib/turnParts';
import { Brain, Settings } from 'lucide-react';

type Turn = { id: number; role: 'user' | 'arturo'; text: string; tools?: string[]; spawned?: string[]; pending?: boolean; streaming?: boolean; state?: SendState;
  /** A streamed turn that ran tools, in the order it happened: text, tool cards, more text. */
  parts?: TurnPart[];
  decision?: { options: string[]; onPick: (v: string) => void };
  /** A card the brain wrote (ask_choices): picking sends the option's words as the operator's turn. */
  choices?: ChoiceCard;
  /** A pairing code (pair_device). Lives only in this page's state: never in thread history. */
  pairCard?: PairCard };
type Step = 'runtime' | 'onboarding' | 'done';


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
  const [step, setStep] = useState<Step>(firstStep(ls(LS_ONBOARDED) === '1'));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<ArturoHealth | null>(null);
  const [starting, setStarting] = useState(false);   // G15: proxy not up yet (orchestra up boot window)
  const [drawer, setDrawer] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const brainChoice = useArturoBrain((s) => s.choice);
  const chooseBrain = useArturoBrain((s) => s.choose);
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
  // Zero-key dictation (item B): the Mic button transcribes on-device into the draft. Shared
  // hook with the "Ask Arturo" pill so every composer has the same buttons. Separate from the
  // AudioLines "Live voice mode" button, the live call where Arturo talks back (needs a voice key).
  // Dictation needs nothing from OrchestraOS: the browser's own mic permission is its only gate.
  const { mode: dictMode, dictating, note: dictNote, toggle: toggleDictation, stop: stopDictation, clearNote: clearDictNote } =
    useDictation(draft, setDraft, () => taRef.current?.focus());
  // Live voice mode: the same call the "Ask Arturo" pill starts (hooks/useHandsFreeCall). Its
  // captions commit as turns in this thread; a call that cannot start says why, here.
  const call = useHandsFreeCall({
    onFinal: (text, role) => { if (role === 'user') user(text); else say(text); },
    onUnavailable: (reason) => { say(`${HANDS_FREE} could not start: ${reason}`); },
    onEnded: (text) => { say(text); },
  });
  const fileInput = useRef<HTMLInputElement>(null);
  const convId = useRef<string>(ls(LS_CONV) || '');
  useEffect(() => { if (!convId.current) { convId.current = newConversationId('web'); lsSet(LS_CONV, convId.current); } }, []);
  // Warm this conversation's CLI while the operator is still reading or typing, and again if
  // they switch brain (a different model is a different process). The first turn then adopts
  // a live process instead of starting one.
  useEffect(() => { arturoPrewarm(convId.current, toWireBrain(brainChoice)); }, [brainChoice]);
  useEffect(() => { if (drawer) void listThreads().then(setThreads); }, [drawer]);
  // On a phone the drawer covers the composer, and the only way out was a ~110 px sliver of dark
  // page that does not read as a control. Escape closes it; there is also a close button inside.
  useEffect(() => {
    if (!drawer) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawer(false); };
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [drawer]);

  /** Resume a previous conversation: its turns come from the server, so "pick up right where
   *  we left off" holds across a reload, another surface, and a service restart. */
  async function resumeThread(id: string) {
    const t = await loadThread(id);
    if (!t) return;
    convId.current = id; lsSet(LS_CONV, id);
    // A thread answers on the brain it last used; one that used the default brain goes back to it.
    const resumedBrain = brainFromThread(t, {});
    chooseBrain(resumedBrain);
    arturoPrewarm(id, toWireBrain(resumedBrain));
    // The page's own opener is not the operator's words: hidden here too, on every reload. Opening a
    // thread does NOT end onboarding (it used to): done is the server's word only.
    setTurns(hydrateTurns(t.turns).map((x) => ({ id: nextId.current++, ...x })));
    setDrawer(false);
  }

  /** New thread — the one you leave stays in the list rather than becoming unreachable. */
  function startNewThread() {
    convId.current = newConversationId('web'); lsSet(LS_CONV, convId.current);
    arturoPrewarm(convId.current, toWireBrain(brainChoice));
    setTurns([]);
    setDrawer(false);
  }

  const lastUserIdx = (() => { for (let i = turns.length - 1; i >= 0; i--) if (turns[i].role === 'user') return i; return -1; })();
  const feedRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  const startedStep = useRef<Step | null>(null);   // StrictMode double-invokes effects
  // The onboarding thread (the server's, so every browser resumes the same one) and the opener while it
  // runs: a send typed meanwhile waits for it instead of racing it (DEC-1791511578959986).
  const onbConv = useRef<string>('');
  const opener = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const h = await arturoHealth();
      if (!alive) return;
      if (h.operator?.name) { setName(h.operator.name); lsSet(LS_NAME, h.operator.name); }   // the server knows the operator
      // The server's flag (finish_onboarding) ORed with this browser's: every existing install already
      // has the local one, so nobody is sent through onboarding again.
      if (h.onboarded) { lsSet(LS_ONBOARDED, '1'); setStep('done'); }
      if (h.onboarding_conversation) onbConv.current = h.onboarding_conversation;
      // The conversation this browser was in is on the server: show it (it used to open empty, and
      // only a sidebar round trip brought the history back). Onboarding hydrates its own thread.
      if (h.onboarded || ls(LS_ONBOARDED) === '1') {
        const t = convId.current ? await loadThread(convId.current) : null;
        if (alive && t) setTurns(hydrateTurns(t.turns).map((x) => ({ id: nextId.current++, ...x })));
      }
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
    if (step === 'runtime') void runtimeStep();
    if (step === 'onboarding') void openOnboarding();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  /** The brain speaks first: one invisible opener, marked onboarding_open (the server never lets it
   *  create anything, and the page never shows it as the operator's words). */
  function openOnboarding() {
    opener.current = runOpener().finally(() => { opener.current = null; });
  }

  /** Into the onboarding thread, with its history on screen BEFORE the opener runs. On a thread that
   *  already holds the opener the server answers as a RETURN (no greeting, the next step, its card
   *  again) and stores nothing; its reply then refreshes the unanswered question in place. */
  async function runOpener(switched = false) {
    const conv = onboardingConversation(onbConv.current, convId.current);
    if (conv !== convId.current) { convId.current = conv; lsSet(LS_CONV, conv); arturoPrewarm(conv, toWireBrain(brainChoice)); }
    const t = await loadThread(conv);
    const stored = t ? hydrateTurns(t.turns).map((x) => ({ id: nextId.current++, ...x })) : [];
    // A message the operator sent while the history loaded stays, after it (it waits for this opener).
    setTurns((prev) => [...stored, ...prev.filter((x) => x.role === 'user' && x.state)]);
    const id = nextId.current++;
    // The opener's bubble goes ABOVE anything typed meanwhile: it answers first.
    setTurns((prev) => {
      const at = prev.findIndex((x) => x.role === 'user' && x.state);
      const bubble = { id, role: 'arturo' as const, text: '', pending: true };
      return at < 0 ? [...prev, bubble] : [...prev.slice(0, at), bubble, ...prev.slice(at)];
    });
    let r = await arturoText(onboardingTurn('onboarding_open', ONBOARDING_OPENER), conv);
    for (let tries = 0; isBusy(r) && tries < 90; tries++) {
      await new Promise((ok) => setTimeout(ok, 2000));
      r = await arturoText(onboardingTurn('onboarding_open', ONBOARDING_OPENER), conv);
    }
    if (r.ok && r.onboarding_conversation) onbConv.current = r.onboarding_conversation;
    if (r.ok && r.onboarding_conversation && (r.switch || r.onboarding_conversation !== conv) && !switched) {
      // Another browser started the first run (or won the pin a moment before this one): continue THAT thread.
      setTurns((all) => all.filter((x) => x.role === 'user' && x.state));
      return runOpener(true);
    }
    if (r.ok && r.resumed) {
      setTurns((all) => mergeResumeReply(all, id, { text: r.reply_text || '', choices: r.choices, pairCard: r.pair_card }));
    } else {
      patch(id, { pending: false, text: r.ok ? (r.reply_text || '(no reply)') : 'I could not start just now. Send me anything and I will pick it up.',
        tools: r.tools_called, spawned: r.spawned, choices: r.choices, pairCard: r.pair_card });
    }
    if (onboardingDone(r)) { lsSet(LS_ONBOARDED, '1'); setStep('done'); }
  }

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
    const known = h.operator?.name || name;
    if (h.operator?.name && h.operator.name !== name) { setName(h.operator.name); lsSet(LS_NAME, h.operator.name); }
    const who = known ? `Good to see you, ${known}. ` : '';
    if (h.onboarded) { setTurns((t) => t.filter((x) => x.id !== id)); lsSet(LS_ONBOARDED, '1'); setStep('done'); return; }
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
    // A brain exists: from here the brain runs the onboarding. The page says nothing of its own.
    setTurns((t) => t.filter((x) => x.id !== id));
    setStep('onboarding');
  }

  const user = (text: string, state?: SendState) => { const id = nextId.current++; setTurns((t) => [...t, { id, role: 'user', text, state }]); return id; };

  /** Send the composer's text, or `picked`: the words of a card option the operator tapped. */
  async function send(picked?: string) {
    const text = (picked ?? draft).trim();
    if (!text || busy) return;
    const waitFor = opener.current;           // the opener is still running: queue behind it
    if (picked === undefined) {
      if (dictating) stopDictation();    // the sent text is final; don't re-append into the empty box
      clearDictNote();
      setDraft('');
    }
    // A card is answered once: by a tap, or by whatever the operator typed instead.
    setTurns((t) => t.map((x) => x.choices ? { ...x, choices: undefined } : x));
    const uid = user(text, 'sending');   // the bubble appears NOW; the box is already empty
    if (waitFor) {
      setBusy(true); await waitFor;
      // The opener may have brought a card back; this message answers it, so it goes like any other.
      setTurns((t) => t.map((x) => x.choices ? { ...x, choices: undefined } : x));
    }
    // While onboarding, every turn is a BRAIN turn under the playbook: the marker makes the proxy add
    // it, the brain understands the reply (dictated or typed, any phrasing) and acts with a tool or
    // asks again in its own words. Nothing is parsed here.
    const isOnboarding = carriesOnboardingMarker(step, convId.current, onbConv.current);
    const body = text;
    setBusy(true);
    const id = say('', { pending: true });
    // The path rides in front of the message: Arturo runs on this machine with tool
    // access, so a path is openable — a filename alone would be decoration.
    const pre = attachmentPreamble(attachments);
    const withFiles = pre ? `${pre}\n\n${body}` : body;
    // The onboarding marker is applied LAST so it is always line 1 — the proxy anchors on it
    // (an attached file must not push it down; peer review DEC-1790048447550594).
    const sent = isOnboarding ? onboardingTurn('onboarding', withFiles) : withFiles;
    setAttachments([]);
    const onSent = () => patch(uid, { state: 'sent' });
    const turnBrain = toWireBrain(brainChoice);
    // Stream the reply into the pending bubble as it is written. `streamed` is what the
    // operator has already read, so a failure can clear it rather than leave half a sentence.
    let streamed = '';
    // The turn as it happens — text, tool cards, more text (DEC-1790747153605131).
    let parts: TurnPart[] = [];
    // pending renders the thinking dot INSTEAD of the text, so the first delta ends it —
    // otherwise the reply streams into a bubble nobody can see (found on staging).
    const onDelta = (t: string) => {
      streamed += t;
      parts = applyTextDelta(parts, t);
      patch(id, { pending: false, streaming: true, text: streamed, parts });
    };
    // A tool starting is the turn visibly doing something, so it ends the thinking dot too.
    const onToolCall = (c: Parameters<typeof applyToolCall>[1]) => {
      parts = applyToolCall(parts, c);
      patch(id, { pending: false, streaming: true, parts });
    };
    const onToolResult = (res: Parameters<typeof applyToolResult>[1]) => {
      parts = applyToolResult(parts, res);
      patch(id, { parts });
    };
    // The whole-reply path is taking over (the server's fallback, or ours): it answers from the
    // top, so what this attempt showed goes. Nothing shown yet = nothing to clear.
    const onReset = () => {
      if (!streamed && parts.length === 0) return;
      streamed = ''; parts = [];
      patch(id, { pending: true, streaming: false, text: '', parts: undefined });
    };
    const streamOpts = { onSent, brain: turnBrain, onDelta, onToolCall, onToolResult, onReset };
    let r = await arturoTurn(sent, convId.current, null, streamOpts);
    // Another tab or device is mid-turn in this conversation: the server serializes, so wait and resend.
    for (let tries = 0; isBusy(r) && tries < 90; tries++) {
      await new Promise((ok) => setTimeout(ok, 2000));
      r = await arturoTurn(sent, convId.current, null, streamOpts);
    }
    if (!r.ok) { streamed = ''; parts = []; patch(id, { pending: true, streaming: false, text: '', parts: undefined }); }
    if (!r.ok && isStarting(r)) {          // G15: still booting -> say so, wait for health, retry once
      patch(id, { pending: false, text: STARTING_TEXT, parts: undefined });
      const ready = await waitForArturo();
      setHealth(ready);
      if (ready.ok) { parts = []; patch(id, { pending: true, text: '', parts: undefined }); r = await arturoTurn(sent, convId.current, null, streamOpts); }
    }
    setBusy(false);
    patch(uid, { state: r.ok ? 'acked' : 'failed' });
    if (!r.ok) {
      const chosenErr = describeTurnError(r);
      patch(id, { pending: false, text: isStarting(r)
        ? 'I am still starting up and could not answer yet — give `orchestra up` a moment and send that again.'
        : chosenErr ? chosenErr.message
        : `I could not reach my brain: ${r.error || 'unknown'}. Is \`orchestra up\` running? Check /health on the Arturo service.` });
      return;
    }
    patch(id, { pending: false, streaming: false, text: r.reply_text || streamed || '(no reply)', tools: r.tools_called, spawned: r.spawned,
      choices: r.choices, pairCard: r.pair_card,
      // Kept only when tools ran: a turn of text alone renders exactly as it always has.
      parts: hasToolParts(parts) ? parts : undefined });
    if (r.operator?.name && r.operator.name !== name) { setName(r.operator.name); lsSet(LS_NAME, r.operator.name); }
    // A device the server saw connect (check_paired): its card drops the code and says "paired".
    const nowPaired = r.paired || [];
    if (nowPaired.length) {
      setTurns((t) => t.map((x) => x.pairCard && nowPaired.includes(x.pairCard.device_id)
        ? { ...x, pairCard: { ...x.pairCard, code: '', paired: true } } : x));
    }
    // By EFFECT: onboarding ends when the server says so (finish_onboarding wrote its flag), never on
    // a reply's wording.
    if (isOnboarding && onboardingDone(r)) { lsSet(LS_ONBOARDED, '1'); setStep('done'); }
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !(e.nativeEvent as KeyboardEvent).isComposing) { e.preventDefault(); void send(); }
  };
  const grow = () => { const el = taRef.current; if (!el) return; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 140) + 'px'; };
  useEffect(() => { if (dictating) grow(); }, [draft, dictating]);   // live text grows the box

  const brain = health?.brain;
  // The chip shows the CHOSEN brain when there is one; otherwise what the install defaults to.
  const model = starting ? 'starting…' : brainChoice ? brainChoice.label : brainLabel(brain);
  const eff = brainChoice ? 'CLI' : brain?.kind === 'runtime' ? 'CLI' : brain?.kind === 'api' ? 'API' : '';
  const empty = turns.length === 0 && !call.inCall;

  return (
    <div className="arturo-shell">
      <header className="arturo-header" inert={drawer || undefined}>
        <button className="arturo-glyph" aria-label={drawer ? 'Close menu' : 'Open settings'} aria-expanded={drawer}
                onClick={() => setDrawer((d) => !d)}>
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

      {/* While the modal drawer is open the page behind it must not be focusable or typable —
          on a phone the composer sits under the overlay (gm/jev-helper first-run report). */}
      <div className="arturo-feed" ref={feedRef} inert={drawer || undefined}>
        {empty ? (
          <div className="arturo-hero">
            <ArturoMark />
            <div className="greet serif">{greeting(name)}</div>
            {starting && <div className="tools" style={{ color: 'rgba(255,255,255,0.45)', fontSize: 13 }}>{STARTING_TEXT}</div>}
          </div>
        ) : turns.map((t, i) => t.role === 'user' ? (
          <div key={t.id} className="turn-user"><div className="bubble-user">{t.text}</div>
            {(t.state === 'failed' || (t.state && i === lastUserIdx)) && <div className={`turn-state ${t.state}`}>{sendStateLabel(t.state)}</div>}</div>
        ) : (
          <div key={t.id} className="turn-assistant">
            <ArturoMark className="mark-sm" />
            {t.pending ? <span className="thinking" aria-label="thinking" />
              : hasToolParts(t.parts) ? (
                // In the order it happened. Indices are stable: parts only ever grow at the end.
                <div className="turn-parts">
                  {groupParts(t.parts!).map((g, gi) => g.kind === 'tools'
                    ? <ToolRun key={gi} tools={g.tools} />
                    : <div key={gi} className="txt">{renderText(g.text)}</div>)}
                </div>
              )
              : <div className="txt">{renderText(t.text)}</div>}
            {!hasToolParts(t.parts) && t.tools && t.tools.length > 0 && <div className="tools">ran {t.tools.join(', ')}</div>}
            <SpawnedAgentCard ids={t.spawned} />
            {t.pairCard && <PairCodeCard card={t.pairCard} />}
            {t.choices && t.choices.multi && (
              <ChoicesCard options={t.choices.options} note={t.choices.note} exclusive={t.choices.exclusive} onSubmit={(picked) => { void send(picked.join(', ')); }} />
            )}
            {t.choices && !t.choices.multi && (
              <div className="decision-card">
                {t.choices.note && <div className="decision-note">{t.choices.note}</div>}
                {t.choices.options.map((o) => (
                  <button key={o} className="decision-opt" onClick={() => { void send(o); }}>{o}<span className="r" /></button>
                ))}
                <div className="decision-freetext">✎ Or type your own answer below…</div>
              </div>
            )}
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
        {call.liveUser && <div className="turn-user"><div className="bubble-user" style={{ opacity: 0.7 }}>{call.liveUser}…</div></div>}
        {call.liveArturo && <div className="turn-assistant"><ArturoMark className="mark-sm" /><div className="txt" style={{ opacity: 0.7 }}>{call.liveArturo}…</div></div>}
        {call.callState === 'connecting' && <div className="turn-assistant"><ArturoMark className="mark-sm" /><span className="thinking" aria-label="connecting the call" /></div>}
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

      <div className="arturo-composer" inert={drawer || undefined}>
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
        {(attachments.length > 0 || uploading || uploadError || dictNote) && (
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
            {dictNote && <span className="attach-error">{dictNote}</span>}
          </div>
        )}
        <textarea ref={taRef} rows={1} value={draft} placeholder="Ask Arturo"
          onChange={(e) => { setDraft(e.target.value); grow(); }} onKeyDown={onKey} aria-label="Message Arturo" />
        <div className="ctrl-row">
          <div className="cluster">
            <button className="circle-btn" aria-label="Attach a file" title="Attach a file"
                    onClick={() => fileInput.current?.click()} disabled={uploading}><Plus size={18} /></button>
            <button className="model-chip" onClick={() => setModelOpen(true)} aria-label={`Brain: ${model}. Change`} title={`Answering on ${model}`}><b>{model}</b>{eff && <span className="eff">{eff}</span>}</button>
          </div>
          <div className="cluster">
            <button className={dictMode === 'idle' ? 'circle-btn' : `circle-btn ${dictMode}`}
                    aria-label={dictMode === 'listening' ? 'Stop dictation' : dictMode === 'recording' ? 'Stop recording' : dictMode === 'transcribing' ? 'Transcribing' : 'Dictate'}
                    aria-pressed={dictating} title={dictateTitle(call.inCall, dictMode)}
                    onClick={toggleDictation} disabled={dictateLocked(call.inCall, dictMode)}><Mic size={16} /></button>
            {call.inCall ? (
              <button className="circle-btn white" aria-label="End call" aria-pressed title="End call"
                      onClick={() => void call.toggle({ route: '/' })}><PhoneOff size={18} /></button>
            ) : draft.trim() ? (
              <button className="circle-btn white" aria-label="Send" onClick={() => void send()} disabled={busy}><ArrowUp size={18} /></button>
            ) : (
              <button className="circle-btn white" aria-label={HANDS_FREE} title={handsFreeTitle(handsFreeReady(health))}
                      disabled={!handsFreeReady(health)} onClick={() => void call.toggle({ route: '/' })}><AudioLines size={16} /></button>
            )}
          </div>
        </div>
      </div>

      {drawer && (
        <>
          <div className="arturo-drawer-back" onClick={() => setDrawer(false)} />
          <nav className="arturo-drawer" aria-label="Menu">
            <div className="drawer-top">
              <div className="brand">OrchestraOS</div>
              <button className="drawer-close" aria-label="Close menu" onClick={() => setDrawer(false)}>
                <X size={18} />
              </button>
            </div>
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
      <ModelSelectorSheet
        open={modelOpen}
        onClose={() => setModelOpen(false)}
        selection={brainChoice ? { providerId: brainChoice.provider, modelId: brainChoice.model } : null}
        onPick={(p) => chooseBrain({ provider: p.providerId, model: p.modelId, label: p.modelLabel })}
        onPickDefault={() => chooseBrain(null)}
      />
    </div>
  );
}
