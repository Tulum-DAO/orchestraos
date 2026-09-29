import { Router } from 'express';
import { getVoiceAgentState, getVoiceTranscripts } from '../services/state-reader.js';
import { execFile } from 'child_process';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;

// Voice-call transcript proxy → the watch gateway (single owner of the call
// JSONs + auth). Web voice-call cards fetch through here so the browser never
// needs the gateway token. Read-only; the proxy owns all writes.
import { readGatewayToken } from '../lib/gateway-token.js';  // #85: honour WATCH_GATEWAY_TOKEN_FILE
const GATEWAY_URL = process.env.WATCH_GATEWAY_URL || 'http://127.0.0.1:9091';

// GET /api/voice/call?call_id= → gateway GET /voice-call. Passes the gateway
// status through so the card can drive its UX: 200 {ok,call}; 404 not-found
// (finalized-then-GC'd); 503 transient (mid-write, retry); 400 invalid id.
router.get('/call', async (req, res) => {
  const callId = String(req.query.call_id || '').trim();
  if (!callId) { res.status(400).json({ ok: false, error: 'call_id required' }); return; }
  let token = '';
  token = readGatewayToken();
  if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
  try {
    const r = await fetch(`${GATEWAY_URL}/voice-call?call_id=${encodeURIComponent(callId)}`, {
      headers: { 'Authorization': `Bearer ${token}` },
      signal: AbortSignal.timeout(10000),
    });
    const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
    res.status(r.status).json(body);
  } catch (err: any) {
    res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err.message });
  }
});

// GET /api/voice/agents — all voice agents with state
router.get('/agents', (_, res) => {
  const state = getVoiceAgentState();
  if (!state) {
    res.json({ agents: [], total: 0 });
    return;
  }
  const agents = Object.entries(state).map(([pmId, info]: [string, any]) => ({
    pm_id: pmId,
    agent_id: info.agent_id,
    name: info.name,
    voice: info.voice,
    phone_number: info.phone_number || null,
    updated_at: info.updated_at || info.created_at,
  }));
  res.json({ agents, total: agents.length });
});

// GET /api/voice/transcripts — recent call transcripts
router.get('/transcripts', (req, res) => {
  const limit = parseInt(req.query.limit as string) || 20;
  res.json(getVoiceTranscripts(limit));
});

// POST /api/voice/sync-prompts — trigger prompt sync
router.post('/sync-prompts', (_, res) => {
  const script = join(ORCHESTRA, 'voice-agent.py');
  execFile('python3', [script, 'sync-prompts'], { timeout: 30000, cwd: ORCHESTRA }, (err, stdout, stderr) => {
    if (err) {
      res.status(500).json({ error: 'Sync failed', detail: stderr || err.message });
    } else {
      res.json({ status: 'synced', output: stdout.trim() });
    }
  });
});

export default router;
