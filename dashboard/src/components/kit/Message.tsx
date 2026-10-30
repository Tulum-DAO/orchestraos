/**
 * Message — the thread kit's reply: its words in Markdown (components/kit/Markdown.tsx) and a Copy action, in one of
 * three skins.
 *
 *   modern   Arturo's page: the words flat in the thread at the reply's own size (`.txt`), a copy icon under them
 *   bubbles  the agent chat: 13px words, the time and a copy icon under them (on hover; always on touch)
 *   pane     Arturo's floating pane: the words in the pane's bubble
 *
 * Copy takes the words as written (the thread model's copyText: no tool or call cards), never the rendered page.
 */
import type { ReactNode } from 'react';
import Copy from './Copy';
import Markdown, { type Skin } from './Markdown';

export type { Skin } from './Markdown';

/** A reply's words, in its skin. */
export function MessageText({ text, skin }: { text: string; skin: Skin }) {
  if (skin === 'modern') return <div className="txt"><Markdown text={text} skin="modern" /></div>;
  if (skin === 'pane') return <div className="bubble"><Markdown text={text} skin="pane" /></div>;
  return <Markdown text={text} skin="bubbles" />;
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

export default function Message({ text, copyText = text, skin, meta }: {
  text: string; copyText?: string; skin: Skin; meta?: ReactNode;
}) {
  if (skin === 'bubbles') {
    return (
      <div className="flex justify-start group" data-kit-message="bubbles">
        <div className="w-full min-w-0">
          <MessageText text={text} skin="bubbles" />
          <MessageActions copyText={copyText} skin="bubbles" meta={meta} />
        </div>
      </div>
    );
  }
  return (
    <div className="group min-w-0" data-kit-message={skin}>
      <MessageText text={text} skin={skin} />
      <MessageActions copyText={copyText} skin={skin} meta={meta} />
    </div>
  );
}
