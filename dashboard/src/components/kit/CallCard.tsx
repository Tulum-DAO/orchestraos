/**
 * CallCard — THE one voice-call card, for Arturo and for every agent (lib/threadKit/calls.ts reads the marker, the
 * call-ended line, a thread's call turns and a saved call record). One look everywhere, Arturo's: centred in the
 * thread at one width, a centred "Voice chat · 00:15" header with a menu (Copy transcript), thumbs and an X that
 * collapses it to that one centred line; click the label to open it again. A long call scrolls inside a fixed height
 * (callCard.css), following the end while it is live.
 *
 *  - Arturo's page ("modern") and his floating pane ("pane"): the call's turns from the thread, open by default. Live,
 *    the clock counts up and the caption being spoken shows faded. Colours are Arturo's own variables (arturo.css;
 *    the pane sets its dark set), so the page and the pane read the same.
 *  - An agent's chat (CallAgentContext; "bubbles" asks for the same card): the call as that agent's calls route keeps
 *    it (lib/agentCalls.ts), collapsed by default. Its turns are labelled You / the agent's name, its thumbs are
 *    saved through the route, and a chip sits where the agent's work started ("gm ran 3 steps") and brings that
 *    spot of the thread into view. A server without the route yet: the saved call record's turns, as on Arturo's.
 *    In the Bubbles chat the card carries the same colour set (callCard.css .call-scope), light and dark.
 *
 * A tool run on the call is the kit's tool card (components/kit/ToolCard.tsx), and a card id said on the call is a
 * card chip, never the id.
 */
import { useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AudioLines, ListTree, MoreHorizontal, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { CallAgentContext } from '../chat/callAgentContext';
import { fetchAgentCall, focusWork, saveCallRating, withWork, type AgentCall } from '../../lib/agentCalls';
import './panel.css';
import '../arturo/arturo.css';
import './callCard.css';
import ToolCard from './ToolCard';
import { withRefs } from './RefText';
import { MessageText } from './Message';
import { AgentTurn, UserTurn } from './Turns';
import type { Skin } from './Markdown';
import { fromPart } from '../../lib/toolCalls';
import { groupParts, hasToolParts, type TurnPart } from '../../lib/turnParts';
import { fetchVoiceCall } from '../../lib/api';
import { fmtClock, recordLines, transcriptText, type CallLine, type CallRecord } from '../../lib/threadKit/calls';
import { assistantName } from '../../lib/assistantName';

/** `key`: the turn's own name in the thread (stable as older turns of the call load above it). */
export interface CallBubble { role: 'user' | 'arturo'; text: string; parts?: TurnPart[]; key?: string }

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

/** Arturo's tools that hand the work to a background job (services/arturo: async_task and the tools built on it). */
const JOB_TOOLS = new Set(['async_task', 'research', 'deep_query', 'deep_think', 'ask_gm', 'spawn_agent']);
/** Open Arturo's job drawer (components/arturo/JobTray.tsx listens). */
export const OPEN_JOBS_EVENT = 'orchestra:open-jobs';
const openJobs = () => window.dispatchEvent(new Event(OPEN_JOBS_EVENT));

export default function CallCard(p: CallCardProps) {
  const agent = useContext(CallAgentContext);
  // An agent's chat: its ended calls are that agent's (their transcript, thumbs and work from its calls route).
  const ofAgent = agent && p.callId && !p.live ? agent : null;
  // Bubbles has no Arturo colour set around it: the card brings its own (callCard.css).
  return <OneCallCard {...p} skin={p.skin === 'bubbles' ? 'modern' : (p.skin ?? 'modern')} agent={ofAgent} scoped={p.skin === 'bubbles'} />;
}

/** One agent call from its calls route: undefined while asked, null when the route has none (404 or not yet). */
function useAgentCall(agent: { agentId: string } | null, callId: string | null): AgentCall | null | undefined {
  const [got, setGot] = useState<{ key: string; call: AgentCall | null } | null>(null);
  const key = agent && callId ? `${agent.agentId}/${callId}` : '';
  useEffect(() => {
    if (!agent || !callId) return;
    let alive = true;
    void fetchAgentCall(agent.agentId, callId).then((c) => { if (alive) setGot({ key: `${agent.agentId}/${callId}`, call: c }); });
    return () => { alive = false; };
  }, [agent, callId]);
  if (!key) return null;
  return got?.key === key ? got.call : undefined;
}

/** A record's lines as the call's bubbles and tool cards, in the skin's classes. */
function Lines({ lines, skin, bubble }: { lines: CallLine[]; skin: Skin; bubble: (role: 'user' | 'arturo') => string }) {
  return (
    <>
      {lines.map((l, i) => l.kind === 'tools'
        ? <div key={i} className="call-tools"><ToolCard calls={l.calls} skin={skin} /></div>
        : <div key={i} className={bubble(l.role)}>{withRefs(l.text, skin)}</div>)}
    </>
  );
}

function OneCallCard({ callId, turns = [], live = false, startTs, endTs, liveSince, liveUser, liveArturo, connecting, skin, agent, scoped }:
  CallCardProps & { skin: Skin; agent: { agentId: string; name: string; skin?: 'bubbles' | 'modern' } | null; scoped: boolean }) {
  // An agent's call turns take its chat's skin; Arturo's keep his card's own bubbles.
  const turnSkin = agent?.skin ?? 'modern';
  // Arturo's call is open by default (it is the conversation); an agent's ended call is one line until opened.
  const [open, setOpen] = useState(!agent);
  const agentCall = useAgentCall(agent, callId);
  // The saved record: Arturo's always; an agent's only when its calls route has no such call (null).
  const { record, read } = useCallRecord(callId, !agent || agentCall === null);
  const [now, setNow] = useState(() => Date.now());
  const [thumb, setThumb] = useState<'up' | 'down' | null>(null);
  const [rated, setRated] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  // The route's saved thumbs, once it has answered (until you change them here).
  if (agentCall && rated !== agentCall.call_id) { setRated(agentCall.call_id); setThumb(agentCall.rating ?? null); }
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
    : typeof agentCall?.duration_s === 'number' ? agentCall.duration_s
    : record?.started_at && record?.ended_at ? record.ended_at - record.started_at
    : startTs && endTs ? endTs - startTs : null;
  // An unknown length is left out rather than shown as a placeholder.
  const label = seconds == null ? 'Voice chat' : `Voice chat · ${fmtClock(seconds)}`;
  const who = agent?.name || assistantName();

  // The record's lines stand in only when the thread holds none for this call.
  const agentTurns = agentCall && Array.isArray(agentCall.turns) ? agentCall.turns : [];
  const fromRecord = turns.length || agentCall ? [] : recordLines(record?.turns);
  const said = turns.length ? turns
    : agentCall ? agentTurns.map((t) => ({ role: t.role === 'user' ? 'user' : 'arturo', text: t.text }))
    : fromRecord.flatMap((l) => (l.kind === 'say' ? [{ role: l.role, text: l.text }] : []));

  // A long call scrolls inside the card; while it is live, and you are at its end, it follows the end.
  const grew = `${said.length}|${liveUser?.length ?? 0}|${liveArturo?.length ?? 0}|${open}`;
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el && live && pinned.current) el.scrollTop = el.scrollHeight;
  }, [grew, live]);

  const copy = async () => {
    try { await navigator.clipboard.writeText(transcriptText(said, who)); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* the browser refused the clipboard: nothing copied, nothing claimed */ }
    setMenu(false);
  };

  // An agent's thumbs are saved through its calls route; a failed save puts the old ones back and says so.
  const rate = async (r: 'up' | 'down') => {
    const next = thumb === r ? null : r;
    const before = thumb;
    setThumb(next); setNote(null);
    if (agent && agentCall && callId && !(await saveCallRating(agent.agentId, callId, next))) { setThumb(before); setNote("Couldn't save that. Try again."); }
  };
  const thumbs = (
    <span className="call-thumbs">
      <button type="button" className="call-icon hit-44" aria-label="Good call" aria-pressed={thumb === 'up'} data-call-thumb="up"
              onClick={(e) => { e.stopPropagation(); void rate('up'); }}>
        <ThumbsUp size={14} fill={thumb === 'up' ? 'currentColor' : 'none'} />
      </button>
      <button type="button" className="call-icon hit-44" aria-label="Bad call" aria-pressed={thumb === 'down'} data-call-thumb="down"
              onClick={(e) => { e.stopPropagation(); void rate('down'); }}>
        <ThumbsDown size={14} fill={thumb === 'down' ? 'currentColor' : 'none'} />
      </button>
    </span>
  );

  if (!open) {
    return (
      <div className={`call-card is-collapsed touch-compact${scoped ? ' call-scope' : ''}`} role="group" aria-label={label} data-call-card={callId ?? 'live'} data-skin={skin}
           data-agent-call={agent ? callId ?? '' : undefined}>
        <div className="call-head">
          <span />
          <span className="call-title">
            <button type="button" className="call-label" aria-expanded={false} onClick={() => setOpen(true)} data-call-open>
              <AudioLines size={15} aria-hidden /><span>{label}</span>
            </button>
            {thumbs}
          </span>
          <span />
        </div>
        {note && <p className="call-note" role="alert">{note}</p>}
      </div>
    );
  }

  const bubble = (role: 'user' | 'arturo') => `call-bubble ${role}`;
  return (
    <div className={`call-card touch-compact${live ? ' is-live' : ''}${scoped ? ' call-scope' : ''}`} role="group" aria-label={label} data-call-card={callId ?? 'live'} data-skin={skin}
         data-agent-call={agent ? callId ?? '' : undefined}>
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
      {/* Its lines are anchor rows of their own (lib/useStickToBottom.ts): a reader inside a long call stays on the line
          they are reading while older turns of it load above. */}
      {note && <p className="call-note" role="alert">{note}</p>}
      <div className="call-body" data-anchor-rows ref={bodyRef} data-call-scroll
           onScroll={(e) => { const el = e.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}>
        {agentCall ? (
          <>
            {withWork(agentTurns, agentCall.work).map((e, i) => e.kind === 'turn' ? (
              // In the agent's own chat skin (components/kit/Turns): Bubbles shows no names, Modern labels each turn.
              <div key={i} className="call-said-row" data-call-said={e.turn.role} data-turn-skin={turnSkin}>
                {e.turn.role === 'user'
                  ? <UserTurn skin={turnSkin} label={turnSkin === 'modern' ? <span className="call-who">You</span> : undefined}>{withRefs(e.turn.text, turnSkin)}</UserTurn>
                  : <AgentTurn skin={turnSkin} label={turnSkin === 'modern' ? <span className="call-who">{agent!.name}</span> : undefined}>
                      <MessageText text={e.turn.text} skin={turnSkin} />
                    </AgentTurn>}
              </div>
            ) : (
              <button key={i} type="button" className="call-work-chip" data-work-chip={e.work.uuid}
                      onClick={() => { if (!focusWork(e.work.uuid)) setNote('That part of the chat is further up: scroll up to load it.'); }}>
                <ListTree size={12} aria-hidden /> {agent!.name} ran {e.work.steps} {e.work.steps === 1 ? 'step' : 'steps'}
              </button>
            ))}
            {agentTurns.length === 0 && <p className="call-note">No words were kept for this call.</p>}
          </>
        ) : turns.length ? turns.map((t, i) => t.role === 'user'
          ? <div key={t.key ?? i} className={bubble('user')}>{withRefs(t.text, skin)}</div>
          : hasToolParts(t.parts)
            ? groupParts(t.parts!).map((g, gi) => g.kind === 'tools'
                ? (
                  <div key={`${t.key ?? i}-${gi}`} className="call-tools">
                    <ToolCard calls={g.tools.map(fromPart)} skin={skin} />
                    {/* Arturo handed work to a job: a chip that opens his job drawer (JobTray) */}
                    {g.tools.some((x) => JOB_TOOLS.has(x.name)) && (
                      <button type="button" className="call-job-chip mt-1 inline-flex items-center gap-1.5 rounded-full border border-line bg-field px-2.5 py-0.5 text-px12 text-ink-2 hover:text-ink"
                              onClick={openJobs} data-job-chip>
                        Background job · open
                      </button>
                    )}
                  </div>
                )
                : g.text.trim() ? <div key={`${t.key ?? i}-${gi}`} className={bubble('arturo')}>{withRefs(g.text, skin)}</div> : null)
            : t.text.trim() ? <div key={t.key ?? i} className={bubble('arturo')}>{withRefs(t.text, skin)}</div> : null)
          : <Lines lines={fromRecord} skin={skin} bubble={bubble} />}
        {liveUser && <div className="call-bubble user is-partial">{liveUser}…</div>}
        {liveArturo && <div className="call-bubble arturo is-partial">{liveArturo}…</div>}
        {connecting && !said.length && <p className="call-note">Connecting…</p>}
        {!agentCall && !live && !connecting && !said.length && !fromRecord.length && (
          <p className="call-note">{callId && (!read || agentCall === undefined) ? 'Loading the transcript…' : record ? 'Nothing was said on this call.' : 'No transcript was saved for this call.'}</p>
        )}
      </div>
    </div>
  );
}
