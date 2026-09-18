/**
 * ModelSelectorSheet.tsx — Agent Page v1 B2, the provider/model bottom sheet.
 *
 * Replaces the ModelSelector.tsx placeholder (integrator wiring, not done
 * here per FILE-OWNERSHIP RULE — see /tmp/gm-build-b2.md for the exact
 * one-line swap).
 *
 * Row of provider logos from GET /api/runtimes/available; a provider that is
 * !installed or !authed (including 'unverified' — W1: unverified is never
 * treated as usable) renders greyed with its auth_reason. Tapping an
 * available provider expands its models filtered to availability. Selecting
 * a model writes through useModelSelection (persisted).
 *
 * Re-queries on every open (no stale sheet across a login/logout).
 */
import { useEffect, useState } from 'react';
import { X, Plus } from 'lucide-react';
import { ProviderConnectModal } from './ProviderConnectModal';
import { useModelSelection } from '../../stores/modelSelection';
import {
  buildProviderRows,
  modelsForProvider,
  type ProviderAvailability,
  type ProviderRow,
} from '../../lib/modelSelectorFilter';

interface RuntimesAvailableResponse {
  providers: ProviderAvailability[];
  probed_at: number;
  ttl_s: number;
}

/** Sentinel for the "Add a provider" tile: it expands like a provider, but is not one. */
const ADD_PROVIDER = '__add_provider__';

/** What it actually takes for a provider to show up in this row. A provider appears when
 *  its CLI is INSTALLED and SIGNED IN — the sheet only reports what the probe found, so the
 *  honest answer is the command to run, not a form that pretends to add one from here. */
function AddProviderPanel({ rows }: { rows: ProviderRow[] }) {
  const missing = rows.filter((r) => !r.selectable);
  return (
    <div className="text-xs text-foreground/70 leading-relaxed px-1 py-2 flex flex-col gap-2">
      <p className="text-foreground/80">
        A provider shows up here once its CLI is installed on this machine and signed in.
      </p>
      {missing.length > 0 && (
        <ul className="flex flex-col gap-1">
          {missing.map((r) => (
            <li key={r.provider.id} className="flex flex-col">
              <span className="text-foreground/80">{r.provider.label}</span>
              <span className="text-foreground/45">{r.greyReason}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-foreground/45">
        Install it, run the CLI once and sign in, then reopen this sheet — it re-probes every
        time it opens. Providers themselves come from the install's provider catalogue.
      </p>
    </div>
  );
}

interface ModelSelectorSheetProps {
  open: boolean;
  onClose: () => void;
}

export function ModelSelectorSheet({ open, onClose }: ModelSelectorSheetProps) {
  const [rows, setRows] = useState<ProviderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null);
  // G21: the tile the operator tapped while it was DISCONNECTED — it opens the connect modal
  // instead of doing nothing, which is also the only way the reason reaches a phone.
  const [connectRow, setConnectRow] = useState<ProviderRow | null>(null);
  const select = useModelSelection((s) => s.select);
  const currentProviderId = useModelSelection((s) => s.providerId);
  const currentModelId = useModelSelection((s) => s.modelId);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch('/api/runtimes/available')
      .then((res) => {
        if (!res.ok) throw new Error(`GET /api/runtimes/available -> ${res.status}`);
        return res.json() as Promise<RuntimesAvailableResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        setRows(buildProviderRows(data.providers));
        setExpandedProviderId((prev) => prev ?? currentProviderId ?? null);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Re-query every time the sheet opens; currentProviderId is read once per
    // open to pre-expand, not to re-trigger the fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-t-2xl bg-background border-t border-border p-4 pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-foreground">Choose a model</h2>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded hover:bg-muted">
            <X size={18} />
          </button>
        </div>

        {loading && <div className="text-xs text-foreground/50 py-4">Checking providers…</div>}
        {error && (
          <div className="text-xs text-red-500 py-2" role="alert">
            Could not load providers: {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {/* Provider tiles — a centred 4-up grid, not a scrolling flex row. With the
                "Add a provider" tile there are four: a flex row either ran off the edge of a
                phone (hiding the fourth) or wrapped it onto a lonely second line. The grid
                keeps all four on one line, equal width, centred under the heading. */}
            <div className="grid grid-cols-4 gap-2 mb-4 mx-auto max-w-sm">
              {rows.map((row) => (
                <button
                  key={row.provider.id}
                  onClick={() => row.selectable ? setExpandedProviderId(row.provider.id) : setConnectRow(row)}
                  title={row.selectable ? row.provider.label : `${row.provider.label}: ${row.greyReason} — tap to connect`}
                  className={`flex flex-col items-center justify-center gap-1 h-full min-h-[4.5rem] px-2 py-2 rounded-lg border transition-colors ${
                    row.selectable
                      ? expandedProviderId === row.provider.id
                        ? 'border-foreground/60 bg-muted'
                        : 'border-border hover:bg-muted'
                      // Unusable providers say so with a RED BORDER, not a line of text.
                      // The reason still reaches the operator two ways that do not distort
                      // the tile: the hover/long-press title, and the "Add a provider"
                      // panel, which lists every missing provider with the probe's reason.
                      // Clickable now: tapping it opens the connect modal (TMUX / OAUTH).
                      : 'border-red-500/70 opacity-60 hover:opacity-90 hover:bg-muted'
                  }`}
                >
                  <span
                    className="w-6 h-6 shrink-0 text-foreground [&>svg]:w-full [&>svg]:h-full"
                    // logo_svg is a static, build-time mark from config/providers.json
                    // (never user input) — inline SVG so it can inherit currentColor and
                    // scale to the button. The real product marks carry their own colours
                    // (Claude's sunburst, Gemini's spark gradient); the OpenAI/Codex mark
                    // is the white variant, which reads on both themes' sheet backgrounds.
                    dangerouslySetInnerHTML={{ __html: row.provider.logo_svg }}
                  />
                  <span className="text-[10px] text-foreground/70">{row.provider.label}</span>
                </button>
              ))}

              {/* Fourth tile: add a provider. Never a dead end — it expands the same way a
                  provider does, with what actually has to happen for one to appear here. */}
              <button
                onClick={() => setExpandedProviderId(ADD_PROVIDER)}
                title="Add a provider"
                className={`flex flex-col items-center justify-center gap-1 h-full min-h-[4.5rem] px-2 py-2 rounded-lg border border-dashed transition-colors ${
                  expandedProviderId === ADD_PROVIDER
                    ? 'border-foreground/60 bg-muted text-foreground'
                    : 'border-border text-foreground/60 hover:bg-muted'
                }`}
              >
                <span className="w-6 h-6 shrink-0 flex items-center justify-center">
                  <Plus size={18} />
                </span>
                <span className="text-[10px] text-foreground/70 leading-tight text-center">Add a provider</span>
              </button>
            </div>

            {/* Models for the expanded provider */}
            <div className="flex flex-col gap-1 max-h-64 overflow-y-auto">
              {(() => {
                if (expandedProviderId === ADD_PROVIDER) return <AddProviderPanel rows={rows} />;
                const expandedRow = rows.find((r) => r.provider.id === expandedProviderId);
                if (!expandedRow) return null;
                const models = modelsForProvider(expandedRow);
                if (models.length === 0) {
                  return <div className="text-xs text-foreground/40 py-2">No models available for this provider.</div>;
                }
                return models.map((model) => (
                  <button
                    key={model.id}
                    onClick={() => {
                      select({
                        providerId: expandedRow.provider.id,
                        modelId: model.id,
                        modelLabel: model.label,
                        capabilities: model.capabilities,
                      });
                      onClose();
                    }}
                    className={`text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                      currentProviderId === expandedRow.provider.id && currentModelId === model.id
                        ? 'bg-muted text-foreground'
                        : 'text-foreground/80 hover:bg-muted'
                    }`}
                  >
                    {model.label}
                    {model.capabilities.video && (
                      <span className="ml-2 text-[10px] text-foreground/40">video</span>
                    )}
                  </button>
                ));
              })()}
            </div>
          </>
        )}
      </div>

      <ProviderConnectModal
        provider={connectRow ? {
          id: connectRow.provider.id,
          label: connectRow.provider.label,
          installed: connectRow.provider.installed,
          authed: connectRow.provider.authed,
          auth_reason: connectRow.provider.auth_reason ?? connectRow.greyReason,
        } : null}
        onClose={() => setConnectRow(null)}
      />
    </div>
  );
}

export default ModelSelectorSheet;
