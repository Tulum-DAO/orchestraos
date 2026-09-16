/**
 * MessageBubble — renders a single user or assistant message.
 * Detects ASCII/markdown tables (including box-drawing │) and renders as HTML.
 */
import { clsx } from 'clsx';
import type { UserMessageEvent, AssistantMessageEvent } from '../../lib/claude-code-protocol';

interface Props {
  event: UserMessageEvent | AssistantMessageEvent;
}

const BORDER_CHARS_RE = /^[|│\-─━═╌╍┄┅┈┉+┼├┤┬┴┌┐└┘:\s]+$/;
const HAS_DASH = /[─━═╌╍┄┅┈┉┼├┤┬┴┌┐└┘\-+]/;
function isBorder(s: string): boolean {
  const t = s.trim();
  return BORDER_CHARS_RE.test(t) && HAS_DASH.test(t);
}
const HAS_PIPE = /[|│]/;
const PIPE_SPLIT = /[|│]/;

interface TableData { headers: string[]; rows: string[][]; }

function extractTable(rawLines: string[]): { table: TableData; beforeIdx: number; afterIdx: number } | null {
  let firstIdx = -1, lastIdx = -1;
  for (let i = 0; i < rawLines.length; i++) {
    const t = rawLines[i].trim();
    if (!t) continue;
    if (HAS_PIPE.test(t) || isBorder(t)) {
      if (firstIdx === -1) firstIdx = i;
      lastIdx = i;
    }
  }
  if (firstIdx === -1) return null;

  const region = rawLines.slice(firstIdx, lastIdx + 1);
  const completeRows: string[] = [];
  let accumulator = '';

  for (const line of region) {
    const t = line.trim();
    if (!t) continue;
    if (isBorder(t) && !/[a-zA-Z0-9]/.test(t)) continue;

    if (HAS_PIPE.test(t)) {
      accumulator = accumulator ? accumulator + ' ' + t : t;
      const trimmed = accumulator.trim();
      const startsP = trimmed[0] === '|' || trimmed[0] === '│';
      const endsP = trimmed[trimmed.length - 1] === '|' || trimmed[trimmed.length - 1] === '│';
      if (startsP && endsP && trimmed.length > 1) {
        completeRows.push(trimmed);
        accumulator = '';
      }
    }
  }
  if (accumulator.trim()) {
    const trimmed = accumulator.trim();
    const startsP = trimmed[0] === '|' || trimmed[0] === '│';
    const endsP = trimmed[trimmed.length - 1] === '|' || trimmed[trimmed.length - 1] === '│';
    if (startsP) completeRows.push(endsP ? trimmed : trimmed + ' │');
  }

  if (completeRows.length < 2) return null;
  const parseCells = (row: string): string[] => row.split(PIPE_SPLIT).map(c => c.trim()).filter(c => c !== '');
  const headers = parseCells(completeRows[0]);
  if (headers.length < 2) return null;
  const rows = completeRows.slice(1).map(parseCells).filter(r => r.length === headers.length);
  if (rows.length === 0) return null;

  return { table: { headers, rows }, beforeIdx: firstIdx, afterIdx: lastIdx + 1 };
}

function renderTable(table: TableData) {
  return (
    <div className="overflow-x-auto my-1.5 -mx-1 rounded-lg border border-neutral-700/50">
      <table className="text-[11px] border-collapse w-full min-w-max">
        <thead>
          <tr className="bg-neutral-800/60">
            {table.headers.map((h, i) => (
              <th key={i} className="text-left px-3 py-1.5 text-neutral-300 font-medium whitespace-nowrap border-b border-neutral-700">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, ri) => (
            <tr key={ri} className="border-b border-neutral-700/50 last:border-0 hover:bg-neutral-800/30">
              {row.map((cell, ci) => (
                <td key={ci} className="px-3 py-1.5 text-neutral-300 align-top whitespace-nowrap">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderContent(content: string) {
  const lines = content.split('\n');
  const extracted = extractTable(lines);
  if (!extracted) return <span className="whitespace-pre-wrap">{content}</span>;
  const { table, beforeIdx, afterIdx } = extracted;
  const before = lines.slice(0, beforeIdx).join('\n').trim();
  const after = lines.slice(afterIdx).join('\n').trim();
  return (
    <>
      {before && <span className="whitespace-pre-wrap">{before + '\n'}</span>}
      {renderTable(table)}
      {after && <span className="whitespace-pre-wrap">{'\n' + after}</span>}
    </>
  );
}

export default function MessageBubble({ event }: Props) {
  const isUser = event.type === 'user_message';
  const hasTable = !isUser && HAS_PIPE.test(event.content) && event.content.split('\n').some(l => isBorder(l.trim()));

  return (
    <div className={clsx('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={clsx(
        'text-xs px-3 py-2 rounded-2xl max-w-[85%] break-words leading-relaxed',
        isUser ? 'bg-blue-600/20 text-blue-200 rounded-br-md whitespace-pre-wrap' : 'bg-neutral-800 text-neutral-200 rounded-bl-md',
        !isUser && !hasTable && 'whitespace-pre-wrap'
      )}>
        {hasTable ? renderContent(event.content) : event.content}
      </div>
    </div>
  );
}
