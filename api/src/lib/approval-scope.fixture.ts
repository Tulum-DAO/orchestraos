/**
 * The data dir for approval-scope.test.ts. It must be imported FIRST: state-reader and
 * _canonical-approvals read ORCHESTRA_DIR once, at import, so setting it inside a test is too
 * late (the router would read the real registry and the real approvals). node --test runs each
 * test file in its own process, so this env never reaches another file.
 *
 * One fixture card: a pending card asked by gm (the operator's), in the frozen filesystem store
 * the list still reads.
 */
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const DATA_DIR = mkdtempSync(join(tmpdir(), 'apr-scope-'));
mkdirSync(join(DATA_DIR, 'state', 'approvals', 'pending'), { recursive: true });
writeFileSync(join(DATA_DIR, 'state', 'approvals', 'pending', 'apr_gm_fs.json'),
  JSON.stringify({ id: 'apr_gm_fs', agent_id: 'gm', title: 'Operator card', status: 'pending' }));
// A registry only this dir has, so a test can tell it is the one being read.
writeFileSync(join(DATA_DIR, 'registry.json'), JSON.stringify({ agents: { 'fixture-marker': { tags: [] } } }));
process.env.ORCHESTRA_DIR = DATA_DIR;
process.env.APPROVAL_DB_PATH = join(DATA_DIR, 'state', 'tasks.db');
process.env.NTFY_TOKEN_FILE = join(DATA_DIR, 'no-ntfy-token');   // nothing here may push to a phone
