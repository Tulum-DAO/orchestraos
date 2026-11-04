/**
 * settingsRegistry.ts — every setting Settings shows that lives on the server (docs/superpowers/specs/
 * the settings design doc §6): one entry per key with its section, scope ("user" = You, "everyone" = the
 * whole install), whether only an admin may change it, its validator, and read/write adapters onto the EXISTING
 * store. No new database. This-device settings never come here (the browser keeps them).
 *
 * Stores, phase 1:
 *   state/users/<you>/prefs.json         general.defaultChatStyle
 *   orchestra.toml [operator] timezone   general.timeZone (edited in place, comments kept)
 *   gateway /arturo/ptt/vendor, /voice   voice.provider, voice.defaultVoice (the Arturo service owns those files)
 *   state/agent-calls.json default.on    voice.newAgentsTakeCalls
 *   state/voice-limits.json              voice.dailyLimitMin
 *   state/agent-defaults.json            agents.defaultModel
 *   state/seat-settings-default.json     agents.autoCompact, agents.compactAt, agents.showCompaction
 *   built in + [rotation]                agents.safetyLimits (read-only)
 */
import fs from 'fs';
import path from 'path';
import * as toml from 'toml';
import { readUserPrefs, writeUserPref } from './userPrefs.js';
import { readTomlString, setTomlStringChecked, isTimeZone, writeFileKeepingMode } from './tomlEdit.js';
import { agentCallsDefault, writeAgentCallsDefault } from './agentCallGate.js';
import { readVoiceLimit, writeVoiceLimit, validDailyLimit, DEFAULT_DAILY_LIMIT, DAILY_LIMIT_MAX } from './voiceLimits.js';
import { BUILTIN_MODELS, MODEL_ID_RE, readDefaultModels, writeDefaultModel } from './agentDefaults.js';
import { BUILTIN, COMPACT_MAX, COMPACT_MIN, GUARD_PCT, readInstallDefault, validSetting, writeInstallDefault, type SettingField } from './seatSettings.js';

export interface GatewayClient {
  get(p: string): Promise<{ status: number; body: any }>;
  put(p: string, body: unknown): Promise<{ status: number; body: any }>;
}
export interface SettingsCtx {
  dataDir: string; configPath: string; user: string; gateway: GatewayClient;
  /** Who is making the change, as "Changed by" names them (lib/principal changedBy: the Tailscale login). */
  by?: string;
}
export type SectionId = 'general' | 'voice' | 'agents';
export interface Choice { value: string; label: string; available: boolean; reason?: string }
export interface ReadResult { value: unknown; source: 'store' | 'builtin'; choices?: Choice[]; detail?: string | null }
export interface SettingEntry {
  key: string;
  section: SectionId;
  scope: 'user' | 'everyone';
  admin: boolean;
  readOnly?: boolean;
  /** A secret (an API key): its value never leaves the server. The row, the change log and the push carry only
   *  {set, last4} (secretSummary). None exist in phase 1; bring-your-own-key will add them. */
  secret?: boolean;
  /** Where it lives, for people and for the test that every key resolves to a real store. */
  store: string;
  /** A delayed effect, said on the row ("Applies to new calls"). */
  note?: string;
  validate: (v: unknown) => string | null;
  read: (ctx: SettingsCtx) => Promise<ReadResult>;
  write?: (ctx: SettingsCtx, v: unknown) => Promise<void>;
}

/** What may be shown or logged of a secret: whether one is set, and its last four characters. */
export function secretSummary(v: unknown): { set: boolean; last4?: string } {
  if (typeof v === 'string') return v ? { set: true, last4: v.slice(-4) } : { set: false };
  if (v && typeof v === 'object' && 'set' in v) {
    const o = v as { set?: unknown; last4?: unknown };
    return o.set === true ? { set: true, ...(typeof o.last4 === 'string' ? { last4: o.last4.slice(-4) } : {}) } : { set: false };
  }
  return { set: v !== null && v !== undefined };
}

const oneOf = (vals: readonly string[], msg: string) => (v: unknown) => (typeof v === 'string' && vals.includes(v) ? null : msg);
const bool = (label: string) => (v: unknown) => (typeof v === 'boolean' ? null : `${label}: on or off`);

async function gw(ctx: SettingsCtx, method: 'get' | 'put', p: string, body?: unknown): Promise<any> {
  const r = method === 'get' ? await ctx.gateway.get(p) : await ctx.gateway.put(p, body);
  if (r.status < 200 || r.status >= 300 || r.body?.ok === false) throw new Error(String(r.body?.error || `the voice service answered ${r.status}`));
  return r.body;
}

const VOICE_VENDOR = 'openai';   // the dashboard offers GPT-Live only (lib/arturoVoice.ts)
const unreachable = (e: unknown) => `Couldn't reach the voice service: ${e instanceof Error ? e.message : String(e)}`;

function agentsWithOwnVoice(dataDir: string, vendor: string): number {
  try {
    const a = JSON.parse(fs.readFileSync(path.join(dataDir, 'state', 'voice-choice.json'), 'utf-8'))?.agents;
    return a && typeof a === 'object' ? Object.values(a).filter((r: any) => r && typeof r === 'object' && r[vendor]).length : 0;
  } catch { return 0; }
}

const configText = (ctx: SettingsCtx) => fs.readFileSync(ctx.configPath, 'utf-8');
function contextCeilingPct(ctx: SettingsCtx): number {
  try {
    const v = (toml.parse(configText(ctx)) as any)?.rotation?.ctx_ceiling_pct;
    return typeof v === 'number' ? Math.round(v * 100) : 80;
  } catch { return 80; }
}

function install(key: string, field: SettingField, label: string): SettingEntry {
  return {
    key, section: 'agents', scope: 'everyone', admin: true,
    store: `state/seat-settings-default.json default.${field}`,
    note: "Applies from each agent's next turn. Agents with their own setting keep it.",
    validate: (v) => (validSetting(field, v) ? null
      : field === 'compact_at' ? `${label}: a whole number from ${COMPACT_MIN} to ${COMPACT_MAX}` : `${label}: on or off`),
    read: async (c) => {
      const d = readInstallDefault(c.dataDir);
      return field in d ? { value: d[field], source: 'store' } : { value: BUILTIN[field], source: 'builtin' };
    },
    write: async (c, v) => writeInstallDefault(c.dataDir, { [field]: v as boolean | number }),
  };
}

export const SETTINGS: readonly SettingEntry[] = [
  {
    key: 'general.defaultChatStyle', section: 'general', scope: 'user', admin: false,
    store: 'state/users/<you>/prefs.json default_chat_style',
    validate: oneOf(['bubbles', 'modern'], 'Pick Bubbles or Modern'),
    read: async (c) => {
      const v = readUserPrefs(c.dataDir, c.user).default_chat_style;
      return v === 'modern' || v === 'bubbles' ? { value: v, source: 'store' } : { value: 'bubbles', source: 'builtin' };
    },
    write: async (c, v) => writeUserPref(c.dataDir, c.user, 'default_chat_style', v),
  },
  {
    key: 'general.timeZone', section: 'general', scope: 'everyone', admin: true,
    store: 'orchestra.toml [operator] timezone',
    validate: (v) => (isTimeZone(v) ? null : 'Pick a time zone from the list'),
    read: async (c) => {
      const v = readTomlString(configText(c), 'operator', 'timezone');
      return v === null ? { value: '', source: 'builtin' } : { value: v, source: 'store' };
    },
    // Checked: a shape the line editor can't handle is refused with a reason (502) and the file is left as it was.
    write: async (c, v) => writeFileKeepingMode(c.configPath, setTomlStringChecked(configText(c), 'operator', 'timezone', v as string)),
  },
  {
    key: 'voice.provider', section: 'voice', scope: 'everyone', admin: true,
    store: 'state/voice-vendor.json (through the gateway, PUT /arturo/ptt/vendor)',
    note: 'Applies to new calls',
    validate: (v) => (typeof v === 'string' && /^[a-z0-9_-]{1,32}$/.test(v) ? null : 'Pick a provider from the list'),
    read: async (c) => {
      try {
        const b = await gw(c, 'get', '/arturo/ptt/vendor');
        const rows: any[] = Array.isArray(b.vendors) ? b.vendors : [];
        return {
          value: typeof b.vendor === 'string' ? b.vendor : null, source: 'store',
          choices: rows.map((r) => ({ value: String(r.id), label: String(r.title || r.id), available: r.available === true,
            ...(r.available === true ? {} : { reason: 'Not available on this install' }) })),
        };
      } catch (e) { return { value: null, source: 'store', choices: [], detail: unreachable(e) }; }
    },
    write: async (c, v) => { await gw(c, 'put', '/arturo/ptt/vendor', { vendor: v, by: (c.by ?? c.user).slice(0, 40) }); },
  },
  {
    key: 'voice.defaultVoice', section: 'voice', scope: 'everyone', admin: true,
    store: 'state/voice-choice.json (through the gateway, PUT /arturo/ptt/voice)',
    note: 'Applies to new calls',
    validate: (v) => (typeof v === 'string' && v.length > 0 && v.length <= 200 ? null : 'Pick a voice from the list'),
    read: async (c) => {
      const n = agentsWithOwnVoice(c.dataDir, VOICE_VENDOR);
      const detail = n ? `${n} agent${n === 1 ? ' uses its' : 's use their'} own voice` : null;
      try {
        const b = await gw(c, 'get', `/arturo/ptt/voice?vendor=${VOICE_VENDOR}`);
        return { value: b.voice_id ?? null, source: 'store', detail };
      } catch (e) { return { value: null, source: 'store', detail: unreachable(e) }; }
    },
    write: async (c, v) => { await gw(c, 'put', '/arturo/ptt/voice', { vendor: VOICE_VENDOR, voice_id: v }); },
  },
  {
    key: 'voice.newAgentsTakeCalls', section: 'voice', scope: 'everyone', admin: true,
    store: 'state/agent-calls.json default.on',
    // scripts/headless_adopt.py turns calls on for new Claude seats only; other runtimes stay on tmux.
    note: 'Applies to agents made from now on. Claude agents only for now.',
    validate: bool('New agents take calls'),
    read: async (c) => {
      const v = agentCallsDefault(c.dataDir);
      return v === null ? { value: true, source: 'builtin' } : { value: v, source: 'store' };
    },
    write: async (c, v) => writeAgentCallsDefault(c.dataDir, v as boolean),
  },
  {
    key: 'voice.dailyLimitMin', section: 'voice', scope: 'everyone', admin: true,
    store: 'state/voice-limits.json daily_cap_min',
    note: 'Applies to new calls',
    validate: (v) => (validDailyLimit(v) ? null : `A whole number of minutes from 1 to ${DAILY_LIMIT_MAX}`),
    read: async (c) => {
      const v = readVoiceLimit(c.dataDir);
      // With no voice-limits.json the voice service uses its own VOICE_DAILY_CAP_MIN (voice_usage.py), which this API
      // can't see: the number shown is only the usual default.
      return v === null ? { value: DEFAULT_DAILY_LIMIT, source: 'builtin', detail: "From the voice service's environment until set here" }
        : { value: v, source: 'store' };
    },
    write: async (c, v) => writeVoiceLimit(c.dataDir, v as number),
  },
  {
    key: 'agents.defaultModel', section: 'agents', scope: 'everyone', admin: true,
    store: 'state/agent-defaults.json models',
    note: 'Applies to agents made from now on',
    validate: (v) => {
      const o = v as { runtime?: unknown; model?: unknown } | null;
      return o && typeof o === 'object' && typeof o.runtime === 'string' && o.runtime in BUILTIN_MODELS
        && typeof o.model === 'string' && MODEL_ID_RE.test(o.model) ? null : 'Pick a model from the list';
    },
    read: async (c) => {
      const stored = readDefaultModels(c.dataDir);
      return { value: { ...BUILTIN_MODELS, ...stored }, source: Object.keys(stored).length ? 'store' : 'builtin' };
    },
    write: async (c, v) => { const o = v as { runtime: string; model: string }; writeDefaultModel(c.dataDir, o.runtime, o.model); },
  },
  install('agents.autoCompact', 'auto_compact', 'Auto-compact'),
  install('agents.compactAt', 'compact_at', 'Compact at'),
  install('agents.showCompaction', 'show_compaction', 'Show compaction messages'),
  {
    key: 'agents.safetyLimits', section: 'agents', scope: 'everyone', admin: false, readOnly: true,
    store: `built in (${GUARD_PCT}%) and orchestra.toml [rotation] ctx_ceiling_pct`,
    validate: () => 'This setting is read-only',
    read: async (c) => ({ value: { forced_compaction_pct: GUARD_PCT, context_ceiling_pct: contextCeilingPct(c) }, source: 'builtin' }),
  },
];

export function settingEntry(key: string): SettingEntry | undefined {
  return SETTINGS.find((e) => e.key === key);
}
