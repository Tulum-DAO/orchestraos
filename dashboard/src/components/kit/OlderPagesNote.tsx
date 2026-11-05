/**
 * OlderPagesNote — "Loading earlier messages…", pinned to the top of a thread's scroller while an older page is read
 * (lib/threadKit/useOlderPages.ts). Zero height and always there (only shown or hidden), so the rows under it never
 * move for it, even in a feed that spaces its children apart; never the scroll anchor.
 */
export default function OlderPagesNote({ show }: { show: boolean }) {
  return (
    <div data-anchor-skip aria-hidden={!show} className="sticky top-0 h-0 shrink-0 z-10 flex justify-center pointer-events-none"
      style={show ? undefined : { visibility: 'hidden' }}>
      <span className="mt-2 h-fit text-px11 text-tone-500 px-2.5 py-0.5 rounded-full border border-tone-800 bg-tone-950/90" {...(show ? { 'data-older-loading': '' } : {})}>
        Loading earlier messages…
      </span>
    </div>
  );
}
