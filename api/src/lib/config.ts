// OrchestraOS config loader (TS side).
//
// Reads orchestra.toml (path from ORCHESTRA_CONFIG env, default ../orchestra.toml
// relative to the repo root) plus a handful of secret env vars never read from
// the TOML file itself. Fails loud: a missing/blank required key throws
// ConfigError at first access, not a silent fallback to a guessed path.

import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import * as toml from 'toml';

export class ConfigError extends Error {}

export interface OrchestraConfig {
  dataDir: string;
  gatewayHost: string;
  gatewayPort: number;
  dashboardHost: string;
  dashboardPort: number;
  publicHost: string;
  notifyChannel: 'none' | 'telegram' | 'whatsapp';
  telegramBotToken: string | null;
  telegramChatId: string;
  whatsappToken: string | null;
  whatsappPhoneId: string;
  macTailscaleIp: string;
  macSshUser: string;
  vpsTailscaleIp: string;
  remoteAuthHost: string;
  operatorId: string;
  runtimesEnabled: string[];
  rotationBeatEnabled: boolean;
  rotationBusBeatIntervalSeconds: number;
  rotationCronBeatIntervalSeconds: number;
  rotationBoundaryDeliveryArmed: boolean;
  rotationCtxCeilingPct: number;
  rotationQuotaHeadroomPct: number;
  rotationReadinessTimeoutSeconds: number;
  rotationReapAfterGenerations: number;
  apiHost: string;
  apiPort: number;
  arturoEnabled: boolean;
  arturoPort: number;
}

function repoRoot(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  // api/src/lib -> repo root is three levels up
  return join(here, '..', '..', '..');
}

function configPath(): string {
  return process.env.ORCHESTRA_CONFIG || join(repoRoot(), 'orchestra.toml');
}

function requireKey(table: any, keys: string[], section: string): any {
  let cur = table;
  for (const k of keys) {
    if (cur == null || typeof cur !== 'object' || !(k in cur)) {
      throw new ConfigError(
        `orchestra.toml missing required key '${keys.join('.')}' in [${section}] ` +
          `— copy orchestra.example.toml to orchestra.toml and fill it in, or set ` +
          `ORCHESTRA_CONFIG to point at your copy.`
      );
    }
    cur = cur[k];
  }
  if (cur === '') {
    throw new ConfigError(
      `orchestra.toml key '${keys.join('.')}' in [${section}] is set but empty.`
    );
  }
  return cur;
}

let cached: OrchestraConfig | null = null;

export function loadConfig(): OrchestraConfig {
  if (cached) return cached;

  const path = configPath();
  if (!existsSync(path)) {
    throw new ConfigError(
      `No config found at ${path}. Copy orchestra.example.toml to orchestra.toml ` +
        `(or set ORCHESTRA_CONFIG) before starting any OrchestraOS service.`
    );
  }
  const raw = toml.parse(readFileSync(path, 'utf-8'));

  const notifyChannel = requireKey(raw, ['notify', 'channel'], 'notify') as
    | 'none'
    | 'telegram'
    | 'whatsapp';

  let telegramBotToken: string | null = null;
  let telegramChatId = '';
  if (notifyChannel === 'telegram') {
    const tokenEnv = requireKey(
      raw,
      ['notify', 'telegram', 'bot_token_env'],
      'notify.telegram'
    ) as string;
    telegramBotToken = process.env[tokenEnv] || null;
    if (!telegramBotToken) {
      throw new ConfigError(
        `notify.channel is 'telegram' but env var ${tokenEnv} is unset.`
      );
    }
    telegramChatId = requireKey(
      raw,
      ['notify', 'telegram', 'chat_id'],
      'notify.telegram'
    ) as string;
  }

  let whatsappToken: string | null = null;
  let whatsappPhoneId = '';
  if (notifyChannel === 'whatsapp') {
    const tokenEnv = requireKey(
      raw,
      ['notify', 'whatsapp', 'token_env'],
      'notify.whatsapp'
    ) as string;
    whatsappToken = process.env[tokenEnv] || null;
    if (!whatsappToken) {
      throw new ConfigError(
        `notify.channel is 'whatsapp' but env var ${tokenEnv} is unset.`
      );
    }
    whatsappPhoneId = requireKey(
      raw,
      ['notify', 'whatsapp', 'phone_id'],
      'notify.whatsapp'
    ) as string;
  }

  const rotation = raw.rotation || {};

  cached = {
    dataDir: requireKey(raw, ['data', 'dir'], 'data') as string,
    gatewayHost: requireKey(raw, ['gateway', 'host'], 'gateway') as string,
    gatewayPort: Number(requireKey(raw, ['gateway', 'port'], 'gateway')),
    dashboardHost: requireKey(raw, ['dashboard', 'host'], 'dashboard') as string,
    dashboardPort: Number(requireKey(raw, ['dashboard', 'port'], 'dashboard')),
    publicHost: (raw.public && raw.public.host) || '',
    notifyChannel,
    telegramBotToken,
    telegramChatId,
    whatsappToken,
    whatsappPhoneId,
    macTailscaleIp: (raw.machines && raw.machines.mac_tailscale_ip) || '',
    macSshUser: (raw.machines && raw.machines.mac_ssh_user) || '',
    vpsTailscaleIp: (raw.machines && raw.machines.vps_tailscale_ip) || '',
    remoteAuthHost: (raw.machines && raw.machines.remote_auth_host) || '',
    operatorId: (raw.operator && raw.operator.id) || 'operator',
    runtimesEnabled: requireKey(raw, ['runtimes', 'enabled'], 'runtimes') as string[],
    rotationBeatEnabled: rotation.beat_enabled ?? true,
    rotationBusBeatIntervalSeconds: rotation.bus_beat_interval_seconds ?? 60,
    rotationCronBeatIntervalSeconds: rotation.cron_beat_interval_seconds ?? 900,
    rotationBoundaryDeliveryArmed: rotation.boundary_delivery_armed ?? true,
    rotationCtxCeilingPct: rotation.ctx_ceiling_pct ?? 0.8,
    rotationQuotaHeadroomPct: rotation.quota_headroom_pct ?? 0.15,
    rotationReadinessTimeoutSeconds: rotation.readiness_timeout_seconds ?? 120,
    rotationReapAfterGenerations: rotation.reap_after_generations ?? 2,
    apiHost: (raw.api && raw.api.host) || '127.0.0.1',
    apiPort: Number((raw.api && raw.api.port) || 8888),
    arturoEnabled: (raw.arturo && raw.arturo.enabled) ?? true,
    arturoPort: Number((raw.arturo && raw.arturo.port) || 5071),
  };
  return cached;
}
