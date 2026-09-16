import { Router, type Request, type Response } from 'express';
import { readdirSync, readFileSync, existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { execFileSync } from 'child_process';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const BUS = join(ORCHESTRA, 'message_bus.py');

function urlToClient(pageUrl: string): { slug: string; pmAgent: string } | null {
  const clientsDir = join(ORCHESTRA, 'state', 'clients');
  if (!existsSync(clientsDir)) return null;
  const normalizedUrl = pageUrl.replace(/\/$/, '').toLowerCase();
  for (const slug of readdirSync(clientsDir)) {
    const clientFile = join(clientsDir, slug, 'client.json');
    if (!existsSync(clientFile)) continue;
    try {
      const data = JSON.parse(readFileSync(clientFile, 'utf-8'));
      for (const entry of [...(data.deployed_urls || []), ...(data.netlify_sites || [])]) {
        const u = (entry.url || '').replace(/\/$/, '').toLowerCase();
        if (u && normalizedUrl.startsWith(u)) {
          return { slug, pmAgent: data.pm_agent || `pm-${slug}` };
        }
      }
    } catch { /* skip */ }
  }
  return null;
}

router.post('/', (req: Request, res: Response) => {
  const { content, element, agentId, source, timestamp } = req.body;
  if (!content || !element) { res.status(400).json({ error: 'content and element required' }); return; }

  const pageUrl = element.pageUrl || '';
  const client = urlToClient(pageUrl);
  const targetAgent = agentId || client?.pmAgent || 'gm';
  const clientSlug = client?.slug || 'unknown';

  const feedbackDir = join(ORCHESTRA, 'state', 'feedback');
  mkdirSync(feedbackDir, { recursive: true });
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  writeFileSync(join(feedbackDir, `inspect-${clientSlug}-${ts}.json`), JSON.stringify({
    client: clientSlug, targetAgent, content, element, source: source || 'inspect-element',
    timestamp: timestamp || new Date().toISOString(), pageUrl,
  }, null, 2));

  try {
    const subject = `Client feedback: ${element.tagName} on ${pageUrl.split('/').pop() || 'page'}`;
    const body = `${content}\n\n---\nElement: <${element.tagName}> (selector: ${element.selector})\nText: ${(element.textContent || '').slice(0, 100)}\nPage: ${pageUrl}\nViewport: ${element.viewport?.width}x${element.viewport?.height}`;
    execFileSync('python3', [BUS, 'send', '--from', 'client-feedback', '--to', targetAgent,
      '--subject', subject.slice(0, 200), '--body', body, '--priority', 'high', '--type', 'inspect-feedback',
    ], { encoding: 'utf-8', timeout: 10000, cwd: ORCHESTRA });
  } catch (err: any) { console.error('Failed to route feedback:', err.message); }

  const inboxDir = join(ORCHESTRA, 'queue', 'inbox', targetAgent);
  mkdirSync(inboxDir, { recursive: true });
  writeFileSync(join(inboxDir, `inspect-${ts}.json`), JSON.stringify({
    type: 'inspect-feedback', from: 'client-feedback', to: targetAgent,
    subject: `Element change request on ${pageUrl}`, body: content, element, priority: 'high',
    timestamp: new Date().toISOString(),
  }, null, 2));

  res.json({ status: 'received', client: clientSlug, routedTo: targetAgent });
});

router.get('/script.js', (req: Request, res: Response) => {
  const scriptPath = join(ORCHESTRA, 'skills', 'inspect-element.js');
  if (!existsSync(scriptPath)) { res.status(404).send('// inspect-element.js not found'); return; }
  const apiBase = req.query.api as string || `${req.protocol}://${req.get('host')}`;
  const agentId = req.query.agent as string || '';
  const config = `window.__INSPECT_CONFIG={apiEndpoint:'${apiBase}/api/inspect-feedback',agentId:'${agentId}'};\n`;
  res.type('application/javascript').send(config + readFileSync(scriptPath, 'utf-8'));
});

export default router;
