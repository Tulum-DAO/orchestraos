/**
 * ThreadView — the thread kit's view of a conversation: the rows of lib/threadKit/view.ts drawn with the kit's parts
 * (Message and Markdown, the tool card, the call card, the result card, the choice card, card chips). Arturo's page
 * and every agent's page draw their thread through it.
 *
 * Skins: Arturo's page draws "modern" (his own) or "bubbles" (the agent chat's look); an agent's page "bubbles" (its
 * own) or "modern" (Arturo's look: the variables come from kit-modern.css on the page's column). In Bubbles, a call
 * keeps Arturo's call card, the only one that holds a call's turns as they happen. In Modern, an agent's plain
 * messages are drawn here, each with its time (`times`); everything else keeps the chat's own primitive.
 *
 * An agent's page passes node rows, drawn by `renderNode` (the agent chat's own primitives:
 * TranscriptCards GroupedNodeView, built on the same kit parts: tool runs, result cards, answered 4c question cards,
 * system cards, the interrupted run), its decision cards (DecisionBubble, the 4c card) and its just-sent messages
 * (PendingBubble).
 *
 *   - a reply: the thinking dot while it is pending; its words and tool runs in the order they happened; an older
 *     reply's call marker as its call card; "ran …" when only tool names are known; Copy; then the page's extras
 *     (`extras`: what only that page has, such as a pairing code) and its choice card, answered from the server's
 *     record (lib/arturoResume) and sent through the page's own send (`choice.onAnswer`, so a tap keeps its send
 *     states);
 *   - the operator's message: its images, its words (card ids as chips), and the send state on the latest one or a
 *     failed one; a tap that answered a card draws no bubble (the card shows it) unless its send failed;
 *   - finished work: one result card, or the stack a run of them makes;
 *   - a call: the kit's call card, live with its clock and captions while it is on.
 *
 * Stick to the bottom: the page owns the scroller (it holds more than the thread: the hero, its own cards, an agent's
 * live menu) and passes a handle; the view follows whenever what it draws changes, before the paint, so a new message
 * is never drawn once at the old position and then jumped.
 *
 * LIVE TEXT (the hook for a live-text stream): pass `liveText` = { key?, text, parts? } and the view draws it as a
 * streaming reply at the end of the thread, following the bottom, until a row with that key is in the thread (the
 * finished turn replaces it in place). Nothing else changes: the overlay is not part of the thread, so Copy, the
 * send states and the cards-on-screen context ignore it.
 *
 * Every card it draws keeps reporting into the cards-on-screen context (lib/screenCards.ts) through its own renderer.
 */
import { useLayoutEffect, type ReactNode } from 'react';
import ToolCard from './ToolCard';
import CallCard from './CallCard';
import ResultCard from './ResultCard';
import Markdown from './Markdown';
import { MessageActions, MessageText, type Skin } from './Message';
import { withRefs } from './RefText';
import ChoiceCard from '../chat/ChoiceCard';
import DecisionBubble from '../chat/DecisionBubble';
import { MessageTime, PendingBubble } from '../chat/TranscriptCards';
import './kit-modern.css';
import type { GroupedNode } from '../../lib/toolGroups';
import { sendStateLabel, type SendState } from '../../lib/arturo';
import { hasVoiceCallMarker, isCallEndedLine, parseVoiceSegments } from '../../lib/threadKit/calls';
import { showLive, type LiveText, type ThreadRow } from '../../lib/threadKit/view';
import type { MessagePart, ThreadMessage } from '../../lib/threadKit/thread';

export interface ThreadViewCall {
  /** When this page's live call started (ms), for the running clock. */
  liveSince?: number | null;
  liveUser?: string;
  liveArturo?: string;
  connecting?: boolean;
}

export interface ThreadViewChoice {
  /** Who asks on the card (the assistant's name). */
  asker?: string;
  /** A tap: the words to send and what was picked and typed. */
  onAnswer: (message: ThreadMessage, text: string, answer: { picked: string[]; words: string }) => void;
  /** The card's ✕ (two clicks): an error to show, or null. Absent: no ✕. */
  onDismiss?: (message: ThreadMessage) => Promise<string | null>;
}

/** A node row as its primitive drew it: no wrapper, so the agent chat's spacing and layout stay exactly its own. */
function NodeSlot({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/** A reply's words when it has no ordered parts: an ended call's marker is its call card, never the raw marker. */
function ReplyWords({ text, skin }: { text: string; skin: Skin }) {
  if (!hasVoiceCallMarker(text)) return <MessageText text={text} skin={skin} />;
  return (
    <div className={skin === 'modern' ? 'txt' : undefined}>
      {parseVoiceSegments(text).map((seg, i) => seg.kind === 'voicecall'
        ? <CallCard key={i} skin={skin} callId={seg.callId} />
        : isCallEndedLine(seg.text) ? null : <div key={i}><Markdown text={seg.text} skin={skin} /></div>)}
    </div>
  );
}

/** A reply's text and tool runs, in the order they happened. Indices are stable: parts only ever grow at the end. */
function Parts({ parts, skin }: { parts: MessagePart[]; skin: Skin }) {
  return (
    <div className="turn-parts">
      {parts.map((p, i) => p.kind === 'tools'
        ? <ToolCard key={i} calls={p.calls} skin={skin} />
        : <MessageText key={i} text={p.text} skin={skin} />)}
    </div>
  );
}

function Reply({ m, skin, extras, choice, times }: { m: ThreadMessage; skin: Skin; extras?: (m: ThreadMessage) => ReactNode; choice?: ThreadViewChoice; times?: boolean }) {
  return (
    <div className={skin === 'bubbles' ? 'turn-assistant group w-full min-w-0' : 'turn-assistant'} data-thread-key={m.key}>
      {m.pending ? <span className="thinking" aria-label="thinking" />
        : m.parts ? <Parts parts={m.parts} skin={skin} />
        : <ReplyWords text={m.text} skin={skin} />}
      {!m.parts && m.toolNames && m.toolNames.length > 0 && <div className="tools">ran {m.toolNames.join(', ')}</div>}
      {!m.pending && !m.streaming && (m.copyText || times) && (
        <MessageActions copyText={m.copyText} skin={skin} meta={times ? <MessageTime ts={m.at} /> : undefined} />
      )}
      {extras?.(m)}
      {m.choices && choice && (
        <ChoiceCard note={m.choices.note} options={m.choices.options} multi={m.choices.multi} exclusive={m.choices.exclusive}
          answered={m.answered} dismissed={m.dismissed} cardId={m.choices.card_id} asker={choice.asker}
          onAnswer={(text, a) => choice.onAnswer(m, text, a)}
          onDismiss={choice.onDismiss && m.choices.card_id ? () => choice.onDismiss!(m) : undefined} />
      )}
    </div>
  );
}

function UserMessage({ m, skin, latest, times }: { m: ThreadMessage; skin: Skin; latest: boolean; times?: boolean }) {
  const images = (m.attachments ?? []).filter((a) => a.kind === 'image' && a.url);
  const state = m.sendState as SendState | undefined;
  if (skin === 'bubbles') {
    // The agent chat's bubble (TranscriptCards UserBubble), with the send state under it.
    return (
      <div className="flex flex-col items-end" data-thread-key={m.key}>
        {images.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5 mb-1 max-w-[85%]" data-attachments>
            {images.map((a) => (
              <a key={a.url!} href={a.url!} target="_blank" rel="noopener noreferrer">
                <img src={a.url!} alt="attached image" loading="lazy" className="h-24 max-w-[12rem] rounded-xl object-cover border border-border bg-muted" />
              </a>
            ))}
          </div>
        )}
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-blue-600/90 px-3.5 py-2 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words"
             style={{ overflowWrap: 'anywhere' }}>
          {withRefs(m.text, skin)}
        </div>
        {(state === 'failed' || (state && latest)) && <div className={`turn-state ${state}`}>{sendStateLabel(state)}</div>}
        {times && <MessageTime ts={m.at} align="right" />}
      </div>
    );
  }
  return (
    <div className="turn-user" data-thread-key={m.key}>
      {images.length > 0 && (
        <div className="msg-images">
          {images.map((a) => <a key={a.url!} href={a.url!} target="_blank" rel="noreferrer"><img src={a.url!} alt="attached image" loading="lazy" /></a>)}
        </div>
      )}
      {/* A long path or address breaks inside the bubble rather than pushing the page sideways on a phone. */}
      <div className="bubble-user" style={{ overflowWrap: 'anywhere' }}>{withRefs(m.text, skin)}</div>
      {(state === 'failed' || (state && latest)) && <div className={`turn-state ${state}`}>{sendStateLabel(state)}</div>}
      {times && <MessageTime ts={m.at} align="right" />}
    </div>
  );
}

export default function ThreadView({ rows, skin = 'modern', by, lastUserKey, call, choice, extras, liveText, stick, renderNode, onDecisionAnswered, times }: {
  rows: ThreadRow[];
  skin?: Skin;
  /** Who did the finished work, when a result does not say. */
  by?: string;
  /** The operator's latest turn (lib/threadKit/view lastUserKey): only it, or a failed one, shows its send state. */
  lastUserKey?: string;
  call?: ThreadViewCall;
  choice?: ThreadViewChoice;
  /** What only the page draws under a reply (a pairing code, an onboarding step, the agents it started). */
  extras?: (message: ThreadMessage) => ReactNode;
  /** Words still being written (a live-text stream): drawn as a streaming reply at the end. */
  liveText?: LiveText | null;
  /** The page's scroller handle (lib/useStickToBottom): followed whenever the thread grows. */
  stick?: { follow: () => void };
  /** An agent's transcript node (its chat's own primitive), and whether its tool run was stopped. */
  renderNode?: (node: GroupedNode, interrupted: boolean) => ReactNode;
  /** One of the agent's cards was answered here: refresh the list it came from. */
  onDecisionAnswered?: () => void;
  /** Under each message, when it was written (an agent's page: the one question it has to answer). */
  times?: boolean;
}) {
  const live = showLive(rows, liveText) ? liveText! : null;
  useLayoutEffect(() => { stick?.follow(); }, [rows, live?.text, live?.parts?.length, call?.liveUser, call?.liveArturo]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {rows.map((r) => {
        if (r.kind === 'call') {
          return (
            <CallCard key={r.key} skin={skin === 'bubbles' ? 'modern' : skin} callId={r.callId} turns={r.turns.map((t) => ({ role: t.role, text: t.text, ...(t.parts ? { parts: t.parts } : {}) }))}
              live={r.live} startTs={r.startTs} endTs={r.endTs}
              liveSince={r.live ? call?.liveSince : null} liveUser={r.live ? call?.liveUser : undefined}
              liveArturo={r.live ? call?.liveArturo : undefined} connecting={r.live && !!call?.connecting} />
          );
        }
        if (r.kind === 'result') {
          return (
            <div key={r.key} className="turn-assistant" data-turn-result>
              <ResultCard results={r.results.map((x) => ({ ...x, by: x.by ?? by }))} skin={skin === 'modern' ? 'modern' : 'bubbles'} />
            </div>
          );
        }
        if (r.kind === 'node') {
          if (skin === 'modern' && r.message) {
            return r.message.role === 'user'
              ? <UserMessage key={r.key} m={r.message} skin={skin} latest={false} times={times} />
              : <Reply key={r.key} m={r.message} skin={skin} times={times} />;
          }
          return renderNode ? <NodeSlot key={r.key}>{renderNode(r.node, r.interrupted)}</NodeSlot> : null;
        }
        if (r.kind === 'decision') return <DecisionBubble key={r.key} decision={r.decision} onAnswered={() => onDecisionAnswered?.()} />;
        if (r.kind === 'pending') return <PendingBubble key={r.key} text={r.text} kind={r.pending} />;
        if (r.kind !== 'message') return null;
        if (r.role === 'user') {
          if (r.cardEcho && r.sendState !== 'failed') return null;
          return <UserMessage key={r.key} m={r} skin={skin} latest={r.key === lastUserKey} times={times} />;
        }
        return <Reply key={r.key} m={r} skin={skin} extras={extras} choice={choice} times={times} />;
      })}
      {live && (
        <div className={skin === 'bubbles' ? 'flex justify-start' : 'turn-assistant'} data-live-text>
          <div className={skin === 'bubbles' ? 'w-full min-w-0' : undefined}>
            {live.parts?.length ? <Parts parts={live.parts} skin={skin} /> : <MessageText text={live.text} skin={skin} />}
          </div>
        </div>
      )}
    </>
  );
}
