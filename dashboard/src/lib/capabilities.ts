/**
 * Optional features whose backing script does not ship in every install (GET /api/capabilities,
 * api/src/lib/capabilities.ts). The route answers 501 "not available in this install"; the UI hides
 * the entry so nobody clicks into it.
 *
 * Only an explicit `false` hides. Unknown (still loading, request failed, or an older API without the
 * endpoint) keeps the entry: hiding a working feature because a probe failed would be worse.
 */
export type CapabilityName = 'learningLog' | 'inspectScript';

export function isCapable(caps: unknown, name: CapabilityName): boolean {
  if (!caps || typeof caps !== 'object') return true;
  return (caps as Record<string, unknown>)[name] !== false;
}
