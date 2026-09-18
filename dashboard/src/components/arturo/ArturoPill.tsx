/**
 * ArturoPill — the always-available "Ask Arturo" pill on every non-home page (track T4, S5/S6).
 * Tap → the composer expands over the dimmed page with a context chip carrying
 * {route, entityKind, entityId, hint} derived from the current URL; the turn goes through the
 * same POST /api/arturo/text path as the home, with the context as its first line.
 */
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { Mic, ArrowUp, X } from 'lucide-react';
import './arturo.css';
import { arturoText, newConversationId, contextFromLocation } from '../../lib/arturo';

/** One thread, kept for the life of the install (not the page): the pill is the same
 *  conversation wherever you open it, and its history is visible in the pane — the same
 *  contract as the dashboard's assistant panel, not a one-shot floating reply. */
interface PillTurn { role: 'user' | 'arturo'; text: string; tools?: string[]; route?: string; at: number }
const LS_THREAD = 'orchestra.arturo.pill.thread';
const LS_CONV = 'orchestra.arturo.pill.conversation';
const MAX_KEPT = 200;

function loadThread(): PillTurn[] {
  try {
    const raw = localStorage.getItem(LS_THREAD);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(-MAX_KEPT) : [];
  } catch { return []; }
}
function saveThread(turns: PillTurn[]) {
  try { localStorage.setItem(LS_THREAD, JSON.stringify(turns.slice(-MAX_KEPT))); } catch { /* private mode */ }
}
function loadConv(): string {
  try {
    const existing = localStorage.getItem(LS_CONV);
    if (existing) return existing;
    const fresh = newConversationId('pill');
    localStorage.setItem(LS_CONV, fresh);
    return fresh;
  } catch { return newConversationId('pill'); }
}


export function ArturoPill() {
  const location = useLocation();
  const params = useParams();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  // Seed from storage in the initializer, not an effect: the thread is already there when
  // the panel first paints, and no cascading render is triggered.
  const [turns, setTurns] = useState<PillTurn[]>(loadThread);
  const [busy, setBusy] = useState(false);
  const conv = useRef('');
  const ta = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const ctx = contextFromLocation(location.pathname, params as Record<string, string | undefined>, location.search);

  // The thread outlives the panel and the page: seeded above, conversation id resolved once.
  useEffect(() => { if (!conv.current) conv.current = loadConv(); }, []);
  useEffect(() => { if (open) setTimeout(() => ta.current?.focus(), 30); }, [open]);
  useEffect(() => { scroller.current?.scrollTo({ top: 1e9, behavior: 'smooth' }); }, [turns, open, busy]);

  const append = (t: PillTurn) => setTurns((prev) => { const next = [...prev, t].slice(-MAX_KEPT); saveThread(next); return next; });

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft(''); setBusy(true);
    append({ role: 'user', text, route: ctx.route, at: Date.now() });
    const r = await arturoText(text, conv.current, ctx);
    setBusy(false);
    append(r.ok
      ? { role: 'arturo', text: r.reply_text || '(no reply)', tools: r.tools_called, at: Date.now() }
      : { role: 'arturo', text: `Could not reach Arturo: ${r.error || 'unknown'}`, at: Date.now() });
  }

  function clearThread() {
    setTurns([]); saveThread([]);
    try { const fresh = newConversationId('pill'); localStorage.setItem(LS_CONV, fresh); conv.current = fresh; } catch { /* private mode */ }
  }

  const label = `${ctx.entityKind}${ctx.entityId ? ' · ' + ctx.entityId : ''}`;

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
      <div className="arturo-pill-back" onClick={() => setOpen(false)} />
      <div className="arturo-pill-panel" role="dialog" aria-label="Ask Arturo">
        <div className="arturo-pill-head">
          <span className="who">Arturo</span>
          <span className="ctx">{label}</span>
          <span className="spacer" />
          {turns.length > 0 && (
            <button className="linkish" onClick={clearThread} aria-label="Start a new thread">New thread</button>
          )}
          <Link to="/" className="linkish accent">Open Arturo</Link>
          <button onClick={() => setOpen(false)} aria-label="Close" className="linkish"><X size={14} /></button>
        </div>

        <div className="arturo-pill-thread" ref={scroller}>
          {turns.length === 0 && !busy && (
            <p className="empty">Ask about what you are looking at, or anything else. This thread is kept — it is the same conversation on every page.</p>
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
          <div className="cluster"><span className="model-chip"><b>Arturo</b></span></div>
          <div className="cluster">
            <button className="circle-btn white" aria-label="Send" onClick={() => void send()} disabled={busy || !draft.trim()}><ArrowUp size={18} /></button>
          </div>
        </div>
      </div>
    </>
  );
}
