/**
 * Minimal, dependency-free markdown renderer for clean transcript text.
 * Handles: fenced code (```), inline code, bold, headings, bullet/numbered
 * lists, and paragraphs. Long fenced code blocks collapse (goal 2). No CDN
 * imports (see reference_vps_browser_testing — never import JS from a CDN).
 */
import { useState } from 'react';

const CODE_COLLAPSE_LINES = 14;

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const lines = code.replace(/\n$/, '').split('\n');
  const long = lines.length > CODE_COLLAPSE_LINES;
  const [open, setOpen] = useState(!long);
  const shown = open ? lines : lines.slice(0, CODE_COLLAPSE_LINES);
  return (
    <div className="my-1.5 rounded-md border border-neutral-800 bg-neutral-950 overflow-hidden">
      <div className="flex items-center justify-between px-2.5 py-1 bg-neutral-900/60 border-b border-neutral-800">
        <span className="text-[10px] font-mono text-neutral-500">{lang || 'code'}</span>
        {long && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="text-[10px] text-blue-400 hover:text-blue-300"
          >
            {open ? 'Collapse' : `Show ${lines.length} lines`}
          </button>
        )}
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

/** Inline markdown: `code`, **bold**, *italic*. */
function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('`')) {
      out.push(
        <code key={`${keyBase}-c${k}`} className="px-1 py-0.5 rounded bg-neutral-800 text-[12px] font-mono text-amber-200">
          {tok.slice(1, -1)}
        </code>
      );
    } else if (tok.startsWith('**')) {
      out.push(<strong key={`${keyBase}-b${k}`} className="font-semibold text-neutral-100">{tok.slice(2, -2)}</strong>);
    } else {
      out.push(<em key={`${keyBase}-i${k}`}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
    k++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.split('\n');
  let i = 0;
  let bi = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      const lang = fence[1];
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      i++; // closing fence
      blocks.push(<CodeBlock key={`cb${bi++}`} code={buf.join('\n')} lang={lang} />);
      continue;
    }

    // heading
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      const lvl = h[1].length;
      const cls = lvl <= 1 ? 'text-base font-semibold' : lvl === 2 ? 'text-sm font-semibold' : 'text-sm font-medium';
      blocks.push(
        <div key={`h${bi++}`} className={`${cls} text-neutral-100 mt-2 mb-1`}>
          {renderInline(h[2], `h${bi}`)}
        </div>
      );
      i++;
      continue;
    }

    // list (bullet or numbered) — gather consecutive items
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''));
        i++;
      }
      blocks.push(
        <ul key={`ul${bi++}`} className="list-disc pl-5 my-1 space-y-0.5">
          {items.map((it, idx) => (
            <li key={idx} className="text-[13px] text-neutral-200">{renderInline(it, `li${bi}-${idx}`)}</li>
          ))}
        </ul>
      );
      continue;
    }

    // blank
    if (!line.trim()) {
      i++;
      continue;
    }

    // paragraph — gather until blank / structural line
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !/^```|^#{1,4}\s|^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={`p${bi++}`} className="text-[13px] leading-relaxed text-neutral-200 whitespace-pre-wrap break-words">
        {renderInline(para.join('\n'), `p${bi}`)}
      </p>
    );
  }

  return <div className="space-y-0.5">{blocks}</div>;
}
