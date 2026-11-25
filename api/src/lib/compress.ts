/**
 * Response compression for the JSON the dashboard reads per seat.
 *
 * A seat switch fires one transcript request per visible seat; the biggest first pages were 1 MB
 * of JSON on the wire with no Content-Encoding at all. gzip cuts that 5-10x. The middleware is
 * mounted once in server.ts, in front of every route, with ONE filter (compressibleResponse):
 *
 *  - an event stream only when its route asked for it (gzipEventStream below) and so flushes the
 *    compressor after every event it writes: the transcript streams do (their 40-item snapshot on
 *    connect is a first page in all but name); every other SSE route (/events, /stream, any
 *    text/event-stream) writes a line at a time and a compressor would hold those lines in its
 *    buffer, so it is left alone;
 *  - never the media route: it serves image and video bytes by id, with byte ranges, already
 *    encoded by their format;
 *  - never a response that already names a Content-Encoding (the transcript route sends its
 *    cached first page gzipped itself, services/transcript-first-page-cache.ts);
 *  - otherwise what the `compression` package would compress anyway (a compressible type,
 *    at least `threshold` bytes).
 */
import compression from 'compression';
import type { Request, Response, RequestHandler } from 'express';

/** Below this many bytes the gzip header costs more than it saves. */
export const COMPRESS_THRESHOLD = 1024;

const STREAM_PATH = /\/(stream|events)\/?$/;

/** A route that writes an event stream calls this BEFORE its writeHead to have the stream gzipped.
 *  It then owes a `res.flush()` after every write (sseWrite does both), or its events sit in the
 *  compressor until the next one. Nothing happens when the middleware is not mounted. */
export function gzipEventStream(res: Response): void {
  res.locals.gzipEventStream = true;
}

/** One SSE frame out, through the compressor if there is one: write, then flush, so the client
 *  gets the event now and not with the next one. */
export function sseWrite(res: Response, frame: string): void {
  res.write(frame);
  (res as Response & { flush?: () => void }).flush?.();
}

export function compressibleResponse(req: Request, res: Response): boolean {
  // originalUrl, not req.path: inside a mounted router (app.use('/api/media', ...)) req.path has had
  // the mount prefix stripped, so '/api/media/abc' reads as '/abc' there.
  const path = String(req.originalUrl || req.url || req.path || '').split('?')[0];
  const type = String(res.getHeader('content-type') || '').toLowerCase();
  if (STREAM_PATH.test(path) || type.startsWith('text/event-stream')) {
    if (res.locals?.gzipEventStream !== true) return false;
  }
  if (path.startsWith('/api/media/') || path === '/api/media') return false;
  const encoding = String(res.getHeader('content-encoding') || '').toLowerCase();
  if (encoding && encoding !== 'identity') return false;
  return compression.filter(req, res);
}

export function apiCompression(): RequestHandler {
  return compression({ threshold: COMPRESS_THRESHOLD, filter: compressibleResponse });
}
