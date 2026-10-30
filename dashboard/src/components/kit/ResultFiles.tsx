/**
 * ResultFiles — the files a result card names, each linked to what the library knows of it (4g.5): a file an agent
 * made or showed opens in the floating file window through its library item (a gone scratch file from its kept
 * copy), a project's file through the project's own route. A path the library does not know, or a gone file with
 * no copy, stays the plain path. One lookup per card.
 */
import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileText } from 'lucide-react';
import FileWindow from './FileWindow';
import { cascade } from '../../lib/projectFiles';
import { projectFileSource } from '../../lib/fileWindow';
import { fetchLibraryLookup, fileTarget, isGone, librarySource, shortPath, type FileTarget } from '../../lib/drawerLibrary';

// Above the thread, its composer and the drawer.
let zTop = 90;
const CHIP = 'inline-flex max-w-full items-center gap-1 rounded-md border border-current/15 px-1.5 py-0.5 font-mono text-[11.5px]';

interface Win { path: string; target: FileTarget; x: number; y: number; z: number }

export default function ResultFiles({ files, agent }: { files: string[]; agent?: string }) {
  const lookup = useQuery({
    queryKey: ['library-lookup', agent ?? '', ...files], queryFn: () => fetchLibraryLookup(files, agent),
    enabled: files.length > 0, staleTime: 60_000, retry: false,
  });
  const [wins, setWins] = useState<Win[]>([]);
  const opened = useRef(0);
  const open = (path: string, target: FileTarget) => {
    const z = ++zTop;
    setWins((ws) => {
      if (ws.some((w) => w.path === path)) return ws.map((w) => (w.path === path ? { ...w, z } : w));
      const { x, y } = cascade(opened.current++, window.innerWidth, window.innerHeight);
      return [...ws, { path, target, x, y, z }];
    });
  };
  const close = (path: string) => setWins((ws) => ws.filter((w) => w.path !== path));
  return (
    <>
      {files.map((f) => {
        const e = lookup.data?.[f];
        const target = fileTarget(e);
        if (!target) {
          const gone = isGone(e);
          return (
            <span key={f} title={gone ? `${f} (gone)` : f} data-result-file={gone ? 'gone' : 'plain'}
              className={`${CHIP} text-muted-foreground${gone ? ' line-through opacity-70' : ''}`}>
              <FileText size={11} aria-hidden /><span className="break-all">{f}</span>
            </span>
          );
        }
        return (
          <button key={f} type="button" title={`Open ${target.title}`} data-result-file={target.kind} onClick={() => open(f, target)}
            className={`${CHIP} text-primary hover:underline text-left`}>
            <FileText size={11} aria-hidden /><span className="break-all">{f}</span>
          </button>
        );
      })}
      {wins.map((w) => {
        const t = w.target;
        const src = t.kind === 'item' ? librarySource(t.item) : projectFileSource(t.slug, t.rel);
        const subtitle = t.kind === 'item' ? [t.item.project, shortPath(t.item)].filter(Boolean).join(' · ') : `${t.slug} · ${t.rel}`;
        return (
          <FileWindow key={w.path} overDialog src={src} title={t.title} subtitle={subtitle} x={w.x} y={w.y} z={w.z}
            onFocus={() => { const z = ++zTop; setWins((ws) => ws.map((x) => (x.path === w.path ? { ...x, z } : x))); }}
            onClose={() => close(w.path)} />
        );
      })}
    </>
  );
}
