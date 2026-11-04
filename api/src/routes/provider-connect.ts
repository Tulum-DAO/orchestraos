/**
 * provider-connect.ts — install a provider CLI and sign in to it from the model picker, with no terminal.
 *
 *   GET    /api/providers/:id/connect          the attempt as the sheet shows it ({phase:'idle'} when there is none)
 *   POST   /api/providers/:id/connect  {fresh?} start one, or resume the live one (same code, same timer)
 *   POST   /api/providers/:id/connect/code {code}  Gemini: the code Google showed, typed into agy
 *   DELETE /api/providers/:id/connect          cancel: stop the CLI, forget the attempt
 *   GET    /api/providers/:id/connect/output   the raw screen / install log, for Details (read-only)
 *
 * :id is codex | grok | gemini. Admin only (the install's role, lib/principal installRole), like every other
 * install-wide change: a member gets 403 not_admin and the sheet explains instead of offering the button.
 * Every POST / DELETE must be `Content-Type: application/json` (else 415): a plain cross-site form can't send that.
 * The sign-in sessions are NOT listed for the web terminal (lib/loginShells.ts): nobody attaches to them.
 * The work itself is lib/providerConnect/manager.ts.
 */
import { Router, type Request, type Response } from 'express';
import { installRole, principal, type InstallRole } from '../lib/principal.js';
import { ConnectManager } from '../lib/providerConnect/manager.js';
import { productionConnectDeps } from '../lib/providerConnect/productionDeps.js';
import { specFor } from '../lib/providerConnect/specs.js';
import { invalidateRuntimesCache } from './runtimes-available.js';

export interface ProviderConnectRouterDeps {
  manager: ConnectManager;
  role: (req: Request) => InstallRole;
}

export function createProviderConnectRouter(deps: ProviderConnectRouterDeps): Router {
  const router = Router();

  // Mutations only as JSON. Checked before the role so the refusal is the same for everyone.
  router.use((req, res, next) => {
    if ((req.method === 'POST' || req.method === 'DELETE') && !req.is('application/json')) {
      res.status(415).json({ ok: false, reason: 'json_required' });
      return;
    }
    next();
  });

  /** The provider, or the response already sent. */
  const guard = (req: Request, res: Response) => {
    if (deps.role(req) !== 'admin') {
      res.status(403).json({ ok: false, reason: 'not_admin', error: 'Only an admin can install or sign in to providers' });
      return null;
    }
    const spec = specFor(req.params.id);
    if (!spec) { res.status(400).json({ ok: false, reason: 'unknown_provider' }); return null; }
    return spec;
  };

  router.get('/:id/connect', (req, res) => {
    const spec = guard(req, res); if (!spec) return;
    res.json({ ok: true, ...(deps.manager.view(spec.id) ?? { provider: spec.id, phase: 'idle' }) });
  });

  router.post('/:id/connect', (req, res) => {
    const spec = guard(req, res); if (!spec) return;
    const fresh = req.body?.fresh === true;
    const by = principal(req)?.username ?? 'unknown';
    console.log(`[provider-connect] start provider=${spec.id} fresh=${fresh} by=${by}`);
    res.json({ ok: true, ...deps.manager.start(spec.id, { fresh }) });
  });

  router.post('/:id/connect/code', async (req, res) => {
    const spec = guard(req, res); if (!spec) return;
    const r = await deps.manager.submitCode(spec.id, req.body?.code);
    if (!r.ok) { res.status(r.reason === 'bad_code' ? 400 : 409).json({ ok: false, reason: r.reason }); return; }
    res.json({ ok: true, ...r.view });
  });

  router.delete('/:id/connect', async (req, res) => {
    const spec = guard(req, res); if (!spec) return;
    const r = await deps.manager.cancel(spec.id);
    // Never cut an install short: the sheet hides Cancel then, and this says why if it is called anyway.
    if (r === 'installing') { res.status(409).json({ ok: false, reason: 'installing' }); return; }
    res.json({ ok: true, cancelled: r === 'cancelled' });
  });

  router.get('/:id/connect/output', async (req, res) => {
    const spec = guard(req, res); if (!spec) return;
    res.json({ ok: true, output: await deps.manager.output(spec.id) });
  });

  return router;
}

/** Advance the attempts every 1.5 s while there are any; never holds the process open. */
export function startConnectLoop(manager: ConnectManager, everyMs = 1500): NodeJS.Timeout {
  // A previous API process's sign-in sessions (marked as this flow's) hold no attempt now: end them.
  void manager.sweepOrphans().then((k) => { if (k.length) console.log(`[provider-connect] swept ${k.join(', ')}`); });
  const t = setInterval(() => { if (manager.active) void manager.tick(); }, everyMs);
  t.unref();
  return t;
}

export const connectManager = new ConnectManager(productionConnectDeps({
  // The picker probes again (POST /api/runtimes/available/refresh) when the sheet says signed in; until then, the
  // next read must not serve the old "not installed / not signed in" answer.
  refreshRuntimes: () => invalidateRuntimesCache(),
}));

export default createProviderConnectRouter({ manager: connectManager, role: (req) => installRole(req) });
