/**
 * The machines a seat may run on, and how this host reaches one: the pure half of
 * services/cross-machine.ts (which spawns, kills, injects into and captures remote seats with it),
 * shared with lib/devicePull.ts (a file a device seat named, copied here to be shown in the chat).
 *
 * There is ONE way to reach another machine: the registry's `machines` section (or a seat's own
 * ssh_host/ssh_user) over SSH with this host's key, the same key and the same options for every
 * caller. Nothing here mints, stores or forwards a credential.
 */
import os from 'node:os';
import { join } from 'node:path';

export interface MachineConfig {
  hostname: string;
  tailscale_ip: string;
  ssh_user: string;
  home: string;
  tmux_path?: string;
}

/**
 * Is a seat with this machine label on THIS host? `orchestra` registers every seat as
 * machine "local" (orchestra_cli/seats.py); the reference fleet calls its own host "vps".
 * Both, and no label at all, mean here. Testing only 'vps' sent every public install's seats
 * down the remote branches: never alive, spawn/kill/inject/capture failing with
 * "Unknown machine: local" (operator report 2026-10-08, cross-machine.thishost.test.ts).
 */
export function isThisHost(machine?: string): boolean {
  return !machine || machine === 'vps' || machine === 'local';
}

/** A machine by id from the registry's `machines`, else the seat's own ssh_host/ssh_user, else null. */
export function machineFrom(machines: Record<string, MachineConfig>, machineId: string, agentDef?: any): MachineConfig | null {
  if (machines && Object.prototype.hasOwnProperty.call(machines, machineId) && machines[machineId]) return machines[machineId];
  if (agentDef?.ssh_host) {
    return {
      hostname: machineId,
      tailscale_ip: agentDef.ssh_host,
      ssh_user: agentDef.ssh_user || 'root',
      home: agentDef.cwd || '/home/' + (agentDef.ssh_user || 'root'),
      tmux_path: agentDef.tmux_path,
    };
  }
  // No hardcoded machine convention: every cross-machine target is declared under registry.json's
  // "machines" section (multi-machine is a documented reference topology, not a built-in default).
  return null;
}

/** This host's SSH key, the one every cross-machine call uses. */
export function sshKeyPath(): string {
  return join(process.env.HOME || os.homedir(), '.ssh', 'id_ed25519');
}

/** The ssh argv for one command on `machine`: the same options for every caller. */
export function sshArgs(machine: MachineConfig, command: string, keyPath: string = sshKeyPath()): string[] {
  return [
    '-o', 'ConnectTimeout=5',
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'IdentitiesOnly=yes',
    '-i', keyPath,
    `${machine.ssh_user}@${machine.tailscale_ip}`,
    command,
  ];
}
