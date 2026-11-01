/**
 * useArturoThreadPages — scrolling up through a long Arturo thread, on his page and in the floating pane, the way an
 * agent's chat does (lib/threadKit/useOlderPages.ts): the view's own turns are the live window (the newest stored
 * page as loaded, plus whatever this view has added since: a sent message, a streamed reply, a live call), and older
 * pages are read above it as the reader nears the top. Stored turns are matched by their seq, so a page that meets
 * the window, or a window re-read after a job posted into the thread, never draws a turn twice.
 *
 * `hydrate` turns a page of stored turns into the view's own turn shape (each view keeps its own fields).
 */
import { useCallback, useMemo } from 'react';
import { loadThreadPage, type ThreadTurn } from './arturoThreads';
import { firstSeqCursor, joinBySeq } from './threadKit/paging';
import { useOlderPages } from './threadKit/useOlderPages';

const seqOf = (t: { seq?: number }) => t.seq;

export function useArturoThreadPages<T extends { seq?: number }>(turns: T[], conversationId: string,
  hydrate: (turns: ThreadTurn[]) => T[], enabled: boolean) {
  const load = useCallback(async (before: string) => {
    const page = await loadThreadPage(conversationId, Number(before));
    if (!page) throw new Error('older turns unavailable');
    return { items: hydrate(page.thread?.turns ?? []), hasOlder: page.hasOlder, next: page.next === null ? null : String(page.next) };
  }, [conversationId, hydrate]);
  const join = useCallback((list: T[], live: T[]) => joinBySeq(list, live, seqOf), []);
  const firstCursor = useMemo(() => firstSeqCursor<T>(seqOf), []);
  // A turn not stored yet has no seq; joinBySeq never keeps one from an earlier copy of the window, so its key here
  // only has to differ from every stored turn's.
  const keyOf = useCallback((t: T) => (t.seq !== undefined ? `s${t.seq}` : 'local'), []);
  return useOlderPages<T>({ live: turns, keyOf, join, firstCursor, load, resetKey: conversationId, enabled });
}
