import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const HEARTBEAT_DIR = join(ORCHESTRA, 'state', 'machine-heartbeats');

// Ensure dir exists
mkdirSync(HEARTBEAT_DIR, { recursive: true });

export interface MachineHeartbeat {
  machine_id: string;
  hostname: string;
  tailscale_ip: string;
  user: string;
  timestamp: string;
  uptime_seconds: number;
  cpu_count: number;
  load_avg: number;
  mem_total_mb: number;
  mem_used_mb: number;
  sessions: { name: string; created: number; attached: number; has_claude: boolean }[];
  session_count: number;
}

function readHeartbeat(machineId: string): MachineHeartbeat | null {
  try {
    return JSON.parse(readFileSync(join(HEARTBEAT_DIR, `${machineId}.json`), 'utf-8'));
  } catch { return null; }
}

function getAllHeartbeats(): Record<string, MachineHeartbeat & { age_seconds: number; status: string }> {
  const result: Record<string, MachineHeartbeat & { age_seconds: number; status: string }> = {};
  try {
    const files = readdirSync(HEARTBEAT_DIR).filter(f => f.endsWith('.json'));
    for (const f of files) {
      const id = f.replace('.json', '');
      const hb = readHeartbeat(id);
      if (hb) {
        const age = (Date.now() - new Date(hb.timestamp).getTime()) / 1000;
        result[id] = {
          ...hb,
          age_seconds: Math.round(age),
          status: age < 120 ? 'online' : age < 600 ? 'stale' : 'offline',
        };
      }
    }
  } catch { /* empty */ }
  return result;
}

// GET /api/machines — list all known machines with status
router.get('/', (_req: Request, res: Response) => {
  const heartbeats = getAllHeartbeats();

  // Also include registry machines that haven't sent a heartbeat
  const registryPath = join(ORCHESTRA, 'registry.json');
  let registryMachines: Record<string, any> = {};
  try {
    registryMachines = JSON.parse(readFileSync(registryPath, 'utf-8')).machines || {};
  } catch { /* empty */ }

  const machines: any[] = [];
  const seen = new Set<string>();

  // Heartbeat machines (live data)
  for (const [id, hb] of Object.entries(heartbeats)) {
    seen.add(id);
    machines.push({
      id,
      hostname: hb.hostname,
      tailscale_ip: hb.tailscale_ip,
      user: hb.user,
      status: hb.status,
      last_seen: hb.timestamp,
      age_seconds: hb.age_seconds,
      session_count: hb.session_count,
      sessions: hb.sessions.map(s => s.name),
      claude_sessions: hb.sessions.filter(s => s.has_claude).map(s => s.name),
      uptime_seconds: hb.uptime_seconds,
      load_avg: hb.load_avg,
      mem_total_mb: hb.mem_total_mb,
      mem_used_mb: hb.mem_used_mb,
    });
  }

  // Registry machines without heartbeat
  for (const [id, minfo] of Object.entries(registryMachines)) {
    if (!seen.has(id)) {
      machines.push({
        id,
        hostname: (minfo as any).hostname || id,
        tailscale_ip: (minfo as any).tailscale_ip || 'unknown',
        user: (minfo as any).ssh_user || 'unknown',
        status: 'no_heartbeat',
        last_seen: null,
        session_count: 0,
        sessions: [],
        claude_sessions: [],
      });
    }
  }

  res.json({ machines, total: machines.length });
});

// GET /api/machines/:id — single machine details
router.get('/:id', (req: Request, res: Response) => {
  const hb = readHeartbeat(req.params.id as string);
  if (!hb) {
    res.status(404).json({ error: 'Machine not found or no heartbeat received' });
    return;
  }
  const age = (Date.now() - new Date(hb.timestamp).getTime()) / 1000;
  res.json({
    ...hb,
    age_seconds: Math.round(age),
    status: age < 120 ? 'online' : age < 600 ? 'stale' : 'offline',
  });
});

// POST /api/machines/:id/heartbeat — receive heartbeat from a machine
router.post('/:id/heartbeat', (req: Request, res: Response) => {
  const machineId = req.params.id;
  const heartbeat: MachineHeartbeat = req.body;

  // Validate
  if (!heartbeat.timestamp || !heartbeat.sessions) {
    res.status(400).json({ error: 'Missing required fields: timestamp, sessions' });
    return;
  }

  // Write to disk
  writeFileSync(
    join(HEARTBEAT_DIR, `${machineId}.json`),
    JSON.stringify(heartbeat, null, 2)
  );

  res.json({ ok: true, machine_id: machineId, sessions_received: heartbeat.session_count });
});

// Export helper for cross-machine to use
export function getMachineSessionsFromHeartbeat(machineId: string): Set<string> {
  const hb = readHeartbeat(machineId);
  if (!hb) return new Set();
  // Only trust heartbeats less than 2 minutes old
  const age = (Date.now() - new Date(hb.timestamp).getTime()) / 1000;
  if (age > 120) return new Set();
  return new Set(hb.sessions.map(s => s.name));
}

export function getMachineStatus(machineId: string): 'online' | 'stale' | 'offline' | 'no_heartbeat' {
  const hb = readHeartbeat(machineId);
  if (!hb) return 'no_heartbeat';
  const age = (Date.now() - new Date(hb.timestamp).getTime()) / 1000;
  if (age < 120) return 'online';
  if (age < 600) return 'stale';
  return 'offline';
}

export default router;
