/**
 * Page not found: the dashboard's catch-all route, and what a view the deployment hides
 * (runtime config hiddenViews) renders instead of the page.
 */
// There was no catch-all, so ANY unmatched path rendered an empty layout instead
// of a 404 — a guessable deep link like /agents/gm silently rendered nothing,
// which is indistinguishable from a page that loaded and had nothing to show
// (gm, 2026-08-19). Same law as the dead attention feed: a failure the operator cannot
// see is worse than one he can. This states the route does not exist; it does
// NOT invent an agent-detail page, which would be a product decision, not a fix.
export default function NotFound() {
  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-neutral-100 mb-2">Page not found</h1>
      <p className="text-sm text-neutral-400 mb-4">
        <code className="text-neutral-300">{window.location.pathname}</code> is not a route in
        this dashboard. Before this message existed, it rendered a blank page.
      </p>
      <a href="/" className="text-sm text-blue-400 hover:underline">Back to Arturo</a>
    </div>
  );
}
