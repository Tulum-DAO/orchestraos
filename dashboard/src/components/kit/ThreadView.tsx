/**
 * ThreadView — the thread kit's view of a conversation: the rows of lib/threadKit/view.ts drawn with the kit's parts
 * (Message and Markdown, the tool card, the call card, the result card, the choice card, card chips). Arturo's page
 * and every agent's page draw their thread through it.
 *
 * Skins: Arturo's page draws "modern" (his own) or "bubbles" (the agent chat's look); an agent's page "bubbles" (its
 * own) or "modern" (Arturo's look: the variables come from kit-modern.css on the page's column); his floating pane
 * "pane" (its small dark rows: the operator's bubble right, his tinted bubble left, Markdown inside, the page's own
 * status notes as quiet lines; a choice card or a result card inside it is drawn in the dark theme, since the pane
 * is dark in either). In Bubbles, a call
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
 * WORDS STILL BEING WRITTEN, in every skin: revealed word by word as they arrive, each fading in (components/kit/
 * StreamMarkdown.tsx, lib/revealStore.ts; SMOOTH_STREAM_SPEC.md). In Bubbles the bubble grows with them
 * (nothing is held back a paragraph at a time any more). The same for a reply still streaming in the
 * thread (by its key, a text part by its key and index) and for the live overlay below.
 *
 * LIVE TEXT (the hook for a live-text stream): pass `liveText` = { key?, text, parts?, blocks?, done? } and the view
 * draws it as a streaming reply at the end of the thread, following the bottom, until a row with that key is in the
 * thread (the finished turn replaces it in place). An agent's live text comes in blocks: each is drawn as its own
 * reply, laid out exactly as its finished row will be (the time and Copy line under it held as blank space), and
 * hands off on its own when its row lands: the row carries on the same reveal (found by the block's uuid), so nothing
 * shown fades again or moves. Nothing else changes: the overlay is not part of the thread, so Copy, the send states
 * and the cards-on-screen context ignore it.
 *
 * Every card it draws keeps reporting into the cards-on-screen context (lib/screenCards.ts) through its own renderer.
 */
import { Fragment, useLayoutEffect, type ReactNode } from 'react';
import ToolCard from './ToolCard';
import CallCard from './CallCard';
import ResultCard from './ResultCard';
import Markdown from './Markdown';
import { MessageActions, MessageText, type RevealAs, type Skin } from './Message';
import { ReactionAnchor } from './ReactionBadges';
import { ReactionRow } from './Reactions';
import { useReactionsCtx } from './reactionsContext';
import { withRefs } from './RefText';
import ChoiceCard from '../chat/ChoiceCard';
import DecisionBubble from '../chat/DecisionBubble';
import { MessageTime, PendingBubble } from '../chat/TranscriptCards';
import './kit-modern.css';
import { UserWords } from './Turns';
import { assistantTurnClass } from '../../lib/chatSkin';
import type { GroupedNode } from '../../lib/toolGroups';
import { sendStateLabel, type SendState } from '../../lib/arturo';
import { hasVoiceCallMarker, isCallEndedLine, parseVoiceSegments } from '../../lib/threadKit/calls';
import { modernUserParts, rowReactions, showLive, type LiveText, type NodeRow, type ThreadRow } from '../../lib/threadKit/view';
import { pendingLabel, type PendingKind } from '../../lib/pendingSends';
import type { LiveBlock } from '../../lib/threadKit/liveBlocks';
import type { MessagePart, ThreadMessage } from '../../lib/threadKit/thread';
import { revealReady } from '../../lib/threadKit/streamMarkdown';
import { aliasReveal, findReveal, revealEntry } from '../../lib/revealStore';

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
function ReplyWords({ text, skin, reveal }: { text: string; skin: Skin; reveal?: RevealAs }) {
  if (!hasVoiceCallMarker(text)) return <MessageText text={text} skin={skin} reveal={reveal} />;
  return (
    <div className={skin === 'modern' ? 'txt' : undefined}>
      {parseVoiceSegments(text).map((seg, i) => seg.kind === 'voicecall'
        ? <CallCard key={i} skin={skin} callId={seg.callId} />
        : isCallEndedLine(seg.text) ? null : <div key={i} className={skin === 'pane' ? 'bubble' : undefined}><Markdown text={seg.text} skin={skin} /></div>)}
    </div>
  );
}

/** A reply's text and tool runs, in the order they happened. Indices are stable: parts only ever grow at the end.
 *  `revealKey`: its text parts are revealed (by `<key>:p<index>`); `streaming`: still being written (a text part is
 *  finished once a tool run follows it; one with no word to show yet is not drawn). Tool runs are drawn as ever. */
function Parts({ parts, skin, revealKey, streaming = false }: { parts: MessagePart[]; skin: Skin; revealKey?: string; streaming?: boolean }) {
  return (
    <div className="turn-parts">
      {parts.map((p, i) => {
        if (p.kind === 'tools') return <ToolCard key={i} calls={p.calls} skin={skin} look="lines" />;
        const done = !streaming || i < parts.length - 1;
        if (streaming && !revealReady(p.text, done)) return null;
        return <MessageText key={i} text={p.text} skin={skin} reveal={revealKey ? { key: `${revealKey}:p${i}`, done, live: streaming } : undefined} />;
      })}
    </div>
  );
}

/** A finished reply's reveal: under its own key, or (Arturo's turns stream as parts) its one text part's. */
function finishedReveal(m: ThreadMessage): RevealAs {
  return { key: findReveal(m.key, m.text) || !findReveal(`${m.key}:p0`, m.text) ? m.key : `${m.key}:p0`, done: true };
}

/** Any time: the held line's words are never seen, only its height. */
const HELD_TS = '2026-01-01T12:00:00Z';

/** One block of the live overlay, laid out as its finished row will be: in Bubbles the chat's Message (the bubble,
 *  then its time and Copy line), in Modern the reply and its actions line. The line under it is held as blank space
 *  (it shows when the row lands), so a block below never moves at the hand-off. */
function LiveBlockRow({ id, text, done, skin, times, anchored }: { id: string; text: string; done: boolean; skin: Skin; times?: boolean; anchored?: boolean }) {
  const words = <MessageText text={text} skin={skin} reveal={{ key: id, done, live: true }} />;
  if (skin === 'pane') return <div className="row arturo" data-live-text>{words}</div>;
  const held = (
    <div className="invisible" aria-hidden data-live-actions>
      <MessageActions copyText={text} skin={skin} meta={skin === 'bubbles' || times ? <MessageTime ts={HELD_TS} /> : undefined} />
    </div>
  );
  if (skin === 'bubbles') {
    return (
      <div className="flex justify-start group" data-live-text>
        <div className="w-full min-w-0">{words}{held}</div>
      </div>
    );
  }
  // Modern: a row that can be reacted to holds its words in the reaction anchor (ReactionAnchor, a block the words are
  // measured in): the live words sit in the same box, so they wrap where the finished row's will.
  return <div className="turn-assistant" data-live-text>{anchored ? <div className="relative">{words}</div> : words}{held}</div>;
}

function Reply({ m, skin, extras, choice, times }: { m: ThreadMessage; skin: Skin; extras?: (m: ThreadMessage) => ReactNode; choice?: ThreadViewChoice; times?: boolean }) {
  if (skin === 'pane') {
    return (
      <div className="row arturo group" data-thread-key={m.key}>
        {m.pending ? <div className="bubble thinking">Thinking…</div>
          : m.parts ? <Parts parts={m.parts} skin={skin} revealKey={m.key} streaming={!!m.streaming} />
          : m.streaming ? (revealReady(m.text) && <MessageText text={m.text} skin={skin} reveal={{ key: m.key, done: false, live: true }} />)
          : <ReplyWords text={m.text} skin={skin} reveal={finishedReveal(m)} />}
        {!m.parts && m.toolNames && m.toolNames.length > 0 && <div className="meta">ran {m.toolNames.join(', ')}</div>}
        {!m.pending && !m.streaming && m.copyText && <MessageActions copyText={m.copyText} skin={skin} />}
        {extras?.(m)}
        {m.choices && choice && <div className="dark w-full"><Choice m={m} choice={choice} /></div>}
      </div>
    );
  }
  return (
    <div className={assistantTurnClass(skin)} data-thread-key={m.key}>
      {m.pending ? <span className="thinking" aria-label="thinking" />
        : m.parts ? <Parts parts={m.parts} skin={skin} revealKey={m.key} streaming={!!m.streaming} />
        : m.streaming ? (revealReady(m.text) && <MessageText text={m.text} skin={skin} reveal={{ key: m.key, done: false, live: true }} />)
        : <ReactionAnchor><ReplyWords text={m.text} skin={skin} reveal={finishedReveal(m)} /></ReactionAnchor>}
      {!m.parts && m.toolNames && m.toolNames.length > 0 && <div className="tools">ran {m.toolNames.join(', ')}</div>}
      {!m.pending && !m.streaming && (m.copyText || times) && (
        <MessageActions copyText={m.copyText} skin={skin} meta={times ? <MessageTime ts={m.at} /> : undefined} />
      )}
      {extras?.(m)}
      {m.choices && choice && <Choice m={m} choice={choice} />}
    </div>
  );
}

/** A reply's choice card, answered from the server's record and sent through the page's own send. */
function Choice({ m, choice }: { m: ThreadMessage; choice: ThreadViewChoice }) {
  const c = m.choices!;
  return (
    <ChoiceCard note={c.note} options={c.options} multi={c.multi} exclusive={c.exclusive}
      answered={m.answered} dismissed={m.dismissed} cardId={c.card_id} asker={choice.asker}
      onAnswer={(text, a) => choice.onAnswer(m, text, a)}
      onDismiss={choice.onDismiss && c.card_id ? () => choice.onDismiss!(m) : undefined} />
  );
}

/** An OLD log's emoji reply under the operator's message (lib/threadKit/view withReactions), where the row cannot be
 *  reacted to (no ref, or no reactions on this page): otherwise its badge is drawn by ReactionRow. */
function Reaction({ emoji }: { emoji: string }) {
  return (
    <div className="flex justify-end !-mt-1.5 pr-3" data-anchor-skip data-reaction>
      <span className="rounded-full border border-border bg-muted px-1.5 text-sm leading-[calc(1.5rem*var(--ui-text-scale,1))] shadow-sm" aria-label={`Reacted ${emoji}`}>{emoji}</span>
    </div>
  );
}

/** A message just sent, in Modern (lib/pendingSends): the Modern words, faded until its own row lands, and where it is
 *  waiting under it. Bubbles draws the chat's PendingBubble. */
function ModernPending({ text, kind }: { text: string; kind: PendingKind }) {
  const label = pendingLabel(kind);
  return (
    <div className="turn-user opacity-70" data-testid="pending-bubble" data-pending-kind={kind}>
      <UserWords skin="modern">{withRefs(text, 'modern')}</UserWords>
      {label && <div className="turn-state">{label}</div>}
    </div>
  );
}

function UserMessage({ m, skin, latest, times }: { m: ThreadMessage; skin: Skin; latest: boolean; times?: boolean }) {
  const images = (m.attachments ?? []).filter((a) => a.kind === 'image' && a.url);
  const state = m.sendState as SendState | undefined;
  if (skin === 'pane') {
    return (
      <div className="row user" data-thread-key={m.key}>
        {images.length > 0 && (
          <div className="msg-images">
            {images.map((a) => <a key={a.url!} href={a.url!} target="_blank" rel="noreferrer"><img src={a.url!} alt="attached image" loading="lazy" /></a>)}
          </div>
        )}
        <div className="bubble" style={{ overflowWrap: 'anywhere' }}>{withRefs(m.text, skin)}</div>
        {(state === 'failed' || (state && latest)) && <div className={`turn-state ${state}`}>{sendStateLabel(state)}</div>}
      </div>
    );
  }
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
        <UserWords skin="bubbles">{withRefs(m.text, skin)}</UserWords>
        {(state === 'failed' || (state && latest)) && <div className={`turn-state ${state}`}>{sendStateLabel(state)}</div>}
        {times && <MessageTime ts={m.at} align="right" />}
      </div>
    );
  }
  // A line with a call marker (an ended call's summary, words said on a call): its words in Modern, and each call as
  // the call card where its marker sat, centred in the thread like any call.
  if (hasVoiceCallMarker(m.text)) {
    const parts = modernUserParts(m.text);
    const last = parts.reduce((at, p, i) => (p.kind === 'words' ? i : at), -1);
    return (
      <div className="flex flex-col items-stretch gap-1" data-thread-key={m.key}>
        {parts.map((p, i) => p.kind === 'call'
          ? <CallCard key={i} skin="modern" callId={p.callId} />
          : (
            <div key={i} className="turn-user">
              <UserWords skin="modern">{withRefs(p.text, skin)}</UserWords>
              {times && i === last && <MessageTime ts={m.at} align="right" />}
            </div>
          ))}
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
      <UserWords skin="modern">{withRefs(m.text, skin)}</UserWords>
      {(state === 'failed' || (state && latest)) && <div className={`turn-state ${state}`}>{sendStateLabel(state)}</div>}
      {times && <MessageTime ts={m.at} align="right" />}
    </div>
  );
}

export default function ThreadView({ rows, skin = 'modern', by, lastUserKey, call, choice, extras, liveText, stick, renderNode, onDecisionAnswered, times, noteText }: {
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
  /** How the page words one of its status notes (the pane: "I can't do that yet — …"). */
  noteText?: (message: ThreadMessage) => string;
}) {
  const live = showLive(rows, liveText) ? liveText! : null;
  // The overlay's blocks, each with its id (an agent's live text) and whether its words are all in; a block whose
  // transcript uuid is known is findable by it, so its row carries on its reveal when it lands.
  const blocks = !live || live.parts?.length ? [] : (live.blocks?.length ? live.blocks : [{ text: live.text } as LiveBlock])
    .map((b, i, all) => ({ id: b.id ?? b.uuid ?? (live.key ? `${live.key}:${i}` : `live:${i}`), uuid: b.uuid ?? null, text: b.text,
      // a block is finished when the turn is, when another follows it, or once it has its uuid (the server names a
      // block only when it is closed)
      done: !!live.done || i < all.length - 1 || !!b.uuid }));
  for (const b of blocks) if (b.uuid) aliasReveal(b.uuid, revealEntry(b.id));
  // The chat's reactions (TranscriptChatView provides them; Arturo's page and pane do not): a message row that can be
  // reacted to is drawn inside its ReactionRow, which adds no element of its own.
  const rx = useReactionsCtx();
  const reactable = (r: NodeRow, content: ReactNode) => (rx && r.ref && (r.node.kind === 'user' || r.node.kind === 'assistant')
    ? <ReactionRow reactRef={r.ref} side={r.node.kind === 'user' ? 'user' : 'agent'} list={rowReactions(r, rx.agentId)}
        text={r.node.text} skin={skin}>{content}</ReactionRow>
    : <>{content}{r.reaction && <Reaction emoji={r.reaction} />}</>);
  useLayoutEffect(() => { stick?.follow(); }, [rows, live?.text, live?.parts?.length, call?.liveUser, call?.liveArturo]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {rows.map((r) => {
        if (r.kind === 'call') {
          return (
            <CallCard key={r.key} skin={skin === 'bubbles' ? 'modern' : skin} callId={r.callId} turns={r.turns.map((t) => ({ role: t.role, text: t.text, ...(t.parts ? { parts: t.parts } : {}), ...(t.id !== undefined ? { key: String(t.id) } : {}) }))}
              live={r.live} startTs={r.startTs} endTs={r.endTs}
              liveSince={r.live ? call?.liveSince : null} liveUser={r.live ? call?.liveUser : undefined}
              liveArturo={r.live ? call?.liveArturo : undefined} connecting={r.live && !!call?.connecting} />
          );
        }
        if (r.kind === 'result') {
          return (
            <div key={r.key} className={skin === 'pane' ? 'row arturo dark' : 'turn-assistant'} data-turn-result>
              <ResultCard results={r.results.map((x) => ({ ...x, by: x.by ?? by }))} skin={skin === 'modern' ? 'modern' : 'bubbles'} />
            </div>
          );
        }
        if (r.kind === 'node') {
          if (skin === 'modern' && r.message) {
            return (
              <Fragment key={r.key}>
                <span data-row-key={r.key} data-anchor-skip aria-hidden className="block h-0 !m-0" />
                {reactable(r, r.message.role === 'user'
                  ? <UserMessage m={r.message} skin={skin} latest={false} times={times} />
                  : <Reply m={r.message} skin={skin} times={times} />)}
              </Fragment>
            );
          }
          // A zero-size mark before the row names it by its key (a call's work chip finds the row it started at; never the
          // scroll anchor, and it takes no space).
          return renderNode ? (
            <Fragment key={r.key}>
              <span data-row-key={r.key} data-anchor-skip aria-hidden className="block h-0 !m-0" />
              {reactable(r, <NodeSlot>{renderNode(r.node, r.interrupted)}</NodeSlot>)}
            </Fragment>
          ) : null;
        }
        // Never the scroll anchor: a card is placed by its time, so one filed before the oldest message loaded moves down
        // into place when an older page brings that moment in; what the reader is looking at is held by the text.
        if (r.kind === 'decision') return <div key={r.key} data-anchor-skip><DecisionBubble decision={r.decision} onAnswered={() => onDecisionAnswered?.()} /></div>;
        if (r.kind === 'pending') return skin === 'modern' ? <ModernPending key={r.key} text={r.text} kind={r.pending} /> : <PendingBubble key={r.key} text={r.text} kind={r.pending} />;
        if (r.kind !== 'message') return null;
        if (r.note) {
          return <div key={r.key} className="row note"><div className="meta">{noteText ? noteText(r) : r.text}</div></div>;
        }
        if (r.role === 'user') {
          if (r.cardEcho && r.sendState !== 'failed') return null;
          return <UserMessage key={r.key} m={r} skin={skin} latest={r.key === lastUserKey} times={times} />;
        }
        return <Reply key={r.key} m={r} skin={skin} extras={extras} choice={choice} times={times} />;
      })}
      {live?.parts?.length ? (
        <div className={skin === 'bubbles' ? 'flex justify-start' : skin === 'pane' ? 'row arturo' : 'turn-assistant'} data-live-text>
          <div className={skin === 'bubbles' ? 'turn-assistant w-full min-w-0' : undefined}>
            <Parts parts={live.parts} skin={skin} revealKey={live.key ?? 'live'} streaming={!live.done} />
          </div>
        </div>
      ) : blocks.map((b) => revealReady(b.text, b.done) && <LiveBlockRow key={b.id} id={b.id} text={b.text} done={b.done} skin={skin} times={times} anchored={!!rx} />)}
    </>
  );
}
