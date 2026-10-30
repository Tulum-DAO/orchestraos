/**
 * The agent page's line about a headless seat's terminal (hooks/useHeadlessDev): switching to it, waiting for the
 * turn to end, why a switch can't happen now, with the choices that fit. Nothing for a tmux seat, or when there is
 * nothing to say.
 */
import { Loader2 } from 'lucide-react';
import type { HeadlessDev } from '../../hooks/useHeadlessDev';

export default function HeadlessDevBanner({ dev }: { dev: HeadlessDev }) {
  const b = dev.banner;
  if (!b) return null;
  const warn = b.tone === 'warn';
  return (
    <div role="status" aria-live="polite" data-headless-dev
         className={`mx-3 mt-2 mb-1 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border px-3 py-2 text-[13px] ${
           warn ? 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-100'
             : 'border-border bg-muted text-foreground'}`}>
      <span className="flex min-w-0 flex-1 items-center gap-2">
        {b.busyIndicator && <Loader2 size={14} className="shrink-0 animate-spin" aria-hidden />}
        <span className="min-w-0 break-words">{b.text}</span>
      </span>
      {b.actions.length > 0 && (
        <span className="flex flex-wrap gap-2">
          {b.actions.map((a, i) => (
            <button key={a.id} type="button" data-dev-action={a.id} onClick={() => dev.act(a.id)}
                    className={i === 0
                      ? 'rounded-full bg-foreground px-3 py-1 text-[12.5px] font-medium text-background hover:opacity-90'
                      : 'rounded-full border border-border px-3 py-1 text-[12.5px] font-medium text-foreground hover:bg-background'}>
              {a.label}
            </button>
          ))}
        </span>
      )}
    </div>
  );
}
