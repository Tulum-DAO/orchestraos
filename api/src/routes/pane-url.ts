/**
 * pane-url.ts — pull a sign-in URL back out of a terminal pane.
 *
 * The wall this exists for: the Antigravity CLI prints its OAuth URL into the pane
 * HARD-WRAPPED across ten lines (it wraps the string itself — `tmux capture-pane -J` does
 * not rejoin it, because these are the CLI's own line breaks, not tmux's). A person cannot
 * click it, and selecting it drags the breaks along, so what lands in the browser is a
 * broken URL. The operator hit exactly this: "nobody can click on the actual url and when
 * you attempt to copy it, there are all of these line breaks".
 *
 * A terminal is the right place for a CLI to talk. It is the wrong place for a human to
 * retrieve 570 characters by hand. So the server reads the pane and hands the UI one intact
 * string it can render as a real link.
 */
import { execFile } from 'node:child_process';
import { Router, type Request, type Response } from 'express';

/** Lines that continue a URL carry no whitespace and are not empty. */
function isContinuation(line: string): boolean {
  const t = line.trim();
  return t.length > 0 && !/\s/.test(t) && t === line.trim();
}

export function extractSignInUrl(pane: string): string | null {
  const lines = (pane || '').split('\n').map((l) => l.replace(/\s+$/, ''));
  const candidates: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const m = /https?:\/\/\S+/.exec(lines[i]);
    if (!m) continue;
    let url = m[0];
    // Only join when the match RUNS TO THE END of its line: a URL followed by prose on the
    // same line is already complete, and joining would glue the next line onto it.
    if (lines[i].endsWith(url)) {
      for (let j = i + 1; j < lines.length; j += 1) {
        if (!isContinuation(lines[j])) break;
        url += lines[j].trim();
      }
    }
    url = url.replace(/[.,;:)\]]+$/, '');        // prose punctuation is not part of the URL
    candidates.push(url);
  }
  if (candidates.length === 0) return null;
  // The sign-in URL is the interesting one; otherwise the longest, which is the one carrying
  // the query string rather than a docs link mentioned in passing.
  const signin = candidates.filter((u) => /accounts\.google\.com|oauth|auth\?|login|signin|device/i.test(u));
  const pool = signin.length > 0 ? signin : candidates;
  return pool.reduce((a, b) => (b.length > a.length ? b : a));
}

export interface PaneUrlDeps {
  capture: (session: string) => Promise<string>;
}

export function defaultPaneUrlDeps(): PaneUrlDeps {
  return {
    capture: (session) =>
      new Promise((resolve) => {
        // -J so tmux's own wrapping is joined too; -p to stdout; -S -200 to reach a URL that
        // has scrolled up a little while the CLI drew its menu.
        execFile('tmux', ['capture-pane', '-p', '-J', '-S', '-200', '-t', session],
          { maxBuffer: 4 * 1024 * 1024 },
          (err, stdout) => resolve(err ? '' : String(stdout || '')));
      }),
  };
}

export function createPaneUrlRouter(deps: PaneUrlDeps = defaultPaneUrlDeps()): Router {
  const router = Router();
  router.get('/:session/sign-in-url', async (req: Request, res: Response) => {
    const session = String(req.params.session || '').slice(0, 80);
    if (!/^[A-Za-z0-9_.-]+$/.test(session)) {
      res.status(400).json({ ok: false, error: 'bad session name' });
      return;
    }
    const pane = await deps.capture(session);
    const url = extractSignInUrl(pane);
    res.json({ ok: true, url: url || null });
  });
  return router;
}

export default createPaneUrlRouter();
