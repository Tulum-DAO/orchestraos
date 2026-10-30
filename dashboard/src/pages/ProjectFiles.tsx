import { useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, File, FileText, FileImage, Folder, Search, UserRound } from 'lucide-react';
import { fetchProject, fetchProjectFiles } from '../lib/api';
import { groupByFolder, cascade, focusFor } from '../lib/projectFiles';
import { isViewHidden } from '../lib/features';
import { setArturoFocus } from '../lib/arturo';
import FileWindow from '../components/kit/FileWindow';
import { fmtSize, projectFileSource } from '../lib/fileWindow';
import { fetchProjectMadeBy, makerOptions, madeByMatches, madeByLabel, type MakerChoice } from '../lib/drawerLibrary';

/**
 * A project's files (the card's "View files"): every file in its folder except .jsonl logs, laid out by the
 * project's folders. A file opens in its own floating window (several at once), and the window in front is
 * what Arturo is told you are looking at, so "what's in this?" is about that file.
 *
 * "Made by" narrows the list to what one agent made (from the library index; a file an agent only showed or named
 * never counts), to files the index could not settle between agents (Unclear, naming them), or to files changed
 * while no agent was working (Unattributed). Each row says who made it. Without the index the filter is not shown.
 */

function iconFor(path: string) {
  if (/\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(path)) return FileImage;
  if (/\.(md|txt|html?|css|js|ts|tsx|json|ya?ml|toml|py|sh|csv|pdf)$/i.test(path)) return FileText;
  return File;
}

interface Win { path: string; z: number; x: number; y: number }

export default function ProjectFiles() {
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const [filter, setFilter] = useState('');
  const [maker, setMaker] = useState<MakerChoice>('everyone');
  const [wins, setWins] = useState<Win[]>([]);
  const [zTop, setZTop] = useState(60);
  const project = useQuery({ queryKey: ['project', slug], queryFn: () => fetchProject(slug) });
  const files = useQuery({ queryKey: ['project-files', slug], queryFn: () => fetchProjectFiles(slug), refetchInterval: 15_000 });
  const name = project.data?.name || slug;
  const unfiled = useMemo(() => new Set(files.data?.unfiled ?? []), [files.data]);
  const madeBy = useQuery({ queryKey: ['project-made-by', slug], queryFn: () => fetchProjectMadeBy(slug), refetchInterval: 60_000, retry: false });
  const makers = useMemo(() => makerOptions((files.data?.files ?? []).map((f) => f.path), madeBy.data), [files.data, madeBy.data]);
  // a choice that has no files any more (its files moved or went) falls back to everyone
  const choice: MakerChoice = makers.some((o) => o.value === maker) ? maker : 'everyone';

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = (files.data?.files ?? []).filter((f) => (!q || f.path.toLowerCase().includes(q))
      && (choice === 'everyone' || madeByMatches(madeBy.data?.files[f.path], choice)));
    return groupByFolder(list);
  }, [files.data, filter, choice, madeBy.data]);

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
          <button onClick={() => navigate('/projects')} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft size={14} /> Projects
          </button>
        )}
        <h1 className="text-xl font-semibold text-foreground">{name} <span className="text-muted-foreground font-normal">files</span></h1>
        {files.data && <span className="text-xs text-muted-foreground font-mono truncate">{files.data.folder}</span>}
      </div>

      {files.error ? (
        <p className="text-sm text-tone-400">This project has no folder yet, or it couldn't be read.</p>
      ) : (
        <div className="rounded-xl border border-tone-800 bg-tone-900 max-w-3xl">
          <div className="p-2 border-b border-tone-800 flex items-center gap-2">
            <Search size={14} className="text-tone-500 ml-1" />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter files"
                   className="bg-transparent text-sm text-tone-200 placeholder-tone-600 outline-none flex-1 min-w-0 py-1" />
            {makers.length > 1 && (
              <label className="flex items-center gap-1.5 shrink-0 max-w-[55%] text-xs text-tone-500">
                <UserRound size={13} className="shrink-0" aria-hidden />
                <span className="hidden sm:inline">Made by</span>
                <select value={choice} onChange={(e) => setMaker(e.target.value as MakerChoice)} aria-label="Made by" data-made-by-filter
                        className="min-w-0 max-w-full truncate bg-tone-800 border border-tone-700 rounded-md text-sm text-tone-200 px-2 py-1 outline-none focus:border-tone-500">
                  {makers.map((o) => <option key={o.value} value={o.value}>{o.label} ({o.count})</option>)}
                </select>
              </label>
            )}
          </div>
          {unfiled.size > 0 && (
            <div className="flex items-start gap-2 px-3 py-2 text-xs text-tone-amber-300 bg-amber-500/10 border-b border-tone-800">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{unfiled.size} file{unfiled.size === 1 ? ' is' : 's are'} outside this project's folders (marked below). The project manager tidies these.</span>
            </div>
          )}
          <div className="py-1">
            {files.isLoading && <p className="text-sm text-tone-500 p-3">Loading…</p>}
            {files.data && files.data.total === 0 && <p className="text-sm text-tone-500 p-3">No files yet. Agents working on this project will put them here.</p>}
            {files.data && files.data.total > 0 && shown.length === 0 && <p className="text-sm text-tone-500 p-3">No files match.</p>}
            {choice === 'unclear' && shown.length > 0 && (
              <p className="text-xs text-tone-500 px-3 pt-2">Changed while more than one agent was working here; each row names who it could be.</p>
            )}
            {choice === 'unattributed' && shown.length > 0 && (
              <p className="text-xs text-tone-500 px-3 pt-2">Changed while no agent was working here.</p>
            )}
            {shown.map(([dir, list]) => (
              <div key={dir || '.'}>
                {dir && <div className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-xs text-tone-500"><Folder size={12} /> {dir}</div>}
                {list.map((f) => {
                  const Icon = iconFor(f.path);
                  const fname = f.path.slice(f.path.lastIndexOf('/') + 1);
                  const entry = madeBy.data?.files[f.path];
                  // under one agent's choice its own name on every row says nothing; others it shares with still show
                  const by = choice.startsWith('agent:') && entry?.agents.length === 1 ? '' : madeByLabel(entry);
                  return (
                    <button key={f.path} onClick={() => openFile(f.path)}
                            className={clsx('w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-tone-800/60',
                              dir && 'pl-6', wins.some((w) => w.path === f.path) ? 'bg-tone-800 text-tone-white' : 'text-tone-300')}>
                      <Icon size={14} className="text-tone-500 shrink-0" />
                      {/* who made it: beside the name on a wide screen, under it on a phone so the candidates fit */}
                      <span className="flex-1 min-w-0 flex flex-col sm:flex-row sm:items-center sm:gap-2">
                        <span className="truncate sm:flex-1">{fname}</span>
                        {by && <span className={clsx('text-[11px] truncate sm:max-w-[45%]', entry?.attribution === 'agent' ? 'text-tone-400' : 'text-tone-500 italic')}
                                     title={by} data-made-by={entry?.attribution}>{by}</span>}
                      </span>
                      {unfiled.has(f.path) && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-tone-amber-300 shrink-0">unfiled</span>}
                      <span className="text-xs text-tone-600 shrink-0">{fmtSize(f.size)}</span>
                    </button>
                  );
                })}
              </div>
            ))}
            {files.data?.truncated && <p className="text-xs text-tone-500 p-3">Showing the first 2,000 files.</p>}
          </div>
        </div>
      )}

      {wins.map((w) => (
        <FileWindow key={w.path} src={projectFileSource(slug, w.path)} title={w.path.slice(w.path.lastIndexOf('/') + 1)} subtitle={`${name} · ${w.path}`}
                    x={w.x} y={w.y} z={w.z} onFocus={() => { if (w.path !== top) raise(w.path); }} onClose={() => close(w.path)} />
      ))}
    </div>
  );
}
