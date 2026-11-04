/**
 * ArturoPill — Arturo's conversation pane, on EVERY page (track T4, S5/S6). Opened by ArturoButton in
 * the top bar, next to the bell (Shaw, 2026-10-10: the floating pill that used to open it covered
 * the text), and anchored under that bar.
 *
 * The pane does NOT dim or block the page. Two things the operator asked for on
 * 2026-09-18, after using the surface:
 *
 *  1. "swap between Arturo's previous conversations and pick up right where we left off" —
 *     so the thread list and every thread's turns come from the SERVER (G20,
 *     /api/arturo/threads). "New thread" leaves the old one IN the list instead of losing
 *     it, and localStorage holds only WHICH thread you were in, never the archive. The home
 *     and the pill read the same list, so it is one thread space, not two.
 *  2. Arturo focuses on the page you are looking at BY DEFAULT — "but if you so choose to,
 *     that's a card on the Arturo chat you should be able to delete". The page context is
 *     therefore a visible card at the top of the thread with an ×; deleting it is how you
 *     tell Arturo to stop focusing on this page, and it can be put back.
 */
import { hydrateTurns, isBusy } from '../../lib/arturoResume';
import { arturoTurn } from '../../lib/arturoStream';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { applyScreenAnswer, focusOf, getScreen, screenLabel, screenPayload, subscribeScreen } from '../../lib/screenCards';
import JobTray from './JobTray';
import { composerKeyAction, composerKeys, keyArgs } from '../../lib/composerGate';
import { useDeviceSetting } from '../../lib/deviceSettings';
import { useStickToBottom } from '../../lib/useStickToBottom';
import RoundAction from '../kit/RoundAction';
import { roundView } from '../../lib/roundAction';
import { useArturoThreadPages } from '../../lib/useArturoThreadPages';
import { useNearTop } from '../../lib/threadKit/useOlderPages';
import OlderPagesNote from '../kit/OlderPagesNote';
import { currentThread, setCurrentThread, subscribeCurrentThread } from '../../lib/arturoCurrent';
import ThreadView from '../kit/ThreadView';
import { arturoRows, lastUserKey } from '../../lib/threadKit/view';
import { cardAnswer, isCardEcho, storedAnswerText } from '../../lib/choiceCard';
import { applyTextDelta, applyToolCall, applyToolResult, hasToolParts, type TurnPart } from '../../lib/turnParts';
import { Link, useLocation, useParams } from 'react-router-dom';
import { Mic, MicOff, X, History, Focus, AudioLines, Plus, Paperclip, Cpu } from 'lucide-react';
import { useDictation } from './useDictation.ts';
import { uploadAttachment, attachmentPreamble, describeAttachment, attachmentImages, type Attachment } from '../../lib/arturoUpload';
import './arturo.css';
import { newConversationId, sendOutcomeText, contextFromLocation, getArturoFocus, subscribeArturoFocus, cardRefusal, isStarting, waitForArturo, STARTING_TEXT, handsFreeTitle, handsFreeReady, dictateLocked, dictateTitle, arturoHealth, type ArturoHealth, type CardAnswer, type ChoiceCard, type SendState } from '../../lib/arturo';
import {
  listThreads, loadThreadPage, dismissCard, contextCardLabel, isContextDismissed, dismissContext,
  restoreContext, contextForTurn, type ThreadSummary,
  type ThreadTurn,
} from '../../lib/arturoThreads';
import { useHandsFreeCall } from '../../hooks/useHandsFreeCall';
import SpawnedAgentCard from './SpawnedAgentCard';
import ModelPicker from '../kit/ModelPicker';
import { useArturoBrain } from '../../stores/arturoBrain';
import { useArturoUi } from '../../stores/arturoUi';
import { brainFromThread, describeTurnError, toWireBrain } from '../../lib/arturoBrain';
import { choiceTitle } from '../../lib/modelTitle';
import { brainProviderId, type HealthBrain } from '../../lib/brainLogo';
import { arturoComposerText, arturoShownText } from '../../lib/arturoComposer';
import { useAssistantName } from '../../lib/assistantName';

interface PillTurn { role: 'user' | 'arturo'; text: string; tools?: string[];
  /** The stored turn's number in the thread (read a page at a time); none on a turn this pane added. */
  seq?: number;
  /** Images the operator attached to this message (upload URLs). */
  images?: string[];
  spawned?: string[]; at: number; live?: boolean; state?: SendState;
  /** A reply still coming (the thinking line), or being written (streamed). */
  pending?: boolean; streaming?: boolean;
  /** A card the reply showed (ask_choices), what answered it (the server's record), or that it was closed; a tap's
   *  own message draws no bubble (the card shows it). */
  choices?: ChoiceCard; answered?: string; dismissed?: boolean; cardEcho?: boolean;
  /** A first-person status note (voice not configured, mic denied…) — rendered as a small interleaved
   *  row at the moment it happened, like a tool call, never as a message and never pinned to the bottom. */
  note?: boolean;
  /** Spoken on a call (a caption committed here, or a stored turn of mode "voice"): it goes in the call's card,
   *  as on Arturo's page (lib/threadKit/calls). */
  voice?: boolean;
  /** The call-ended line of this call id (stored threads tag it): closes the call's card. */
  callEnd?: string;
  /** Tools Arturo ran during a live call, as they happened (parts = tool cards only). */
  callTools?: boolean; parts?: TurnPart[];
  /** When it was said (epoch seconds): the call card's length. */
  ts?: number }
/** Stored turns read the way Arturo's page reads them (lib/arturoResume hydrateTurns): context lines and attachment
 *  markers off, a finished job as its steps and its text, a choice card with the server's answer. A job's result stays
 *  a reply here (its steps, then its words), the pane's small layout, not a result card. */
function hydratePane(turns: ThreadTurn[]): PillTurn[] {
  return hydrateTurns(turns).map((x) => ({ ...x, result: undefined, at: (x.ts || 0) * 1000 }));
}
/** A turn's name in the thread view: its seq once stored, else its place among the pane's own turns. */
function pillKey(t: PillTurn, i: number): string {
  return t.seq !== undefined ? `s${t.seq}` : `l${i}`;
}
/** Which thread was I in: the browser's ONE current thread, shared with the home (lib/arturoCurrent). */
function loadConv(): string {
  const existing = currentThread();
  if (existing) return existing;
  const fresh = newConversationId('pill');
  setCurrentThread(fresh);
  return fresh;
}
function rememberConv(id: string) {
  setCurrentThread(id);
}

export function ArturoPill() {
  const location = useLocation();
  const params = useParams();
  // Shared with ArturoButton in the top bar (stores/arturoUi).
  const open = useArturoUi((st) => st.open);
  const setOpen = useArturoUi((st) => st.setOpen);
  const setExchanges = useArturoUi((st) => st.setExchanges);
  const [draft, setDraft] = useState('');
  const [sendShortcut] = useDeviceSetting('sendShortcut');   // Settings › General › Send shortcut
  const aname = useAssistantName();
  const [turns, setTurns] = useState<PillTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [showThreads, setShowThreads] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [convId, setConvId] = useState<string>(loadConv);
  const [jobKick, setJobKick] = useState(0);
  // The home (or another tab) switched threads: this pane follows, and reloads it when open (the effect on
  // [open, convId] does that).
  useEffect(() => subscribeCurrentThread((id) => setConvId((cur) => (cur === id ? cur : id))), []);
  // Re-render when the card is deleted or restored; the value itself lives in storage.
  const [ctxOn, setCtxOn] = useState(true);
  // Which brain answers the next turn: the same Arturo-scoped choice the home uses.
  const brainChoice = useArturoBrain((s) => s.choice);
  const chooseBrain = useArturoBrain((s) => s.choose);
  const [modelOpen, setModelOpen] = useState(false);
  // Whether this server can start Live voice mode (GEMINI_API_KEY; /health.live), read once: the button
  // is gated the same way as on the home page.
  const [health, setHealth] = useState<ArturoHealth | null>(null);
  useEffect(() => {
    let alive = true;
    void arturoHealth().then((h) => { if (alive) setHealth(h); });
    return () => { alive = false; };
  }, []);
  const ta = useRef<HTMLTextAreaElement>(null);
  // Same composer buttons as the Arturo home (Shaw 2026-09-21: "the buttons we see when Arturo
  // first loads are the same buttons we should see in every instance of Ask Arturo"): attach,
  // dictate, and send-or-voice in the right slot. The page-context card sits where the home's
  // model chip is, and the brain chip beside it picks the model (DEC-1790669162399904).
  const fileInput = useRef<HTMLInputElement>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const { mode: dictMode, dictating, note: dictNote, toggle: toggleDictation, stop: stopDictation, clearNote: clearDictNote } =
    useDictation(draft, setDraft, () => ta.current?.focus());
  const scroller = useRef<HTMLDivElement>(null);
  // Chat/dev mode opens an agent as an overlay WITHOUT changing the URL, so the route says
  // "agents" and not which one. When the overlay publishes a focus, it wins over the route —
  // that is what makes "what is this agent doing" answerable while you sit in its session.
  const [focus, setFocus] = useState(getArturoFocus);
  useEffect(() => subscribeArturoFocus(() => setFocus(getArturoFocus())), []);
  // The cards on screen (lib/screenCards.ts): the context card names the focused one by its question, every turn
  // carries them (the focused card in full, the rest one line each), and a call hears them as they change.
  const screen = useSyncExternalStore(subscribeScreen, getScreen, getScreen);
  const cardsLabel = screenLabel(screen);
  // Provider logos for the brain chip (static marks from config/providers.json via the probe).
  const [logos, setLogos] = useState<Record<string, { svg: string; label: string }>>({});
  useEffect(() => {
    let alive = true;
    fetch('/api/runtimes/available').then((r) => (r.ok ? r.json() : null)).then((j) => {
      if (!alive || !j?.providers) return;
      const m: Record<string, { svg: string; label: string }> = {};
      for (const p of j.providers as { id: string; label?: string; logo_svg?: string }[]) {
        if (p.logo_svg) m[p.id] = { svg: p.logo_svg, label: p.label || p.id };
      }
      setLogos(m);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const brainProvider = brainProviderId(brainChoice, (health?.brain ?? null) as HealthBrain | null);
  const brainLogo = brainProvider ? logos[brainProvider] : undefined;
  const brainName = brainChoice ? choiceTitle(brainChoice) : brainLogo ? `${brainLogo.label} (default)` : 'the default brain';

  // ── Live voice mode, IN THIS PANE ──────────────────────────────────
  // The shared call (hooks/useHandsFreeCall → lib/handsFree → lib/voiceSession → /api/voice/live →
  // gateway /live → Gemini Live), the same one the home composer drives. Arturo's words arrive as
  // transcript frames; YOUR words are captioned on-device by the browser. Both render live below the
  // thread, then commit as turns when final. The call ends with the same `[voice-call: …]` marker the
  // composer path uses, so the transcript card can be fetched by id later.
  const appendRef = useRef<(t: PillTurn) => void>(() => {});
  const noteRef = useRef<(reason: string) => void>(() => {});
  const call = useHandsFreeCall({
    onFinal: (text, role) => appendRef.current({ role, text, at: Date.now(), live: true, voice: true, ts: Date.now() / 1000 }),
    onUnavailable: (reason) => noteRef.current(reason),
    onEnded: (text) => appendRef.current({ role: 'arturo', text, at: Date.now(), live: true, ts: Date.now() / 1000 }),
    // A tool run during a call sits in the call's card where it happened, as on Arturo's page.
    onTool: (e) => setTurns((prev) => {
      if (e.phase === 'start') {
        const c = { call_id: e.callId, name: e.name, args_summary: e.argsSummary };
        const last = prev[prev.length - 1];
        if (last?.callTools) return [...prev.slice(0, -1), { ...last, parts: applyToolCall(last.parts || [], c) }];
        return [...prev, { role: 'arturo', text: '', at: Date.now(), callTools: true, parts: applyToolCall([], c) }];
      }
      const at = prev.findLastIndex((t) => t.callTools && t.parts?.some((p) => p.kind === 'tool' && p.callId === e.callId));
      if (at < 0) return prev;
      const next = [...prev];
      next[at] = { ...next[at], parts: applyToolResult(next[at].parts || [], { call_id: e.callId, name: e.name, ok: !!e.ok, summary: e.summary }) };
      return next;
    }),
  });
  const { callState, liveUser, liveArturo, inCall, muted, toggleMute } = call;
  // When this pane's call went live, for the live card's running clock (connecting does not count).
  const [callSince, setCallSince] = useState<number | null>(null);
  useEffect(() => {
    if (callState === 'live') setCallSince((t) => t ?? Date.now());
    else if (!inCall) setCallSince(null);
  }, [callState, inCall]);
  async function toggleCall() {
    const focused = ctx.entityKind && ctx.entityId ? `${ctx.entityKind}:${ctx.entityId}` : null;
    await call.toggle({ route: ctx.route, focusedEntity: focused, threadId: convId });
  }
  const routeCtx = contextFromLocation(location.pathname, params as Record<string, string | undefined>, location.search);
  const ctx = focus
    ? { ...routeCtx, entityKind: focus.kind, entityId: focus.id }
    : routeCtx;
  // Mid-call navigation: the call's screen context follows the page (the hello only carried the start page), and the
  // cards on screen ride with it as they change (scrolling, paging a part, a popover opening).
  const callFocused = ctx.entityKind && ctx.entityId ? `${ctx.entityKind}:${ctx.entityId}` : null;
  const { setContext: setCallContext } = call;
  const callCards = screenPayload(screen)?.payload ?? null;
  const callCardsKey = callCards ? JSON.stringify(callCards) : '';
  useEffect(() => {
    if (inCall) setCallContext({ route: ctx.route, focusedEntity: callFocused, ...(callCards ? { cards: callCards } : {}) });
  }, [inCall, ctx.route, callFocused, callCardsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Pull this thread's turns from the server, so reopening the pill resumes it exactly. */
  // A reload that resolves AFTER the user has already sent something must not overwrite the local
  // turns (it wiped a fresh 'Sending…' bubble when you dictated right after opening the pill).
  const resumeGen = useRef(0);
  const resume = useCallback(async (id: string) => {
    const mine = ++resumeGen.current;
    const t = (await loadThreadPage(id))?.thread ?? null;   // its newest page; older ones load on scrolling up
    if (mine !== resumeGen.current) return;          // superseded by a send or a newer reload
    // A thread the server knows answers on the brain it last used. A fresh thread (null) leaves
    // the current choice alone: resume runs on every open, and must not undo a pick just made.
    if (t) chooseBrain(brainFromThread(t, {}));
    setTurns(hydratePane(t?.turns || []));
  }, [chooseBrain]);

  useEffect(() => { setCtxOn(!isContextDismissed(convId)); }, [convId]);
  useEffect(() => { if (open) { void resume(convId); void listThreads().then(setThreads); setTimeout(() => ta.current?.focus(), 30); } }, [open, convId, resume]);
  // New turns and live captions push the feed up only while you're at the bottom; scrolled up, it stays put.
  const stick = useStickToBottom(scroller);
  // The thread view follows whenever what it draws grows (turns, a streamed reply, a live call's captions).
  // Scrolling up reads older pages of the thread above what is loaded (lib/useArturoThreadPages.ts). Rows are named
  // by the stored turn's seq, or by a turn's place among this pane's own (never by its place in the whole list, which
  // every older page would shift).
  const paged = useArturoThreadPages<PillTurn>(turns, convId, hydratePane, open && turns.length > 0);
  const keyed = useMemo(() => {
    const at = paged.items.length - turns.length;
    return paged.items.map((t, i) => ({ ...t, id: pillKey(t, i - at) }));
  }, [paged.items, turns.length]);
  const olderRef = useRef<HTMLDivElement>(null);
  useNearTop(scroller, olderRef, () => { void paged.loadOlder(); }, paged.items.length, open && turns.length > 0 && paged.hasOlder);
  const rows = useMemo(() => arturoRows(keyed, inCall, aname), [keyed, inCall, aname]);
  useEffect(() => { if (open) stick.toBottom(); }, [open, convId]); // eslint-disable-line react-hooks/exhaustive-deps

  const append = (t: PillTurn) => setTurns((prev) => [...prev, t]);
  // The box grows with its content up to ~6 lines, then scrolls (Shaw: you could not see two typed
  // lines at once). Runs on typing AND on dictation / send, which set the draft without a change event.
  const growTa = () => { const el = ta.current; if (!el) return; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 150) + 'px'; };
  useEffect(() => { growTa(); }, [draft]);
  appendRef.current = append;
  // One note per distinct reason in a row: a refused call can report twice (socket + server).
  noteRef.current = (reason: string) => setTurns((prev) => {
    const last = prev[prev.length - 1];
    if (last && last.note && last.text === reason) return prev;
    return [...prev, { role: 'arturo', text: reason, at: Date.now(), note: true }];
  });

  /** Send the composer's text, or `picked`: the words of a card option tapped in the pane, with the card it answers
   *  (`answer`, kept on that card on the server with this turn; a message typed in the box answers no card there). */
  async function send(picked?: string, answer?: CardAnswer) {
    const text = picked === undefined ? arturoComposerText(draft, attachments.length) : picked.trim();
    if (!text || busy) return;
    if (picked === undefined) {
      if (dictating) stopDictation();    // the sent text is final; don't re-append into the empty box
      clearDictNote();
      setDraft('');
    }
    setBusy(true);
    resumeGen.current++;                 // any in-flight thread reload is now stale
    const at = Date.now();
    // A card is answered once: by a tap, or by whatever was typed instead (the server keeps a tap's answer).
    const openCard = [...turns].reverse().find((x) => x.choices && !x.answered && !x.dismissed)?.choices;
    setTurns((prev) => prev.map((x) => (x.choices && !x.answered && !x.dismissed ? { ...x, answered: text } : x)));
    append({ role: 'user', text: picked === undefined ? arturoShownText(draft) : text, at, state: 'sending',
             images: picked === undefined ? attachmentImages(attachments) : [],
             ...(picked !== undefined && openCard && isCardEcho(openCard, text) ? { cardEcho: true } : {}) });
    // His reply, streamed into this row as it is written (the thinking line until the first words or tool).
    const replyAt = at + 1;
    append({ role: 'arturo', text: '', at: replyAt, pending: true });
    requestAnimationFrame(() => stick.toBottom());      // you sent it: back to the bottom
    const setState = (state: SendState) => setTurns((prev) => prev.map((t) => t.at === at && t.role === 'user' ? { ...t, state } : t));
    const patchReply = (extra: Partial<PillTurn>) => setTurns((prev) => prev.map((t) => t.at === replyAt && t.role === 'arturo' ? { ...t, ...extra } : t));
    let streamed = '';
    let parts: TurnPart[] = [];
    // The path rides in front of the message (same grammar as the home composer).
    const pre = attachmentPreamble(attachments);
    setAttachments([]);
    // The card is the switch: present → this turn carries the page context; deleted → it does not.
    const body = pre ? `${pre}\n\n${text}` : text;
    const turnBrain = toWireBrain(brainChoice);
    // The home's send path, streamed: one id for this send, so a busy conversation, a hop still starting, a dropped
    // stream or a 504 is resolved by asking the server what became of THIS send, never by sending it blind a second
    // time (DEC-1791518421640932).
    // The cards on screen as this message leaves, and which card each ref stands for (an answer Arturo hands back
    // names its card by ref).
    const turnCtx = contextForTurn(convId, ctx);
    const onScreen = turnCtx ? screenPayload(getScreen()) : null;
    const r = await arturoTurn(body, convId, turnCtx && onScreen ? { ...turnCtx, cards: onScreen.payload } : turnCtx, {
      onSent: () => setState('sent'), brain: turnBrain, ...(answer ? { cardAnswer: answer } : {}),
      onDelta: (t: string) => { streamed += t; parts = applyTextDelta(parts, t); patchReply({ pending: false, streaming: true, text: streamed, parts }); },
      onToolCall: (c) => { parts = applyToolCall(parts, c); patchReply({ pending: false, streaming: true, parts }); },
      onToolResult: (res) => { parts = applyToolResult(parts, res); patchReply({ parts }); },
      // The whole-reply path is taking over: it answers from the top, so what this attempt showed goes.
      onReset: () => { if (!streamed && !parts.length) return; streamed = ''; parts = []; patchReply({ pending: true, streaming: false, text: '', parts: undefined }); },
      // Same rule as the home (G15): right after `orchestra up` the service is still booting. Say so and
      // wait for /health; the send then goes again with the same id.
      onStarting: async () => {
        const noteAt = Date.now();
        append({ role: 'arturo', text: STARTING_TEXT, at: noteAt, note: true });
        const ready = await waitForArturo();
        setTurns((prev) => prev.filter((t) => !(t.note && t.at === noteAt)));
        return ready.ok;
      },
    });
    setJobKick((k) => k + 1);        // a job this turn started shows in the tray at once
    setBusy(false);
    setState(r.ok ? 'acked' : 'failed');
    // The card could no longer take this answer (answered elsewhere, or closed): nothing ran; the card shows what it
    // holds and the reply says so.
    const closed = answer ? cardRefusal(r) : null;
    if (closed) {
      setTurns((prev) => prev.map((x) => (x.choices?.card_id !== answer!.card_id ? x
        : closed.kind === 'answered' ? { ...x, answered: storedAnswerText(x.choices.options, closed.answer) }
          : closed.kind === 'dismissed' ? { ...x, answered: undefined, dismissed: true } : { ...x, answered: undefined })
        ).map((x) => (x.at === at && x.role === 'user' ? { ...x, cardEcho: false } : x)));
      patchReply({ pending: false, streaming: false, parts: undefined, text: closed.kind === 'answered'
        ? 'That card was already answered, so I did not send this. Its answer is on the card.'
        : closed.kind === 'dismissed' ? 'That card was closed, so I did not send this.'
          : 'That card is no longer in this conversation, so I did not send this.' });
      return;
    }
    patchReply(r.ok
      ? { pending: false, streaming: false, text: r.reply_text || streamed || '(no reply)', tools: r.tools_called, spawned: r.spawned,
          ...(r.choices ? { choices: r.choices } : {}),
          // Kept only when tools ran: a reply of words alone is drawn as it always was.
          parts: hasToolParts(parts) ? parts : undefined }
      : { pending: false, streaming: false, parts: undefined, text: sendOutcomeText(r) ?? (isStarting(r)
          ? 'I am still starting up and could not answer yet — give `orchestra up` a moment and send that again.'
          : isBusy(r) ? 'Still answering an earlier message in this conversation (another tab or device). Send this again in a moment.'
          : describeTurnError(r)?.message || `Could not reach ${aname}: ${r.error || 'unknown'}`) });
    void listThreads().then(setThreads);      // the thread it just created/updated joins the list
    // Arturo answered a card for the operator: the card on screen sends it through its own route, as a tap would.
    if (r.ok && r.answer_card) {
      const err = await applyScreenAnswer(onScreen?.refs[r.answer_card.card], { picks: r.answer_card.picks ?? [], words: r.answer_card.words });
      if (err) append({ role: 'arturo', text: err, at: Date.now() });
    }
  }

  /** New thread — the one you leave is now IN the list, not lost. */
  function startNewThread() {
    const fresh = newConversationId('pill');
    rememberConv(fresh);
    setConvId(fresh);
    setTurns([]);
    setShowThreads(false);
    void listThreads().then(setThreads);
  }

  async function switchTo(id: string) {
    rememberConv(id);
    setConvId(id);
    setShowThreads(false);
    await resume(id);
  }

  // The model picker belongs to this open pane: closing the pane closes it, so it never comes
  // back on its own the next time Arturo opens. (A tap outside the picker closes it too.)
  useEffect(() => {
    if (!open) return;
    return () => setModelOpen(false);
  }, [open]);

  // On a desktop the pane closes ONLY on its × (or the Arturo button that opened it), so it stays open while
  // you work on the page beside it. On a phone, where it covers the page, a tap outside it closes it too.
  // Not while the model picker is up (it sits above the pane and closes itself),
  // and not on the Arturo button, whose own click toggles the pane. The draft and attachments survive a close.
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!window.matchMedia('(max-width: 768px)').matches) return;
      const t = e.target as Element | null;
      if (!t || modelOpen) return;
      // A card opened from a chip in the pane floats outside it: pressing it is still working in the pane.
      if (panelRef.current?.contains(t) || t.closest('[data-arturo-toggle]') || t.closest('.kit-card-pop')) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [open, modelOpen, setOpen]);

  function toggleContextCard() {
    if (ctxOn) dismissContext(convId); else restoreContext(convId);
    setCtxOn(!ctxOn);
  }

  // The button's badge: exchanges in this thread.
  useEffect(() => { setExchanges(Math.ceil(turns.length / 2)); }, [turns.length, setExchanges]);

  // Closed: nothing here. The way in is ArturoButton, in the top bar next to the bell.
  if (!open) return null;
  return (
    <>
      {/* No backdrop, deliberately (operator, 2026-09-19): the pane must not dim the page and
          must not swallow clicks on it — you keep working while Arturo is open, the way the
          assistant panel in the previous build did. Close with the ×. */}
      <div ref={panelRef} className="arturo-pill-panel" role="dialog" aria-label={`Ask ${aname}`}>
        <div className="arturo-pill-head">
          <span className="who">{aname}</span>
          <span className="spacer" />
          <button className="linkish" onClick={() => setShowThreads((s) => !s)} aria-label="Previous conversations"
                  aria-expanded={showThreads}><History size={14} /> Threads</button>
          <button className="linkish" onClick={startNewThread} aria-label="Start a new thread">New</button>
          <Link to="/" className="linkish accent">Open</Link>
          <button onClick={() => setOpen(false)} aria-label="Close" className="linkish"><X size={14} /></button>
        </div>

        {showThreads && (
          <div className="arturo-thread-list" role="listbox" aria-label="Previous conversations">
            {threads.length === 0 && <p className="empty">No earlier conversations yet.</p>}
            {threads.map((t) => (
              <button key={t.id} role="option" aria-selected={t.id === convId}
                      className={t.id === convId ? 'thread-row current' : 'thread-row'}
                      onClick={() => void switchTo(t.id)}>
                <span className="t-title">{t.title || 'Untitled'}</span>
                <span className="t-meta">{Math.ceil((t.turns || 0) / 2)} · {new Date((t.updated || 0) * 1000).toLocaleDateString()}</span>
                {t.gist && <span className="t-gist">{t.gist}</span>}
              </button>
            ))}
          </div>
        )}

        <div className="arturo-pill-thread" ref={scroller} onScroll={stick.onScroll}>
          {/* One unwrapped hint, tucked under the head divider; gone the moment the first message is sent
              (Shaw 2026-09-22). No Threads sentence — the Threads button says it. */}
          {/* Hidden (not removed) once typing starts, so the pane keeps its size. */}
          {turns.length === 0 && !busy && <p className="empty" style={draft ? { visibility: 'hidden' } : undefined}>Ask about what you are looking at, or anything else.</p>}
          {/* His thread through the kit's thread view (components/kit/ThreadView.tsx) in the pane's own skin: his
              replies (Markdown, tool runs in order, streamed as written), a voice call as one card with its live
              captions, his choice cards, the pane's status notes. */}
          <div ref={olderRef} data-anchor-skip data-older-sentinel aria-hidden style={{ height: 0, flexShrink: 0 }} />
          <OlderPagesNote show={paged.loading} />
          <ThreadView rows={rows} skin="pane" by={aname} lastUserKey={lastUserKey(keyed)} stick={stick}
            call={{ liveSince: callSince, liveUser, liveArturo, connecting: callState === 'connecting' }}
            noteText={(m) => (m.text === STARTING_TEXT ? m.text : `I can't do that yet — ${m.text}.`)}
            choice={{
              onAnswer: (m, text, a) => { const c = m.choices!; void send(text, c.card_id ? cardAnswer(c.card_id, c.options, a.picked, a.words) : undefined); },
              onDismiss: async (m) => {
                const err = await dismissCard(convId, m.choices!.card_id!);
                if (!err) { const k = m.key; setTurns((prev) => prev.map((x, i) => (`t${pillKey(x, i)}` === k ? { ...x, dismissed: true } : x))); }
                return err;
              },
            }}
            extras={(m) => <SpawnedAgentCard ids={m.spawned} />} />
        </div>

        <JobTray threadId={convId} kick={jobKick} onEnded={() => { void resume(convId); }} />
        <input ref={fileInput} type="file" multiple hidden aria-hidden="true"
               onChange={async (e) => {
                 const picked = Array.from(e.target.files || []);
                 e.target.value = '';
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
        <textarea ref={ta} rows={1} value={draft} placeholder={`Ask ${aname}…`} onChange={(e) => { setDraft(e.target.value); growTa(); }}
          onKeyDown={(e) => {
            if (composerKeyAction({ ...keyArgs(e), ...composerKeys('arturoPill', sendShortcut) }) === 'send') { e.preventDefault(); void send(); }
          }} />
        <div className="ctrl-row">
          {/* The page-context CARD lives HERE, in place of the old "Arturo" chip: it sits
              with the composer because it describes what the NEXT message carries, not
              something that happened earlier in the thread. Default on, × deletes it, and
              the stub it leaves behind puts it back. */}
          <div className="cluster">
            <button className="circle-btn" aria-label="Attach a file" title="Attach a file"
                    onClick={() => fileInput.current?.click()} disabled={uploading}><Plus size={18} /></button>
            {ctxOn ? (
              <span className="arturo-context-chip" title={`Looking at ${contextCardLabel(ctx, cardsLabel)}`} aria-label={`Looking at ${contextCardLabel(ctx, cardsLabel)}`}
                    data-context-card={focusOf(screen) ? 'card' : 'page'}>
                <Focus size={12} />
                <span className="cc-label"><b>{contextCardLabel(ctx, cardsLabel)}</b></span>
                <button className="cc-x" onClick={toggleContextCard}
                        aria-label="Stop focusing on this page" title="Stop focusing on this page"><X size={12} /></button>
              </span>
            ) : (
              <button className="arturo-context-chip off" onClick={toggleContextCard}
                      aria-label="Use this page for context">
                <Focus size={12} /> <span className="cc-label">Use this page</span>
              </button>
            )}
          </div>
          <div className="cluster">
            <button className="circle-btn arturo-brain-logo" onClick={() => setModelOpen(true)}
                    title={`Answering on ${brainName}`} aria-label={`Brain: ${brainName}. Change`}>
              {brainLogo
                // logo_svg is a static, build-time mark from config/providers.json (never user input).
                ? <span className="logo" aria-hidden="true" dangerouslySetInnerHTML={{ __html: brainLogo.svg }} />
                // Still loading (or no mark for this provider): just the chip glyph, no text.
                : <Cpu size={18} aria-hidden="true" />}
            </button>
            {inCall ? (
              // On a call this is the mute: it stops only the microphone's audio going up; Arturo's voice,
              // the captions and the job drawer keep coming down.
              <button className={muted ? 'circle-btn muted' : 'circle-btn'} aria-pressed={muted}
                      aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
                      title={muted ? `Unmute: ${aname} can hear you again` : `Mute: ${aname} keeps talking, but can't hear you`}
                      onClick={toggleMute}>{muted ? <MicOff size={16} /> : <Mic size={16} />}</button>
            ) : (
              <button className={dictMode === 'idle' ? 'circle-btn' : `circle-btn ${dictMode}`}
                      aria-label={dictMode === 'listening' ? 'Stop dictation' : dictMode === 'recording' ? 'Stop recording' : dictMode === 'transcribing' ? 'Transcribing' : 'Dictate'}
                      aria-pressed={dictating} title={dictateTitle(inCall, dictMode)}
                      onClick={toggleDictation} disabled={dictateLocked(inCall, dictMode)}><Mic size={16} /></button>
            )}
            {/* The one round button (lib/roundAction.ts): Send, his call when the box is empty (End during it), or a
                disabled Send that says why a call isn't possible. Its icons crossfade in place. */}
            <RoundAction className="circle-btn white" callIcon={<AudioLines size={16} />}
              view={roundView({ hasContent: !!arturoComposerText(draft, attachments.length), sending: busy,
                                call: { inCall, available: handsFreeReady(health), reason: handsFreeTitle(handsFreeReady(health), aname) } },
                              { agent: aname })}
              onAction={(mode) => { if (mode === 'send') void send(); else if (mode === 'call' || mode === 'end') void toggleCall(); }} />
          </div>
        </div>
      </div>
      {/* The kit's model picker, opened by the brain chip; no chip of its own (trigger={false}). */}
      <ModelPicker chooser="arturo" trigger={false} open={open && modelOpen} onOpenChange={setModelOpen}
        value={brainChoice ? { providerId: brainChoice.provider, modelId: brainChoice.model } : null}
        onValueChange={(p) => chooseBrain({ provider: p.providerId, model: p.modelId, label: p.modelLabel })}
        onPickDefault={() => chooseBrain(null)} />
    </>
  );
}
