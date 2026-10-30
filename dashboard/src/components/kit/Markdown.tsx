/**
 * The thread kit's Markdown: the one renderer for every agent's messages, Arturo's included: fenced code (long blocks
 * collapse, each has a copy button), headings, bullet and numbered lists, quotes, tables and links. Parsing lives in
 * lib/threadKit/markdown.ts (tested there). Dependency-free, no CDN.
 *
 * Skins: "bubbles" draws the agent chat's 13px text; "modern" takes the size and colour of the reply it sits in
 * (Arturo's page); "pane" is Arturo's floating pane, always dark, at its 14px. Tables follow the iOS app's rules in
 * every skin (kit.css).
 */
import { Fragment, useState, type ReactNode, type TouchEvent } from 'react';
import Copy from './Copy';
import { parseBlocks, parseInline, tableLayout, tableShares, type Block, type Inline } from '../../lib/threadKit/markdown';
import { isCardId } from '../../lib/cardRefs';
import { withRefs } from './RefText';
import './kit.css';

export type Skin = 'bubbles' | 'modern' | 'pane';

const CODE_COLLAPSE_LINES = 14;

/** A touch that starts in a table or a code scroller stays there: it starts no swipe of the thread's or the page's. */
const keepTouch = (e: TouchEvent) => e.stopPropagation();
const touchProps = { onTouchStart: keepTouch, onTouchMove: keepTouch, onTouchEnd: keepTouch };

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const lines = code.replace(/\n$/, '').split('\n');
  const long = lines.length > CODE_COLLAPSE_LINES;
  const [open, setOpen] = useState(!long);
  const shown = open ? lines : lines.slice(0, CODE_COLLAPSE_LINES);
  return (
    <div className="my-1.5 rounded-md border border-neutral-800 bg-neutral-950 overflow-hidden">
      {/* The title bar: the language, then (at the right) the fold control and a copy button, which copies the
          whole block even while it is folded. */}
      <div className="touch-compact flex items-center gap-2 px-2.5 py-1 bg-neutral-900/60 border-b border-neutral-800">
        <span className="text-[10px] font-mono text-neutral-500">{lang || 'code'}</span>
        <span className="ml-auto" />
        {long && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="text-[10px] text-blue-400 hover:text-blue-300"
          >
            {open ? 'Collapse' : `Show ${lines.length} lines`}
          </button>
        )}
        <Copy text={lines.join('\n')} label="Copy code" size={12}
              className="-mr-1 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800" />
      </div>
      <pre className="kit-code-scroll px-3 py-2 overflow-x-auto text-[12px] leading-relaxed" {...touchProps}>
        <code className="font-mono text-neutral-200 whitespace-pre">{shown.join('\n')}</code>
      </pre>
      {long && !open && (
        <button
          onClick={() => setOpen(true)}
          className="w-full text-center py-1 text-[10px] text-neutral-500 hover:text-neutral-300 bg-neutral-900/40 border-t border-neutral-800"
        >
          … {lines.length - CODE_COLLAPSE_LINES} more lines
        </button>
      )}
    </div>
  );
}

function renderInline(text: string, keyBase: string, skin: Skin): ReactNode[] {
  return parseInline(text).map((p: Inline, k) => {
    const key = `${keyBase}-${k}`;
    switch (p.t) {
      case 'code':
        // A card id an agent quoted as code is still a reference to the card: its chip, never the id.
        if (isCardId(p.text)) return <Fragment key={key}>{withRefs(p.text.trim(), skin, key)}</Fragment>;
        // Modern: the page's own `code` style (Arturo's .txt code), so his replies keep their look.
        return skin === 'modern' ? <code key={key}>{p.text}</code>
          : skin === 'pane' ? <code key={key} className="px-1 py-0.5 rounded bg-white/10 text-[0.92em] font-mono">{p.text}</code>
            : <code key={key} className="px-1 py-0.5 rounded bg-neutral-800 text-[0.92em] font-mono text-amber-200">{p.text}</code>;
      case 'bold':
        return <strong key={key} className="font-semibold">{withRefs(p.text, skin, key)}</strong>;
      case 'italic':
        return <em key={key}>{withRefs(p.text, skin, key)}</em>;
      case 'link':
        return (
          <a key={key} href={p.href} target="_blank" rel="noopener noreferrer"
             className={`underline underline-offset-2 decoration-1 hover:opacity-80 break-words ${skin === 'pane' ? 'text-blue-400' : 'text-blue-600 dark:text-blue-400'}`}>
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
export function MarkdownTable({ block, keyBase, skin }: { block: TableBlock; keyBase: string; skin: Skin }) {
  const wide = tableLayout(block) === 'scroll';
  const n = block.head.length;
  // Up to four columns: each column's floor is its longest word (at most an equal share), the rest shared by its
  // words; the width is the words' own, at most the message's. More: natural widths, each cell capped (kit.css).
  const fit = wide ? null : tableShares(block);
  const columns = fit
    ? fit.columns.map((c) => `minmax(min(calc(${(c.word * 1.15 + 0.5).toFixed(2)}ch + 20px), ${(100 / n).toFixed(3)}%), ${c.weight}fr)`).join(' ')
    : `repeat(${n}, max-content)`;
  return (
    <div className="kit-table-box" data-md-table data-columns={n} data-wide={wide ? '' : undefined}
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
  const body = skin === 'bubbles' ? 'text-[13px] text-foreground/90' : '';
  const strong = skin === 'bubbles' ? 'text-foreground' : '';
  const blocks = parseBlocks(text).map((b, bi): ReactNode => {
    const key = `b${bi}`;
    switch (b.t) {
      case 'code':
        return <CodeBlock key={key} code={b.code} lang={b.lang} />;
      case 'heading': {
        const cls = b.level <= 1 ? 'text-[1.15em] font-semibold' : b.level === 2 ? 'text-[1.05em] font-semibold' : 'font-medium';
        return <div key={key} className={`${cls} ${strong} mt-2 mb-1`}>{renderInline(b.text, key, skin)}</div>;
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
          <p key={key} className={`leading-relaxed whitespace-pre-wrap break-words ${body}`}>
            {renderInline(b.text, key, skin)}
          </p>
        );
    }
  });
  return <div className={`kit-md kit-skin-${skin} space-y-0.5 min-w-0`}>{blocks}</div>;
}
