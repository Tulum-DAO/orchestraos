/*
 * The shared live state for every avatar on the page: one record per agent id (status, the one-line
 * blurb, provider, look) and one AvatarModel per agent, so every instance of an agent (sidebar row,
 * header pill, working blurb, drawer face) shows the same thing on the same animation clock.
 *
 * The hub is the core's plain createAgentState(), wrapped for React with useSyncExternalStore. Not a
 * zustand store like stores/liveStatus.ts: the hub also owns each agent's AvatarModel and the canvas
 * ticker reads it outside React every frame. The live status stream and the saved looks write into it.
 */
import { useSyncExternalStore } from 'react';
import { agentState, identityFor, type AgentHub, type AgentRecord } from './avatarCore';
import { arturoState, STEP_FRESH_MS } from '../../lib/avatarStatus';
import { assistantName, subscribeAssistantName } from '../../lib/assistantName';
import { applyCachedLooks } from '../../lib/avatarLooks';

export const avatarHub: AgentHub = agentState;

/** The assistant's own identity: the id stays "arturo"; its name is the install's (lib/assistantName.ts,
 *  "Arturo" until renamed) and follows a rename. Its provider is set from its brain once known
 *  (arturoProvider), so the badge says what actually runs it; until then there is no badge. */
export const ARTURO_ID = 'arturo';
avatarHub.set(ARTURO_ID, { ...defaultLook(ARTURO_ID), name: assistantName(), provider: null, chosen: false });
subscribeAssistantName(() => avatarHub.set(ARTURO_ID, { name: assistantName() }));
// Saved looks this browser last saw, before the first frame: no default-look flash while /avatars loads.
applyCachedLooks(avatarHub);

/** Arturo's brain changed (its health, or a brain picked on its page): the badge follows. */
export function setArturoProvider(provider: string | null): void {
  const p = provider ? provider.toLowerCase() : null;
  if (avatarHub.get(ARTURO_ID)?.provider !== p) avatarHub.set(ARTURO_ID, { provider: p });
}

// Arturo is not a telemetry seat: its status is worked out here, from what its page says (a turn in flight,
// a live call) and from its step line, which keeps it Working through a background job on any page.
let activity = { busy: false, inCall: false };
let expiry: ReturnType<typeof setTimeout> | null = null;
function recomputeArturo() {
  const r = avatarHub.get(ARTURO_ID);
  const now = Date.now();
  const st = arturoState({ ...activity, blurb: r?.blurb, blurbAt: r?.blurbAt, now });
  if (r?.status !== st) avatarHub.set(ARTURO_ID, { status: st });
  // a step line nobody clears goes stale: look again when it does
  if (expiry) { clearTimeout(expiry); expiry = null; }
  if (r?.blurb && r.blurbAt != null && now - r.blurbAt < STEP_FRESH_MS) {
    expiry = setTimeout(recomputeArturo, r.blurbAt + STEP_FRESH_MS - now + 50);
  }
}
avatarHub.subscribe(ARTURO_ID, recomputeArturo);

/** Arturo's page says whether a turn is in flight or a call is live. */
export function setArturoActivity(a: Partial<typeof activity>): void {
  const next = { ...activity, ...a };
  if (next.busy === activity.busy && next.inCall === activity.inCall) return;
  activity = next;
  recomputeArturo();
}

/** The look an agent has with no saved choice: its hash shape, theme-aware ink, contrasting eyes. */
export function defaultLook(agentId: string): { shape: string; color: string; eyeColor: string } {
  return { shape: identityFor(agentId).shape, color: 'ink', eyeColor: 'auto' };
}

// What a render shows for an agent the hub has not heard of yet: a stable placeholder (never written to the
// hub from render; the avatar creates the real record in an effect).
const placeholders = new Map<string, AgentRecord>();
function placeholder(agentId: string): AgentRecord {
  let p = placeholders.get(agentId);
  if (!p) { p = { id: agentId, status: 'idle', blurb: '', provider: null, since: 0, ...defaultLook(agentId), chosen: false }; placeholders.set(agentId, p); }
  return p;
}

/** The agent's live record (a placeholder until it exists), re-rendering when it changes. */
export function useAgentRecord(agentId: string, hub: AgentHub = avatarHub): AgentRecord {
  const read = () => hub.get(agentId) ?? placeholder(agentId);
  return useSyncExternalStore((cb) => hub.subscribe(agentId, cb), read, read);
}

/** A record for an agent nobody has described yet: idle, with its default look. Call outside render. */
export function ensureAgent(agentId: string, hub: AgentHub = avatarHub): AgentRecord {
  const r = hub.get(agentId);
  if (r && r.shape && r.color) return r;
  // a record made by a status or step update has no look yet: it gets the default (not a saved choice)
  return hub.set(agentId, { ...defaultLook(agentId), ...(r ? {} : { chosen: false }) });
}
