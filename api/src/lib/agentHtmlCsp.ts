/**
 * Content-Security-Policy for agent-written HTML and an inert baseline for every other API response.
 *
 * Questionnaire pages (routes/questionnaires.ts /:id/html) and learning one-pagers
 * (routes/learning.ts /proposals/:id/onepager) are HTML an agent wrote, served same-origin and framed
 * by the dashboard. Without a policy a <script> in that HTML runs as the dashboard's origin with the
 * operator's session: it can call every /api route the operator can (Nod audit, 2026-11-25; nothing
 * exploited). The policy below lets the page keep its inline script and style (the pages are
 * self-contained) and takes the dangerous parts away: no fetch or XHR (connect-src), no form posts
 * (form-action), and `sandbox` makes the document an opaque origin even when a frame forgets its
 * sandbox attribute. Presentation stays: images over https, Google fonts (most pages @import them).
 * A page that renders with system fonts and no pictures is a worse product than a font host seeing
 * the operator's address (gm ruling, 2026-11-25). The dashboard saves answers through its own frame (sandbox +
 * postMessage); a page opened directly can be read but cannot submit.
 *
 * The baseline rides every API response: JSON is not a document, so `default-src 'none'` costs
 * nothing there, and `frame-ancestors 'self'` keeps another site from framing an API page. A route
 * that serves a document sets its own header after the baseline (setHeader replaces).
 */
import type { Response, Request, NextFunction } from 'express';

export const AGENT_HTML_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline' https://fonts.googleapis.com",
  'img-src https: data:',
  'font-src https://fonts.gstatic.com',
  "connect-src 'none'",
  "form-action 'none'",
  "frame-ancestors 'self'",
  'sandbox allow-scripts allow-forms',
].join('; ');

export const API_BASELINE_CSP = "default-src 'none'; frame-ancestors 'self'";

/** Headers for a response whose body is agent-written HTML. Call before res.send. */
export function setAgentHtmlHeaders(res: Response): void {
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Content-Security-Policy', AGENT_HTML_CSP);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'no-referrer');
}

/** Express middleware: the inert baseline on every response. Mount before the routes. */
export function baselineHeaders(_req: Request, res: Response, next: NextFunction): void {
  res.setHeader('Content-Security-Policy', API_BASELINE_CSP);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  next();
}
