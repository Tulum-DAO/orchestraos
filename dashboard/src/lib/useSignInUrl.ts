/**
 * useSignInUrl — poll a terminal pane for the sign-in URL a CLI printed into it.
 *
 * A CLI prints its OAuth URL hard-wrapped across many lines (its own breaks, not tmux's, so
 * `capture-pane -J` does not rejoin them). Nobody can click that, and selecting it drags the
 * breaks and leading spaces along. The server rejoins it
 * (GET /api/agents/:session/sign-in-url); this polls while the pane is open, so the moment a
 * URL appears there is one intact string to render as a link.
 */
import { useEffect, useState } from 'react';

export function useSignInUrl(session: string | null | undefined, everyMs = 2500): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    if (!session) return () => { stop = true; };
    const tick = async () => {
      try {
        const r = await fetch(`/api/agents/${encodeURIComponent(session)}/sign-in-url`);
        const j = await r.json().catch(() => ({}));
        if (!stop && j?.url) setUrl(j.url as string);
      } catch { /* the pane may be unreadable for a moment; keep polling */ }
    };
    void tick();
    const id = setInterval(tick, everyMs);
    return () => { stop = true; clearInterval(id); };
  }, [session, everyMs]);
  return session ? url : null;
}
