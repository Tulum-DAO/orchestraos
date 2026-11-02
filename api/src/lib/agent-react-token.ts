// The agent react token: the one secret an agent shows to react as itself (lib/reactor.ts).
//
// <data dir>/state/agent-react-token, mode 0600, created at API boot when missing (server.ts), the same pattern as
// watch-gateway-token and terminal-relay-secret. `orchestra react` (build step 3) reads it and sends it as
// X-Orchestra-Agent-Token next to X-Orchestra-Agent. One shared token for every agent (spec "Risks": any seat can
// react as another agent; per-seat tokens come with group chats). ORCHESTRA_AGENT_REACT_TOKEN_FILE overrides the path
// (tests), like TERMINAL_RELAY_SECRET_FILE. Read fresh on every check, so a token written after boot is honoured.
import { chmodSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { randomBytes, timingSafeEqual } from 'crypto';
import { dataDir } from './config.js';

export function agentReactTokenFile(): string {
  return process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE || join(dataDir(), 'state', 'agent-react-token');
}

/** The token, or '' when the file is absent, unreadable or empty. */
export function readAgentReactToken(file: string = agentReactTokenFile()): string {
  try { return readFileSync(file, 'utf-8').trim(); } catch { return ''; }
}

/** Create the token if there is none (0600), tighten a too-open file, and return the value. Never rotates one. */
export function ensureAgentReactToken(file: string = agentReactTokenFile()): string {
  const have = readAgentReactToken(file);
  if (have) {
    try { if ((statSync(file).mode & 0o777) !== 0o600) chmodSync(file, 0o600); } catch { /* best effort */ }
    return have;
  }
  mkdirSync(dirname(file), { recursive: true });
  const token = randomBytes(32).toString('hex');
  try {
    writeFileSync(file, token + '\n', { mode: 0o600, flag: 'wx' });
  } catch (e: any) {
    if (e?.code !== 'EEXIST') throw e;
    const raced = readAgentReactToken(file);
    if (raced) return raced;                                   // another process wrote it first
    writeFileSync(file, token + '\n', { mode: 0o600 });        // an empty file: replace it
  }
  chmodSync(file, 0o600);
  return token;
}

/** Constant-time check of a presented token. False for '', a missing file, or any other value. */
export function agentTokenMatches(value: string, file: string = agentReactTokenFile()): boolean {
  const expected = readAgentReactToken(file);
  if (!value || !expected) return false;
  const got = Buffer.from(value);
  const want = Buffer.from(expected);
  if (got.length !== want.length) return false;   // byte lengths: a non-ASCII header can differ from its string length
  return timingSafeEqual(got, want);
}
