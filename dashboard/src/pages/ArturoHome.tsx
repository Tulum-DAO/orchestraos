/**
 * ArturoHome — the MAIN page (track T4). Its own phone shell, inside the dashboard's layout (sidebar beside it):
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
import { useContext, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Mic, MicOff, Plus, ArrowUp, AudioLines, Paperclip, PhoneOff } from 'lucide-react';
import { useHandsFreeCall } from '../hooks/useHandsFreeCall';
import '../components/arturo/arturo.css';
import { ArturoPanel } from '../components/arturo/ArturoPanel';
import Markdown from '../components/chat/Markdown';
import { ModelSelectorSheet } from '../components/agent/ModelSelectorSheet';
import { useArturoBrain } from '../stores/arturoBrain';
import { arturoTurn, arturoPrewarm } from '../lib/arturoStream';
import { describeTurnError, toWireBrain } from '../lib/arturoBrain';
import { arturoHealth, runtimesAvailable, brainLabel, greeting, newConversationId,
  isStarting, isArturoDisabled, waitForArturo, STARTING_TEXT, ARTURO_DISABLED_TEXT, sendOutcomeText, firstStep, onboardingTurn, onboardingDone,
  HANDS_FREE, handsFreeTitle, handsFreeReady, dictateLocked, dictateTitle, ONBOARDING_OPENER, sendStateLabel,
  type ChoiceCard, type PairCard,
  type ArturoHealth, type RuntimeRow, type SendState } from '../lib/arturo';
import { loadThread, dismissCard } from '../lib/arturoThreads';
import { hydrateTurns, onboardingConversation, carriesOnboardingMarker, mergeResumeReply, isBusy } from '../lib/arturoResume';
import WebTerminal from '../components/WebTerminal';
import { installCommand } from '../lib/providerConnect';
import { uploadAttachment, attachmentPreamble, describeAttachment, attachmentImages, type Attachment } from '../lib/arturoUpload';
import { arturoComposerText, arturoShownText } from '../lib/arturoComposer';
import { useDictation } from '../components/arturo/useDictation.ts';
import SpawnedAgentCard from '../components/arturo/SpawnedAgentCard';
import ChoiceDecisionCard from '../components/chat/ChoiceCard';
import { isCardEcho } from '../lib/choiceCard';
import PairCodeCard from '../components/arturo/PairCodeCard';
import ToolCalls from '../components/chat/ToolCalls';
import { fromPart } from '../lib/toolCalls';
import CopyReply from '../components/arturo/CopyReply';
import { replyCopyText } from '../lib/arturoCopy';
import JobTray from '../components/arturo/JobTray';
import { useStickToBottom } from '../lib/useStickToBottom';
import { currentThread, setCurrentThread, subscribeCurrentThread } from '../lib/arturoCurrent';
import { ArturoCallCard } from '../components/arturo/ArturoCallCard';
import AgentIdentityPill from '../components/avatar/AgentIdentityPill';
import { groupCalls } from '../lib/arturoCallGroups';
import { hasVoiceCallMarker, parseVoiceSegments } from '../lib/voiceCall';
import { applyTextDelta, applyToolCall, applyToolResult, groupParts, hasToolParts, type TurnPart } from '../lib/turnParts';
import { Settings, ChevronRight, X } from 'lucide-react';
import { AgentAvatar } from '../components/avatar/AgentAvatar';
import { LookPickerPopover } from '../components/avatar/LookPicker';
import { useAgentRecord, setArturoActivity, setArturoProvider, ARTURO_ID } from '../components/avatar/agentState';
import { arturoProvider } from '../lib/avatarStatus';
import { applyAssistantName, assistantName, named, useAssistantName } from '../lib/assistantName';
import { canChangeLook, showsLookCard, readDismissed, writeDismissed } from '../lib/avatarLooks';
import { HeaderEndSlotContext } from '../lib/headerSlot';
import { SidebarMenuButton } from '../components/SidebarMenuButton';

type Turn = { id: number; role: 'user' | 'arturo'; text: string; tools?: string[]; images?: string[]; spawned?: string[]; pending?: boolean; streaming?: boolean; state?: SendState;
  /** A streamed turn that ran tools, in the order it happened: text, tool cards, more text. */
  parts?: TurnPart[];
  decision?: { options: string[]; onPick: (v: string) => void };
  /** A card the brain wrote (ask_choices): picking sends the option's words as the operator's turn. */
  choices?: ChoiceCard;
  /** What was sent in answer to this turn's card: the card stays, showing it. */
  answered?: string;
  /** Its card was closed unanswered (kept on the server with the reply). */
  dismissed?: boolean;
  /** A card's own answer (a tap): shown on the answered card, so no bubble is drawn unless the send fails. */
  cardEcho?: boolean;
  /** A pairing code (pair_device). Lives only in this page's state: never in thread history. */
  pairCard?: PairCard;
  /** Tools Arturo ran during a live call, as they happened (parts = tool cards only). */
  callTools?: boolean;
  /** Spoken on a call (a caption committed here, or a stored turn of mode "voice"): it goes in the call's card. */
  voice?: boolean;
  /** The call-ended line of this call id (stored threads tag it): closes the call's card. */
  callEnd?: string;
  ts?: number };
type Step = 'runtime' | 'onboarding' | 'done';


const LS_NAME = 'orchestra.arturo.name';
const LS_ONBOARDED = 'orchestra.arturo.onboarded';

function ls(key: string): string | null { try { return localStorage.getItem(key); } catch { return null; } }
function lsSet(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* private mode */ } }
function lsStore(): Storage | null { try { return localStorage; } catch { return null; } }

/** Where the input sits from the home hero's face: straight down. */
const HERO_GLANCE_AT = { x: 0, y: 0.9 };

/** The phone layout (below the dashboard's md breakpoint): Arturo's bar is taller there, with a bigger pill. */
const PHONE_BAR_QUERY = '(max-width: 47.99rem)';
function subscribePhoneBar(cb: () => void) {
  const mq = window.matchMedia(PHONE_BAR_QUERY);
  mq.addEventListener('change', cb);
  return () => mq.removeEventListener('change', cb);
}
const isPhoneBar = () => window.matchMedia(PHONE_BAR_QUERY).matches;

function renderText(t: string) {
  // The one Markdown renderer every agent's messages use (lists, tables, links, code), at the reply's own size.
  return <Markdown text={t} size="modern" />;
}

/** An ended call's "Call ended (vc_…). [voice-call: …]" line is a call card (collapsed; opens to the transcript),
 *  never the raw marker. Any other words in the turn stay as text. */
const CALL_ENDED_RE = /^\s*Call ended \(vc_[A-Za-z0-9_-]+\)\.?\s*$/;
function renderReply(t: string) {
  if (!hasVoiceCallMarker(t)) return <div className="txt">{renderText(t)}</div>;
  return (
    <div className="txt">
      {parseVoiceSegments(t).map((seg, i) => seg.kind === 'voicecall'
        ? <ArturoCallCard key={i} callId={seg.callId} turns={[]} live={false} />
        : CALL_ENDED_RE.test(seg.text) ? null : <div key={i}>{renderText(seg.text)}</div>)}
    </div>
  );
}

export default function ArturoHome() {
  const registerEndSlot = useContext(HeaderEndSlotContext);
  const [name, setName] = useState<string>(ls(LS_NAME) || '');
  const [step, setStep] = useState<Step>(firstStep(ls(LS_ONBOARDED) === '1'));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<ArturoHealth | null>(null);
  const [starting, setStarting] = useState(false);   // G15: proxy not up yet (orchestra up boot window)
  const [panelOpen, setPanelOpen] = useState(false);
  const arturoRec = useAgentRecord(ARTURO_ID);
  const phoneBar = useSyncExternalStore(subscribePhoneBar, isPhoneBar, () => false);
  const aname = useAssistantName();
  const canSaveLook = canChangeLook();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [lookDismissed, setLookDismissed] = useState(() => readDismissed(lsStore(), ARTURO_ID));
  const showLookCard = showsLookCard({ chosen: arturoRec.chosen, canSave: canSaveLook, dismissed: lookDismissed });
  const dismissLookCard = () => { writeDismissed(lsStore(), ARTURO_ID); setLookDismissed(true); };
  const gearRef = useRef<HTMLButtonElement>(null);
  const [modelOpen, setModelOpen] = useState(false);
  const brainChoice = useArturoBrain((s) => s.choice);
  const chooseBrain = useArturoBrain((s) => s.choose);
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
  // hook with the Arturo pane so every composer has the same buttons. Separate from the
  // AudioLines "Live voice mode" button, the live call where Arturo talks back (needs a voice key).
  // Dictation needs nothing from OrchestraOS: the browser's own mic permission is its only gate.
  const { mode: dictMode, dictating, note: dictNote, toggle: toggleDictation, stop: stopDictation, clearNote: clearDictNote } =
    useDictation(draft, setDraft, () => taRef.current?.focus());
  // Live voice mode: the same call the Arturo pane starts (hooks/useHandsFreeCall). Its
  // captions commit as turns in this thread; a call that cannot start says why, here.
  const call = useHandsFreeCall({
    // Stamped with the time they arrive, so the call's card has its length before the saved call record does.
    onFinal: (text, role) => { const at = { voice: true, ts: Date.now() / 1000 }; if (role === 'user') user(text, undefined, at); else say(text, at); },
    onUnavailable: (reason) => { say(`${HANDS_FREE} could not start: ${reason}`); },
    onEnded: (text) => { say(text, { ts: Date.now() / 1000 }); },
    // A tool run during a call sits in the thread where it happened: after the caller's line, before Arturo's
    // reply. Back-to-back tools share one collapsed run.
    onTool: (e) => setTurns((prev) => {
      if (e.phase === 'start') {
        const call = { call_id: e.callId, name: e.name, args_summary: e.argsSummary };
        const last = prev[prev.length - 1];
        if (last?.callTools) return [...prev.slice(0, -1), { ...last, parts: applyToolCall(last.parts || [], call) }];
        return [...prev, { id: nextId.current++, role: 'arturo', text: '', callTools: true, parts: applyToolCall([], call) }];
      }
      const at = prev.findLastIndex((t) => t.callTools && t.parts?.some((p) => p.kind === 'tool' && p.callId === e.callId));
      if (at < 0) return prev;
      const next = [...prev];
      next[at] = { ...next[at], parts: applyToolResult(next[at].parts || [], { call_id: e.callId, name: e.name, ok: !!e.ok, summary: e.summary }) };
      return next;
    }),
  });
  // When this page's call went live, for the live card's running clock (connecting does not count).
  const [callSince, setCallSince] = useState<number | null>(null);
  useEffect(() => {
    if (call.callState === 'live') setCallSince((t) => t ?? Date.now());
    else if (!call.inCall) setCallSince(null);
  }, [call.callState, call.inCall]);
  const fileInput = useRef<HTMLInputElement>(null);
  const convId = useRef<string>(currentThread() || '');
  const [jobKick, setJobKick] = useState(0);
  const lastShown = useRef<string>('');
  /** A background job ended: its result was posted into the thread server-side; show the thread as it is now. */
  const reloadThread = async () => {
    const id = convId.current;
    const t = id ? await loadThread(id) : null;
    if (id !== convId.current) return;               // switched again while it loaded
    const switched = lastShown.current !== id;
    lastShown.current = id;
    setTurns(t ? hydrateTurns(t.turns).map((x) => ({ id: nextId.current++, ...x })) : []);   // a new thread is empty
    if (switched) requestAnimationFrame(() => stick.toBottom());   // another thread opened: start at its end
  };
  useEffect(() => { if (!convId.current) { convId.current = newConversationId('web'); setCurrentThread(convId.current); } }, []);
  // The sidebar, the pane or another tab switched threads: the home follows and shows that thread.
  useEffect(() => subscribeCurrentThread((id) => {
    if (id === convId.current) return;
    convId.current = id;
    void reloadThread();
  }), []); // eslint-disable-line react-hooks/exhaustive-deps
  // Warm this conversation's CLI while the operator is still reading or typing, and again if
  // they switch brain (a different model is a different process). The first turn then adopts
  // a live process instead of starting one.
  useEffect(() => { arturoPrewarm(convId.current, toWireBrain(brainChoice)); }, [brainChoice]);
  /** New thread — the one you leave stays in the sidebar's Chats rather than becoming unreachable. */
  function startNewThread() {
    convId.current = newConversationId('web'); setCurrentThread(convId.current);
    arturoPrewarm(convId.current, toWireBrain(brainChoice));
    setTurns([]);
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
  // New turns and live captions push the feed up only while you're at the bottom; scrolled up, it stays put.
  const stick = useStickToBottom(feedRef);
  useEffect(() => { stick.follow(); }, [turns]); // eslint-disable-line react-hooks/exhaustive-deps
  // A live call's captions grow below the last turn: follow them, or they stay below the fold until the
  // turn commits (they looked like they only appeared once each side finished speaking).
  useEffect(() => { stick.follow(); }, [call.liveUser, call.liveArturo]); // eslint-disable-line react-hooks/exhaustive-deps

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
    if (conv !== convId.current) { convId.current = conv; setCurrentThread(conv); arturoPrewarm(conv, toWireBrain(brainChoice)); }
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
    // Whole, through the one send path: a busy thread or a slow reply is resolved by this opener's own id.
    const r = await arturoTurn(onboardingTurn('onboarding_open', ONBOARDING_OPENER), conv, null, { stream: false });
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
    if (isArturoDisabled(h)) {
      setHealth(h);
      patch(id, { pending: false, text: named(ARTURO_DISABLED_TEXT, assistantName()) });
      return;
    }
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

  const user = (text: string, state?: SendState, extra: Partial<Turn> = {}) => { const id = nextId.current++; setTurns((t) => [...t, { id, role: 'user', text, state, ...extra }]); return id; };

  /** Send the composer's text, or `picked`: the words of a card option the operator tapped. */
  async function send(picked?: string) {
    const text = picked === undefined
      ? arturoComposerText(draft, attachments.length)
      : picked.trim();
    if (!text || busy) return;
    const waitFor = opener.current;           // the opener is still running: queue behind it
    if (picked === undefined) {
      if (dictating) stopDictation();    // the sent text is final; don't re-append into the empty box
      clearDictNote();
      setDraft('');
    }
    // A card is answered once: by a tap, or by whatever the operator typed instead.
    setTurns((t) => t.map((x) => x.choices && !x.answered && !x.dismissed ? { ...x, answered: text } : x));
    // the bubble appears NOW; the box is already empty. It shows what they typed, never the attachments' filler.
    // A tap on a card is shown on the answered card instead (no bubble unless the send fails).
    const openCard = picked === undefined ? undefined : [...turns].reverse().find((x) => x.choices && !x.answered && !x.dismissed)?.choices;
    const uid = user(picked === undefined ? arturoShownText(draft) : text, 'sending',
      openCard && isCardEcho(openCard, text) ? { cardEcho: true } : {});
    requestAnimationFrame(() => stick.toBottom());      // you sent it: back to the bottom
    const sentImages = attachmentImages(attachments);
    if (sentImages.length) setTurns((t) => t.map((x) => x.id === uid ? { ...x, images: sentImages } : x));
    if (waitFor) {
      setBusy(true); await waitFor;
      // The opener may have brought a card back; this message answers it, so it goes like any other.
      setTurns((t) => t.map((x) => x.choices && !x.answered && !x.dismissed ? { ...x, answered: text } : x));
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
    // One send, one id (DEC-1791518421640932): busy, a hop still starting, a timeout or a dropped stream are
    // resolved by asking the server what became of THIS send; it runs at most once.
    const r = await arturoTurn(sent, convId.current, null, {
      onSent, brain: turnBrain, onDelta, onToolCall, onToolResult, onReset,
      // G15: still booting -> say so, wait for health; the send then goes again with the same id.
      onStarting: async () => {
        patch(id, { pending: false, text: STARTING_TEXT, parts: undefined });
        const ready = await waitForArturo();
        setHealth(ready);
        parts = []; streamed = '';
        patch(id, { pending: true, text: '', parts: undefined });
        return ready.ok;
      },
    });
    setJobKick((k) => k + 1);        // a job this turn started shows in the tray at once
    if (!r.ok) { streamed = ''; parts = []; patch(id, { pending: true, streaming: false, text: '', parts: undefined }); }
    setBusy(false);
    patch(uid, { state: r.ok ? 'acked' : 'failed' });
    if (!r.ok) {
      const chosenErr = describeTurnError(r);
      patch(id, { pending: false, text: sendOutcomeText(r) ?? (isStarting(r)
        ? 'I am still starting up and could not answer yet — give `orchestra up` a moment and send that again.'
        : chosenErr ? chosenErr.message
        : isBusy(r) ? 'I am still answering an earlier message in this conversation (another tab or device). Send this again once that reply is in.'
        : `I could not reach my brain: ${r.error || 'unknown'}. Is \`orchestra up\` running? Check /health on the ${aname} service.`) });
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
  const arturoDisabled = isArturoDisabled(health);
  // The chip shows the CHOSEN brain when there is one; otherwise what the install defaults to.
  const model = arturoDisabled ? 'turned off' : starting ? 'starting…' : brainChoice ? brainChoice.label : brainLabel(brain);
  const eff = brainChoice ? 'CLI' : brain?.kind === 'runtime' ? 'CLI' : brain?.kind === 'api' ? 'API' : '';
  // Arturo's avatar works while a turn is in flight here or a call is live (agentState.ts adds its steps).
  useEffect(() => { setArturoActivity({ busy, inCall: call.inCall }); }, [busy, call.inCall]);
  useEffect(() => { if (brain) setArturoProvider(arturoProvider(brain, brainChoice)); }, [brain, brainChoice]);
  useEffect(() => { if (health?.ok) applyAssistantName(health.assistant_name); }, [health]);
  useEffect(() => () => setArturoActivity({ busy: false, inCall: false }), []);
  const empty = turns.length === 0 && !call.inCall;
  const lookCard = showLookCard ? (
    <div key="lookcard" className="arturo-lookcard la-on-card">
      <button type="button" className="arturo-lookcard-open" onClick={() => setPickerOpen(true)}>
        <AgentAvatar agentId={ARTURO_ID} role="activity" size={24} label={aname} />
        <span>Pick a look for {aname}</span>
        <ChevronRight size={16} aria-hidden />
      </button>
      <button type="button" className="arturo-lookcard-x" aria-label="Dismiss" onClick={dismissLookCard}><X size={14} /></button>
    </div>
  ) : null;

  return (
    <div className="arturo-shell">
      <header className="arturo-header">
        {/* The sidebar button (opens it on a phone, collapses it on a desktop), then a fresh conversation
            here on the home; the one you leave stays in the sidebar's Chats. touch-compact keeps the
            sidebar button at its desktop size on a phone, as in the agent top bar. */}
        <div className="arturo-header-start touch-compact">
          <SidebarMenuButton />
          <button className="arturo-glyph" aria-label="New chat" title="New chat" onClick={startNewThread}>
            <Plus size={22} strokeWidth={2.2} absoluteStrokeWidth />
          </button>
        </div>
        {/* Arturo's avatar and name, centred: tapping it opens the slide-out, as the gear does. A still
            picture (no wander or other motion up here); on a phone it shows a dot while Arturo works. Bigger on
            a phone, where the bar is taller. */}
        <AgentIdentityPill className="arturo-title" agentId={ARTURO_ID} name={aname} size={phoneBar ? 36 : 32}
          aria-label={`${aname}: open settings`} aria-expanded={panelOpen} aria-controls="arturo-panel" onClick={() => setPanelOpen(true)}
          avatar={{ agentId: `${ARTURO_ID}:pill`, sleepAfterMs: 0, state: arturoRec.status === 'working' ? 'working' : 'idle',
                    shape: arturoRec.shape, color: arturoRec.color, eyeColor: arturoRec.eyeColor, provider: arturoRec.provider }} />
        {/* The shared bell (DashboardLayout portals it into this slot, as on agent pages), then the gear. */}
        <div className="arturo-header-end">
          <div ref={registerEndSlot ?? undefined} data-header-end-slot className="touch-compact flex items-center" />
          {/* The gear toggles Arturo's slide-out panel (Overview, Memory, Activity). */}
          <button ref={gearRef} className="arturo-glyph" aria-label="Open brain" aria-expanded={panelOpen} aria-controls="arturo-panel"
                  onClick={() => setPanelOpen((o) => !o)}>
            <Settings size={22} strokeWidth={2.2} absoluteStrokeWidth />
          </button>
        </div>
      </header>

      <div className="arturo-glow" />
      <div className="arturo-feed" ref={feedRef} onScroll={stick.onScroll}>
        {empty ? (
          <div className="arturo-hero">
            {/* Arturo's own face, not the brand mark: its saved look, a pop-in and one wink hello, then calm idle
                (blinks, a slow look-around, eyes on the cursor as the active agent; no bob or breathing). It
                glances down at the input while you type and takes its working glances while Arturo works. Its eyes
                follow the mouse wherever it is on the page, even at rest (only this face does). No
                status dot. Reduced motion: a still face, no wink. */}
            <AgentAvatar agentId={`${ARTURO_ID}:hero`} role="drawer" size={88} selected followAlways calm greet="wink"
                         state={arturoRec.status === 'working' ? 'working' : 'idle'} sleepAfterMs={0}
                         shape={arturoRec.shape} color={arturoRec.color} eyeColor={arturoRec.eyeColor} provider={arturoRec.provider}
                         glanceKey={draft} glanceAt={HERO_GLANCE_AT}
                         className="arturo-hero-face la-on-canvas" label={aname} />
            <div className="greet serif">{greeting(name)}</div>
            {starting && <div className="tools" style={{ color: 'rgba(var(--ink), var(--t45))', fontSize: 13 }}>{STARTING_TEXT}</div>}
            {arturoDisabled && <div className="tools" style={{ color: 'var(--grey)', fontSize: 13 }}>{named(ARTURO_DISABLED_TEXT, aname)}</div>}
            {lookCard}
          </div>
        ) : [lookCard, ...groupCalls(turns, call.inCall).map((item) => item.kind === 'call' ? (
          <ArturoCallCard key={item.key} callId={item.callId} turns={item.turns} live={item.live} startTs={item.startTs} endTs={item.endTs}
            liveSince={item.live ? callSince : null} liveUser={item.live ? call.liveUser : undefined} liveArturo={item.live ? call.liveArturo : undefined}
            connecting={item.live && call.callState === 'connecting'} />
        ) : ((t: Turn, i: number) => t.role === 'user' && t.cardEcho && t.state !== 'failed' ? null : t.role === 'user' ? (
          <div key={t.id} className="turn-user">{t.images && t.images.length > 0 && <div className="msg-images">{t.images.map((u) => <a key={u} href={u} target="_blank" rel="noreferrer"><img src={u} alt="attached image" loading="lazy" /></a>)}</div>}<div className="bubble-user">{t.text}</div>
            {(t.state === 'failed' || (t.state && i === lastUserIdx)) && <div className={`turn-state ${t.state}`}>{sendStateLabel(t.state)}</div>}</div>
        ) : (
          <div key={t.id} className="turn-assistant">
            {t.pending ? <span className="thinking" aria-label="thinking" />
              : hasToolParts(t.parts) ? (
                // In the order it happened. Indices are stable: parts only ever grow at the end.
                <div className="turn-parts">
                  {groupParts(t.parts!).map((g, gi) => g.kind === 'tools'
                    ? <ToolCalls key={gi} calls={g.tools.map(fromPart)} />
                    : <div key={gi} className="txt">{renderText(g.text)}</div>)}
                </div>
              )
              : renderReply(t.text)}
            {!hasToolParts(t.parts) && t.tools && t.tools.length > 0 && <div className="tools">ran {t.tools.join(', ')}</div>}
            {!t.pending && !t.streaming && replyCopyText(t.text, t.parts) && <CopyReply text={replyCopyText(t.text, t.parts)} />}
            <SpawnedAgentCard ids={t.spawned} />
            {t.pairCard && <PairCodeCard card={t.pairCard} />}
            {/* The one decision card (components/chat/ChoiceCard.tsx): his cards answer with words in this chat. */}
            {t.choices && (
              <ChoiceDecisionCard note={t.choices.note} options={t.choices.options} multi={t.choices.multi}
                exclusive={t.choices.exclusive} answered={t.answered} dismissed={t.dismissed}
                onAnswer={(text) => { void send(text); }}
                onDismiss={t.choices.card_id ? async () => {
                  const err = await dismissCard(convId.current, t.choices!.card_id!);
                  if (!err) patch(t.id, { dismissed: true });
                  return err;
                } : undefined} />
            )}
            {t.decision && (
              <ChoiceDecisionCard options={t.decision.options} onAnswer={(text) => {
                // An option runs its step; anything typed instead is an ordinary message to him.
                if (t.decision!.options.includes(text)) { patch(t.id, { decision: undefined }); t.decision!.onPick(text); }
                else void send(text);
              }} />
            )}
          </div>
        ))(item.turn, item.index))]}
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
        {/* While Arturo works: who, and the step it is on (pushed over the live status stream), just above the
            composer at the left. Its avatar moves in step with the sidebar's: one shared model. */}
        {arturoRec.status === 'working' && (
          <div className="arturo-working la-on-card" role="status" aria-live="polite">
            <AgentAvatar agentId={ARTURO_ID} role="activity" size={24} label={aname} />
            <span className="arturo-working-name">{aname}</span>
            <span className="arturo-working-step">{arturoRec.blurb || 'Working…'}</span>
          </div>
        )}
        {convId.current && <JobTray threadId={convId.current} kick={jobKick} onEnded={() => { void reloadThread(); }} />}
        <input ref={fileInput} type="file" multiple hidden aria-hidden="true"
               onChange={async (e) => {
                 const picked = Array.from(e.target.files || []);
                 e.target.value = '';                 // re-picking the same file must work
                 if (picked.length === 0) return;
                 setUploading(true); setUploadError(null);
                 for (const f of picked) {
                   const r = await uploadAttachment(f);
                   if (r.ok) setAttachments((prev) => [...prev, { name: r.name, path: r.path, size: r.size, preview: f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined }]);
                   else setUploadError(r.error);     // refusals are shown, never swallowed
                 }
                 setUploading(false);
               }} />
        {(attachments.length > 0 || uploading || uploadError || dictNote) && (
          <div className="arturo-attachments">
            {attachments.map((a, i) => (
              <span key={i} className="attach-chip">
                {a.preview ? <img src={a.preview} alt="" className="attach-thumb" /> : <Paperclip size={12} />} {describeAttachment(a)}
                <button aria-label={`Remove ${a.name}`}
                        onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}>×</button>
              </span>
            ))}
            {uploading && <span className="attach-note">Uploading…</span>}
            {uploadError && <span className="attach-error">{uploadError}</span>}
            {dictNote && <span className="attach-error">{dictNote}</span>}
          </div>
        )}
        <textarea ref={taRef} rows={1} value={draft} placeholder={arturoDisabled ? `${aname} is turned off` : `Ask ${aname}`} disabled={arturoDisabled}
          onChange={(e) => { setDraft(e.target.value); grow(); }} onKeyDown={onKey} aria-label={`Message ${aname}`} />
        <div className="ctrl-row">
          <div className="cluster">
            <button className="circle-btn" aria-label="Attach a file" title="Attach a file"
                    onClick={() => fileInput.current?.click()} disabled={uploading || arturoDisabled}><Plus size={18} /></button>
            <button className="model-chip" onClick={() => setModelOpen(true)} aria-label={`Brain: ${model}. Change`} title={`Answering on ${model}`}><b>{model}</b>{eff && <span className="eff">{eff}</span>}</button>
          </div>
          <div className="cluster">
            {call.inCall ? (
              // On a call this is the mute: it stops only the microphone's audio going up; Arturo's voice,
              // the captions and the job drawer keep coming down.
              <button className={call.muted ? 'circle-btn muted' : 'circle-btn'} aria-pressed={call.muted}
                      aria-label={call.muted ? 'Unmute microphone' : 'Mute microphone'}
                      title={call.muted ? `Unmute: ${aname} can hear you again` : `Mute: ${aname} keeps talking, but can't hear you`}
                      onClick={call.toggleMute}>{call.muted ? <MicOff size={16} /> : <Mic size={16} />}</button>
            ) : (
              <button className={dictMode === 'idle' ? 'circle-btn' : `circle-btn ${dictMode}`}
                      aria-label={dictMode === 'listening' ? 'Stop dictation' : dictMode === 'recording' ? 'Stop recording' : dictMode === 'transcribing' ? 'Transcribing' : 'Dictate'}
                      aria-pressed={dictating} title={dictateTitle(call.inCall, dictMode)}
                      onClick={toggleDictation} disabled={dictateLocked(call.inCall, dictMode) || arturoDisabled}><Mic size={16} /></button>
            )}
            {call.inCall ? (
              <button className="circle-btn white" aria-label="End call" aria-pressed title="End call"
                      onClick={() => void call.toggle({ route: '/', threadId: convId.current })}><PhoneOff size={18} /></button>
            ) : arturoComposerText(draft, attachments.length) ? (
              <button className="circle-btn white" aria-label="Send" onClick={() => void send()} disabled={busy}><ArrowUp size={18} /></button>
            ) : (
              <button className="circle-btn white" aria-label={HANDS_FREE} title={handsFreeTitle(handsFreeReady(health), aname)}
                      disabled={!handsFreeReady(health)} onClick={() => void call.toggle({ route: '/', threadId: convId.current })}><AudioLines size={16} /></button>
            )}
          </div>
        </div>
      </div>

      <LookPickerPopover agentId={ARTURO_ID} open={pickerOpen} onClose={() => setPickerOpen(false)} canSave={canSaveLook} />
      <ArturoPanel open={panelOpen} onClose={() => setPanelOpen(false)} returnFocusTo={gearRef} brain={eff ? `${model} ${eff}` : model} />
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
