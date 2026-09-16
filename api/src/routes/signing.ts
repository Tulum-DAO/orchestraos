import { Router, type Request, type Response } from 'express';
import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

const router = Router();

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const SHAW_TELEGRAM_ID = process.env.SHAW_TELEGRAM_ID || '';
const STATE_DIR = join(process.env.ORCHESTRA_DIR || loadConfig().dataDir, 'state/clients');

// POST /api/signing/webhook — receives signed agreement data
router.post('/webhook', async (req: Request, res: Response) => {
  try {
    const { agreement, signer_name, signer_company, signed_at, user_agent, signature_image } = req.body;

    if (!agreement || !signer_name) {
      res.status(400).json({ error: 'Missing required fields: agreement, signer_name' });
      return;
    }

    const timestamp = signed_at || new Date().toISOString();
    const slug = (signer_company || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+$/, '');

    // Save signing record to disk
    const sigDir = join(STATE_DIR, slug, 'signatures');
    if (!existsSync(sigDir)) mkdirSync(sigDir, { recursive: true });

    const record = {
      agreement,
      signer_name,
      signer_company: signer_company || '',
      signed_at: timestamp,
      user_agent: user_agent || '',
      received_at: new Date().toISOString(),
      ip: req.ip || req.headers['x-forwarded-for'] || 'unknown'
    };

    // Save signature image separately if provided
    if (signature_image && signature_image.startsWith('data:image')) {
      const sigFilename = `${agreement.replace(/[^a-zA-Z0-9]+/g, '-')}-${Date.now()}.png`;
      const base64Data = signature_image.replace(/^data:image\/\w+;base64,/, '');
      writeFileSync(join(sigDir, sigFilename), Buffer.from(base64Data, 'base64'));
      (record as any).signature_file = sigFilename;
    }

    // Save JSON record
    const recordFile = `signed-${agreement.replace(/[^a-zA-Z0-9]+/g, '-')}-${Date.now()}.json`;
    writeFileSync(join(sigDir, recordFile), JSON.stringify(record, null, 2));

    // Send Telegram notification to the operator
    if (TELEGRAM_BOT_TOKEN && SHAW_TELEGRAM_ID) {
      const message = [
        `📝 **AGREEMENT SIGNED**`,
        ``,
        `**${agreement}**`,
        `Signed by: ${signer_name}`,
        signer_company ? `Company: ${signer_company}` : '',
        `Time: ${new Date(timestamp).toLocaleString('en-US', { timeZone: 'America/Chicago' })} CT`,
        ``,
        `Record saved to: ${slug}/signatures/${recordFile}`
      ].filter(Boolean).join('\n');

      await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: SHAW_TELEGRAM_ID,
          text: message,
          parse_mode: 'Markdown'
        })
      }).catch(err => console.error('Telegram notification failed:', err));
    }

    console.log(`[signing] ${agreement} signed by ${signer_name} (${signer_company})`);
    res.json({ ok: true, message: 'Signature recorded', record_file: recordFile });

  } catch (err) {
    console.error('[signing] Error:', err);
    res.status(500).json({ error: 'Failed to process signature', detail: String(err) });
  }
});

// GET /api/signing/records/:slug — list all signing records for a client
router.get('/records/:slug', (req: Request, res: Response) => {
  try {
    const sigDir = join(STATE_DIR, req.params.slug as string, 'signatures');
    if (!existsSync(sigDir)) {
      res.json({ records: [], total: 0 });
      return;
    }
    const { readdirSync, readFileSync } = require('fs');
    const files = readdirSync(sigDir).filter((f: string) => f.endsWith('.json'));
    const records = files.map((f: string) => {
      try { return JSON.parse(readFileSync(join(sigDir, f), 'utf8')); }
      catch { return null; }
    }).filter(Boolean);
    res.json({ records, total: records.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load records', detail: String(err) });
  }
});

export default router;
