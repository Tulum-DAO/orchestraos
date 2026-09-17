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

export function ArturoPill() {
  const location = useLocation();
  const params = useParams();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [reply, setReply] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const conv = useRef('');
  useEffect(() => { if (!conv.current) conv.current = newConversationId('pill'); }, []);
  const ta = useRef<HTMLTextAreaElement>(null);
  const ctx = contextFromLocation(location.pathname, params as Record<string, string | undefined>, location.search);

  useEffect(() => { if (open) setTimeout(() => ta.current?.focus(), 30); }, [open]);
  const [replyRoute, setReplyRoute] = useState(location.pathname);
  const shownReply = replyRoute === location.pathname ? reply : '';

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true); setDraft('');
    setReply('…'); setReplyRoute(location.pathname);
    const r = await arturoText(text, conv.current, ctx);
    setBusy(false);
    setReply(r.ok ? (r.reply_text || '(no reply)') + ((r.tools_called || []).length ? `\n— ran ${r.tools_called!.join(', ')}` : '')
      : `Could not reach Arturo: ${r.error || 'unknown'}`);
  }

  const label = `${ctx.entityKind}${ctx.entityId ? ' · ' + ctx.entityId : ''}`;

  if (!open) {
    return (
      <button className="arturo-pill" onClick={() => setOpen(true)} aria-label="Ask Arturo">
        <Mic className="mic" /> Ask Arturo
      </button>
    );
  }
  return (
    <>
      <div className="arturo-pill-back" onClick={() => setOpen(false)} />
      <div className="arturo-pill-panel" role="dialog" aria-label="Ask Arturo">
        <div className="context-chip">Context: <b>{label}</b>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 10 }}>
            <Link to="/" style={{ color: '#d97757', textDecoration: 'none' }}>Open Arturo</Link>
            <button onClick={() => setOpen(false)} aria-label="Close" style={{ background: 'none', border: 0, color: '#fff' }}><X size={14} /></button>
          </span>
        </div>
        {shownReply && <div className="reply">{shownReply}</div>}
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
