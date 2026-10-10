/**
 * capabilities: optional features whose backing script does not ship in every install.
 *
 * Data-dir sweep S4 (gm ruling): a route whose script is absent answers an honest
 * 501 "not available in this install" (never a 500 with an ENOENT stack), and the
 * dashboard reads GET /api/capabilities to hide or grey the entry, so nobody clicks
 * into an error. Presence is checked per request: dropping the script in later turns
 * the feature on without a restart.
 */
import { existsSync } from 'fs';
import { join } from 'path';
import type { Response } from 'express';
import { CODE_ROOT } from './codeRoot.js';
import { loadConfig } from './config.js';

/** Where each optional script lives. `code` = the checkout; `data` = the install's data dir. */
const OPTIONAL = {
  learningLog: { root: 'code', parts: ['scripts', 'log-interaction.py'] },       // services/learning.ts
  inspectScript: { root: 'data', parts: ['skills', 'inspect-element.js'] },      // GET /api/inspect-feedback/script.js
} as const;

export type Capability = keyof typeof OPTIONAL;
export const CAPABILITIES = Object.keys(OPTIONAL) as Capability[];

/** Read per call (not at import): tests and a later ORCHESTRA_ROOT / ORCHESTRA_DIR are honoured. */
export function capabilityPath(c: Capability): string {
  const o = OPTIONAL[c];
  const base = o.root === 'code'
    ? (process.env.ORCHESTRA_ROOT || CODE_ROOT)
    : (process.env.ORCHESTRA_DIR || loadConfig().dataDir);
  return join(base, ...o.parts);
}

export function hasCapability(c: Capability): boolean {
  return existsSync(capabilityPath(c));
}

export function capabilities(): Record<Capability, boolean> {
  return Object.fromEntries(CAPABILITIES.map((c) => [c, hasCapability(c)])) as Record<Capability, boolean>;
}

/** The one honest answer for a feature this install does not have. */
export function notAvailable(res: Response, c: Capability): void {
  res.status(501).json({ error: 'not available in this install', capability: c });
}
