/**
 * CompactionRow — the chat's compaction notice (lib/compaction.ts), drawn in the conversation's order as one of the
 * kit's tool-call rows (components/kit/ToolCard.tsx): the same card, the same row (an icon, what happened, and the
 * running / done / failed mark), the way Arturo's thread shows a message sent to another agent. So it reads as
 * something that happened in the conversation, never as a divider. It changes in place: "Compacting the chat…" with
 * the running mark, then "The chat was compacted" with the done mark; a /compact the person typed that could not be
 * done reads "Couldn't compact the chat right now." with the failed mark. Nothing is drawn for anything else.
 *
 * The same row in every chat style; its colours are the kit's --bui-* tokens, set for light and dark.
 */
import { Check, LoaderCircle, Shrink, X } from 'lucide-react';
import { compactionText, type CompactionItem } from '../../lib/compaction';

export default function CompactionRow({ state, trigger }: { state: CompactionItem['state']; trigger?: CompactionItem['trigger'] }) {
  const text = compactionText(state, trigger);
  if (!text) return null;
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[min(92%,34rem)] rounded-lg border border-line px-2.5 py-1" data-testid="tool-calls" data-compaction={state} role="status">
        <div className="-mx-[3px] flex h-7 min-w-0 items-center gap-2 px-[3px]" data-tool-row="compaction">
          <span className="flex size-4 shrink-0 items-center justify-center text-ink-3"><Shrink size={13} /></span>
          <span className={`min-w-0 flex-1 truncate text-px12h font-medium ${state === 'failed' ? 'text-ink-2' : 'text-ink'}`}>{text}</span>
          {state === 'compacting' ? <LoaderCircle size={13} className="shrink-0 animate-spin text-ink-3" aria-label="running" />
            : state === 'failed' ? <X size={13} className="shrink-0 text-red" aria-label="failed" />
            : <Check size={13} className="shrink-0 text-green" aria-label="done" />}
        </div>
      </div>
    </div>
  );
}
