import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { fmtSize, type FileSource } from '../../lib/fileWindow';
import { textViewFor, parseCsv } from '../../lib/projectFiles';
import FloatingWindow from '../FloatingWindow';
import Markdown from '../chat/Markdown';
import CopyButton from '../CopyButton';

/**
 * One file in a floating window: an image or PDF as itself, markdown rendered, an agent's HTML as an inert page,
 * a CSV as a table, anything else as text, with a copy button for text files. Where the file comes from is the
 * caller's: a project's file (the project files page) or a library item (the drawer's Library).
 */

function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text);
  if (!rows.length) return <p className="text-sm text-muted-foreground p-4">Empty file.</p>;
  const [head, ...body] = rows;
  return (
    <table className="text-xs text-card-foreground border-collapse m-3">
      <thead><tr>{head.map((c, i) => <th key={i} className="text-left font-medium text-card-foreground border border-border px-2 py-1 bg-muted">{c}</th>)}</tr></thead>
      <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="border border-border px-2 py-1 align-top whitespace-pre-wrap">{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

const useFile = (src: FileSource) => useQuery({ queryKey: src.key, queryFn: src.load, refetchInterval: 15_000 });

function FileView({ src }: { src: FileSource }) {
  const { data, isLoading, error } = useFile(src);
  const path = src.path;
  if (isLoading) return <p className="text-sm text-muted-foreground p-4">Loading…</p>;
  if (error || !data) return <p className="text-sm text-destructive p-4">Couldn't open that file.</p>;
  if (data.kind === 'image') {
    return <div className="p-4"><img src={src.rawUrl} alt={path} className="max-w-full rounded-lg border border-border" /></div>;
  }
  if (data.kind === 'pdf') return <iframe title={path} src={src.rawUrl} className="w-full h-full bg-white" />;
  if (data.kind === 'binary') return <p className="text-sm text-muted-foreground p-4">This file isn't text, so it can't be shown here ({fmtSize(data.size)}).</p>;
  const view = textViewFor(path);
  return (
    <>
      {view === 'markdown' && <div className="p-4 text-sm text-card-foreground"><Markdown text={data.text} /></div>}
      {/* An agent's HTML is shown as a page with NO script and no access to the dashboard (empty sandbox). */}
      {view === 'html' && <iframe title={path} sandbox="" srcDoc={data.text} className="w-full h-full bg-white" />}
      {view === 'csv' && <CsvTable text={data.text} />}
      {view === 'code' && <pre className="text-xs leading-relaxed text-card-foreground p-4 whitespace-pre-wrap break-words font-mono">{data.text}</pre>}
      {data.truncated && <p className="text-xs text-muted-foreground px-4 pb-4">Showing the first 512 KB.</p>}
    </>
  );
}

/** A text file window's copy button: the file's text as it is on disk (a markdown file's source, not its
 *  rendering). Reads the same query the window's body does, so it fetches nothing extra; none for images, PDFs
 *  and other files that aren't text. */
function FileCopy({ src }: { src: FileSource }) {
  const { data } = useFile(src);
  if (!data || data.kind === 'image' || data.kind === 'pdf' || data.kind === 'binary' || typeof data.text !== 'string') return null;
  return <CopyButton text={data.text} label="Copy file" size={15} className="p-1 text-muted-foreground hover:text-foreground hover:bg-accent" />;
}

export default function FileWindow({ src, title, subtitle, x, y, z, onFocus, onClose, overDialog }: {
  src: FileSource; title: string; subtitle?: string; x: number; y: number; z: number;
  onFocus: () => void; onClose: () => void;
  /** Opened from inside a modal dialog (the drawer): drawn on the page's body, so the dialog's slide transform
   *  doesn't hold it. The opener keeps Esc and Tab from reaching the dialog while windows are open. */
  overDialog?: boolean;
}) {
  const win = (
    <FloatingWindow title={title} subtitle={subtitle} x={x} y={y} z={z} onFocus={onFocus} onClose={onClose}
                    actions={<FileCopy src={src} />}>
      <FileView src={src} />
    </FloatingWindow>
  );
  return overDialog ? createPortal(<div data-file-window>{win}</div>, document.body) : win;
}
