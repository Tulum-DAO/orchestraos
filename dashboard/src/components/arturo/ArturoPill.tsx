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
import { Mic, ArrowUp, X, History, Focus } from 'lucide-react';
import './arturo.css';
import { arturoText, newConversationId, contextFromLocation } from '../../lib/arturo';
import {
  listThreads, loadThread, contextCardLabel, isContextDismissed, dismissContext,
  restoreContext, contextForTurn, type ThreadSummary,
} from '../../lib/arturoThreads';

interface PillTurn { role: 'user' | 'arturo'; text: string; tools?: string[]; at: number }
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
  const scroller = useRef<HTMLDivElement>(null);
  const ctx = contextFromLocation(location.pathname, params as Record<string, string | undefined>, location.search);

  /** Pull this thread's turns from the server, so reopening the pill resumes it exactly. */
  const resume = useCallback(async (id: string) => {
    const t = await loadThread(id);
    setTurns((t?.turns || []).map((x) => ({ role: x.role === 'user' ? 'user' : 'arturo', text: x.content, at: (x.ts || 0) * 1000 })));
  }, []);

  useEffect(() => { setCtxOn(!isContextDismissed(convId)); }, [convId]);
  useEffect(() => { if (open) { void resume(convId); void listThreads().then(setThreads); setTimeout(() => ta.current?.focus(), 30); } }, [open, convId, resume]);
  useEffect(() => { scroller.current?.scrollTo({ top: 1e9, behavior: 'smooth' }); }, [turns, open, busy]);

  const append = (t: PillTurn) => setTurns((prev) => [...prev, t]);

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft(''); setBusy(true);
    append({ role: 'user', text, at: Date.now() });
    // The card is the switch: present → this turn carries the page context; deleted → it does not.
    const r = await arturoText(text, convId, contextForTurn(convId, ctx));
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
        </div>

        <textarea ref={ta} rows={1} value={draft} placeholder="Ask Arturo" onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }} />
        <div className="ctrl-row">
          {/* The page-context CARD lives HERE, in place of the old "Arturo" chip: it sits
              with the composer because it describes what the NEXT message carries, not
              something that happened earlier in the thread. Default on, × deletes it, and
              the stub it leaves behind puts it back. */}
          <div className="cluster">
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
            <button className="circle-btn white" aria-label="Send" onClick={() => void send()} disabled={busy || !draft.trim()}><ArrowUp size={18} /></button>
          </div>
        </div>
      </div>
    </>
  );
}
