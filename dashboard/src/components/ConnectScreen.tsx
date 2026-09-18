/**
 * ConnectScreen — the web first-run "connect to your gateway" screen.
 *
 * devex-review contract (verbatim): asks for gateway URL + token; repairs input rather
 * than rejecting it; probes identity then capabilities capped at ~5s (a sentence, never a
 * forever-spinner); shows exactly one of five states; the connected state shows the
 * connection, not an empty list; plain http on a LAN is legal with one quiet line.
 *
 * The URL defaults to this page's own origin — for the common case the PWA is served BY
 * the gateway host, so the stranger only pastes a token. It stays editable for a gateway
 * elsewhere.
 */
import { useState } from 'react';
import {
  repairGatewayUrl,
  probeGateway,
  messageFor,
  type ConnectOutcome,
  type ParsedGateway,
} from '../lib/gatewayConnect';
import { useGatewayConfig } from '../stores/gatewayConfig';

type Phase =
  | { kind: 'idle' }
  | { kind: 'probing' }
  | { kind: 'result'; outcome: ConnectOutcome; parsed: ParsedGateway; pending?: number };

const defaultUrl = typeof window !== 'undefined' ? window.location.origin : '';

export default function ConnectScreen() {
  const connect = useGatewayConfig((s) => s.connect);
  const [url, setUrl] = useState(defaultUrl);
  const [token, setToken] = useState('');
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [inputError, setInputError] = useState<string | null>(null);

  // Live repair preview (also drives the plain-http LAN notice). Never rejects — only a
  // truly unparseable string sets a soft inputError.
  let preview: ParsedGateway | null = null;
  try {
    preview = repairGatewayUrl(url);
  } catch {
    preview = null;
  }
  const showHttpLanNotice = !!preview && preview.scheme === 'http' && preview.isLan;

  async function onConnect(e: React.FormEvent) {
    e.preventDefault();
    setInputError(null);
    let parsed: ParsedGateway | null;
    try {
      parsed = repairGatewayUrl(url);
    } catch {
      setInputError("That doesn't look like an address. Try something like myhost:8890.");
      return;
    }
    if (!parsed) {
      setInputError('Enter your gateway address to connect.');
      return;
    }
    setPhase({ kind: 'probing' });
    const { outcome, pending } = await probeGateway(parsed, token.trim(), { timeoutMs: 5000 });
    setPhase({ kind: 'result', outcome, parsed, pending });
    if (outcome === 'CONNECTED') {
      connect({ baseUrl: parsed.baseUrl, token: token.trim() });
      // The gate keeps ConnectScreen mounted until the operator hits Continue, so the
      // success sentence is seen — an empty queue must never be the first thing shown.
    }
  }

  const probing = phase.kind === 'probing';
  const result = phase.kind === 'result' ? phase : null;
  const connected = result?.outcome === 'CONNECTED';

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100 px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-semibold">Connect to your gateway</h1>
          <p className="mt-1 text-sm text-neutral-400">
            Point this dashboard at your OrchestraOS gateway. Run <code className="text-neutral-300">orchestra pair</code> on
            the server for a token.
          </p>
        </div>

        <form onSubmit={onConnect} className="space-y-4">
          <label className="block">
            <span className="text-xs font-medium text-neutral-400">Gateway URL</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="myhost:8890"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-blue-500"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-neutral-400">Access token</span>
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              type="password"
              placeholder="paste the code from orchestra pair"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-blue-500"
            />
          </label>

          {showHttpLanNotice && (
            <p className="text-[11px] text-neutral-500">
              Plain http is fine on a local network — this connection isn't encrypted, which is expected on a LAN.
            </p>
          )}
          {inputError && <p className="text-[12px] text-amber-400">{inputError}</p>}

          {!connected && (
            <button
              type="submit"
              disabled={probing}
              className="w-full rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-60"
            >
              {probing ? 'Connecting…' : 'Connect'}
            </button>
          )}
        </form>

        {result && !connected && (
          <div className="mt-4 rounded-lg border border-red-800/60 bg-red-950/30 px-3 py-2 text-[13px] leading-relaxed text-red-200">
            {messageFor(result.outcome, result.parsed)}
          </div>
        )}

        {connected && result && (
          <div className="mt-4 space-y-3">
            <div className="rounded-lg border border-emerald-800/60 bg-emerald-950/30 px-3 py-2 text-[13px] leading-relaxed text-emerald-200">
              {messageFor('CONNECTED', result.parsed)}
              {typeof result.pending === 'number' && result.pending > 0 && (
                <span> {result.pending} waiting.</span>
              )}
            </div>
            <button
              onClick={() => window.location.reload()}
              className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-500"
            >
              Continue to OrchestraOS
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
