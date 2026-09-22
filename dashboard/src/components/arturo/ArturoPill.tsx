/**
 * ArturoPill — the always-available "Ask Arturo" pill, bottom-right on EVERY page (track T4, S5/S6).
 *
 * Tap → a conversation pane anchored bottom-right. It does NOT dim or block the page. Two things the operator asked for on
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
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { Mic, ArrowUp, X, History, Focus, PhoneOff, AudioLines, Plus, Paperclip } from 'lucide-react';
import { useDictation } from './useDictation.ts';
import { uploadAttachment, attachmentPreamble, describeAttachment, type Attachment } from '../../lib/arturoUpload';
import './arturo.css';
import { arturoText, newConversationId, contextFromLocation, getArturoFocus, subscribeArturoFocus } from '../../lib/arturo';
import {
  listThreads, loadThread, contextCardLabel, isContextDismissed, dismissContext,
  restoreContext, contextForTurn, type ThreadSummary,
} from '../../lib/arturoThreads';
import { VoiceSession, type VoiceSessionState } from '../../lib/voiceSession';

interface PillTurn { role: 'user' | 'arturo'; text: string; tools?: string[]; at: number; live?: boolean }
const LS_CONV = 'orchestra.arturo.pill.conversation';

/** Which thread was I in — the ONLY thing still kept in the browser. */
function loadConv(): string {
  try {
    const existing = localStorage.getItem(LS_CONV);
    if (existing) return existing;
    const fresh = newConversationId('pill');
    localStorage.setItem(LS_CONV, fresh);
    return fresh;
  } catch { return newConversationId('pill'); }
}
function rememberConv(id: string) {
  try { localStorage.setItem(LS_CONV, id); } catch { /* private mode */ }
}

export function ArturoPill() {
  const location = useLocation();
  const params = useParams();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<PillTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [showThreads, setShowThreads] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [convId, setConvId] = useState<string>(loadConv);
  // Re-render when the card is deleted or restored; the value itself lives in storage.
  const [ctxOn, setCtxOn] = useState(true);
  const ta = useRef<HTMLTextAreaElement>(null);
  // Same composer buttons as the Arturo home (Shaw 2026-09-21: "the buttons we see when Arturo
  // first loads are the same buttons we should see in every instance of Ask Arturo"): attach,
  // dictate, and send-or-voice in the right slot. Only the home's model chip has no twin here —
  // the page-context card sits in its place.
  const fileInput = useRef<HTMLInputElement>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const { mode: dictMode, dictating, note: dictNote, toggle: toggleDictation, stop: stopDictation } =
    useDictation(draft, setDraft, () => ta.current?.focus());
  const scroller = useRef<HTMLDivElement>(null);
  // Chat/dev mode opens an agent as an overlay WITHOUT changing the URL, so the route says
  // "agents" and not which one. When the overlay publishes a focus, it wins over the route —
  // that is what makes "what is this agent doing" answerable while you sit in its session.
  const [focus, setFocus] = useState(getArturoFocus);
  useEffect(() => subscribeArturoFocus(() => setFocus(getArturoFocus())), []);

  // ── live voice call, IN THIS PANE ──────────────────────────────────────────
  // The same VoiceSession the agent composer's call button uses (lib/voiceSession.ts →
  // /api/voice/live → gateway /live → Gemini Live). Arturo's words arrive as
  // {event:"transcript"} frames from the server; YOUR words are captioned on-device by
  // the browser's SpeechRecognition (startDictation), because the gateway deliberately
  // does not transcribe the caller. Both render live below the thread, then commit as
  // turns when final. The call ends with the same `[voice-call: …]` marker the composer
  // path uses, so the transcript card can be fetched by id later.
  const [callState, setCallState] = useState<VoiceSessionState>('idle');
  const [liveUser, setLiveUser] = useState('');
  const [liveArturo, setLiveArturo] = useState('');
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const voice = useRef<VoiceSession | null>(null);
  const appendRef = useRef<(t: PillTurn) => void>(() => {});
  function voiceSession(): VoiceSession {
    if (!voice.current) {
      voice.current = new VoiceSession({
        onPartial: (text, role) => { if (role === 'user') setLiveUser(text); else setLiveArturo(text); },
        onFinal: (text, role) => {
          if (role === 'user') setLiveUser(''); else setLiveArturo('');
          appendRef.current({ role, text, at: Date.now(), live: true });
        },
        onUnavailable: (reason) => setVoiceNote(reason),
        onStateChange: (s) => setCallState(s),
        onCallEnded: (marker, id) => {
          setLiveUser(''); setLiveArturo('');
          appendRef.current({ role: 'arturo', text: `Call ended (${id}). ${marker}`, at: Date.now(), live: true });
        },
      });
    }
    return voice.current;
  }
  const inCall = callState === 'live' || callState === 'connecting';
  async function toggleCall() {
    setVoiceNote(null);
    if (inCall) { voiceSession().stop(); voiceSession().stopDictation(); return; }
    const focused = ctx.entityKind && ctx.entityId ? `${ctx.entityKind}:${ctx.entityId}` : null;
    await voiceSession().start({ route: ctx.route, focusedEntity: focused });
    voiceSession().startDictation();          // your own captions, on-device
  }
  useEffect(() => () => { voice.current?.stop(); voice.current?.stopDictation(); }, []);
  const routeCtx = contextFromLocation(location.pathname, params as Record<string, string | undefined>, location.search);
  const ctx = focus
    ? { ...routeCtx, entityKind: focus.kind, entityId: focus.id }
    : routeCtx;

  /** Pull this thread's turns from the server, so reopening the pill resumes it exactly. */
  const resume = useCallback(async (id: string) => {
    const t = await loadThread(id);
    setTurns((t?.turns || []).map((x) => ({ role: x.role === 'user' ? 'user' : 'arturo', text: x.content, at: (x.ts || 0) * 1000 })));
  }, []);

  useEffect(() => { setCtxOn(!isContextDismissed(convId)); }, [convId]);
  useEffect(() => { if (open) { void resume(convId); void listThreads().then(setThreads); setTimeout(() => ta.current?.focus(), 30); } }, [open, convId, resume]);
  useEffect(() => { scroller.current?.scrollTo({ top: 1e9, behavior: 'smooth' }); }, [turns, open, busy]);

  const append = (t: PillTurn) => setTurns((prev) => [...prev, t]);
  appendRef.current = append;

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    if (dictating) stopDictation();      // the sent text is final; don't re-append into the empty box
    setDraft(''); setBusy(true);
    append({ role: 'user', text, at: Date.now() });
    // The path rides in front of the message (same grammar as the home composer).
    const pre = attachmentPreamble(attachments);
    setAttachments([]);
    // The card is the switch: present → this turn carries the page context; deleted → it does not.
    const r = await arturoText(pre ? `${pre}\n\n${text}` : text, convId, contextForTurn(convId, ctx));
    setBusy(false);
    append(r.ok
      ? { role: 'arturo', text: r.reply_text || '(no reply)', tools: r.tools_called, at: Date.now() }
      : { role: 'arturo', text: `Could not reach Arturo: ${r.error || 'unknown'}`, at: Date.now() });
    void listThreads().then(setThreads);      // the thread it just created/updated joins the list
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

  // Escape closes: the backdrop used to be the click-anywhere-to-close, and it is gone.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  /** Sit ABOVE whatever the page already docks at the bottom.
   *  The agent session page docks its own composer there — Inject, the inject-mode toggle and
   *  the two call buttons — and a pill pinned to bottom:20px lands right on top of them
   *  (measured: 4 controls covered). Measured at runtime rather than keyed to routes, so a
   *  page that grows a dock later is handled without touching this file. */
  useEffect(() => {
    const place = () => {
      const vh = window.innerHeight;
      let clear = 0;
      for (const el of Array.from(document.querySelectorAll<HTMLElement>('div,footer,form,section'))) {
        if (el.closest('.arturo-pill-panel') || el.classList.contains('arturo-pill')) continue;
        const cs = getComputedStyle(el);
        if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
        const r = el.getBoundingClientRect();
        if (r.height === 0 || r.width < window.innerWidth * 0.4) continue;
        // A DOCK is a strip. A full-screen overlay (the focused-agent view is fixed inset-0)
        // is not, and treating it as one computed a clearance of the whole viewport and threw
        // the pill off the top of the screen entirely.
        if (r.height > vh * 0.45) continue;
        if (vh - r.bottom > 12) continue;           // not docked to the bottom
        clear = Math.max(clear, Math.round(vh - r.top));
      }
      document.documentElement.style.setProperty('--arturo-pill-bottom', clear ? `${clear + 12}px` : '');
    };
    place();
    window.addEventListener('resize', place);
    const t = window.setInterval(place, 1000);     // docks appear after their data loads
    return () => { window.removeEventListener('resize', place); window.clearInterval(t); };
  }, [location.pathname]);

  function toggleContextCard() {
    if (ctxOn) dismissContext(convId); else restoreContext(convId);
    setCtxOn(!ctxOn);
  }

  if (!open) {
    return (
      <button className="arturo-pill" onClick={() => setOpen(true)} aria-label="Ask Arturo">
        <Mic className="mic" /> Ask Arturo
        {turns.length > 0 && <span className="arturo-pill-count">{Math.ceil(turns.length / 2)}</span>}
      </button>
    );
  }
  return (
    <>
      {/* No backdrop, deliberately (operator, 2026-09-19): the pane must not dim the page and
          must not swallow clicks on it — you keep working while Arturo is open, the way the
          assistant panel in the previous build did. Close with the × or Escape. */}
      <div className="arturo-pill-panel" role="dialog" aria-label="Ask Arturo">
        <div className="arturo-pill-head">
          <span className="who">Arturo</span>
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
              </button>
            ))}
          </div>
        )}

        <div className="arturo-pill-thread" ref={scroller}>
          {turns.length === 0 && !busy && (
            <p className="empty">Ask about what you are looking at, or anything else. Every conversation is kept — open <b>Threads</b> to go back to one.</p>
          )}
          {turns.map((t, i) => (
            <div key={i} className={t.role === 'user' ? 'row user' : 'row arturo'}>
              <div className="bubble">{t.text}</div>
              {t.tools && t.tools.length > 0 && <div className="meta">ran {t.tools.join(', ')}</div>}
            </div>
          ))}
          {busy && <div className="row arturo"><div className="bubble thinking">Thinking…</div></div>}
          {liveUser && <div className="row user"><div className="bubble live">{liveUser}…</div></div>}
          {liveArturo && <div className="row arturo"><div className="bubble live">{liveArturo}…</div></div>}
          {callState === 'connecting' && <div className="row arturo"><div className="bubble thinking">Connecting the call…</div></div>}
          {voiceNote && <div className="row arturo"><div className="bubble">I can't do that yet — {voiceNote}.</div></div>}
        </div>

        <input ref={fileInput} type="file" multiple hidden aria-hidden="true"
               onChange={async (e) => {
                 const picked = Array.from(e.target.files || []);
                 e.target.value = '';
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
        <textarea ref={ta} rows={1} value={draft} placeholder="Ask Arturo" onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }} />
        <div className="ctrl-row">
          {/* The page-context CARD lives HERE, in place of the old "Arturo" chip: it sits
              with the composer because it describes what the NEXT message carries, not
              something that happened earlier in the thread. Default on, × deletes it, and
              the stub it leaves behind puts it back. */}
          <div className="cluster">
            <button className="circle-btn" aria-label="Attach a file" title="Attach a file"
                    onClick={() => fileInput.current?.click()} disabled={uploading}><Plus size={18} /></button>
            {ctxOn ? (
              <span className="arturo-context-chip">
                <Focus size={12} />
                <span className="cc-label">Looking at <b>{contextCardLabel(ctx)}</b></span>
                <button className="cc-x" onClick={toggleContextCard}
                        aria-label="Stop focusing on this page"><X size={12} /></button>
              </span>
            ) : (
              <button className="arturo-context-chip off" onClick={toggleContextCard}
                      aria-label="Use this page for context">
                <Focus size={12} /> <span className="cc-label">Use this page</span>
              </button>
            )}
          </div>
          <div className="cluster">
            <button className={dictMode === 'idle' ? 'circle-btn' : `circle-btn ${dictMode}`}
                    aria-label={dictMode === 'listening' ? 'Stop dictation' : dictMode === 'recording' ? 'Stop recording' : dictMode === 'transcribing' ? 'Transcribing' : 'Dictate'}
                    aria-pressed={dictating} title={inCall ? 'Captions run on their own during a call' : dictMode === 'transcribing' ? 'Transcribing on the server…' : 'Dictate'}
                    onClick={toggleDictation} disabled={inCall || dictMode === 'transcribing'}><Mic size={16} /></button>
            {inCall ? (
              <button className="circle-btn white" aria-label="End call" aria-pressed title="End call"
                      onClick={() => void toggleCall()}><PhoneOff size={18} /></button>
            ) : draft.trim() ? (
              <button className="circle-btn white" aria-label="Send" onClick={() => void send()} disabled={busy}><ArrowUp size={18} /></button>
            ) : (
              <button className="circle-btn white" aria-label="Voice mode" title="Talk to Arturo (live)"
                      onClick={() => void toggleCall()}><AudioLines size={16} /></button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
