/**
 * SignInLink — the sign-in URL a CLI printed into a terminal pane, as a real link.
 *
 * A CLI prints its OAuth URL hard-wrapped across many lines (the CLI's own breaks, not
 * tmux's, so `capture-pane -J` does not rejoin them). Nobody can click that, and selecting
 * it drags the breaks and the leading spaces along, so what reaches the browser is broken.
 * The server rejoins it (GET /api/agents/:session/sign-in-url); this polls while the pane is
 * open so the moment a URL appears, there is one button to press.
 */
import { useEffect, useState } from 'react';
import { ExternalLink, Copy, Check } from 'lucide-react';
import { useSignInUrl } from '../lib/useSignInUrl';

export default function SignInLink({ session, label = 'Open the sign-in page' }:
    { session: string | null | undefined; label?: string }) {
  const url = useSignInUrl(session);
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (!copied) return; const t = setTimeout(() => setCopied(false), 1500); return () => clearTimeout(t); }, [copied]);
  if (!url) return null;
  return (
    <div className="flex items-center gap-2" data-testid="signin-link">
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        title={url}
        className="flex-1 flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[#d97757] text-white text-sm font-medium"
      >
        <ExternalLink size={13} /> {label}
      </a>
      <button
        aria-label="Copy the sign-in link"
        title="Copy the link"
        onClick={() => { void navigator.clipboard?.writeText(url); setCopied(true); }}
        className="p-2 rounded-lg border border-neutral-700 text-neutral-300 hover:bg-neutral-800"
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
    </div>
  );
}
