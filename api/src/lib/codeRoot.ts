/**
 * codeRoot — where this install's CODE lives (the checkout), as opposed to its DATA.
 *
 * Under `orchestra up` the two differ: ORCHESTRA_DIR is the DATA dir (state/, logs/, registry.json,
 * default ~/.orchestra) and ORCHESTRA_ROOT is the checkout (orchestra_cli/settings.py). Anything the
 * repo ships (spawn-agent.sh, message_bus.py, scripts/*, prompts/*) must be resolved from HERE, never
 * from ORCHESTRA_DIR: a code path under the data dir does not exist on a real install, and the feature
 * fails (the dashboard's Spawn button ran ~/.orchestra/spawn-agent.sh).
 *
 * This file is <root>/api/src/lib/codeRoot.ts (or <root>/api/dist/lib/codeRoot.js when built), so the
 * checkout is three directories up when ORCHESTRA_ROOT is not set (npm start, npm run dev).
 */
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

export const CODE_ROOT: string =
  process.env.ORCHESTRA_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** A path inside the checkout. */
export function codePath(...parts: string[]): string {
  return join(CODE_ROOT, ...parts);
}
