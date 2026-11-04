/**
 * StreamMarkdown — a streamed reply's Markdown, revealed word by word (SMOOTH_STREAM_SPEC.md): the same look as the
 * kit's Markdown (components/kit/Markdown.tsx), with every word an inline span at its final place from the moment it
 * is drawn (transparent), switched on by the reveal loop (lib/revealStore.ts), which only adds a class: a
 * CSS opacity fade, on the compositor. Bold, italic, inline code, links and card chips fade the same way; a list
 * item's marker and a quote's bar fade in with their first word, a code block's frame with its first line.
 *
 * What is drawn is what can no longer change (lib/threadKit/streamMarkdown.ts revealSafeLength): a word once drawn
 * never moves. Finished blocks are planned once and their elements are never rendered again (memo on a stable plan);
 * only the block still growing is read again. A fenced code block shows a whole line at a time; a table appears whole
 * once it is complete.
 *
 * The reveal state lives in the store, not here, so the finished message that replaces the live one (or a block
 * moving from growing to finished) carries on exactly where it was: nothing shown fades again, nothing moves.
 */
import { Fragment, memo, useLayoutEffect, useRef, type ReactNode } from 'react';
import CardChip from './CardChip';
import { CodeBlock, MarkdownTable, type Skin } from './Markdown';
import { headingClass, inlineCodeClass, linkClass, revealAttrs } from './markdownLook';
import type { RevealEntry } from '../../lib/revealStore';
import type { BlockPlan, Seg, Word } from '../../lib/threadKit/streamMarkdown';
import { BUBBLES_BODY, PARAGRAPH } from '../../lib/chatSkin';
import './kit.css';
import './reveal.css';

function Words({ words, skin }: { words: Word[]; skin: Skin }) {
  return <>{words.map((w, i) => ('sp' in w ? <Fragment key={`s${i}`}>{w.sp}</Fragment>
    : 'ref' in w ? <span key={`u${w.u}`} className="rv" {...revealAttrs(w.u)}><CardChip id={w.ref} skin={skin} /></span>
      : <span key={`u${w.u}`} className="rv" {...revealAttrs(w.u)}>{w.w}</span>))}</>;
}

function Segs({ segs, skin }: { segs: Seg[]; skin: Skin }): ReactNode {
  return segs.map((s, i) => {
    switch (s.t) {
      case 'code': return <code key={i} className={`${inlineCodeClass(skin) ?? ''} rv`.trim()} {...revealAttrs(s.u)}>{s.text}</code>;
      case 'link': return <a key={i} href={s.href} target="_blank" rel="noopener noreferrer" className={`${linkClass(skin)} rv`} {...revealAttrs(s.u)}>{s.text}</a>;
      case 'bold': return <strong key={i} className="font-semibold"><Words words={s.words} skin={skin} /></strong>;
      case 'italic': return <em key={i}><Words words={s.words} skin={skin} /></em>;
      default: return <Fragment key={i}><Words words={s.words} skin={skin} /></Fragment>;
    }
  });
}

/** One block, drawn once per plan: a finished block's plan never changes, so it is never rendered again. */
const PlanBlock = memo(function PlanBlock({ plan: b, skin, index }: { plan: BlockPlan; skin: Skin; index: number }) {
  const body = skin === 'bubbles' ? BUBBLES_BODY : '';
  const strong = skin === 'bubbles' ? 'text-foreground' : '';
  switch (b.t) {
    case 'code':
      return <CodeBlock code={b.code} lang={b.lang} dark={skin === 'pane'} units={{ lines: b.lines, fold: b.fold }} />;
    case 'heading':
      return <div className={`${headingClass(b.level)} ${strong} mt-2 mb-1`}><Segs segs={b.segs} skin={skin} /></div>;
    case 'list': {
      const items = b.items.map((it, idx) => (
        <li key={idx} className={`${body}${it.u !== null ? ' rv-mark' : ''}`} {...revealAttrs(it.u, true)}><Segs segs={it.segs} skin={skin} /></li>
      ));
      return b.ordered
        ? <ol start={b.start} className="list-decimal pl-5 my-1 space-y-0.5">{items}</ol>
        : <ul className="list-disc pl-5 my-1 space-y-0.5">{items}</ul>;
    }
    case 'quote':
      return (
        <blockquote className={`border-l-2 border-current/30 pl-3 my-1 opacity-80 whitespace-pre-wrap break-words ${body}${b.u !== null ? ' rv-bar' : ''}`} {...revealAttrs(b.u, true)}>
          <Segs segs={b.segs} skin={skin} />
        </blockquote>
      );
    case 'table':
      return <MarkdownTable block={b.block} keyBase={`b${index}`} skin={skin} reveal={b.u} />;
    default:
      return <p className={`${PARAGRAPH} ${body}`}><Segs segs={b.segs} skin={skin} /></p>;
  }
});

export default function StreamMarkdown({ entry, text, done, skin }: { entry: RevealEntry; text: string; done: boolean; skin: Skin }) {
  const plan = entry.read(text, done);
  const blocks = plan.blocks();
  const gen = entry.gen;
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    entry.attach(el);
    return () => entry.detach(el);
  }, [entry, gen]);
  // After every commit: the units drawn now, those already released switched on before the paint.
  useLayoutEffect(() => { entry.sync(); });
  return (
    <div key={gen} ref={ref} className={`kit-md kit-skin-${skin} space-y-0.5 min-w-0`} data-reveal-md data-rk={entry.id}>
      {blocks.map((b, i) => <PlanBlock key={i} plan={b} skin={skin} index={i} />)}
    </div>
  );
}
