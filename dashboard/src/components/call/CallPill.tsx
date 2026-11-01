/** On a call with this row's agent (or with Arturo, on his row): a green "···· 0:10" pill. */
import CallTimer from './CallTimer';

export default function CallPill() {
  return (
    <span className="shrink-0 flex items-center gap-1 rounded-full bg-green-600 px-1.5 py-px text-[10px] font-semibold text-white" data-rail-call
          title="On a call" aria-label="On a call">
      <span aria-hidden className="tracking-[-0.1em]">····</span><CallTimer />
    </span>
  );
}
