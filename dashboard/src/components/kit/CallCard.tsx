/**
 * CallCard — the thread kit's one voice-call card, wherever a conversation shows a call (lib/threadKit/calls.ts reads
 * the marker, the call-ended line, a thread's call turns and a saved call record). Two skins of the same card:
 *
 *  - "modern" (Arturo's page) and "pane" (his floating pane): an inset card holding the call's turns as bubbles,
 *    under a centred "Voice chat · 00:15" header with a menu (Copy transcript), thumbs and a collapse button.
 *    Collapsed, only the header line stays. Live, the clock counts up and the caption being spoken shows faded.
 *    The turns come from the thread (a call's turns are stored there, mode "voice"); the saved record is read for the
 *    exact length, and for the turns when the thread holds none. Colours are Arturo's own variables (arturo.css; the
 *    pane sets its dark set), so the page and the pane read the same.
 *  - "bubbles" (an agent's chat, where a call shows as a `[voice-call:]` marker): a gold-edged card in the theme's
 *    colours (it was dark-only, so its header all but vanished in light mode), collapsed until opened; opening reads the saved record and shows its summary and turns. Unreadable: a quiet "transcript
 *    unavailable" chip. A live call older than 4h reads as stale.
 *
 * Either way a tool run on the call is the kit's tool card (components/kit/ToolCard.tsx), and a card id said on the
 * call is a card chip, never the id.
 */
import { useEffect, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { AudioLines, Loader2, MoreHorizontal, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import ToolCard from './ToolCard';
import { withRefs } from './RefText';
import type { Skin } from './Markdown';
import { fromPart } from '../../lib/toolCalls';
import { groupParts, hasToolParts, type TurnPart } from '../../lib/turnParts';
import { fetchVoiceCall } from '../../lib/api';
import { callCounts, fmtCallDuration, fmtClock, isStaleLive, recordLines, transcriptText, type CallLine, type CallRecord } from '../../lib/threadKit/calls';
import { assistantName } from '../../lib/assistantName';

export interface CallBubble { role: 'user' | 'arturo'; text: string; parts?: TurnPart[] }

// One read per call per page load, however often the feed re-renders. A miss is not remembered: a call that has just
// ended may not be saved yet.
const records = new Map<string, Promise<CallRecord | null>>();
function callRecord(id: string): Promise<CallRecord | null> {
  let p = records.get(id);
  if (!p) {
    p = fetchVoiceCall(id).then((r) => (r.status === 200 && r.ok && r.call ? r.call as CallRecord : null)).catch(() => null);
    records.set(id, p);
    void p.then((c) => { if (!c) records.delete(id); });
  }
  return p;
}

/** The saved record of a call, read once `enabled`: the record (null when there is none) and whether it was read. */
function useCallRecord(callId: string | null, enabled: boolean): { record: CallRecord | null; read: boolean } {
  const [state, setState] = useState<{ id: string | null; record: CallRecord | null; read: boolean }>({ id: null, record: null, read: false });
  useEffect(() => {
    if (!callId || !enabled) return;
    let alive = true;
    void callRecord(callId).then((c) => { if (alive) setState({ id: callId, record: c, read: true }); });
    return () => { alive = false; };
  }, [callId, enabled]);
  return state.id === callId ? { record: state.record, read: state.read } : { record: null, read: false };
}

export interface CallCardProps {
  callId: string | null;
  skin?: Skin;
  /** The call's turns from the thread (Arturo's skins); none = the saved record's. */
  turns?: CallBubble[];
  live?: boolean;
  /** Thread times (epoch seconds), the length until the record says exactly. */
  startTs?: number;
  endTs?: number;
  /** When this page's live call started (ms), for the running clock. */
  liveSince?: number | null;
  /** The caption being spoken right now, not yet committed. */
  liveUser?: string;
  liveArturo?: string;
  connecting?: boolean;
}

export default function CallCard(p: CallCardProps) {
  return p.skin === 'bubbles' ? <ChatCallCard callId={p.callId} /> : <ArturoSkin {...p} skin={p.skin ?? 'modern'} />;
}

/** A record's lines as the call's bubbles and tool cards, in the skin's classes. */
function Lines({ lines, skin, bubble }: { lines: CallLine[]; skin: Skin; bubble: (role: 'user' | 'arturo') => string }) {
  return (
    <>
      {lines.map((l, i) => l.kind === 'tools'
        ? <div key={i} className={skin === 'bubbles' ? undefined : 'call-tools'}><ToolCard calls={l.calls} skin={skin} /></div>
        : skin === 'bubbles'
          ? <div key={i} className={clsx('flex', l.role === 'user' ? 'justify-end' : 'justify-start')}><div className={bubble(l.role)}>{withRefs(l.text, skin)}</div></div>
          : <div key={i} className={bubble(l.role)}>{withRefs(l.text, skin)}</div>)}
    </>
  );
}

function ArturoSkin({ callId, turns = [], live = false, startTs, endTs, liveSince, liveUser, liveArturo, connecting, skin }: CallCardProps & { skin: Skin }) {
  const [open, setOpen] = useState(true);
  const { record, read } = useCallRecord(callId, true);
  const [now, setNow] = useState(() => Date.now());
  const [thumb, setThumb] = useState<'up' | 'down' | null>(null);
  const [menu, setMenu] = useState(false);
  const [copied, setCopied] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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

  // The record's lines stand in only when the thread holds none for this call.
  const fromRecord = turns.length ? [] : recordLines(record?.turns);
  const said = turns.length ? turns : fromRecord.flatMap((l) => (l.kind === 'say' ? [{ role: l.role, text: l.text }] : []));

  const copy = async () => {
    try { await navigator.clipboard.writeText(transcriptText(said, assistantName())); setCopied(true); setTimeout(() => setCopied(false), 1500); }
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
      <div className="call-card is-collapsed touch-compact" role="group" aria-label={label} data-call-card={callId ?? 'live'} data-skin={skin}>
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

  const bubble = (role: 'user' | 'arturo') => `call-bubble ${role}`;
  return (
    <div className={`call-card touch-compact${live ? ' is-live' : ''}`} role="group" aria-label={label} data-call-card={callId ?? 'live'} data-skin={skin}>
      <div className="call-head">
        <div className="call-menu" ref={menuRef}>
          <button type="button" className="call-icon hit-44" aria-label="Call options" aria-haspopup="menu" aria-expanded={menu}
                  onClick={() => setMenu((m) => !m)}>
            <MoreHorizontal size={16} />
          </button>
          {menu && (
            <div className="call-menu-list" role="menu">
              <button type="button" role="menuitem" disabled={!said.length} onClick={() => { void copy(); }}>Copy transcript</button>
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
        {turns.length ? turns.map((t, i) => t.role === 'user'
          ? <div key={i} className={bubble('user')}>{withRefs(t.text, skin)}</div>
          : hasToolParts(t.parts)
            ? groupParts(t.parts!).map((g, gi) => g.kind === 'tools'
                ? <div key={`${i}-${gi}`} className="call-tools"><ToolCard calls={g.tools.map(fromPart)} skin={skin} /></div>
                : g.text.trim() ? <div key={`${i}-${gi}`} className={bubble('arturo')}>{withRefs(g.text, skin)}</div> : null)
            : t.text.trim() ? <div key={i} className={bubble('arturo')}>{withRefs(t.text, skin)}</div> : null)
          : <Lines lines={fromRecord} skin={skin} bubble={bubble} />}
        {liveUser && <div className="call-bubble user is-partial">{liveUser}…</div>}
        {liveArturo && <div className="call-bubble arturo is-partial">{liveArturo}…</div>}
        {connecting && !said.length && <p className="call-note">Connecting…</p>}
        {!live && !connecting && !said.length && !fromRecord.length && (
          <p className="call-note">{callId && !read ? 'Loading the transcript…' : record ? 'Nothing was said on this call.' : 'No transcript was saved for this call.'}</p>
        )}
      </div>
    </div>
  );
}

/** An agent's chat: collapsed until opened; opening reads the saved record. */
function ChatCallCard({ callId }: { callId: string | null }) {
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(false);
  const { record, read } = useCallRecord(callId, asked);
  const failed = asked && read && !record;
  const loading = asked && !read;

  // Unreadable, and closed: a plain chip.
  if (failed && !open) {
    return (
      <div className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted px-2.5 py-1 text-[11px] text-muted-foreground"
           data-call-card={callId ?? ''} data-skin="bubbles">
        ☎ Voice call · transcript unavailable
      </div>
    );
  }

  const counts = record?.turns ? callCounts(record.turns) : null;
  const dur = record ? fmtCallDuration(record.started_at, record.ended_at) : null;
  const stale = record ? isStaleLive(record.status, record.started_at) : false;
  const live = record?.status === 'live' && !stale;
  const bubble = (role: 'user' | 'arturo') => clsx('max-w-[85%] rounded-2xl px-3 py-1.5 text-[12px] leading-relaxed whitespace-pre-wrap break-words',
    role === 'user' ? 'rounded-br-sm bg-blue-600/80 text-white' : 'rounded-bl-sm bg-muted text-foreground');

  return (
    <div className="rounded-lg border border-amber-500/45 bg-amber-500/5 overflow-hidden max-w-[92%]" data-call-card={callId ?? ''} data-skin="bubbles">
      <button onClick={() => { setOpen((o) => !o); setAsked(true); }} aria-expanded={open}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-amber-500/10">
        <span className="text-[12px] text-amber-600 dark:text-amber-300">☎</span>
        <span className="text-[12px] font-medium text-foreground">Voice call</span>
        {counts && dur && (
          <span className="text-[11px] text-muted-foreground">· {dur} · {counts.talk} turns · {counts.tool} tool runs</span>
        )}
        {live && <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400">live</span>}
        {stale && <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">stale</span>}
        <span className="text-[10px] text-muted-foreground ml-auto">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="border-t border-amber-500/20 px-2.5 py-2 space-y-2">
          {loading && (
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Loader2 size={13} className="animate-spin" /> loading transcript…
            </div>
          )}
          {failed && <div className="text-[11px] text-amber-600 dark:text-amber-400/90">Transcript unavailable — try again shortly.</div>}
          {record && (
            <>
              {record.summary && (
                <div className="text-[11px] text-muted-foreground border-l-2 border-border pl-2 whitespace-pre-wrap">{withRefs(record.summary)}</div>
              )}
              <div className="space-y-1.5"><Lines lines={recordLines(record.turns)} skin="bubbles" bubble={bubble} /></div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
