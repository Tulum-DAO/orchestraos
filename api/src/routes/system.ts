import { Router, type Request, type Response } from 'express';
import os from 'os';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import {
  getMacHeartbeat,
  getRateLimitState,
  getGMSession,
  isDispatcherRunning,
  getRegistry,
  getAllAgentStates,
  getShawPresence,
} from '../services/state-reader.js';
import { getTmuxSessions, getTmuxSessionNames } from '../services/tmux-monitor.js';
import { getMacStatus, getSyncStatus, getVpsTmuxSessions, probeMacTmux } from '../services/cross-machine.js';
import { loadConfig } from '../lib/config.js';

const router = Router();

const IS_MAC = os.hostname().includes('MacBook') || os.platform() === 'darwin';

router.get('/', async (_req: Request, res: Response) => {
  try {
    const mem = process.memoryUsage();
    const totalMem = os.totalmem();
    const freeMem = os.freemem();
    const tmuxSessions = getTmuxSessions();
    const registry = getRegistry() as Record<string, unknown> | null;
    const agentDefs = (registry?.agents ?? {}) as Record<string, unknown>;
    const states = getAllAgentStates();

    const agentCount = Object.keys(agentDefs).length;
    const stateCount = Object.keys(states).length;
    const onlineCount = Object.values(states).filter(
      (s) => (s as Record<string, unknown>).status === 'running' || (s as Record<string, unknown>).status === 'active'
    ).length;

    const localSessions = getTmuxSessionNames();
    const syncStatus = getSyncStatus();

    // Build machine status differently based on where API is running
    let macMachine: any;
    let vpsMachine: any;

    if (IS_MAC) {
      // Running on Mac — Mac is local, VPS is remote
      const macAgents = Object.entries(agentDefs).filter(([, a]: any) => a.machine === 'mac').map(([id]) => id);
      const macAlive = macAgents.filter(id => {
        const session = (agentDefs[id] as any)?.tmux_session || id;
        return localSessions.has(session);
      }).length;

      macMachine = {
        status: 'online',
        location: 'local',
        last_heartbeat: new Date().toISOString(),
        tailscale_ip: loadConfig().macTailscaleIp,
        agents_hosted: macAgents,
        agents_alive: macAlive,
        consecutive_failures: 0,
      };

      const vpsSessions = getVpsTmuxSessions();
      vpsMachine = {
        status: 'online' as const,
        hostname: os.hostname(),
        tailscale_ip: loadConfig().vpsTailscaleIp,
        agents_hosted: Object.entries(agentDefs).filter(([, a]: any) => a.machine === 'vps').map(([id]) => id),
        agents_alive: vpsSessions.size,
      };
    } else {
      // Running on VPS — Mac needs SSH probe
      const macState = getMacStatus();
      const macSessions = await probeMacTmux();
      const vpsSessions = getVpsTmuxSessions();

      macMachine = {
        status: macState.status,
        last_heartbeat: macState.heartbeat?.last_check || null,
        tailscale_ip: loadConfig().macTailscaleIp,
        agents_hosted: Object.entries(agentDefs).filter(([, a]: any) => a.machine === 'mac').map(([id]) => id),
        agents_alive: macSessions.size,
        consecutive_failures: macState.heartbeat?.consecutive_failures || 0,
      };

      vpsMachine = {
        status: 'online' as const,
        hostname: os.hostname(),
        tailscale_ip: loadConfig().vpsTailscaleIp,
        agents_hosted: Object.entries(agentDefs).filter(([, a]: any) => a.machine === 'vps').map(([id]) => id),
        agents_alive: vpsSessions.size,
      };
    }

    res.json({
      hostname: os.hostname(),
      platform: os.platform(),
      arch: os.arch(),
      uptime_seconds: os.uptime(),
      load_avg: os.loadavg(),
      memory: {
        total_bytes: totalMem,
        free_bytes: freeMem,
        used_pct: Math.round(((totalMem - freeMem) / totalMem) * 100),
        process_rss_bytes: mem.rss,
      },
      services: {
        dispatcher_running: isDispatcherRunning(),
        gm_session: getGMSession(),
        tmux_session_count: tmuxSessions.length,
        tmux_sessions: tmuxSessions,
      },
      heartbeats: {
        mac: IS_MAC ? { status: 'local', last_check: new Date().toISOString() } : getMacHeartbeat(),
      },
      rate_limit: getRateLimitState(),
      agents: {
        registered: agentCount,
        with_state: stateCount,
        online: onlineCount,
      },
      machines: {
        mac: macMachine,
        vps: vpsMachine,
      },
      sync: syncStatus,
      shaw_presence: getShawPresence(),
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load system status', detail: String(err) });
  }
});

// GET /api/system/snapshot — live system state (updated every 5 min by state-snapshot.sh)
router.get('/snapshot', (_req: Request, res: Response) => {
  try {
    const snapshotPath = join(process.env.ORCHESTRA_DIR!, 'state', 'live-snapshot.json');
    if (!existsSync(snapshotPath)) {
      res.status(404).json({ error: 'No snapshot yet — state-snapshot.sh may not have run' });
      return;
    }
    const data = JSON.parse(readFileSync(snapshotPath, 'utf-8'));
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: 'Failed to read snapshot', detail: String(err) });
  }
});

export default router;
