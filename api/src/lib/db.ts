/**
 * db.ts — Single shared better-sqlite3 handle for tasks.db.
 *
 * Replaces the per-call python sqlite3 subprocess pattern in the API routes.
 * Holding one long-lived connection in-process makes the Node API a true
 * single writer and prevents the "malformed" corruption we kept hitting when
 * multiple subprocesses raced on the same file.
 *
 * On first import: opens state/tasks.db, runs integrity_check, and if the
 * file is corrupt it archives it (plus -wal/-shm siblings) under
 * state/backups/ before recreating the file from the embedded schema.
 */

import Database, { type Database as DB } from 'better-sqlite3';
import { existsSync, mkdirSync, statSync, writeFileSync } from 'fs';
import { spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { openClassifyRecover as recoverDb, type RecoveryOpts } from './db-recovery.js';
import { loadConfig } from './config.js';

const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const DB_PATH = join(ORCHESTRA, 'state', 'tasks.db');
const BACKUPS_DIR = join(ORCHESTRA, 'state', 'backups');
const ALERTS_DIR = join(ORCHESTRA, 'state', 'ALERTS');

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS tasks (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  title           TEXT NOT NULL,
  description     TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',
  priority        TEXT DEFAULT 'medium',
  project_id      TEXT,
  phase_id        TEXT,
  parent_id       TEXT REFERENCES tasks(id),
  north_star_id   TEXT,
  cohort_id       TEXT,
  assigned_to     TEXT,
  created_by      TEXT NOT NULL,
  source          TEXT DEFAULT 'dashboard',
  routed_to       TEXT,
  agents_spawned  TEXT,
  client          TEXT,
  tags            TEXT,
  due_date        TEXT,
  blocked_by      TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  started_at      TEXT,
  completed_at    TEXT,
  time_spent_ms   INTEGER DEFAULT 0,
  tokens_used     INTEGER DEFAULT 0,
  deployment_state TEXT DEFAULT 'dev',
  message_id      TEXT,
  conversation_id TEXT,
  roadmap_phase_id TEXT,
  pipeline_id     TEXT,
  stage           TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON tasks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(tenant_id, project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_cohort ON tasks(cohort_id);

CREATE TABLE IF NOT EXISTS task_activity (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id         TEXT NOT NULL,
  actor           TEXT NOT NULL,
  action          TEXT NOT NULL,
  from_value      TEXT,
  to_value        TEXT,
  comment         TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_task ON task_activity(task_id);

CREATE TABLE IF NOT EXISTS task_templates (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  name            TEXT NOT NULL,
  description     TEXT,
  tasks           TEXT NOT NULL,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_templates_tenant ON task_templates(tenant_id);

CREATE TABLE IF NOT EXISTS north_stars (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  scope           TEXT NOT NULL,
  project_id      TEXT,
  client_id       TEXT,
  objective       TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',
  priority        TEXT DEFAULT 'P1',
  target_date     TEXT,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  last_progress   TEXT,
  stale_notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ns_tenant ON north_stars(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ns_project ON north_stars(tenant_id, project_id);

CREATE TABLE IF NOT EXISTS key_results (
  id              TEXT PRIMARY KEY,
  north_star_id   TEXT NOT NULL REFERENCES north_stars(id) ON DELETE CASCADE,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  description     TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  measured_at     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_kr_northstar ON key_results(north_star_id);

CREATE TABLE IF NOT EXISTS people (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  name            TEXT NOT NULL,
  aliases         TEXT,
  title           TEXT,
  company         TEXT,
  email           TEXT,
  phone           TEXT,
  linkedin        TEXT,
  facebook        TEXT,
  instagram       TEXT,
  website         TEXT,
  relationship    TEXT,
  source          TEXT,
  context         TEXT,
  goals           TEXT,
  beliefs         TEXT,
  environment     TEXT,
  situation       TEXT,
  pipeline_id     TEXT,
  stage_id        TEXT,
  company_slugs   TEXT,                     -- CRM P0 (§4.1): resolved client slug(s), JSON array; UI shows [0] as primary
  resolved        INTEGER NOT NULL DEFAULT 1, -- CRM P0: 0 => lead row ('unlinked/needs review'), never dropped
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_people_tenant ON people(tenant_id);
CREATE INDEX IF NOT EXISTS idx_people_pipeline ON people(pipeline_id, stage_id);

CREATE TABLE IF NOT EXISTS person_projects (
  person_id       TEXT NOT NULL,
  project_id      TEXT NOT NULL,
  role            TEXT,
  PRIMARY KEY (person_id, project_id)
);

CREATE TABLE IF NOT EXISTS person_activity (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id       TEXT NOT NULL,
  type            TEXT NOT NULL,
  summary         TEXT,
  source          TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_person_activity ON person_activity(person_id, created_at);

CREATE TABLE IF NOT EXISTS person_connections (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  person_a        TEXT NOT NULL,
  person_b        TEXT NOT NULL,
  relationship    TEXT,
  context         TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_person_connections_a ON person_connections(person_a);
CREATE INDEX IF NOT EXISTS idx_person_connections_b ON person_connections(person_b);

CREATE TABLE IF NOT EXISTS pipelines (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  name            TEXT NOT NULL,
  color           TEXT,
  sort_order      INTEGER DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
  id              TEXT PRIMARY KEY,
  pipeline_id     TEXT NOT NULL,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  name            TEXT NOT NULL,
  sort_order      INTEGER DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stages_pipeline ON pipeline_stages(pipeline_id);

CREATE TABLE IF NOT EXISTS project_blockers (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  project_id      TEXT NOT NULL,
  description     TEXT,
  type            TEXT,
  linked_id       TEXT,
  status          TEXT NOT NULL DEFAULT 'active',
  created_by      TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_blockers_project ON project_blockers(project_id, status);

CREATE TABLE IF NOT EXISTS agent_work_state (
  agent_id         TEXT PRIMARY KEY,
  tenant_id        TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  generation       INTEGER NOT NULL DEFAULT 1,
  original_intent  TEXT,
  current_task     TEXT,
  progress         TEXT,
  decisions        TEXT,
  key_context      TEXT,
  user_tone        TEXT,
  next_action      TEXT,
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_conversations (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id         TEXT NOT NULL,
  generation       INTEGER NOT NULL DEFAULT 1,
  role             TEXT NOT NULL,
  content          TEXT,
  metadata         TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_conv ON agent_conversations(agent_id, generation, created_at);

CREATE TABLE IF NOT EXISTS agent_conversation_summaries (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id         TEXT NOT NULL,
  generation       INTEGER NOT NULL,
  summary          TEXT,
  decisions        TEXT,
  files_changed    TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_conv_summaries ON agent_conversation_summaries(agent_id, generation);

CREATE TABLE IF NOT EXISTS messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT,
  task_id         TEXT,
  parent_id       TEXT,
  type            TEXT NOT NULL,
  from_agent      TEXT NOT NULL,
  to_agent        TEXT NOT NULL,
  subject         TEXT,
  body            TEXT,
  priority        TEXT NOT NULL DEFAULT 'medium',
  source          TEXT DEFAULT 'system',
  status          TEXT NOT NULL DEFAULT 'pending',
  retry_count     INTEGER NOT NULL DEFAULT 0,
  max_retries     INTEGER NOT NULL DEFAULT 5,
  metadata        TEXT,
  depends_on      TEXT,
  gather_mode     TEXT DEFAULT 'gather_all',
  tenant_id       TEXT DEFAULT '${loadConfig().operatorId}',
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  attempted_at    TEXT,
  delivered_at    TEXT,
  acknowledged_at TEXT,
  archived_at     TEXT,
  error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_inbox ON messages(to_agent, status, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type, status);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS conversations (
  id              TEXT PRIMARY KEY,
  subject         TEXT,
  participants    TEXT,
  task_id         TEXT,
  mode            TEXT DEFAULT 'fire_and_forget',
  max_iterations  INTEGER DEFAULT 1,
  iteration_count INTEGER DEFAULT 0,
  completion_condition TEXT,
  status          TEXT DEFAULT 'open',
  tenant_id       TEXT DEFAULT '${loadConfig().operatorId}',
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roadmaps (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  project         TEXT NOT NULL,
  pm_agent        TEXT NOT NULL,
  title           TEXT,
  vision          TEXT,
  status          TEXT DEFAULT 'draft',
  current_phase   INTEGER DEFAULT 0,
  created_from    TEXT,
  approved_at     TEXT,
  created_at      TEXT,
  updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS roadmap_phases (
  id              TEXT PRIMARY KEY,
  roadmap_id      TEXT NOT NULL,
  phase_number    INTEGER NOT NULL,
  title           TEXT,
  description     TEXT,
  status          TEXT DEFAULT 'pending',
  milestone       TEXT,
  started_at      TEXT,
  completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS roadmap_tasks (
  id                  TEXT PRIMARY KEY,
  phase_id            TEXT NOT NULL,
  task_id             TEXT,
  agent_id            TEXT,
  dependency_tasks    TEXT,
  status              TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS agent_hierarchy (
  agent_id        TEXT NOT NULL,
  tenant_id       TEXT NOT NULL DEFAULT '${loadConfig().operatorId}',
  parent_id       TEXT,
  project         TEXT,
  role            TEXT,
  always_on       BOOLEAN DEFAULT 0,
  spawned_at      TEXT,
  stopped_at      TEXT,
  status          TEXT DEFAULT 'running',
  generation      INTEGER DEFAULT 1,
  task_id         TEXT,
  conversation_id TEXT,
  last_active     TEXT,
  metadata        TEXT,
  PRIMARY KEY (agent_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_hierarchy_parent ON agent_hierarchy(parent_id);
CREATE INDEX IF NOT EXISTS idx_hierarchy_status ON agent_hierarchy(status);
`;

function tsStamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}_${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
}

type ArchivedFile = { src: string; dst: string; sizeBytes: number; mtime: string };

/**
 * Alert-on-recreate (commission #3a, 2026-08-23). The silent archive+recreate
 * path wiped ~9,200 messages on 08-23 with ZERO notification — the wipe hid for
 * hours. This makes it LOUD via two independent channels so it can never be
 * silent again: a durable alert artifact under state/ALERTS/ (impossible to
 * miss, survives, greppable) and a best-effort Telegram through the shared,
 * delivery-verifying scripts/tg-notify.sh (keeps creds out of this Node module).
 * EVERYTHING is wrapped so a failed alarm can NEVER break API startup — a broken
 * alert must not brick the database it is trying to protect.
 */
function alertDbRecreate(archived: ArchivedFile[], reason: string): void {
  try {
    // best-effort: read how much was in the archived main file. It is corrupt
    // (integrity_check just failed), so queries may throw — file size + mtime
    // are the always-available loss signal (a healthy tasks.db is ~19MB; the
    // 08-23 corrupt image was 2.3MB of April-only pages).
    let rowSummary = 'unavailable (archived file unreadable)';
    const mainArchive = archived.find((p) => p.src.endsWith('tasks.db'));
    if (mainArchive) {
      try {
        const probe = new Database(mainArchive.dst, { readonly: true });
        const counts: Record<string, number> = {};
        for (const t of ['messages', 'approval_requests', 'questionnaires', 'north_stars', 'tasks']) {
          try {
            counts[t] = (probe.prepare(`SELECT COUNT(*) AS c FROM ${t}`).get() as { c: number }).c;
          } catch {
            /* table/page gone in the corrupt image — leave it out */
          }
        }
        probe.close();
        rowSummary = JSON.stringify(counts);
      } catch {
        /* corrupt file unreadable — size/mtime below still tell the story */
      }
    }
    const sizes = archived
      .map((p) => `${p.src.split('/').pop()}=${p.sizeBytes}B@${p.mtime}`)
      .join(', ');
    const msg =
      `🚨 tasks.db RECREATED EMPTY (data-loss). db.ts integrity_check FAILED (${reason}); ` +
      `the corrupt DB was archived and a fresh EMPTY schema was created. Archived: ${sizes}. ` +
      `Rows in archived main: ${rowSummary}. Recovery material: state/backups/*.corrupt-*. ` +
      `This is the silent-wipe class from 08-23 — investigate the multi-writer corruption root before it recurs.`;

    // Channel 1 — durable artifact (never lost, greppable by any watcher/human).
    mkdirSync(ALERTS_DIR, { recursive: true });
    const stamp = tsStamp();
    const alertFile = join(ALERTS_DIR, `db-recreate-${stamp}.json`);
    writeFileSync(
      alertFile,
      JSON.stringify(
        { ts: new Date().toISOString(), severity: 'CRITICAL', event: 'tasks_db_recreated', reason, archived, rowSummary, message: msg },
        null,
        2,
      ),
    );
    console.error(`[db][CRITICAL] ${msg} (durable alert: ${alertFile})`);

    // Channel 2 — best-effort Telegram via the shared verified sender. Uses the
    // resolved ORCHESTRA path so a scratch/shadow env points at its own stub.
    try {
      const r = spawnSync('bash', [join(ORCHESTRA, 'scripts', 'tg-notify.sh'), '--from', 'db.ts', msg], {
        timeout: 25000,
        stdio: 'ignore',
      });
      if (r.status !== 0) {
        console.error(`[db][CRITICAL] tg-notify exited ${r.status} — durable alert ${alertFile} still stands`);
      }
    } catch (e: any) {
      console.error(`[db][CRITICAL] tg-notify spawn failed: ${e?.message} — durable alert ${alertFile} still stands`);
    }
  } catch (e: any) {
    // Absolute last resort: the alarm itself must never break db init.
    console.error(`[db][CRITICAL] tasks.db was recreated (${reason}) but the alert path threw: ${e?.message}`);
  }
}

// Non-fatal integrity taxonomy (DEC-1788702356376911 D3): benign findings and
// in-place REINDEX repairs get a LOUD durable artifact (and, for WARN, a
// Telegram) — but must NOT emit the CRITICAL "RECREATED EMPTY (data-loss)" text,
// which now fires only on a genuine wipe path (fresh install is silent). Wrapped
// so a failed alarm can never break db init.
function alertDbFinding(severity: 'INFO' | 'WARN', summary: string, findings: string[]): void {
  try {
    mkdirSync(ALERTS_DIR, { recursive: true });
    const stamp = tsStamp();
    const alertFile = join(ALERTS_DIR, `db-integrity-${severity.toLowerCase()}-${stamp}.json`);
    const msg = `tasks.db integrity ${severity}: ${summary}. Findings: ${findings.join(' | ')}`;
    writeFileSync(
      alertFile,
      JSON.stringify({ ts: new Date().toISOString(), severity, event: 'tasks_db_integrity_finding', summary, findings }, null, 2),
    );
    console.error(`[db][${severity}] ${msg} (durable alert: ${alertFile})`);
    if (severity === 'WARN') {
      try {
        spawnSync('bash', [join(ORCHESTRA, 'scripts', 'tg-notify.sh'), '--from', 'db.ts', `⚠️ ${msg}`], { timeout: 25000, stdio: 'ignore' });
      } catch { /* durable artifact stands */ }
    }
  } catch (e: any) {
    console.error(`[db] integrity alarm (${severity}) threw: ${e?.message}`);
  }
}

// NOTE: the old rename-based archiveCorruptDb() + empty-recreate path was REMOVED
// (DEC-1788702356376911). Recovery now lives in openClassifyRecover/haltFatal:
// FATAL archives by COPY (original stays, so a crash-loop re-halts, never wipes)
// and HALTS instead of recreating empty. renameSync is no longer used here.

function applyPragmas(d: DB): void {
  d.pragma('journal_mode = WAL');
  d.pragma('synchronous = NORMAL');
  d.pragma('busy_timeout = 5000');
  d.pragma('foreign_keys = ON');
}

// Wire the testable recovery orchestration (db-recovery.ts) with this module's
// real backups dir + alarm channels. onFatal fires the CRITICAL alarm; the
// recovery module then throws DbFatalError (halt) — db init never recreates
// empty over an existing file (DEC-1788702356376911).
function recoveryOpts(): RecoveryOpts {
  return {
    dbPath: DB_PATH,
    backupsDir: BACKUPS_DIR,
    stamp: tsStamp,
    applyPragmas,
    onFinding: alertDbFinding,
    onFatal: (archivedPaths, reason) => {
      const archived: ArchivedFile[] = archivedPaths.map((dst) => {
        let sizeBytes = -1; let mtime = '';
        try { const st = statSync(dst); sizeBytes = st.size; mtime = st.mtime.toISOString(); } catch { /* -1 */ }
        return { src: dst.replace(/\.corrupt-.*$/, ''), dst, sizeBytes, mtime };
      });
      if (archived.length > 0) alertDbRecreate(archived, reason);
    },
  };
}

function initDb(): DB {
  mkdirSync(dirname(DB_PATH), { recursive: true });

  let db: DB;
  if (existsSync(DB_PATH)) {
    // Existing file: classify + recover in place. NEVER recreate empty here —
    // recoverDb serves (ok/benign/reindexed) or THROWS DbFatalError (halt).
    db = recoverDb(recoveryOpts());
  } else {
    // Fresh install ONLY: no prior data to lose. Create empty, no alarm (D4).
    db = new Database(DB_PATH);
    applyPragmas(db);
    console.log(`[db] created fresh ${DB_PATH} (no prior file — fresh install)`);
  }

  db.exec(SCHEMA_SQL);
  ensureAdditiveColumns(db);
  return db;
}

/**
 * Idempotent additive migrations for tables that already exist (CREATE TABLE
 * IF NOT EXISTS does NOT add new columns to an existing table). SQLite's
 * ALTER TABLE ADD COLUMN has no IF NOT EXISTS, so we guard on table_info.
 * CRM P0 (spec 2026-08-11-crm-pipeline-split §4.1): people.company_slugs +
 * people.resolved. Additive + backward-compatible.
 */
function ensureAdditiveColumns(d: DB): void {
  const has = (table: string, col: string) =>
    (d.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>).some((c) => c.name === col);
  try {
    if (!has('people', 'company_slugs')) d.exec('ALTER TABLE people ADD COLUMN company_slugs TEXT');
    if (!has('people', 'resolved')) d.exec('ALTER TABLE people ADD COLUMN resolved INTEGER NOT NULL DEFAULT 1');
  } catch (e: any) {
    console.error(`[db] additive-column migration error: ${e.message}`);
  }
}

const db: DB = initDb();

export default db;

export function queryDb<T = any>(sql: string, params: any[] = []): T[] {
  try {
    return db.prepare(sql).all(...params) as T[];
  } catch (e: any) {
    console.error(`[db] queryDb error: ${e.message} sql=${sql.slice(0, 120)}`);
    return [];
  }
}

export function execDb(sql: string, params: any[] = []): { changes: number; lastInsertRowid: number | bigint } {
  try {
    const info = db.prepare(sql).run(...params);
    return { changes: info.changes, lastInsertRowid: info.lastInsertRowid };
  } catch (e: any) {
    console.error(`[db] execDb error: ${e.message} sql=${sql.slice(0, 120)}`);
    return { changes: 0, lastInsertRowid: 0 };
  }
}
