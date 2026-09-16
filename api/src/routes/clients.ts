import { Router, type Request, type Response } from 'express';
import { readdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { getAllClientEcosystems, getClientEcosystem } from '../services/state-reader.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');

router.get('/', (_req: Request, res: Response) => {
  try {
    const seen = new Set<string>();
    const clients: any[] = [];

    // Source 1: client-ecosystems (per-client ecosystems, e.g. acme, northwind)
    const ecosystems = getAllClientEcosystems();
    for (const eco of ecosystems as any[]) {
      const id = eco.client_id;
      if (seen.has(id)) continue;
      seen.add(id);
      clients.push({
        id, slug: id,
        name: eco.client_name || id,
        type: eco.type,
        location: eco.location,
        system_count: eco.systems?.length ?? 0,
        primary_contact: eco.contacts?.primary?.name ?? null,
        owner: eco.contacts?.owner?.name ?? null,
      });
    }

    // Source 2: state/clients/*/client.json (per-client examples)
    const clientsDir = join(ORCHESTRA, 'state', 'clients');
    if (existsSync(clientsDir)) {
      for (const slug of readdirSync(clientsDir)) {
        if (seen.has(slug)) continue;
        const clientFile = join(clientsDir, slug, 'client.json');
        if (!existsSync(clientFile)) continue;
        try {
          const data = JSON.parse(readFileSync(clientFile, 'utf-8'));
          seen.add(slug);
          clients.push({
            id: slug, slug,
            name: data.name || data.company || slug,
            type: data.service_scope || 'client',
            status: data.status,
            pm_agent: data.pm_agent,
            primary_contact: data.primary_contact?.name ?? null,
          });
        } catch { /* skip bad files */ }
      }
    }

    clients.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    res.json({ clients, total: clients.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load clients', detail: String(err) });
  }
});

router.get('/:id/ecosystem', (req: Request, res: Response) => {
  const eco = getClientEcosystem(String(req.params.id));
  if (!eco) { res.status(404).json({ error: 'No ecosystem map' }); return; }
  res.json(eco);
});

// GET /api/clients/:id — unified client detail
router.get('/:id', (req: Request, res: Response) => {
  const slug = String(req.params.id);
  try {
    const clientFile = join(ORCHESTRA, 'state', 'clients', slug, 'client.json');
    let client: any = { slug };
    if (existsSync(clientFile)) {
      client = { slug, ...JSON.parse(readFileSync(clientFile, 'utf-8')) };
    }

    // People
    client.people = client.people || client.contacts || client.team || [];

    // Deliverables
    const delFile = join(ORCHESTRA, 'state', 'clients', slug, 'deliverables.json');
    if (existsSync(delFile)) {
      try { client.deliverables = JSON.parse(readFileSync(delFile, 'utf-8')).deliverables || []; }
      catch { client.deliverables = []; }
    } else { client.deliverables = []; }

    // Ecosystem
    client.ecosystem = getClientEcosystem(slug) || null;

    res.json(client);
  } catch (err) {
    res.status(500).json({ error: 'Failed to load client', detail: String(err) });
  }
});

export default router;
