/**
 * Settings (the settings design §6):
 *   GET /api/settings        -> {ok, role, rows: [{key, section, scope, value, editable, readOnly, source,
 *                                changedBy, changedAt, note, detail, choices?}]}
 *   PUT /api/settings/:key   {value} -> {ok, row}  (400 bad value, 403 not an admin, 404 unknown, 405 read-only,
 *                                502 the store refused, with its reason)
 * Each PUT is logged (state/settings-log.jsonl); an Everyone change is pushed to every open dashboard
 * (event: setting on /api/status/stream).
 */
import { Router, type Request, type Response } from 'express';
import { SETTINGS, secretSummary, type Choice, type GatewayClient, type ReadResult, type SettingEntry, type SettingsCtx } from '../lib/settingsRegistry.js';
import { recordSettingChange, lastChanges } from '../lib/settingsLog.js';
import { principal, installRole, changedBy, type InstallRole } from '../lib/principal.js';
import { configPath, dataDir, loadConfig } from '../lib/config.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { readGatewayToken } from '../lib/gateway-token.js';
import { broadcastSetting, type SettingChangeEvent } from '../services/status-stream.js';

export interface SettingRowDto {
  key: string; section: string; scope: 'user' | 'everyone'; value: unknown;
  editable: boolean; readOnly: boolean; source: 'store' | 'builtin';
  changedBy: string | null; changedAt: string | null; note: string | null; detail: string | null; choices?: Choice[];
}

export interface SettingsRouteDeps {
  ctx: (req: Request) => SettingsCtx;
  role: (req: Request) => InstallRole;
  who: (req: Request) => string;
  publish: (c: SettingChangeEvent) => void;
  now?: () => Date;
  entries?: readonly SettingEntry[];
  /** How long one row's read may take before the page goes on without it (a slow voice service). Default 3 s. */
  readTimeoutMs?: number;
}

const READ_TIMEOUT_MS = 3000;

/** One row's read that never fails the page: an error or a read slower than `ms` comes back as no value and the reason. */
async function safeRead(e: SettingEntry, ctx: SettingsCtx, ms: number): Promise<ReadResult> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<ReadResult>((resolve) => {
    timer = setTimeout(() => resolve({ value: null, source: 'store', detail: "Couldn't read this setting in time" }), ms);
  });
  try {
    return await Promise.race([
      Promise.resolve().then(() => e.read(ctx)).catch((err): ReadResult => ({
        value: null, source: 'store', detail: `Couldn't read this setting: ${err instanceof Error && err.message ? err.message : String(err)}` })),
      late,
    ]);
  } finally { clearTimeout(timer); }
}

async function rowFor(e: SettingEntry, ctx: SettingsCtx, role: InstallRole, changes: Map<string, { by: string; at: string }>,
  read: ReadResult): Promise<SettingRowDto> {
  const r = read;
  const last = e.scope === 'everyone' ? changes.get(e.key) : undefined;
  const readOnly = !!e.readOnly || !e.write;
  return {
    key: e.key, section: e.section, scope: e.scope, value: e.secret ? secretSummary(r.value) : r.value,
    editable: !readOnly && (!e.admin || role === 'admin'), readOnly, source: r.source,
    changedBy: last?.by ?? null, changedAt: last?.at ?? null, note: e.note ?? null, detail: r.detail ?? null,
    ...(r.choices ? { choices: r.choices } : {}),
  };
}

export function createSettingsRouter(deps: SettingsRouteDeps): Router {
  const router = Router();
  const entries = deps.entries ?? SETTINGS;
  const now = deps.now ?? (() => new Date());
  const ms = deps.readTimeoutMs ?? READ_TIMEOUT_MS;

  router.get('/', async (req: Request, res: Response) => {
    const ctx = deps.ctx(req);
    const role = deps.role(req);
    const changes = lastChanges(ctx.dataDir);
    const rows = await Promise.all(entries.map(async (e) => rowFor(e, ctx, role, changes, await safeRead(e, ctx, ms))));
    res.json({ ok: true, role, rows });
  });

  router.put('/:key', async (req: Request, res: Response) => {
    const e = entries.find((x) => x.key === String(req.params.key));
    if (!e) { res.status(404).json({ ok: false, error: 'No such setting' }); return; }
    if (e.readOnly || !e.write) { res.status(405).json({ ok: false, error: 'This setting is read-only' }); return; }
    const role = deps.role(req);
    if (e.admin && role !== 'admin') { res.status(403).json({ ok: false, error: 'Only an admin can change this' }); return; }
    const value = (req.body || {}).value;
    const bad = e.validate(value);
    if (bad) { res.status(400).json({ ok: false, error: bad }); return; }
    const ctx = deps.ctx(req);
    try { await e.write(ctx, value); }
    catch (err) { res.status(502).json({ ok: false, error: err instanceof Error && err.message ? err.message : 'Could not save' }); return; }
    const at = now().toISOString();
    const by = deps.who(req);
    // What the store holds now (agents.defaultModel: the whole map, not the one runtime sent), so other dashboards and
    // the log get the real value. A store that can't be read back right after a good write (the voice service) falls
    // back to what was sent.
    const read = await safeRead(e, ctx, ms);
    const held = read.value !== null && read.value !== undefined ? read.value : value;
    const stored = e.secret ? secretSummary(held) : held;   // a secret is never logged or pushed, only {set, last4}
    recordSettingChange(ctx.dataDir, { key: e.key, scope: e.scope, by, at, value: stored });   // saved already: a log failure is not a 500
    if (e.scope === 'everyone') deps.publish({ key: e.key, value: stored, changedBy: by, changedAt: at });
    // The row answered is what was saved: a read-back that failed or was slow never blanks a row that just saved.
    const shown: ReadResult = read.value === null || read.value === undefined ? { ...read, value: held, detail: null } : read;
    res.json({ ok: true, row: await rowFor(e, ctx, role, lastChanges(ctx.dataDir), shown) });
  });

  return router;
}

async function gatewayFetch(p: string, init: RequestInit): Promise<{ status: number; body: any }> {
  const r = await fetch(`${gatewayUrl()}${p}`, {
    ...init,
    headers: { ...((init.headers as Record<string, string>) || {}), Authorization: `Bearer ${readGatewayToken()}` },
    signal: AbortSignal.timeout(10_000),
  });
  return { status: r.status, body: await r.json().catch(() => ({ ok: false, error: 'bad gateway json' })) };
}

const gateway: GatewayClient = {
  get: (p) => gatewayFetch(p, { method: 'GET' }),
  put: (p, body) => gatewayFetch(p, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
};

export default createSettingsRouter({
  ctx: (req) => ({ dataDir: dataDir(), configPath: configPath(), user: principal(req)?.username ?? loadConfig().operatorId, by: changedBy(req), gateway }),
  role: (req) => installRole(req),
  who: (req) => changedBy(req),
  publish: broadcastSetting,
});
