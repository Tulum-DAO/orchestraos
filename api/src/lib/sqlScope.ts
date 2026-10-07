/**
 * Tenant filtering and ORDER BY column selection for the list routes, without interpolating caller
 * input into SQL text.
 *
 * ITS OWN MODULE, no imports at all, so the test loads the REAL functions without opening tasks.db.
 *
 * THE HOLE (2026-10-07, orchestra-builder g76, read in code): tasks-v2.ts, northstars.ts and
 * people.ts each built ` AND tenant_id = '${scope.clientScope || scope.username}'` from
 * getTenantScope(), which reads x-orchestra-role / x-orchestra-client / x-orchestra-user straight
 * from REQUEST HEADERS on an unauthenticated API. tasks-v2.ts and people.ts also put
 * req.query.sort straight into ORDER BY. better-sqlite3's prepare() refuses stacked statements, so
 * this was read-only — but a single SELECT can read any table in tasks.db.
 *
 * NOT FIXED HERE, and deliberately: the tenant scope is caller-CLAIMED (role defaults to 'admin'
 * from a header), so it is not access control at all. That is a design item, filed separately.
 */

export interface TenantScope { isAdmin: boolean; clientScope: string | null; username: string }

/** The tenant clause as a BOUND parameter. `column` is a fixed identifier chosen by the route. */
export function tenantFilter(scope: TenantScope, column: string): { sql: string; params: string[] } {
  if (scope.isAdmin) return { sql: '', params: [] };
  return { sql: ` AND ${column} = ?`, params: [String(scope.clientScope || scope.username)] };
}

/**
 * ORDER BY cannot take a bound parameter, so the column must come from a closed set. Returns the
 * column, or null for an unknown one (the route answers 400). An absent value gets the default.
 */
export function sortColumn(requested: unknown, allowed: ReadonlySet<string>, fallback: string): string | null {
  if (requested === undefined || requested === null || requested === '') return fallback;
  return typeof requested === 'string' && allowed.has(requested) ? requested : null;
}
