/**
 * Message — the thread kit's reply: its words in Markdown (components/kit/Markdown.tsx) and a Copy action, in one of
 * three skins.
 *
 *   modern   Arturo's page: the words flat in the thread at the reply's own size (`.txt`), a copy icon under them
 *   bubbles  the agent chat: 13px words in a bordered card bubble on the left (one per text part of a turn: the
 *            live overlay draws the same bubble, so the hand-off to the finished reply never jumps), the time and a
 *            copy icon under it (on hover; always on touch)
 *   pane     Arturo's floating pane: the words in the pane's bubble
 *
 * Copy takes the words as written (the thread model's copyText: no tool or call cards), never the rendered page.
 *
 * A reply being written, or one written here a moment ago (`reveal`), is drawn by components/kit/StreamMarkdown.tsx:
 * word by word, each fading in; its bubble (Bubbles, the pane) fades in with its first word. Any other message (an
 * old one, from history) is drawn by the kit's Markdown at once, as ever.
 */
import type { ReactNode } from 'react';
import Copy from './Copy';
import Markdown, { type Skin } from './Markdown';
import StreamMarkdown from './StreamMarkdown';
import { findReveal, revealEntry, type RevealEntry } from '../../lib/revealStore';
import { SlotBadges, SlotBar } from './ReactionBadges';
import { anchorProps, useReactionSlot } from './reactionsContext';
import { BUBBLES_AGENT, bubbleWidthOf } from '../../lib/chatSkin';

export type { Skin } from './Markdown';

/** How a reply's words are revealed: by this key; `live` while it is being written (the reveal starts here), else only
 *  if it was written here (its entry is found, and its words start with the words drawn): an old message never
 *  animates. `done`: no more words are coming. */
export interface RevealAs { key: string; done: boolean; live?: boolean }

function entryFor(reveal: RevealAs | undefined, text: string): RevealEntry | undefined {
  if (!reveal) return undefined;
  return reveal.live ? revealEntry(reveal.key) : findReveal(reveal.key, text);
}

/** The words: revealed (a streamed reply) or drawn at once. */
function Words({ text, skin, entry, done }: { text: string; skin: Skin; entry?: RevealEntry; done: boolean }) {
  return entry ? <StreamMarkdown entry={entry} text={text} done={done} skin={skin} /> : <Markdown text={text} skin={skin} />;
}

/** A streamed reply's bubble fades in with its first word (the reveal's unit 0). */
const bubbleReveal = (entry?: RevealEntry) => (entry ? { 'data-ru-root': '', 'data-ru': 0 } : {});

/** A reply's words, in its skin. `reactions`: this bubble is the message a reaction is given to (Bubbles), so it
 *  carries the message's badges and hover icons on its own border. Anywhere else (a call transcript inside someone
 *  else's message, the live overlay, the lifted copy) it is just the bubble. */
export function MessageText({ text, skin, reactions = false, widthFrom, reveal }: {
  text: string; skin: Skin; reactions?: boolean;
  /** Bubbles: the text the bubble's width is read from, when not the words drawn (chatSkin bubbleWidthOf). */
  widthFrom?: string;
  reveal?: RevealAs;
}) {
  const entry = entryFor(reveal, text);
  const done = reveal?.done ?? true;
  if (skin === 'modern') return <div className="txt"><Words text={text} skin="modern" entry={entry} done={done} /></div>;
  if (skin === 'pane') return <div className={`bubble${entry ? ' rv' : ''}`} {...bubbleReveal(entry)}><Words text={text} skin="pane" entry={entry} done={done} /></div>;
  return <AgentBubble text={text} reactions={reactions} widthFrom={widthFrom} entry={entry} done={done} />;
}

/** Bubbles: the agent's reply bubble. With a reaction slot (Reactions.tsx ReactionRow) it is the anchor the badges
 *  straddle (desktop: its top inner corner; a phone: its bottom inner corner) and the hover icons sit beside. */
function AgentBubble({ text, reactions, widthFrom, entry, done }: { text: string; reactions: boolean; widthFrom?: string; entry?: RevealEntry; done: boolean }) {
  const slot = useReactionSlot();
  const s = reactions ? slot : null;
  return (
    <div className={`${BUBBLES_AGENT} ${bubbleWidthOf(widthFrom ?? text)}${entry ? ' rv' : ''}`} data-agent-bubble {...anchorProps(s, 'agent')} data-rx-bubble={s ? '' : undefined}
         {...bubbleReveal(entry)}>
      <Words text={text} skin="bubbles" entry={entry} done={done} />
      {s && <><SlotBar side="agent" /><SlotBadges side="agent" /></>}
    </div>
  );
}

/** Under a reply: what the caller adds (the agent chat's time), then Copy. Nothing to copy, no button. */
export function MessageActions({ copyText, skin, meta }: { copyText: string; skin: Skin; meta?: ReactNode }) {
  const copy = copyText.trim() ? copyText : '';
  if (skin === 'modern') {
    // An agent's page in Modern adds the time (`meta`) beside Copy; Arturo's page adds nothing.
    return copy ? (
      <div className="reply-actions" style={meta ? { alignItems: 'center', gap: 6 } : undefined}>
        <Copy text={copy} label="Copy reply" title="Copy" size={15} className="call-icon" />
        {meta}
      </div>
    ) : <>{meta}</>;
  }
  const tone = skin === 'pane' ? 'text-white/40 hover:text-white/80' : 'text-neutral-500 hover:text-foreground';
  return (
    <div className="flex items-center gap-1">
      {meta}
      {copy && (
        <Copy text={copy} label="Copy reply" size={12}
              className={`mt-0.5 ${tone} opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity`} />
      )}
    </div>
  );
}

export default function Message({ text, copyText = text, skin, meta, revealKey }: {
  text: string; copyText?: string; skin: Skin; meta?: ReactNode;
  /** The message's key: if it was streamed here, its words carry on that reveal (MessageText `reveal`). */
  revealKey?: string;
}) {
  const reveal = revealKey ? { key: revealKey, done: true } : undefined;
  if (skin === 'bubbles') {
    return (
      <div className="flex justify-start group" data-kit-message="bubbles">
        <div className="w-full min-w-0">
          {/* In a message that can be reacted to, the bubble carries its badges and hover icons (ReactionBadges). */}
          <MessageText text={text} skin="bubbles" reactions reveal={reveal} />
          <MessageActions copyText={copyText} skin="bubbles" meta={meta} />
        </div>
      </div>
    );
  }
  return (
    <div className="group min-w-0" data-kit-message={skin}>
      <MessageText text={text} skin={skin} reveal={reveal} />
      <MessageActions copyText={copyText} skin={skin} meta={meta} />
    </div>
  );
}
