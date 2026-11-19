/**
 * Refuse a browser WRITE that another site started (CSRF).
 *
 * The CORS check in server.ts only withholds Access-Control-Allow-Origin, which stops a foreign page READING a
 * reply but not SENDING the request. A "simple" cross-site POST (text/plain or a form) needs no preflight, and
 * behind a front door that signs the caller in by network identity (a Tailscale login stamped by `tailscale
 * serve`, then a vouching proxy) no cookie is involved: any page the operator opens on a tailnet device could
 * fire one at the API as the operator. The browser says where a request came from in Sec-Fetch-Site, and every
 * proxy in front of this API passes it through, so a write is refused when the browser says another site
 * (`cross-site`) or another origin on the same site (`same-site`: a different port of the same host) started it.
 *
 * A browser that sends no Sec-Fetch-Site (Safari before 16.4) still sends Origin on a cross-site POST: then the
 * write passes only if that Origin is one the CORS layer already accepts (same-origin or allowlisted; the caller
 * passes that decision in, so the two can never disagree).
 *
 * Reads (GET/HEAD/OPTIONS) are untouched: a foreign page cannot read them anyway. `same-origin`, `none` (typed
 * or bookmarked by the user) and no header at all (a non-browser caller: the CLI, the iOS app, a script) pass.
 */
import type { NextFunction, Request, Response } from 'express';

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);
const FOREIGN = new Set(['cross-site', 'same-site']);

export function isCrossSiteWrite(method: string | undefined, secFetchSite: string | string[] | undefined): boolean {
  if (SAFE.has(String(method || '').toUpperCase())) return false;
  const v = Array.isArray(secFetchSite) ? secFetchSite.join(',') : String(secFetchSite || '');
  // A duplicated header is suspicious: any foreign value in it refuses.
  return v.split(',').some((p) => FOREIGN.has(p.trim().toLowerCase()));
}

/** A write from a browser without Sec-Fetch-Site whose Origin the CORS layer would not accept. */
export function isForeignOriginWrite(method: string | undefined, secFetchSite: string | string[] | undefined,
  origin: string | undefined, originAllowed: (origin: string) => boolean): boolean {
  if (SAFE.has(String(method || '').toUpperCase())) return false;
  if (secFetchSite !== undefined && String(secFetchSite) !== '') return false;   // judged by isCrossSiteWrite
  if (!origin) return false;                                                     // not a browser
  return !originAllowed(origin);
}

/** Middleware. originAllowed(origin, req) must be the same decision the CORS layer makes. */
export function crossSiteWriteGuard(originAllowed: (origin: string, req: Request) => boolean) {
  return function refuseCrossSiteWrites(req: Request, res: Response, next: NextFunction) {
    const site = req.headers['sec-fetch-site'];
    const origin = typeof req.headers.origin === 'string' ? req.headers.origin : undefined;
    if (isCrossSiteWrite(req.method, site) || isForeignOriginWrite(req.method, site, origin, (o) => originAllowed(o, req))) {
      res.status(403).json({ ok: false, error: 'cross-site write refused' });
      return;
    }
    next();
  };
}
