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
 * Reads (GET/HEAD/OPTIONS) are untouched: a foreign page cannot read them anyway. `same-origin`, `none` (typed
 * or bookmarked by the user) and an absent header (a non-browser caller: the CLI, the iOS app, a script) pass.
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

export function refuseCrossSiteWrites(req: Request, res: Response, next: NextFunction) {
  if (!isCrossSiteWrite(req.method, req.headers['sec-fetch-site'])) return next();
  res.status(403).json({ ok: false, error: 'cross-site write refused' });
}
