/**
 * CreateIssueButton — human-only GitHub issue filing from a red-alert card.
 * Never auto-files. Shows a scrubbed preview; operator confirms. Target repo
 * defaults to Tulum-DAO/orchestraos (overridable). With GitHub auth, files via
 * API (or comments on an open issue with the same fingerprint). Without auth,
 * a prefilled github.com /issues/new URL is offered — nothing is filed automatically.
 */
import { useState } from 'react';
import { post } from '../../lib/api';

type Preview = {
  ok: boolean;
  report_id?: string;
  fingerprint?: string;
  repo?: string | null;
  repo_configured?: boolean;
  auth_ok?: boolean;
  title?: string;
  body?: string;
  can_file?: boolean;
  can_open_url?: boolean;
  new_issue_url?: string;
  blocked_reason?: string | null;
  error?: string;
};

type CreateResult = {
  ok: boolean;
  action?: string;
  url?: string;
  number?: number;
  dry_run?: boolean;
  error?: string;
  title?: string;
  body?: string;
  repo?: string;
};

function reportIdFromOpKey(opKey: string | null | undefined): string | null {
  if (!opKey) return null;
  const m = /^red-alert:(.+)$/.exec(opKey);
  return m ? m[1] : null;
}

export function CreateIssueButton({ opKey, reportId }: { opKey?: string | null; reportId?: string | null }) {
  const rid = reportId || reportIdFromOpKey(opKey);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<CreateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!rid) return null;

  const loadPreview = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const p = await post<Preview>(`/red-alert/reports/${encodeURIComponent(rid)}/issue/preview`, {});
      setPreview(p);
      setOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'preview failed');
      setOpen(true);
    } finally {
      setLoading(false);
    }
  };

  const confirm = async (dryRun: boolean) => {
    setLoading(true); setError(null);
    try {
      const r = await post<CreateResult>(`/red-alert/reports/${encodeURIComponent(rid)}/issue`, {
        confirm: true,
        dry_run: dryRun,
      });
      setResult(r);
      if (!r.ok) setError(r.error || 'create failed');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'create failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-2">
      <button
        type="button"
        className="text-xs px-2 py-1 rounded border border-tone-600 hover:bg-tone-800 text-tone-200"
        onClick={() => void loadPreview()}
        disabled={loading}
      >
        {loading && !open ? 'Loading…' : 'Create issue'}
      </button>
      {open && (
        <div className="mt-2 p-3 rounded border border-tone-600 bg-tone-900 text-xs space-y-2">
          <div className="font-semibold text-tone-100">GitHub issue preview (scrubbed)</div>
          {error && <div className="text-red-400">{error}</div>}
          {preview && (
            <>
              {!preview.auth_ok && (
                <div className="text-amber-400">
                  No GitHub auth on this box — open the prefilled issue URL (nothing is filed automatically).
                  {preview.blocked_reason ? <> {preview.blocked_reason}</> : null}
                </div>
              )}
              <div><span className="text-tone-400">Repo:</span> {preview.repo || 'Tulum-DAO/orchestraos'}</div>
              <div><span className="text-tone-400">Fingerprint:</span> {preview.fingerprint}</div>
              <div className="font-medium text-tone-100">{preview.title}</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-tone-300 bg-black/30 p-2 rounded">{preview.body}</pre>
              <div className="flex gap-2 flex-wrap">
                <button type="button" className="px-2 py-1 rounded bg-tone-700 hover:bg-tone-600"
                  disabled={loading} onClick={() => void confirm(true)}>
                  Dry-run
                </button>
                {preview.can_file ? (
                  <button type="button" className="px-2 py-1 rounded bg-red-700 hover:bg-red-600 disabled:opacity-40"
                    disabled={loading}
                    onClick={() => void confirm(false)}>
                    Confirm & file
                  </button>
                ) : preview.new_issue_url ? (
                  <a className="px-2 py-1 rounded bg-red-700 hover:bg-red-600 text-white"
                    href={preview.new_issue_url} target="_blank" rel="noreferrer">
                    Open prefilled issue
                  </a>
                ) : null}
                <button type="button" className="px-2 py-1 rounded border border-tone-600"
                  onClick={() => { setOpen(false); setPreview(null); setResult(null); }}>
                  Cancel
                </button>
              </div>
            </>
          )}
          {result?.ok && (
            <div className="text-green-400">
              {result.action === 'open_url'
                ? (result.dry_run ? 'Dry-run: would open prefilled issue URL' : 'Open the prefilled issue URL (nothing was filed)')
                : result.dry_run ? `Dry-run: would ${result.action}` : `${result.action}d`}
              {result.url ? <> — <a className="underline" href={result.url} target="_blank" rel="noreferrer">{result.url}</a></> : null}
              {result.number != null ? ` (#${result.number})` : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default CreateIssueButton;
