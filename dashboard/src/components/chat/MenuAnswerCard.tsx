/**
 * MenuAnswerCard — renders a kind='menu' approval row's options + (V3)
 * universal Respond on the web dashboard (gm-mine-menu-card seam 2). Shares
 * OptionsCard's numbered-button visual language (this is the button reuse;
 * OptionsCard itself is single-select fire-and-forget and OptionsMenuCard is
 * the pane-menu two-phase arm/confirm flow — neither models A1/A2/A3 for a
 * canonical approval_requests row, so this is the thin adapter between that
 * visual language and the /:id/answer contract).
 *
 * Contracts (DOCS/SURFACE_CONTRACTS.md):
 *   A1. option_n OR answer_text alone is a valid answer.
 *   A2. option_n AND answer_text may both be submitted.
 *   A3. Selecting an option never wipes typed answer_text (state is lifted by
 *       the caller via menuAnswer.ts's selectOption/setText — this component
 *       never merges/clears the two).
 *   V3. Options-are-actions + a universal Respond button.
 */
import { clsx } from 'clsx';
import { Loader2, Send } from 'lucide-react';
import { isFreeText, canSubmit, type MenuAnswerState, type MenuOption } from '../../lib/menuAnswer';

export interface MenuAnswerBlockProps {
  item: { id: string; menu?: { options?: MenuOption[]; question?: string; read_only?: boolean; notice?: string } | null };
  state: MenuAnswerState;
  onSelect: (n: string | null) => void;
  onText: (text: string) => void;
  onRespond: () => void;
  sending?: boolean;
  error?: string;
}

export default function MenuAnswerBlock({ item, state, onSelect, onText, onRespond, sending, error }: MenuAnswerBlockProps) {
  const options = item.menu?.options ?? [];
  const readOnly = !!item.menu?.read_only; // W5 fail-safe echo (multipart, un-hydrated)

  if (readOnly) {
    return (
      <div className="text-xs text-amber-400/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
        {item.menu?.notice || 'Multi-part menu — open the agent to answer.'}
      </div>
    );
  }

  const hasFreeText = options.some(isFreeText);

  return (
    <div className="space-y-2 pt-1">
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => (
          <button
            key={opt.n}
            type="button"
            onClick={() => onSelect(state.selectedN === opt.n ? null : opt.n)}
            disabled={sending}
            className={clsx(
              'text-xs px-3 py-1.5 min-h-[38px] rounded-lg transition-colors text-left disabled:opacity-50',
              state.selectedN === opt.n
                ? 'bg-blue-600/20 text-blue-300 ring-1 ring-blue-500/40'
                : 'bg-neutral-800 text-neutral-300 hover:text-neutral-100 hover:bg-neutral-700'
            )}
          >
            <span className="text-neutral-500 mr-1.5 font-mono">{opt.n}.</span>
            {opt.label}
          </button>
        ))}
      </div>

      {hasFreeText && (
        <input
          type="text"
          value={state.text}
          onChange={(e) => onText(e.target.value)}
          disabled={sending}
          placeholder="Write in your own answer..."
          className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-xs text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-blue-500 disabled:opacity-50"
        />
      )}

      <button
        type="button"
        onClick={onRespond}
        disabled={sending || !canSubmit(state)}
        className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
      >
        {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
        Respond
      </button>

      {error && <div className="text-[11px] text-amber-400/90">{error}</div>}
    </div>
  );
}
