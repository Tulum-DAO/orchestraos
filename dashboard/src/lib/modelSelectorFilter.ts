/**
 * modelSelectorFilter.ts — pure filter/greying logic for the B2
 * ModelSelectorSheet, split out of the component so it is unit-testable
 * without a DOM/React harness (dashboard has no test runner wired; this file
 * has zero React/DOM imports so it runs directly under `tsx --test`).
 *
 * Mirrors the API's RuntimesAvailableResponse shape (api/src/routes/
 * runtimes-available.ts) without importing it — the dashboard build doesn't
 * share a package with the api, so the shape is duplicated deliberately
 * narrow (only the fields this file reads).
 */

export interface ProviderCapabilities {
  text: boolean;
  image: boolean;
  audio: boolean;
  video: boolean;
  context_window: number | null;
}

export interface ProviderModel {
  id: string;
  label: string;
  capabilities: ProviderCapabilities;
}

export interface ProviderAvailability {
  id: string;
  label: string;
  logo_svg: string;
  installed: boolean;
  authed: boolean | 'unverified';
  auth_reason?: string;
  models: ProviderModel[];
}

export interface ProviderRow {
  provider: ProviderAvailability;
  /** True iff the provider row is selectable (tappable to expand its models). */
  selectable: boolean;
  /** Greyed rows show this next to the logo; undefined when selectable. */
  greyReason?: string;
}

/**
 * A provider is selectable only when it is BOTH installed and positively
 * authed — 'unverified' is NOT selectable (W1: unverified must never be
 * treated as usable). Every non-selectable provider carries a human-readable
 * greyReason so the sheet never renders a silently-disabled row.
 */
export function buildProviderRows(providers: ProviderAvailability[]): ProviderRow[] {
  return providers.map((provider) => {
    if (!provider.installed) {
      return { provider, selectable: false, greyReason: provider.auth_reason || 'not installed' };
    }
    if (provider.authed === true) {
      return { provider, selectable: true };
    }
    if (provider.authed === 'unverified') {
      return { provider, selectable: false, greyReason: provider.auth_reason || 'unverified' };
    }
    // authed === false
    return { provider, selectable: false, greyReason: provider.auth_reason || 'not signed in' };
  });
}

/**
 * The expanded-tile toggle. Tapping the tile that is already open CLOSES it: without this
 * an expansion could only be undone by closing the whole sheet (reported on staging,
 * 2026-09-29). Tapping a different tile switches to it rather than collapsing.
 */
export function nextExpanded(current: string | null | undefined, clicked: string): string | null {
  return current === clicked ? null : clicked;
}

/**
 * The pre-expansion applied when the sheet has not been touched yet: open on the provider
 * already in use. `undefined` means "the operator has not chosen"; `null` is a DELIBERATE
 * collapse and must survive a re-probe, or hitting refresh would silently re-open the tile
 * they just closed.
 */
export function initialExpanded(
  prev: string | null | undefined,
  currentProviderId: string | null,
): string | null {
  return prev === undefined ? currentProviderId ?? null : prev;
}

/** Models for a provider are only ever offered from a selectable provider. */
export function modelsForProvider(row: ProviderRow): ProviderModel[] {
  return row.selectable ? row.provider.models : [];
}

export type CapabilityKey = keyof ProviderCapabilities;

/**
 * Filters a provider's models down to those that positively support a given
 * capability. `context_window` is numeric so it is excluded from this
 * boolean-capability filter (callers compare it directly).
 */
export function filterModelsByCapability(
  models: ProviderModel[],
  capability: Exclude<CapabilityKey, 'context_window'>,
): ProviderModel[] {
  return models.filter((m) => m.capabilities[capability] === true);
}

/**
 * The video-upload capability gate (used by AgentChip / composer wiring):
 * true only when the selected provider is selectable AND the selected
 * model's `video` capability is exactly `true` — never inferred, never
 * defaulted to true for an unknown model.
 */
export function isVideoUploadAllowed(
  rows: ProviderRow[],
  providerId: string | null,
  modelId: string | null,
): boolean {
  if (!providerId || !modelId) return false;
  const row = rows.find((r) => r.provider.id === providerId);
  if (!row || !row.selectable) return false;
  const model = row.provider.models.find((m) => m.id === modelId);
  return model?.capabilities.video === true;
}
