import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ExternalLink, Eye, File, FileImage, HelpCircle, Link2 } from 'lucide-react';
import { fetchAgents } from '../../lib/api';
import { cascade } from '../../lib/projectFiles';
import { fmtSize } from '../../lib/fileWindow';
import {
  LIBRARY_GROUPS, GROUP_LABEL, CHIP_LABEL, BUCKET_LABEL, PAGE, PREVIEW, groupQuery, fetchLibraryItems, fetchUnclear, mergePage,
  byBucket, whenLabel, itemTitle, itemSubtitle, baseName, shortPath, hostOf, isWebLink, unclearLine, librarySource, rosterOf,
  type LibraryGroup, type LibraryItem,
} from '../../lib/drawerLibrary';
import FileWindow from '../kit/FileWindow';
import { ARTURO_ID } from '../avatar/agentState';
import './drawerLibrary.css';

/**
 * The drawer's Library: what this agent made, from the library index. Files, Media and Links, then what it only
 * showed or named (Mentioned) and files the index could not settle between agents ("Unclear who made it", with
 * the candidates), each kept apart from what it made. The All view shows the newest few of each; a chip (or a
 * group's Show more) shows one group under Today / This week / Older, 50 at a time. A file opens in a floating
 * window over the page; a link opens in a new tab.
 */

type Chip = 'all' | LibraryGroup;
interface Win { item: LibraryItem; x: number; y: number; z: number }
// Above the drawer (71) and the look picker (80) that can open from it.
const WIN_Z = 90;

export function DrawerLibrary({ agentId, name }: { agentId: string; name: string }) {
  const [chip, setChip] = useState<Chip>('all');
  const [showRetired, setShowRetired] = useState(false);
  const [wins, setWins] = useState<Win[]>([]);
  const zTop = useRef(WIN_Z);
  const [now] = useState(() => Date.now());

  const roster = useQuery({
    queryKey: ['library-roster'], queryFn: fetchAgents, staleTime: 30_000,
    select: (a: unknown) => rosterOf(a, [ARTURO_ID]),
  });
  const current = roster.data ?? null;

  // The All view: the newest few of each group, one more than shown to know whether there are more.
  const previews = useQuery({
    queryKey: ['library-preview', agentId],
    queryFn: async () => {
      const groups = LIBRARY_GROUPS.filter((g) => g !== 'unclear') as Exclude<LibraryGroup, 'unclear'>[];
      const lists = await Promise.all(groups.map((g) => fetchLibraryItems(groupQuery(g, agentId, 0, PREVIEW + 1))));
      return Object.fromEntries(groups.map((g, i) => [g, lists[i]])) as Record<Exclude<LibraryGroup, 'unclear'>, LibraryItem[]>;
    },
  });
  const unclear = useQuery({ queryKey: ['library-unclear', agentId, showRetired], queryFn: () => fetchUnclear(agentId, showRetired) });

  // One group, 50 at a time from the index (unclear files are already all here; they page on this side).
  const paged = useInfiniteQuery({
    queryKey: ['library-group', agentId, chip],
    enabled: chip !== 'all' && chip !== 'unclear',
    initialPageParam: 0,
    queryFn: ({ pageParam }) => fetchLibraryItems(groupQuery(chip as Exclude<LibraryGroup, 'unclear'>, agentId, pageParam, PAGE)),
    getNextPageParam: (last, all) => (last.length >= PAGE ? all.length * PAGE : undefined),
  });
  const [unclearShown, setUnclearShown] = useState(PAGE);

  const counts = useMemo(() => {
    const c: Partial<Record<LibraryGroup, number>> = {};
    for (const g of LIBRARY_GROUPS) c[g] = g === 'unclear' ? unclear.data?.length : previews.data?.[g as Exclude<LibraryGroup, 'unclear'>]?.length;
    return c;
  }, [previews.data, unclear.data]);
  const loading = previews.isLoading || unclear.isLoading;
  const chips = LIBRARY_GROUPS.filter((g) => g === chip || (counts[g] ?? 0) > 0);

  const pick = (c: Chip) => { setChip(c); setUnclearShown(PAGE); };

  const open = (item: LibraryItem) => {
    const z = ++zTop.current;
    setWins((ws) => {
      if (ws.some((w) => w.item.id === item.id)) return ws.map((w) => (w.item.id === item.id ? { ...w, z } : w));
      const { x, y } = cascade(ws.length, window.innerWidth, window.innerHeight);
      return [...ws, { item, x, y, z }];
    });
  };
  const raise = (id: string) => { const z = ++zTop.current; setWins((ws) => ws.map((w) => (w.item.id === id ? { ...w, z } : w))); };
  const close = (id: string) => setWins((ws) => ws.filter((w) => w.item.id !== id));

  // While a window is open, Esc closes the one in front (not the drawer), and Tab inside a window stays its own
  // rather than being pulled back into the drawer. Capture runs before the drawer's listener.
  useEffect(() => {
    if (!wins.length) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation(); e.preventDefault();
        setWins((ws) => (ws.length ? ws.filter((w) => w !== ws.reduce((a, b) => (b.z > a.z ? b : a))) : ws));
      } else if (e.key === 'Tab' && (document.activeElement as HTMLElement | null)?.closest('[data-file-window]')) {
        e.stopImmediatePropagation();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [wins.length]);

  const row = (it: LibraryItem, g: LibraryGroup) => <ItemRow key={it.id} it={it} group={g} agentId={agentId} current={current}
                                                            showRetired={showRetired} now={now} onOpen={open} />;

  const groupBody = (g: LibraryGroup) => {
    if (g === 'unclear') {
      const all = unclear.data ?? [];
      return { items: all.slice(0, unclearShown), more: all.length > unclearShown, next: () => setUnclearShown((n) => n + PAGE), busy: false };
    }
    const items = (paged.data?.pages ?? []).reduce<{ items: LibraryItem[]; more: boolean }>((acc, p) => mergePage(acc.items, p), { items: [], more: false }).items;
    return { items, more: !!paged.hasNextPage, next: () => void paged.fetchNextPage(), busy: paged.isFetchingNextPage };
  };

  const retiredSwitch = (
    <label className="dl-retired">
      <input type="checkbox" checked={showRetired} onChange={(e) => setShowRetired(e.target.checked)} data-library-retired />
      <span>Show retired agents</span>
    </label>
  );

  let body: ReactNode;
  if (previews.isError) {
    body = <p className="arturo-panel-empty">The library can't be read right now.</p>;
  } else if (loading) {
    body = <p className="arturo-panel-empty">Loading…</p>;
  } else if (chip === 'all') {
    const any = LIBRARY_GROUPS.some((g) => (counts[g] ?? 0) > 0);
    body = !any ? (
      <div className="arturo-panel-empty" data-library-empty>
        <p>Nothing yet. Files, pictures and links {name} makes will be listed here.</p>
        <p className="arturo-panel-note">A project's files are under <a href="/projects">Projects</a>.</p>
      </div>
    ) : LIBRARY_GROUPS.filter((g) => (counts[g] ?? 0) > 0).map((g) => {
      const list = g === 'unclear' ? unclear.data ?? [] : previews.data?.[g as Exclude<LibraryGroup, 'unclear'>] ?? [];
      return (
        <section key={g} className="dl-group" data-library-group={g}>
          <h3 className="arturo-panel-section">{GROUP_LABEL[g]}</h3>
          {g === 'mentioned' && <p className="dl-hint">Shown or named, not made by {name}.</p>}
          {g === 'unclear' && <p className="dl-hint">Changed while more than one agent was working there.</p>}
          <div className="dl-list">{list.slice(0, PREVIEW).map((it) => row(it, g))}</div>
          {list.length > PREVIEW && <button type="button" className="dl-more" onClick={() => pick(g)}>Show more</button>}
          {g === 'unclear' && retiredSwitch}
        </section>
      );
    });
  } else {
    const { items, more, next, busy } = groupBody(chip);
    const waiting = chip !== 'unclear' && paged.isLoading;
    body = (
      <section className="dl-group" data-library-group={chip}>
        {chip === 'mentioned' && <p className="dl-hint">Shown or named, not made by {name}.</p>}
        {chip === 'unclear' && retiredSwitch}
        {waiting ? <p className="arturo-panel-empty">Loading…</p>
          : items.length === 0 ? <p className="arturo-panel-empty">None.</p>
          : byBucket(items, now).map(([b, list]) => (
            <div key={b} data-library-bucket={b}>
              <h4 className="dl-bucket">{BUCKET_LABEL[b]}</h4>
              <div className="dl-list">{list.map((it) => row(it, chip))}</div>
            </div>
          ))}
        {more && <button type="button" className="dl-more" disabled={busy} onClick={next}>{busy ? 'Loading…' : 'Show more'}</button>}
      </section>
    );
  }

  return (
    <div className="dl" data-drawer-library>
      {!loading && chips.length > 0 && (
        <div className="dl-chips" role="group" aria-label="Show">
          {(['all', ...chips] as Chip[]).map((c) => (
            <button key={c} type="button" className={`dl-chip${chip === c ? ' is-on' : ''}`} aria-pressed={chip === c}
                    data-library-chip={c} onClick={() => pick(c)}>
              {c === 'all' ? 'All' : CHIP_LABEL[c]}
            </button>
          ))}
        </div>
      )}
      {body}
      {wins.map((w) => (
        <FileWindow key={w.item.id} overDialog src={librarySource(w.item)} title={itemTitle(w.item)}
                    subtitle={[w.item.project, shortPath(w.item)].filter(Boolean).join(' · ')}
                    x={w.x} y={w.y} z={w.z} onFocus={() => raise(w.item.id)} onClose={() => close(w.item.id)} />
      ))}
    </div>
  );
}

const ICON = { files: File, media: FileImage, links: Link2, mentioned: Eye, unclear: HelpCircle } as const;

function ItemRow({ it, group, agentId, current, showRetired, now, onOpen }: {
  it: LibraryItem; group: LibraryGroup; agentId: string; current: Set<string> | null; showRetired: boolean; now: number;
  onOpen: (it: LibraryItem) => void;
}) {
  const Icon = it.kind === 'media' && group !== 'links' ? FileImage : ICON[group];
  const title = itemTitle(it);
  // Under the title: the command's words when it came with some, else where it is (its name, when the title is a
  // description of its content) and its size.
  const sp = shortPath(it);
  const name = baseName(sp);
  const where = title === name ? sp.slice(0, -name.length).replace(/\/$/, '') : name;
  const detail = group === 'links' ? hostOf(it.url)
    : group === 'unclear' ? unclearLine(it, agentId, current, showRetired)
    : itemSubtitle(it) || [where, it.size ? fmtSize(it.size) : ''].filter(Boolean).join(' · ');
  const inner = (
    <>
      <Icon size={16} className="dl-icon" aria-hidden />
      <span className="dl-main">
        <span className="dl-title">{title}</span>
        {detail && <span className="dl-detail">{detail}</span>}
      </span>
      <span className="dl-when">{whenLabel(it.at, now)}</span>
      {group === 'links' && <ExternalLink size={13} className="dl-icon" aria-hidden />}
    </>
  );
  if (it.kind === 'link') {
    return isWebLink(it.url)
      ? <a className="dl-row" href={it.url!} target="_blank" rel="noopener noreferrer" title={it.url!} data-library-item={it.id}>{inner}</a>
      : <div className="dl-row" data-library-item={it.id}>{inner}</div>;
  }
  return (
    <button type="button" className="dl-row" title={it.path ?? undefined} onClick={() => onOpen(it)} data-library-item={it.id}>
      {inner}
    </button>
  );
}
