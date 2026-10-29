/**
 * The one Markdown renderer for every agent's messages, Arturo's included: fenced code (long blocks collapse, each
 * has a copy button), headings, bullet and numbered lists, quotes, tables and links. Parsing lives in
 * lib/markdownBlocks.ts (tested there). Dependency-free, no CDN.
 *
 * `size`: "bubbles" draws the agent chat's 13px text; "modern" takes the size and colour of the reply it sits in
 * (Arturo's page), so the same message reads right in either look.
 */
import { useState, type ReactNode } from 'react';
import CopyButton from '../CopyButton';
import { parseBlocks, parseInline, type Inline } from '../../lib/markdownBlocks';

const CODE_COLLAPSE_LINES = 14;

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
        <CopyButton text={lines.join('\n')} label="Copy code" size={12}
                    className="-mr-1 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800" />
      </div>
      <pre className="px-3 py-2 overflow-x-auto text-[12px] leading-relaxed">
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

type Size = 'bubbles' | 'modern';

function renderInline(text: string, keyBase: string, size: Size = 'bubbles'): ReactNode[] {
  return parseInline(text).map((p: Inline, k) => {
    const key = `${keyBase}-${k}`;
    switch (p.t) {
      case 'code':
        // Modern: the page's own `code` style (Arturo's .txt code), so his replies keep their look.
        return size === 'modern' ? <code key={key}>{p.text}</code>
          : <code key={key} className="px-1 py-0.5 rounded bg-neutral-800 text-[0.92em] font-mono text-amber-200">{p.text}</code>;
      case 'bold':
        return <strong key={key} className="font-semibold">{p.text}</strong>;
      case 'italic':
        return <em key={key}>{p.text}</em>;
      case 'link':
        return (
          <a key={key} href={p.href} target="_blank" rel="noopener noreferrer"
             className="underline underline-offset-2 decoration-1 text-blue-600 dark:text-blue-400 hover:opacity-80 break-words">
            {p.text}
          </a>
        );
      default:
        return p.text;
    }
  });
}

export default function Markdown({ text, size = 'bubbles' }: { text: string; size?: Size }) {
  const body = size === 'modern' ? '' : 'text-[13px] text-foreground/90';
  const strong = size === 'modern' ? '' : 'text-foreground';
  const blocks = parseBlocks(text).map((b, bi): ReactNode => {
    const key = `b${bi}`;
    switch (b.t) {
      case 'code':
        return <CodeBlock key={key} code={b.code} lang={b.lang} />;
      case 'heading': {
        const cls = b.level <= 1 ? 'text-[1.15em] font-semibold' : b.level === 2 ? 'text-[1.05em] font-semibold' : 'font-medium';
        return <div key={key} className={`${cls} ${strong} mt-2 mb-1`}>{renderInline(b.text, key, size)}</div>;
      }
      case 'list': {
        const items = b.items.map((it, idx) => <li key={idx} className={body}>{renderInline(it, `${key}-${idx}`, size)}</li>);
        return b.ordered
          ? <ol key={key} start={b.start} className="list-decimal pl-5 my-1 space-y-0.5">{items}</ol>
          : <ul key={key} className="list-disc pl-5 my-1 space-y-0.5">{items}</ul>;
      }
      case 'quote':
        return (
          <blockquote key={key} className={`border-l-2 border-current/30 pl-3 my-1 opacity-80 whitespace-pre-wrap break-words ${body}`}>
            {renderInline(b.text, key, size)}
          </blockquote>
        );
      case 'table':
        return (
          // Wide tables scroll inside their own box, never the page.
          <div key={key} className="my-1.5 w-fit max-w-full overflow-x-auto rounded-md border border-current/15" data-md-table>
            <table className={`border-collapse ${size === 'modern' ? '' : 'text-[12.5px]'}`}>
              <thead>
                <tr>
                  {b.head.map((h, c) => (
                    <th key={c} style={{ textAlign: b.align[c] ?? 'left' }}
                        className="px-2.5 py-1.5 font-semibold border-b border-current/15 bg-current/[0.04] whitespace-nowrap">
                      {renderInline(h, `${key}-h${c}`, size)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, ri) => (
                  <tr key={ri} className="border-t border-current/10">
                    {r.map((cell, c) => (
                      <td key={c} style={{ textAlign: b.align[c] ?? 'left' }} className="px-2.5 py-1.5 align-top">
                        {renderInline(cell, `${key}-${ri}-${c}`, size)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      default:
        return (
          <p key={key} className={`leading-relaxed whitespace-pre-wrap break-words ${body}`}>
            {renderInline(b.text, key, size)}
          </p>
        );
    }
  });
  return <div className="space-y-0.5 min-w-0">{blocks}</div>;
}
