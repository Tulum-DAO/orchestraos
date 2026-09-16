import { execFile } from 'child_process';
import { readFileSync, statSync, existsSync } from 'fs';
import { join } from 'path';
import os from 'os';
import { getTmuxSessionNames } from './tmux-monitor.js';

const IS_VPS = !os.platform().includes('darwin');
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const SSH_KEY = join(process.env.HOME || os.homedir(), '.ssh', 'id_ed25519');
const CACHE_TTL = 30_000; // 30s

// --- Machine Registry ---
// Reads machines + agents from registry.json to resolve SSH details for ANY Tailscale machine

interface MachineConfig {
  hostname: string;
  tailscale_ip: string;
  ssh_user: string;
  home: string;
  tmux_path?: string;
}

function getRegistryMachines(): Record<string, MachineConfig> {
  try {
    const reg = JSON.parse(readFileSync(join(ORCHESTRA, 'registry.json'), 'utf-8'));
    return reg.machines || {};
  } catch { return {}; }
}

// Resolve machine config: check registry machines first, then agent-level ssh_user/ssh_host
function resolveMachine(machineId: string, agentDef?: any): MachineConfig | null {
  const machines = getRegistryMachines();

  // Direct match in machines section
  if (machines[machineId]) return machines[machineId];

  // Agent-level SSH config (for Kai's machines etc.)
  if (agentDef?.ssh_host) {
    return {
      hostname: machineId,
      tailscale_ip: agentDef.ssh_host,
      ssh_user: agentDef.ssh_user || 'root',
      home: agentDef.cwd || '/home/' + (agentDef.ssh_user || 'root'),
      tmux_path: agentDef.tmux_path,
    };
  }

  // No hardcoded machine convention — every cross-machine target must be
  // declared under registry.json's "machines" section (see orchestra.example.toml
  // for the single-machine default; multi-machine is a documented reference
  // topology, not a built-in default).
  return null;
}

// --- SSH Execution ---

function sshExec(machine: MachineConfig, command: string, timeout = 8000): Promise<{ ok: boolean; stdout: string }> {
  return new Promise((resolve) => {
    const sshArgs = [
      '-o', 'ConnectTimeout=5',
      '-o', 'StrictHostKeyChecking=no',
      '-o', 'IdentitiesOnly=yes',
      '-i', SSH_KEY,
      `${machine.ssh_user}@${machine.tailscale_ip}`,
      command,
    ];
    execFile('ssh', sshArgs, { timeout }, (err, stdout) => {
      resolve({ ok: !err, stdout: stdout?.trim() || '' });
    });
  });
}

// --- Heartbeat-based lookups (instant, no SSH) ---

function readJSON(path: string): any {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

export type MachineStatus = 'online' | 'sleeping' | 'offline' | 'unknown';

export function getMacHeartbeat() {
  return readJSON(join(ORCHESTRA, 'state', 'mac-heartbeat.json'));
}

export function getMacStatus(): { status: MachineStatus; heartbeat: any } {
  const hb = getMacHeartbeat();
  if (!hb) return { status: 'unknown', heartbeat: null };
  const lastCheck = new Date(hb.last_check || 0).getTime();
  const age = Date.now() - lastCheck;
  const failures = hb.consecutive_failures || 0;
  let status: MachineStatus;
  if (hb.status === 'online' && age < 300_000) status = 'online';
  else if (failures >= 3 || age > 600_000) status = 'sleeping';
  else if (age > 1800_000) status = 'offline';
  else status = 'sleeping';
  return { status, heartbeat: hb };
}

// Legacy compatibility
let macTmuxCache: { sessions: Set<string>; timestamp: number } = { sessions: new Set(), timestamp: 0 };
export function getMacSessionsCache(): Set<string> { return macTmuxCache.sessions; }

// --- Unified Agent Status ---

export interface UnifiedAgentStatus {
  agent_id: string;
  machine: string;
  tmux_alive: boolean;
  machine_status: MachineStatus;
}

export async function getUnifiedAgentStatus(registry: any): Promise<Record<string, UnifiedAgentStatus>> {
  const agents = registry?.agents || {};
  const localSessions = getTmuxSessionNames();

  // Collect heartbeat data for ALL machines (instant, from disk)
  const heartbeatSessions: Record<string, Set<string>> = {};
  const heartbeatStatuses: Record<string, MachineStatus> = {};
  try {
    const { getMachineSessionsFromHeartbeat, getMachineStatus: getHBStatus } = await import('../routes/machines.js');
    const hbDir = join(ORCHESTRA, 'state', 'machine-heartbeats');
    if (existsSync(hbDir)) {
      const { readdirSync } = await import('fs');
      for (const f of readdirSync(hbDir)) {
        if (!f.endsWith('.json')) continue;
        const machineId = f.replace('.json', '');
        heartbeatSessions[machineId] = getMachineSessionsFromHeartbeat(machineId);
        const hbStat = getHBStatus(machineId);
        heartbeatStatuses[machineId] = hbStat === 'no_heartbeat' ? 'unknown' : hbStat as MachineStatus;
      }
    }
  } catch { /* heartbeat module not available */ }

  const result: Record<string, UnifiedAgentStatus> = {};

  for (const [id, config] of Object.entries(agents) as [string, any][]) {
    const machine = config.machine || 'unknown';
    const session = config.tmux_session || id;

    let tmux_alive: boolean;
    let machine_status: MachineStatus;

    if (machine === 'vps') {
      tmux_alive = localSessions.has(session);
      machine_status = 'online';
    } else if (heartbeatSessions[machine] && heartbeatSessions[machine].size > 0) {
      // Any machine with heartbeat data — Mac, Kai's machines, future machines
      tmux_alive = heartbeatSessions[machine].has(session);
      machine_status = heartbeatStatuses[machine] || 'unknown';
    } else if (machine === 'mac') {
      // Fallback for Mac: legacy heartbeat
      const macState = getMacStatus();
      tmux_alive = macTmuxCache.sessions.has(session);
      machine_status = macState.status;
    } else {
      tmux_alive = false;
      machine_status = 'unknown';
    }

    result[id] = { agent_id: id, machine, tmux_alive, machine_status };
  }

  return result;
}

// --- Spawn / Kill / Inject — works with ANY machine ---

export function spawnAgent(agentId: string, registry: any, task?: string): Promise<{ success: boolean; output: string }> {
  const agent = registry?.agents?.[agentId];
  if (!agent) return Promise.resolve({ success: false, output: 'Agent not found in registry' });

  const machine = agent.machine || 'vps';
  const spawnScript = join(ORCHESTRA, 'spawn-agent.sh');
  const args = task ? [agentId, '--task', task] : [agentId];

  if (machine === 'vps') {
    return new Promise((resolve) => {
      execFile('bash', [spawnScript, ...args], { timeout: 30000, cwd: ORCHESTRA }, (err, stdout, stderr) => {
        resolve({ success: !err, output: stdout || stderr || (err?.message || '') });
      });
    });
  }

  // Remote machine — resolve SSH config
  const machineConfig = resolveMachine(machine, agent);
  if (!machineConfig) {
    return Promise.resolve({ success: false, output: `Unknown machine: ${machine}. Add to registry machines or set ssh_host on agent.` });
  }

  // Remote spawn: SSH in, start tmux session with claude
  const session = agent.tmux_session || agentId;
  const cwd = agent.cwd || machineConfig.home;
  const tmux = machineConfig.tmux_path || agent.tmux_path || 'tmux';
  const claudeBin = agent.claude_path || 'claude';
  const remoteCmd = `export PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.nvm/versions/node/v22.22.2/bin:$PATH && cd ${cwd} && ${tmux} new-session -d -s ${session} '${claudeBin} --dangerously-skip-permissions' 2>&1 && echo SPAWNED || echo FAILED`;

  return sshExec(machineConfig, remoteCmd, 30000).then(({ ok, stdout }) => ({
    success: ok && stdout.includes('SPAWNED'),
    output: stdout,
  }));
}

export function killAgent(agentId: string, registry: any): Promise<{ success: boolean; output: string }> {
  const agent = registry?.agents?.[agentId];
  if (!agent) return Promise.resolve({ success: false, output: 'Agent not found in registry' });

  const machine = agent.machine || 'vps';
  const session = agent.tmux_session || agentId;

  if (machine === 'vps') {
    return new Promise((resolve) => {
      execFile('tmux', ['kill-session', '-t', session], { timeout: 5000 }, (err, stdout, stderr) => {
        resolve({ success: !err, output: stdout || stderr || 'killed' });
      });
    });
  }

  const machineConfig = resolveMachine(machine, agent);
  if (!machineConfig) {
    return Promise.resolve({ success: false, output: `Unknown machine: ${machine}` });
  }

  const tmux = machineConfig.tmux_path || 'tmux';
  return sshExec(machineConfig, `export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH && ${tmux} kill-session -t ${session} 2>/dev/null; echo done`).then(({ ok, stdout }) => ({
    success: ok,
    output: stdout,
  }));
}

// Inject text into a remote agent's tmux session
export function injectToRemoteAgent(agentId: string, text: string, registry: any): Promise<{ success: boolean; output: string }> {
  const agent = registry?.agents?.[agentId];
  if (!agent) return Promise.resolve({ success: false, output: 'Agent not found' });

  const machine = agent.machine || 'vps';
  const session = agent.tmux_session || agentId;

  if (machine === 'vps') {
    return new Promise((resolve) => {
      // Escape single quotes in the text
      const escaped = text.replace(/'/g, "'\\''");
      execFile('tmux', ['send-keys', '-t', session, escaped, 'Enter'], { timeout: 5000 }, (err) => {
        resolve({ success: !err, output: err ? err.message : 'injected' });
      });
    });
  }

  const machineConfig = resolveMachine(machine, agent);
  if (!machineConfig) {
    return Promise.resolve({ success: false, output: `Unknown machine: ${machine}` });
  }

  const tmux = machineConfig.tmux_path || 'tmux';
  const escaped = text.replace(/'/g, "'\\''").replace(/"/g, '\\"');
  return sshExec(machineConfig, `export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH && ${tmux} send-keys -t ${session} "${escaped}" Enter`, 10000).then(({ ok, stdout }) => ({
    success: ok,
    output: stdout || 'injected',
  }));
}

// Capture output from a remote agent's tmux session
export function captureRemoteOutput(agentId: string, lines: number, registry: any): Promise<{ success: boolean; output: string }> {
  const agent = registry?.agents?.[agentId];
  if (!agent) return Promise.resolve({ success: false, output: 'Agent not found' });

  const machine = agent.machine || 'vps';
  const session = agent.tmux_session || agentId;

  if (machine === 'vps') {
    return new Promise((resolve) => {
      execFile('tmux', ['capture-pane', '-t', session, '-p', '-S', `-${lines}`], { timeout: 5000 }, (err, stdout) => {
        resolve({ success: !err, output: stdout || '' });
      });
    });
  }

  const machineConfig = resolveMachine(machine, agent);
  if (!machineConfig) {
    return Promise.resolve({ success: false, output: `Unknown machine: ${machine}` });
  }

  const tmux = machineConfig.tmux_path || 'tmux';
  return sshExec(machineConfig, `export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH && ${tmux} capture-pane -t ${session} -p -S -${lines}`, 10000).then(({ ok, stdout }) => ({
    success: ok,
    output: stdout,
  }));
}

// --- Legacy exports for backward compat ---

export function probeMacTmux(): Promise<Set<string>> {
  const machineConfig = resolveMachine('mac');
  if (!machineConfig) return Promise.resolve(new Set());
  if (Date.now() - macTmuxCache.timestamp < CACHE_TTL) return Promise.resolve(macTmuxCache.sessions);

  const macState = getMacStatus();
  if (macState.status !== 'online' && macState.status !== 'unknown') return Promise.resolve(macTmuxCache.sessions);

  const tmux = machineConfig.tmux_path || 'tmux';
  return sshExec(machineConfig, `${tmux} list-sessions 2>/dev/null | cut -d: -f1 || echo ''`).then(({ ok, stdout }) => {
    if (!ok) return macTmuxCache.sessions;
    const sessions = new Set(stdout.split('\n').filter(Boolean));
    macTmuxCache = { sessions, timestamp: Date.now() };
    return sessions;
  });
}

export function probeVpsTmux(): Promise<Set<string>> {
  return Promise.resolve(getTmuxSessionNames());
}

export function getVpsTmuxSessions(): Set<string> {
  return getTmuxSessionNames();
}

export function getSyncStatus(): { last_sync: string | null; stale: boolean } {
  try {
    const stat = statSync(join(ORCHESTRA, 'state', 'mac-heartbeat.json'));
    const age = Date.now() - stat.mtime.getTime();
    return { last_sync: stat.mtime.toISOString(), stale: age > 600_000 };
  } catch {
    return { last_sync: null, stale: true };
  }
}

// Export resolveMachine for other modules
export { resolveMachine };
