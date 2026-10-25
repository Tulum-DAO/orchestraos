/**
 * The agent page's Export menu (GET /api/agents/:id/transcript.md). Pure helpers, tested without a
 * browser: the URL, and the file name the server suggests.
 */
export function exportUrl(agentId: string, opts: { last?: number; tools?: 'full' } = {}): string {
  const q = new URLSearchParams();
  if (opts.last) q.set('last', String(opts.last));
  if (opts.tools === 'full') q.set('tools', 'full');
  const s = q.toString();
  return `/api/agents/${encodeURIComponent(agentId)}/transcript.md${s ? `?${s}` : ''}`;
}

/** The filename from a Content-Disposition header, else `<agent>.md`. Never a path. */
export function exportFileName(disposition: string | null | undefined, agentId: string): string {
  const m = /filename="?([^";]+)"?/i.exec(disposition || '');
  const name = (m?.[1] || `${agentId}.md`).split(/[\\/]/).pop() || `${agentId}.md`;
  return name.replace(/[^A-Za-z0-9._-]/g, '_');
}
