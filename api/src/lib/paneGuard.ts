/**
 * Whose pane is a seat's tmux session? A switched-on headless seat's pane holds its supervisor, not its CLI; only in
 * Dev (state/headless/<seat>/dev) does the CLI's TUI run there. Keys or text typed into a supervisor's pane reach no
 * one, and Ctrl-C there stops the supervisor itself, so the routes that type into a pane check this first.
 */
import { existsSync } from 'fs';
import { join } from 'path';
import { dataDir } from './config.js';
import { isSafeAgentId } from './agentPaths.js';
import { headlessSeats } from '../services/status-stream.js';

export function headlessInDev(agentId: string): boolean {
  if (!isSafeAgentId(agentId)) return false;
  try { return existsSync(join(dataDir(), 'state', 'headless', agentId, 'dev')); } catch { return false; }
}

/** True when typing into this seat's pane would type into its supervisor (headless and not in Dev). */
export function paneHoldsSupervisor(agentId: string, headless: Set<string> = headlessSeats(),
  inDev: (id: string) => boolean = headlessInDev): boolean {
  return headless.has(agentId) && !inDev(agentId);
}

export const PANE_IS_SUPERVISOR = "This agent runs headless, so its terminal isn't its CLI. Open Dev to type into it.";
