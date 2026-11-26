/**
 * The data dir for unified-approvals.task-refusal.test.ts. Imported FIRST: the router and _canonical-approvals read
 * ORCHESTRA_DIR at import, so it has to be set before they load. node --test runs each file in its own process, so
 * none of this reaches another test. A scratch gateway (http stub) and token stand in for the real ones.
 */
import { testTmpDir } from '../lib/testTmp.js';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const DATA_DIR = testTmpDir('apr-task-refusal-');
mkdirSync(join(DATA_DIR, 'state'), { recursive: true });
writeFileSync(join(DATA_DIR, 'registry.json'), JSON.stringify({ agents: {} }));
export const TOKEN = 'gateway-token-for-the-refusal-test';
writeFileSync(join(DATA_DIR, 'state', 'watch-gateway-token'), TOKEN + '\n');
process.env.ORCHESTRA_DIR = DATA_DIR;
process.env.APPROVAL_DB_PATH = join(DATA_DIR, 'state', 'tasks.db');
process.env.WATCH_GATEWAY_TOKEN_FILE = join(DATA_DIR, 'state', 'watch-gateway-token');
process.env.NTFY_TOKEN_FILE = join(DATA_DIR, 'no-ntfy-token');   // nothing here may push to a phone
process.env.HOME = join(DATA_DIR, 'home');                       // approval.py must not read the real home
mkdirSync(process.env.HOME, { recursive: true });
