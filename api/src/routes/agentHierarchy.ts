/**
 * The hierarchy pair (`tier`, `reports_to`) for an /agents row.
 *
 * ITS OWN MODULE, deliberately, with no imports: the test that covers it must be able to load
 * the REAL function. The previous test re-implemented these two lines and asserted against its
 * own copy, so deleting `reports_to` from the route left it green — it pinned nothing but that
 * JS `||` works. Importing agents.ts instead would drag in the sqlite identity-store reader and
 * fail to load at all, so the function moves to where it can actually be tested.
 *
 * `|| undefined` on reports_to is the whole point: a seat with no parent must come back ABSENT,
 * never `''`. "We do not know" and "it reports to nobody" are different claims, and an empty
 * string renders and sorts as a real parent named nothing — which is how a standalone seat gets
 * filed under a group that does not exist.
 */
export function hierarchyFieldsFor(def: Record<string, unknown>): { tier: string; reports_to?: string } {
  return {
    tier: (def.tier as string) || 'T2',
    reports_to: (def.reports_to as string | undefined) || undefined,
  };
}
