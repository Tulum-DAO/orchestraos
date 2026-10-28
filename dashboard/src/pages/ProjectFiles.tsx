import { useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, File, FileText, FileImage, Folder, Search } from 'lucide-react';
import { fetchProject, fetchProjectFiles, fetchProjectFile, projectFileRawUrl } from '../lib/api';
import { groupByFolder, textViewFor, parseCsv, cascade, focusFor } from '../lib/projectFiles';
import { isViewHidden } from '../lib/features';
import { setArturoFocus } from '../lib/arturo';
import FloatingWindow from '../components/FloatingWindow';
import Markdown from '../components/chat/Markdown';
import CopyButton from '../components/CopyButton';

/**
 * A project's files (the card's "View files"): every file in its folder except .jsonl logs, laid out by the
 * project's folders. A file opens in its own floating window (several at once), and the window in front is
 * what Arturo is told you are looking at, so "what's in this?" is about that file.
 */

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function iconFor(path: string) {
  if (/\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(path)) return FileImage;
  if (/\.(md|txt|html?|css|js|ts|tsx|json|ya?ml|toml|py|sh|csv|pdf)$/i.test(path)) return FileText;
  return File;
}

function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text);
  if (!rows.length) return <p className="text-sm text-neutral-500 p-4">Empty file.</p>;
  const [head, ...body] = rows;
  return (
    <table className="text-xs text-neutral-300 border-collapse m-3">
      <thead><tr>{head.map((c, i) => <th key={i} className="text-left font-medium text-neutral-200 border border-neutral-800 px-2 py-1 bg-neutral-800/60">{c}</th>)}</tr></thead>
      <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="border border-neutral-800 px-2 py-1 align-top whitespace-pre-wrap">{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

function FileView({ slug, path }: { slug: string; path: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['project-file', slug, path], queryFn: () => fetchProjectFile(slug, path), refetchInterval: 15_000,
  });
  if (isLoading) return <p className="text-sm text-neutral-500 p-4">Loading…</p>;
  if (error || !data) return <p className="text-sm text-red-400 p-4">Couldn't open that file.</p>;
  if (data.kind === 'image') {
    return <div className="p-4"><img src={projectFileRawUrl(slug, path)} alt={path} className="max-w-full rounded-lg border border-neutral-800" /></div>;
  }
  if (data.kind === 'pdf') return <iframe title={path} src={projectFileRawUrl(slug, path)} className="w-full h-full bg-white" />;
  if (data.kind === 'binary') return <p className="text-sm text-neutral-500 p-4">This file isn't text, so it can't be shown here ({fmtSize(data.size)}).</p>;
  const view = textViewFor(path);
  return (
    <>
      {view === 'markdown' && <div className="p-4 text-sm text-neutral-200"><Markdown text={data.text} /></div>}
      {/* An agent's HTML is shown as a page with NO script and no access to the dashboard (empty sandbox). */}
      {view === 'html' && <iframe title={path} sandbox="" srcDoc={data.text} className="w-full h-full bg-white" />}
      {view === 'csv' && <CsvTable text={data.text} />}
      {view === 'code' && <pre className="text-xs leading-relaxed text-neutral-300 p-4 whitespace-pre-wrap break-words font-mono">{data.text}</pre>}
      {data.truncated && <p className="text-xs text-neutral-500 px-4 pb-4">Showing the first 512 KB.</p>}
    </>
  );
}

/** A text file window's copy button: the file's text as it is on disk (a markdown file's source, not its
 *  rendering). Reads the same query the window's body does, so it fetches nothing extra; none for images, PDFs
 *  and other files that aren't text. */
function FileCopy({ slug, path }: { slug: string; path: string }) {
  const { data } = useQuery({ queryKey: ['project-file', slug, path], queryFn: () => fetchProjectFile(slug, path), refetchInterval: 15_000 });
  if (!data || data.kind === 'image' || data.kind === 'pdf' || data.kind === 'binary' || typeof data.text !== 'string') return null;
  return <CopyButton text={data.text} label="Copy file" size={15} className="p-1 text-neutral-400 hover:text-white hover:bg-neutral-800" />;
}

interface Win { path: string; z: number; x: number; y: number }

export default function ProjectFiles() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const [filter, setFilter] = useState('');
  const [wins, setWins] = useState<Win[]>([]);
  const [zTop, setZTop] = useState(60);
  const project = useQuery({ queryKey: ['project', slug], queryFn: () => fetchProject(slug) });
  const files = useQuery({ queryKey: ['project-files', slug], queryFn: () => fetchProjectFiles(slug), refetchInterval: 15_000 });
  const name = project.data?.name || slug;
  const unfiled = useMemo(() => new Set(files.data?.unfiled ?? []), [files.data]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = files.data?.files ?? [];
    return groupByFolder(q ? list.filter((f) => f.path.toLowerCase().includes(q)) : list);
  }, [files.data, filter]);

  const top = wins.length ? wins.reduce((a, b) => (b.z > a.z ? b : a)).path : null;
  // Arturo sees what is in front of you: the top file window, else this project.
  useEffect(() => { setArturoFocus(focusFor(slug, name, files.data?.folder, top)); }, [slug, name, files.data?.folder, top]);
  useEffect(() => () => setArturoFocus(null), []);

  const raise = (path: string) => {
    const z = zTop + 1;
    setZTop(z);
    setWins((ws) => ws.map((w) => (w.path === path ? { ...w, z } : w)));
  };
  const openFile = (path: string) => {
    if (wins.some((w) => w.path === path)) { raise(path); return; }
    const z = zTop + 1;
    setZTop(z);
    const { x, y } = cascade(wins.length, window.innerWidth, window.innerHeight);
    setWins((ws) => [...ws, { path, z, x, y }]);
  };
  const close = (path: string) => setWins((ws) => ws.filter((w) => w.path !== path));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        {!isViewHidden('/projects') && (
          <button onClick={() => navigate('/projects')} className="flex items-center gap-1.5 text-sm text-neutral-400 hover:text-white">
            <ArrowLeft size={14} /> Projects
          </button>
        )}
        <h1 className="text-xl font-semibold text-white">{name} <span className="text-neutral-500 font-normal">files</span></h1>
        {files.data && <span className="text-xs text-neutral-600 font-mono truncate">{files.data.folder}</span>}
      </div>

      {files.error ? (
        <p className="text-sm text-neutral-400">This project has no folder yet, or it couldn't be read.</p>
      ) : (
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 max-w-3xl">
          <div className="p-2 border-b border-neutral-800 flex items-center gap-2">
            <Search size={14} className="text-neutral-500 ml-1" />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter files"
                   className="bg-transparent text-sm text-neutral-200 placeholder-neutral-600 outline-none flex-1 py-1" />
          </div>
          {unfiled.size > 0 && (
            <div className="flex items-start gap-2 px-3 py-2 text-xs text-amber-300 bg-amber-500/10 border-b border-neutral-800">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{unfiled.size} file{unfiled.size === 1 ? ' is' : 's are'} outside this project's folders (marked below). The project manager tidies these.</span>
            </div>
          )}
          <div className="py-1">
            {files.isLoading && <p className="text-sm text-neutral-500 p-3">Loading…</p>}
            {files.data && files.data.total === 0 && <p className="text-sm text-neutral-500 p-3">No files yet. Agents working on this project will put them here.</p>}
            {shown.map(([dir, list]) => (
              <div key={dir || '.'}>
                {dir && <div className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-xs text-neutral-500"><Folder size={12} /> {dir}</div>}
                {list.map((f) => {
                  const Icon = iconFor(f.path);
                  const fname = f.path.slice(f.path.lastIndexOf('/') + 1);
                  return (
                    <button key={f.path} onClick={() => openFile(f.path)}
                            className={clsx('w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-neutral-800/60',
                              dir && 'pl-6', wins.some((w) => w.path === f.path) ? 'bg-neutral-800 text-white' : 'text-neutral-300')}>
                      <Icon size={14} className="text-neutral-500 shrink-0" />
                      <span className="truncate flex-1">{fname}</span>
                      {unfiled.has(f.path) && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 shrink-0">unfiled</span>}
                      <span className="text-xs text-neutral-600 shrink-0">{fmtSize(f.size)}</span>
                    </button>
                  );
                })}
              </div>
            ))}
            {files.data?.truncated && <p className="text-xs text-neutral-500 p-3">Showing the first 2,000 files.</p>}
          </div>
        </div>
      )}

      {wins.map((w) => (
        <FloatingWindow key={w.path} title={w.path.slice(w.path.lastIndexOf('/') + 1)} subtitle={`${name} · ${w.path}`}
                        x={w.x} y={w.y} z={w.z} onFocus={() => { if (w.path !== top) raise(w.path); }} onClose={() => close(w.path)}
                        actions={<FileCopy slug={slug} path={w.path} />}>
          <FileView slug={slug} path={w.path} />
        </FloatingWindow>
      ))}
    </div>
  );
}
