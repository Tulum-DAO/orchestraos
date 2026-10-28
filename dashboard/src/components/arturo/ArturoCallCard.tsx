/**
 * ArturoCallCard — one voice call in Arturo's feed: an inset card holding the call's turns as bubbles, under a
 * centred "Voice chat · 00:15" header with a menu, thumbs and a close (collapse) button. Collapsed, only that
 * header line stays, with no box; tapping it opens the card again. Live, the header counts up and the
 * caption being spoken shows faded at the end.
 *
 * Arturo's page only. Its colours are Arturo's own variables (arturo.css), so light and dark come from the same
 * rules and the bubbles match Arturo's typed ones; agent chats keep their VoiceCallCard as it is.
 *
 * The turns come from the thread itself (a call's turns are stored there, mode "voice"), so nothing is fetched
 * to draw them. The saved call record is read only for the exact call length, and for the turns when the
 * thread has none (a call that ended before any were stored here).
 */
import { useEffect, useRef, useState } from 'react';
import { AudioLines, MoreHorizontal, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import ToolRun from './ToolRun';
import { groupParts, hasToolParts, type TurnPart } from '../../lib/turnParts';
import { fetchVoiceCall, type VoiceCall } from '../../lib/api';
import { fmtClock, transcriptText } from '../../lib/arturoCallGroups';
import { assistantName } from '../../lib/assistantName';

export interface CallBubble { role: 'user' | 'arturo'; text: string; parts?: TurnPart[] }

// One read per call per page load, however often the feed re-renders.
const records = new Map<string, Promise<VoiceCall | null>>();
function callRecord(id: string): Promise<VoiceCall | null> {
  let p = records.get(id);
  if (!p) {
    p = fetchVoiceCall(id).then((r) => (r.status === 200 && r.ok && r.call ? r.call : null)).catch(() => null);
    records.set(id, p);
    // A miss is not remembered: a call that has just ended may not be saved yet.
    void p.then((c) => { if (!c) records.delete(id); });
  }
  return p;
}

export function ArturoCallCard({ callId, turns, live, startTs, endTs, liveSince, liveUser, liveArturo, connecting }: {
  callId: string | null;
  turns: CallBubble[];
  live: boolean;
  /** Thread times (epoch seconds), the length until the record says exactly. */
  startTs?: number;
  endTs?: number;
  /** When this page's live call started (ms), for the running clock. */
  liveSince?: number | null;
  /** The caption being spoken right now, not yet committed. */
  liveUser?: string;
  liveArturo?: string;
  connecting?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const [record, setRecord] = useState<VoiceCall | null>(null);
  const [recordRead, setRecordRead] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [thumb, setThumb] = useState<'up' | 'down' | null>(null);
  const [menu, setMenu] = useState(false);
  const [copied, setCopied] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!callId) return;
    let alive = true;
    void callRecord(callId).then((c) => { if (alive) { setRecord(c); setRecordRead(true); } });
    return () => { alive = false; };
  }, [callId]);

  // The live clock ticks once a second, and only while the call is live.
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);

  useEffect(() => {
    if (!menu) return;
    const down = (e: PointerEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenu(false); };
    document.addEventListener('pointerdown', down);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', down); document.removeEventListener('keydown', key); };
  }, [menu]);

  const seconds = live
    ? (liveSince ? (now - liveSince) / 1000 : 0)
    : record?.started_at && record?.ended_at ? record.ended_at - record.started_at
    : startTs && endTs ? endTs - startTs : null;
  // An unknown length is left out rather than shown as a placeholder.
  const label = seconds == null ? 'Voice chat' : `Voice chat · ${fmtClock(seconds)}`;

  // The record's turns stand in only when the thread holds none for this call.
  const shown: CallBubble[] = turns.length ? turns
    : (record?.turns || []).filter((t) => (t.role === 'user' || t.role === 'arturo') && (t.text || '').trim())
        .map((t) => ({ role: t.role === 'user' ? 'user' as const : 'arturo' as const, text: t.text || '' }));

  const copy = async () => {
    try { await navigator.clipboard.writeText(transcriptText(shown, assistantName())); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* the browser refused the clipboard: nothing copied, nothing claimed */ }
    setMenu(false);
  };

  const thumbs = (
    <span className="call-thumbs">
      <button type="button" className="call-icon hit-44" aria-label="Good call" aria-pressed={thumb === 'up'}
              onClick={(e) => { e.stopPropagation(); setThumb((v) => (v === 'up' ? null : 'up')); }}>
        <ThumbsUp size={14} fill={thumb === 'up' ? 'currentColor' : 'none'} />
      </button>
      <button type="button" className="call-icon hit-44" aria-label="Bad call" aria-pressed={thumb === 'down'}
              onClick={(e) => { e.stopPropagation(); setThumb((v) => (v === 'down' ? null : 'down')); }}>
        <ThumbsDown size={14} fill={thumb === 'down' ? 'currentColor' : 'none'} />
      </button>
    </span>
  );

  if (!open) {
    return (
      <div className="call-card is-collapsed touch-compact" role="group" aria-label={label}>
        <div className="call-head">
          <span />
          <span className="call-title">
            <button type="button" className="call-label" aria-expanded={false} onClick={() => setOpen(true)}>
              <AudioLines size={15} aria-hidden /><span>{label}</span>
            </button>
            {thumbs}
          </span>
          <span />
        </div>
      </div>
    );
  }

  return (
    <div className={`call-card touch-compact${live ? ' is-live' : ''}`} role="group" aria-label={label}>
      <div className="call-head">
        <div className="call-menu" ref={menuRef}>
          <button type="button" className="call-icon hit-44" aria-label="Call options" aria-haspopup="menu" aria-expanded={menu}
                  onClick={() => setMenu((m) => !m)}>
            <MoreHorizontal size={16} />
          </button>
          {menu && (
            <div className="call-menu-list" role="menu">
              <button type="button" role="menuitem" disabled={!shown.length} onClick={() => { void copy(); }}>Copy transcript</button>
            </div>
          )}
        </div>
        <span className="call-title">
          <span className="call-label">
            <AudioLines size={15} aria-hidden /><span>{copied ? 'Copied' : label}</span>
          </span>
          {thumbs}
        </span>
        <button type="button" className="call-icon hit-44" aria-label="Collapse voice chat" aria-expanded onClick={() => { setOpen(false); setMenu(false); }}>
          <X size={16} />
        </button>
      </div>
      <div className="call-body">
        {shown.map((t, i) => t.role === 'user'
          ? <div key={i} className="call-bubble user">{t.text}</div>
          : hasToolParts(t.parts)
            ? groupParts(t.parts!).map((g, gi) => g.kind === 'tools'
                ? <div key={`${i}-${gi}`} className="call-tools"><ToolRun tools={g.tools} /></div>
                : g.text.trim() ? <div key={`${i}-${gi}`} className="call-bubble arturo">{g.text}</div> : null)
            : t.text.trim() ? <div key={i} className="call-bubble arturo">{t.text}</div> : null)}
        {liveUser && <div className="call-bubble user is-partial">{liveUser}…</div>}
        {liveArturo && <div className="call-bubble arturo is-partial">{liveArturo}…</div>}
        {connecting && !shown.length && <p className="call-note">Connecting…</p>}
        {!live && !connecting && !shown.length && (
          <p className="call-note">{callId && !recordRead ? 'Loading the transcript…' : record ? 'Nothing was said on this call.' : 'No transcript was saved for this call.'}</p>
        )}
      </div>
    </div>
  );
}
