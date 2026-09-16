/**
 * db-recovery.ts — testable open/classify/recover/halt orchestration for tasks.db
 * (DEC-1788702356376911 v2). Kept OUT of db.ts (which runs initDb() at import
 * against a fixed path) so it can be unit-tested against temp DBs with injected
 * alarms. db.ts wires this with the real backups dir + alarm callbacks.
 *
 * Contract (never wipe an existing file):
 *   OK/BENIGN     -> return the open handle (BENIGN fires an INFO alarm).
 *   INDEX         -> consistent pre-reindex backup (checkpoint+copy) THEN REINDEX
 *                    in place; re-check; serve if cleared (WARN), else HALT.
 *   FATAL/throw   -> archive the file SET by COPY (original stays, so a crash-loop
 *                    re-halts instead of wiping) and HALT — never recreate empty.
 */
import Database, { type Database as DB } from 'better-sqlite3';
import { existsSync, copyFileSync, mkdirSync, statSync } from 'fs';
import { join } from 'path';
import { classifyIntegrity, IntegrityClass } from './db-integrity.js';

export interface RecoveryOpts {
  dbPath: string;
  backupsDir: string;
  stamp: () => string;
  applyPragmas: (d: DB) => void;
  onFinding: (severity: 'INFO' | 'WARN', summary: string, findings: string[]) => void;
  onFatal: (archived: string[], reason: string) => void; // fire CRITICAL alarm
}

export class DbFatalError extends Error {}

function runIntegrity(d: DB) {
  let rows: string[];
  try {
    rows = (d.prepare('PRAGMA integrity_check').all() as Array<{ integrity_check: string }>)
      .map((r) => r.integrity_check);
  } catch {
    rows = [classifyIntegrity.THROWN];
  }
  return classifyIntegrity(rows);
}

function archiveByCopy(opts: RecoveryOpts, reason: string): never {
  const archived: string[] = [];
  mkdirSync(opts.backupsDir, { recursive: true });
  const stamp = opts.stamp();
  for (const suffix of ['', '-wal', '-shm']) {
    const src = opts.dbPath + suffix;
    if (!existsSync(src)) continue;
    const dst = join(opts.backupsDir, `tasks.db${suffix}.corrupt-${stamp}`);
    try { copyFileSync(src, dst); archived.push(dst); } catch { /* best effort */ }
  }
  opts.onFatal(archived, reason);
  throw new DbFatalError(
    `[db] FATAL integrity (${reason}); halting to preserve on-disk data — restore via runbook, do NOT delete ${opts.dbPath}`,
  );
}

export function openClassifyRecover(opts: RecoveryOpts): DB {
  // A non-sqlite/garbage file throws on first pragma; map that to FATAL-halt.
  let d: DB;
  try {
    d = new Database(opts.dbPath);
    opts.applyPragmas(d);
  } catch (e: any) {
    return archiveByCopy(opts, `open/pragma threw: ${e?.message}`);
  }

  const res = runIntegrity(d);

  if (res.cls === IntegrityClass.OK) return d;

  if (res.cls === IntegrityClass.BENIGN) {
    opts.onFinding('INFO', 'benign integrity note — serving existing DB unchanged', res.rows);
    return d;
  }

  if (res.cls === IntegrityClass.INDEX) {
    try {
      d.pragma('wal_checkpoint(TRUNCATE)');
      copyFileSync(opts.dbPath, join(opts.backupsDir, `tasks.db.pre-reindex-${opts.stamp()}`));
    } catch (e: any) {
      d.close();
      return archiveByCopy(opts, `index finding but pre-reindex backup failed: ${e?.message}`);
    }
    try {
      d.exec('REINDEX');
    } catch (e: any) {
      d.close();
      return archiveByCopy(opts, `REINDEX threw: ${e?.message}`);
    }
    const after = runIntegrity(d);
    if (after.cls === IntegrityClass.OK || after.cls === IntegrityClass.BENIGN) {
      opts.onFinding('WARN', 'index corruption REINDEXed in place; data retained', res.findings);
      return d;
    }
    d.close();
    return archiveByCopy(opts, `REINDEX did not clear (${after.cls}): ${after.findings.join('; ')}`);
  }

  // FATAL
  d.close();
  return archiveByCopy(opts, res.findings.join('; ') || 'unclassified integrity failure');
}
