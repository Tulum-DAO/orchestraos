/**
 * SwitchCallDialog — the question lib/switchCall.ts asks before a live call is ended for another: "End the call with
 * <agent> and call <X>?", Keep the call / End and call.
 */
import { useSwitchAsk } from '../../lib/switchCall';

export default function SwitchCallDialog() {
  const ask = useSwitchAsk((s) => s.ask);
  if (!ask) return null;
  const answer = (yes: boolean) => { useSwitchAsk.setState({ ask: null }); ask.resolve(yes); };
  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center bg-black/40 p-4" onClick={() => answer(false)} data-switch-call>
      <div role="alertdialog" aria-modal aria-label="Switch calls" onClick={(e) => e.stopPropagation()}
           className="w-full max-w-sm rounded-2xl border border-border bg-card p-5 text-foreground shadow-2xl">
        <p className="text-px15 font-medium">{ask.text}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" autoFocus onClick={() => answer(false)} className="rounded-full px-4 py-1.5 text-sm hover:bg-muted">Keep the call</button>
          <button type="button" onClick={() => answer(true)} className="rounded-full bg-red-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-600">End and call</button>
        </div>
      </div>
    </div>
  );
}
