/**
 * The thread kit's Markdown: the one renderer for every agent's messages, Arturo's included: fenced code (long blocks
 * collapse, each has a copy button), headings, bullet and numbered lists, quotes, tables and links. Parsing lives in
 * lib/threadKit/markdown.ts (tested there). Dependency-free, no CDN.
 *
 * Skins: "bubbles" draws the agent chat's 13px text; "modern" takes the size and colour of the reply it sits in
 * (Arturo's page); "pane" is Arturo's floating pane, always dark, at its 14px. Tables follow the iOS app's rules in
 * every skin (kit.css).
 */
import { BUBBLES_BODY, PARAGRAPH } from '../../lib/chatSkin';
import { Fragment, useState, type ReactNode, type TouchEvent } from 'react';
import Copy from './Copy';
import { parseBlocks, parseInline, tableLayout, tableShares, type Block, type Inline } from '../../lib/threadKit/markdown';
import { isCardId } from '../../lib/cardRefs';
import { CODE_FOLD_LINES } from '../../lib/threadKit/streamMarkdown';
import { withRefs } from './RefText';
import { headingClass, inlineCodeClass, linkClass, revealAttrs } from './markdownLook';
import './kit.css';

export type Skin = 'bubbles' | 'modern' | 'pane';

const CODE_COLLAPSE_LINES = CODE_FOLD_LINES;

/** A touch that starts in a table or a code scroller stays there: it starts no swipe of the thread's or the page's. */
const keepTouch = (e: TouchEvent) => e.stopPropagation();
const touchProps = { onTouchStart: keepTouch, onTouchMove: keepTouch, onTouchEnd: keepTouch };

/** The box code is drawn in, in the app's theme (the --bui-* tokens: light on a light page, dark on a dark one;
 *  ), or always dark (`dark`: Arturo's pane, which is dark in either theme): a title bar with its label, then (at the right) what the caller adds (a
 *  fold control) and a Copy button that is always shown and copies `copyText` whole, even while the code is folded.
 *  A fenced block uses it, and so does a tool call's code block in Bubbles' thin lines (kit/ToolCard.tsx). */
export function CodeFrame({ label, copyText, copyLabel = 'Copy code', tools, dark = false, className = '', reveal, children }: {
  label: string; copyText: string; copyLabel?: string; tools?: ReactNode; dark?: boolean; className?: string;
  /** A streamed reply (StreamMarkdown): the reveal unit the frame fades in with (its first line). */
  reveal?: number;
  children: ReactNode;
}) {
  return (
    <div className={`${dark ? 'dark ' : ''}rounded-md border border-line-strong bg-inset text-ink overflow-hidden ${className}${reveal !== undefined ? ' rv' : ''}`} data-code-frame
         {...revealAttrs(reveal, true)}>
      <div className="touch-compact flex items-center gap-2 px-2.5 py-1 bg-field border-b border-line-strong">
        <span className="text-px10 font-mono text-ink-3">{label}</span>
        <span className="ml-auto" />
        {tools}
        <Copy text={copyText} label={copyLabel} size={12}
              className="-mr-1 text-ink-3 hover:text-ink hover:bg-hover-2" />
      </div>
      {children}
    </div>
  );
}

/** A fenced block: long ones fold, Copy takes the whole block. Streamed (`units`, StreamMarkdown): each line is a
 *  reveal unit and the frame fades in with the first; a block that grows past the fold folds as it streams, as the
 *  finished one is drawn, and its "more lines" row is a unit of its own. */
export function CodeBlock({ code, lang, dark = false, units }: { code: string; lang?: string; dark?: boolean; units?: { lines: number[]; fold: number | null } }) {
  const lines = code.replace(/\n$/, '').split('\n');
  const long = lines.length > CODE_COLLAPSE_LINES;
  // Folded while long until the reader opens it (a block that grows long while it streams folds then).
  const [opened, setOpen] = useState<boolean | null>(null);
  const open = opened ?? !long;
  const shown = open ? lines : lines.slice(0, CODE_COLLAPSE_LINES);
  const fold = units?.fold ?? undefined;
  return (
    <CodeFrame className="my-1.5" label={lang || 'code'} copyText={lines.join('\n')} dark={dark} reveal={units?.lines[0]}
      tools={long && (
        <button
          onClick={() => setOpen(!open)}
          className={`text-px10 text-bui-accent-ink hover:opacity-80${fold !== undefined ? ' rv' : ''}`} {...revealAttrs(fold, true)}
        >
          {open ? 'Collapse' : `Show ${lines.length} lines`}
        </button>
      )}>
      <pre className="kit-code-scroll px-3 py-2 overflow-x-auto text-px12 leading-relaxed" {...touchProps}>
        <code className="font-mono text-ink whitespace-pre">{units
          ? shown.map((l, i) => <span key={i} className={units.lines[i] !== undefined ? 'rv' : undefined} {...revealAttrs(units.lines[i])}>{i < shown.length - 1 ? `${l}\n` : l}</span>)
          : shown.join('\n')}</code>
      </pre>
      {long && !open && (
        <button
          onClick={() => setOpen(true)}
          className={`w-full text-center py-1 text-px10 text-ink-3 hover:text-ink-2 bg-field border-t border-line-strong${fold !== undefined ? ' rv' : ''}`}
          {...revealAttrs(fold, true)}
        >
          … {lines.length - CODE_COLLAPSE_LINES} more lines
        </button>
      )}
    </CodeFrame>
  );
}

function renderInline(text: string, keyBase: string, skin: Skin): ReactNode[] {
  return parseInline(text).map((p: Inline, k) => {
    const key = `${keyBase}-${k}`;
    switch (p.t) {
      case 'code':
        // A card id an agent quoted as code is still a reference to the card: its chip, never the id.
        if (isCardId(p.text)) return <Fragment key={key}>{withRefs(p.text.trim(), skin, key)}</Fragment>;
        return <code key={key} className={inlineCodeClass(skin)}>{p.text}</code>;
      case 'bold':
        return <strong key={key} className="font-semibold">{withRefs(p.text, skin, key)}</strong>;
      case 'italic':
        return <em key={key}>{withRefs(p.text, skin, key)}</em>;
      case 'link':
        return (
          <a key={key} href={p.href} target="_blank" rel="noopener noreferrer" className={linkClass(skin)}>
            {p.text}
          </a>
        );
      default:
        // Card ids in the words are drawn as their chips (components/kit/CardChip.tsx).
        return <Fragment key={key}>{withRefs(p.text, skin, key)}</Fragment>;
    }
  });
}

type TableBlock = Extract<Block, { t: 'table' }>;

/** A table by the iOS rules (kit.css): header bold with a rule under it, light row and column lines, a rounded
 *  border, alignment marks respected, every cell wrapping. */
export function MarkdownTable({ block, keyBase, skin, reveal }: { block: TableBlock; keyBase: string; skin: Skin; reveal?: number }) {
  const wide = tableLayout(block) === 'scroll';
  const n = block.head.length;
  // Up to four columns: each column's floor is its longest word (at most an equal share), the rest shared by its
  // words; the width is the words' own, at most the message's. More: natural widths, each cell capped (kit.css).
  const fit = wide ? null : tableShares(block);
  const columns = fit
    ? fit.columns.map((c) => `minmax(min(calc(${(c.word * 1.15 + 0.5).toFixed(2)}ch + 20px), ${(100 / n).toFixed(3)}%), ${c.weight}fr)`).join(' ')
    : `repeat(${n}, max-content)`;
  return (
    <div className={`kit-table-box${reveal !== undefined ? ' rv' : ''}`} {...revealAttrs(reveal, true)} data-md-table data-columns={n} data-wide={wide ? '' : undefined}
         style={fit ? { width: `min(100%, ${fit.ch}ch)` } : undefined} {...touchProps}>
      <table className="kit-table" role="table" style={{ gridTemplateColumns: columns }}>
        <thead role="rowgroup">
          <tr role="row">
            {block.head.map((h, c) => (
              <th key={c} role="columnheader" style={{ textAlign: block.align[c] ?? 'left' }}>
                <div className="kit-cell">{renderInline(h, `${keyBase}-h${c}`, skin)}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {block.rows.map((r, ri) => (
            <tr key={ri} role="row">
              {r.map((cell, c) => (
                <td key={c} role="cell" style={{ textAlign: block.align[c] ?? 'left' }}>
                  <div className="kit-cell">{renderInline(cell, `${keyBase}-${ri}-${c}`, skin)}</div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Markdown({ text, skin = 'bubbles' }: { text: string; skin?: Skin }) {
  const body = skin === 'bubbles' ? BUBBLES_BODY : '';
  const strong = skin === 'bubbles' ? 'text-foreground' : '';
  const blocks = parseBlocks(text).map((b, bi): ReactNode => {
    const key = `b${bi}`;
    switch (b.t) {
      case 'code':
        return <CodeBlock key={key} code={b.code} lang={b.lang} dark={skin === 'pane'} />;
      case 'heading': {
        return <div key={key} className={`${headingClass(b.level)} ${strong} mt-2 mb-1`}>{renderInline(b.text, key, skin)}</div>;
      }
      case 'list': {
        const items = b.items.map((it, idx) => <li key={idx} className={body}>{renderInline(it, `${key}-${idx}`, skin)}</li>);
        return b.ordered
          ? <ol key={key} start={b.start} className="list-decimal pl-5 my-1 space-y-0.5">{items}</ol>
          : <ul key={key} className="list-disc pl-5 my-1 space-y-0.5">{items}</ul>;
      }
      case 'quote':
        return (
          <blockquote key={key} className={`border-l-2 border-current/30 pl-3 my-1 opacity-80 whitespace-pre-wrap break-words ${body}`}>
            {renderInline(b.text, key, skin)}
          </blockquote>
        );
      case 'table':
        return <MarkdownTable key={key} block={b} keyBase={key} skin={skin} />;
      default:
        return (
          <p key={key} className={`${PARAGRAPH} ${body}`}>
            {renderInline(b.text, key, skin)}
          </p>
        );
    }
  });
  return <div className={`kit-md kit-skin-${skin} space-y-0.5 min-w-0`}>{blocks}</div>;
}
