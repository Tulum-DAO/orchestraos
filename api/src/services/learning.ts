/**
 * learning.ts — Bridge to the Python learning engine from Node.js API.
 * Calls log-interaction.py via execFile for zero-dependency SQLite access.
 */

import { execFile } from 'child_process';
import { loadConfig } from '../lib/config.js';
import { capabilityPath, hasCapability } from '../lib/capabilities.js';

const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
// The learning logger is optional (lib/capabilities.ts): absent => one notice, then a quiet no-op,
// instead of a '[LEARNING] Log failed' ENOENT warning on every task and agent action.
let learningAbsentNoted = false;

interface QuickLogParams {
  channel: string;
  userId: string;
  input: string;
  tool?: string;
  toolArgs?: Record<string, unknown>;
  toolResult?: string;
  response?: string;
  error?: string;
  latencyMs?: number;
  feedback?: string;
  correctionText?: string;
}

/**
 * Log a complete interaction in one call (fire-and-forget).
 * Non-blocking — errors are swallowed to avoid breaking the API.
 */
export function logInteraction(params: QuickLogParams): void {
  const data = JSON.stringify({
    input: params.input,
    tool: params.tool,
    tool_args: params.toolArgs,
    tool_result: params.toolResult,
    response: params.response,
    error: params.error,
    latency_ms: params.latencyMs,
    feedback: params.feedback,
    correction_text: params.correctionText,
  });

  if (!hasCapability('learningLog')) {
    if (!learningAbsentNoted) {
      learningAbsentNoted = true;
      console.info('[LEARNING] interaction logging is not installed (scripts/log-interaction.py); skipping');
    }
    return;
  }
  execFile('python3', [capabilityPath('learningLog'), params.channel, params.userId, 'quick', data], {
    timeout: 5000,
    cwd: ORCHESTRA,
  }, (err, stdout, stderr) => {
    if (err) {
      console.warn(`[LEARNING] Log failed: ${err.message}`);
    } else if (stdout) {
      try {
        const result = JSON.parse(stdout.trim());
        if (result.snippet_id) {
          console.log(`[LEARNING] ${result.snippet_id}: composite=${result.composite}`);
        }
      } catch {
        // ignore parse errors
      }
    }
  });
}
