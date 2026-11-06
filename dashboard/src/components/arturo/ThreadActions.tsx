/**
 * ThreadActions — rename and delete one of Arturo's chats, wherever a chat is listed or open.
 *
 *  - EditableThread wraps a list row: a ⋯ button beside it (always shown on a touch screen, on hover or keyboard focus
 *    with a mouse) opens Rename / Delete. Rename turns the row into a field in place: Enter or leaving the field
 *    saves, Esc cancels (as the agent rail's rename and the drawer's name field do).
 *  - ThreadHeaderMenu is the same menu for the chat that is open, in a header: Rename edits the title in the menu.
 *  - DeleteThreadDialog confirms a delete and lists the facts first learned in that chat, with an unticked
 *    "forget these too".
 * The menu and the dialog are portalled to <body> so a scrolling list never clips them; their clicks stop at their
 * root, so a phone's sidebar drawer does not take them for a navigation and close.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import clsx from 'clsx';
import {
  commitDelete, commitRename, subscribeThreadEdits, threadFacts, threadLabel, THREAD_TITLE_MAX, type LearnedFact,
} from '../../lib/arturoThreadEdits';
import { loadThreadPage } from '../../lib/arturoThreads';
import { newConversationId } from '../../lib/arturo';
import { useInstallAdmin } from '../../lib/myRole';
import { useAssistantName } from '../../lib/assistantName';

type Titled = { id: string; title: string; title_custom?: boolean };
const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

/** The title as a field. Enter or leaving it saves (an empty name puts the auto-title back), Esc cancels. The new
 *  title shows at once (commitRename); a refusal puts the old one back and its reason goes to `onError`. */
export function ThreadTitleField({ id, title, custom, onDone, onError, className }: {
  id: string; title: string; custom?: boolean;
  /** How editing ended: Enter, Esc, or focus leaving the field (a click elsewhere: focus stays where it went). */
  onDone: (how: 'enter' | 'escape' | 'blur') => void;
  onError: (e: string | null) => void; className?: string;
}) {
  const [text, setText] = useState(title);
  const done = useRef(false);    // once: Enter then the blur it causes, or Esc then the field's removal, end it once
  const save = (how: 'enter' | 'blur') => {
    if (done.current) return;
    done.current = true;
    onDone(how);
    void commitRename(id, title, text, custom).then(onError);
  };
  const cancel = () => {
    if (done.current) return;
    done.current = true;
    onError(null);
    onDone('escape');
  };
  return (
    <input autoFocus value={text} maxLength={THREAD_TITLE_MAX * 2} aria-label={`New name for ${title || 'this chat'}`}
           data-thread-rename onClick={stop}
           onFocus={(e) => e.currentTarget.select()}
           onChange={(e) => { setText(e.target.value); onError(null); }}
           onKeyDown={(e) => {
             e.stopPropagation();
             if (e.key === 'Enter') { e.preventDefault(); save('enter'); }
             else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
           }}
           onBlur={() => save('blur')}
           className={clsx('min-w-0 text-sm px-1.5 py-0.5 rounded border border-neutral-300 dark:border-neutral-600 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 outline-none focus:border-neutral-500', className)} />
  );
}

const MENU_W = 176;
const MENU_H = 96;   // about two items and a note: enough to tell whether it fits below

/** A small menu, fixed under (or above) the button that opened it. Closes on a choice, Escape, a click elsewhere, or
 *  the page scrolling under it. Focus goes to its first item, and back to the button when it closes. */
function PopMenu({ anchor, onClose, children, label }: { anchor: HTMLElement; onClose: () => void; children: ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  // Placed from the button where it is now (the menu closes if the page scrolls or resizes under it): right-aligned
  // to it, below it, or above it when there is no room below.
  const r = anchor.getBoundingClientRect();
  const w = MENU_W, h = MENU_H;
  const pos = { left: Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)),
                top: r.bottom + h + 8 > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4 };
  useEffect(() => {
    const away = (e: Event) => {
      if (ref.current?.contains(e.target as Node) || anchor.contains(e.target as Node)) return;
      // A field being edited in the menu saves on leaving it: let it see its blur before the menu goes.
      const active = document.activeElement;
      if (active instanceof HTMLElement && ref.current?.contains(active)) active.blur();
      onClose();
    };
    // A title field in the menu handles its own Esc (it cancels, then closes the menu).
    const editing = () => !!ref.current?.contains(document.activeElement) && document.activeElement?.matches('[data-thread-rename]');
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || editing()) return;
      e.stopPropagation(); onClose(); anchor.focus();
    };
    // While a field in it has focus, a phone scrolling it into view or its keyboard resizing the page is not the page
    // moving away from the menu.
    const scrolled = (e: Event) => { if (!ref.current?.contains(e.target as Node) && !editing()) onClose(); };
    const resized = () => { if (!editing()) onClose(); };
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', esc, true);
    window.addEventListener('scroll', scrolled, true);
    window.addEventListener('resize', resized);
    return () => {
      document.removeEventListener('pointerdown', away, true);
      document.removeEventListener('keydown', esc, true);
      window.removeEventListener('scroll', scrolled, true);
      window.removeEventListener('resize', resized);
    };
  }, [anchor, onClose]);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('button, input')?.focus(); }, []);
  return createPortal(
    <div ref={ref} role="menu" aria-label={label} data-thread-menu onClick={stop} onPointerDown={stop}
         style={{ position: 'fixed', top: pos.top, left: pos.left, width: MENU_W }}
         className="z-[1000] p-1 rounded-md border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 shadow-lg">
      {children}
    </div>,
    document.body,
  );
}

const ITEM = 'w-full text-left px-3 py-1.5 max-md:py-2.5 text-sm rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 focus:outline-none focus-visible:bg-neutral-100 dark:focus-visible:bg-neutral-800 disabled:opacity-50';

/** A chat row with its ⋯ menu. `children` is the row itself (a button that opens the chat); it should leave room on
 *  its right for the ⋯ (pr-8). */
export function EditableThread({ thread, children, fieldClassName, rowClassName, moreClassName, role }: {
  thread: Titled; children: ReactNode; fieldClassName?: string; rowClassName?: string; moreClassName?: string;
  /** The row's role in its list (e.g. "listitem"). */
  role?: string;
}) {
  const admin = useInstallAdmin();
  const [renaming, setRenaming] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);    // the ⋯ while its menu is open
  const menu = anchor !== null;
  const setMenu = (open: boolean) => { if (!open) setAnchor(null); };
  const [deleting, setDeleting] = useState<HTMLElement | null>(null);   // the ⋯ the delete was asked from
  const [error, setError] = useState<string | null>(null);
  const close = useCallback(() => setAnchor(null), []);
  const label = threadLabel(thread);
  return (
    <div className={clsx('group relative', rowClassName)} data-thread-row={thread.id} role={role}>
      {renaming
        ? <ThreadTitleField id={thread.id} title={thread.title} custom={thread.title_custom} onDone={() => setRenaming(false)} onError={setError}
                            className={clsx('w-full', fieldClassName)} />
        : children}
      {!renaming && (
        <button type="button" aria-label={`More for ${label}`} aria-haspopup="menu" aria-expanded={menu}
                data-thread-more
                onClick={(e) => { e.stopPropagation(); const el = e.currentTarget; setAnchor((a) => (a ? null : el)); }}
                className={clsx('absolute right-1 top-1/2 -translate-y-1/2 w-6 h-6 max-md:w-8 max-md:h-8 grid place-items-center rounded',
                  'hover:bg-neutral-300/60 dark:hover:bg-neutral-700/60 focus:outline-none focus-visible:ring-1 focus-visible:ring-neutral-500',
                  // a mouse sees it on hover or focus; a touch screen always does (no hover to find it with)
                  menu ? 'opacity-100' : '[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 focus-visible:opacity-100 group-focus-within:opacity-100',
                  moreClassName)}>
          <MoreHorizontal size={14} />
        </button>
      )}
      {anchor && (
        <PopMenu anchor={anchor} onClose={close} label={`${label}: actions`}>
          <button type="button" role="menuitem" className={ITEM} data-thread-action="rename"
                  onClick={() => { setMenu(false); setError(null); setRenaming(true); }}>Rename</button>
          {admin && (
            <button type="button" role="menuitem" className={clsx(ITEM, 'text-red-600 dark:text-red-400')} data-thread-action="delete"
                    onClick={() => { setDeleting(anchor); setMenu(false); setError(null); }}>Delete</button>
          )}
        </PopMenu>
      )}
      {error && <p className="px-2 pb-1 text-xs text-red-600 dark:text-red-400" role="alert" onClick={stop}>{error}</p>}
      {deleting && <DeleteThreadDialog id={thread.id} title={label} returnTo={deleting} onClose={() => setDeleting(null)} />}
    </div>
  );
}

/** The open chat's ⋯, for a header: Rename (in the menu) and Delete. A chat nothing was said in yet has no title and
 *  nothing to delete: the items say so. `buttonClassName` matches the header's own buttons. */
export function ThreadHeaderMenu(props: { id: string; buttonClassName?: string; size?: number }) {
  return <HeaderMenu key={props.id} {...props} />;   // another chat opened: start fresh (nothing read, no error)
}

function HeaderMenu({ id, buttonClassName, size = 22 }: { id: string; buttonClassName?: string; size?: number }) {
  const admin = useInstallAdmin();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);    // the ⋯ while its menu is open
  const open = anchor !== null;
  const setOpen = (v: boolean) => { if (!v) setAnchor(null); };
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState<HTMLElement | null>(null);   // the ⋯ the delete was asked from
  const [error, setError] = useState<string | null>(null);
  // undefined while it is being read; null = not saved yet (nothing said in it)
  const [thread, setThread] = useState<Titled | null | undefined>(undefined);
  const btn = useRef<HTMLButtonElement>(null);
  // Whether the menu was open when the ⋯ was pressed: pressing it moves focus out of a title field being edited,
  // which saves and closes the menu before the click, and the click must not then open it again.
  const openAtPress = useRef<boolean | null>(null);
  const close = useCallback(() => { setAnchor(null); setRenaming(false); }, []);
  useEffect(() => {
    if (!open) return;
    let live = true;
    void loadThreadPage(id, undefined, 1).then((p) => {
      if (live) setThread(p?.thread ? { id: p.thread.id, title: p.thread.title, title_custom: p.thread.title_custom } : null);
    });
    return () => { live = false; };
  }, [open, id]);
  useEffect(() => subscribeThreadEdits((e) => {
    if (e.id !== id) return;
    if (e.kind === 'delete') setThread(null);
    else setThread((t) => (t ? { ...t, title: e.title, ...(e.custom === undefined ? {} : { title_custom: e.custom }) } : t));
  }), [id]);
  return (
    <>
      <button ref={btn} type="button" className={buttonClassName} aria-label="This chat: rename or delete" title="Rename or delete this chat"
              aria-haspopup="menu" aria-expanded={open} data-thread-header-more
              onPointerDown={() => { openAtPress.current = open; }}
              onClick={(e) => {
                const el = e.currentTarget, was = openAtPress.current ?? open;
                openAtPress.current = null;
                setAnchor(was ? null : el);
                if (was) setRenaming(false);
              }}>
        <MoreHorizontal size={size} strokeWidth={2.2} absoluteStrokeWidth />
      </button>
      {anchor && (
        <PopMenu anchor={anchor} onClose={close} label="This chat">
          {renaming && thread ? (
            <div className="p-1">
              <ThreadTitleField id={id} title={thread.title} custom={thread.title_custom} className="w-full"
                                onDone={(how) => { setRenaming(false); setOpen(false); if (how !== 'blur') btn.current?.focus(); }}
                                onError={setError} />
            </div>
          ) : (
            <>
              <button type="button" role="menuitem" className={ITEM} data-thread-action="rename" disabled={!thread}
                      onClick={() => { setError(null); setRenaming(true); }}>
                {thread === undefined ? 'Rename…' : 'Rename'}
              </button>
              {admin && (
                <button type="button" role="menuitem" className={clsx(ITEM, 'text-red-600 dark:text-red-400')} data-thread-action="delete"
                        disabled={!thread} onClick={() => { setDeleting(anchor); setOpen(false); setError(null); }}>Delete</button>
              )}
              {thread === null && <p className="px-3 py-1 text-xs text-neutral-500">Nothing said in this chat yet.</p>}
            </>
          )}
        </PopMenu>
      )}
      {error && createPortal(
        <div role="alert" className="fixed left-1/2 -translate-x-1/2 bottom-6 z-[1000] px-3 py-2 rounded-md text-sm bg-red-600 text-white shadow-lg"
             onClick={() => setError(null)}>{error}</div>, document.body)}
      {deleting && thread && <DeleteThreadDialog id={id} title={threadLabel(thread)} returnTo={deleting} onClose={() => setDeleting(null)} />}
    </>
  );
}

/** Confirm a delete. The chat goes everywhere it was copied; the facts first learned in it go only when the box is
 *  ticked (it starts unticked). The onboarding chat's facts about the operator are never offered: deleting it does
 *  not reset onboarding. */
export function DeleteThreadDialog({ id, title, onClose, returnTo }: {
  id: string; title: string; onClose: () => void;
  /** Where focus goes back to when the dialog closes (the ⋯ it was asked from); else where focus was. */
  returnTo?: HTMLElement | null;
}) {
  const aname = useAssistantName();
  // null while checking; 'unknown' when the check failed (the chat can still be deleted; its facts then stay)
  const [facts, setFacts] = useState<LearnedFact[] | 'unknown' | null>(null);
  const [forget, setForget] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let live = true;
    void threadFacts(id).then((r) => { if (live) setFacts(r ? r.facts : 'unknown'); });
    // Focus starts on Cancel and goes back to the ⋯ (or where it was) when the dialog closes.
    const from = returnTo ?? document.activeElement;
    cancelRef.current?.focus();
    return () => {
      live = false;
      if (from instanceof HTMLElement && from.isConnected) from.focus();
    };
  }, [id, returnTo]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) { e.stopPropagation(); onClose(); } };
    document.addEventListener('keydown', esc, true);
    return () => document.removeEventListener('keydown', esc, true);
  }, [busy, onClose]);
  const remove = async () => {
    setBusy(true);
    setError(null);
    const err = await commitDelete(id, forget && Array.isArray(facts) && facts.length > 0, () => newConversationId('web'));
    setBusy(false);
    if (err) setError(err); else onClose();
  };
  return createPortal(
    <div className="fixed inset-0 z-[1000] grid place-items-center bg-black/50 px-4" data-thread-delete-dialog
         onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget && !busy) onClose(); }}
         onPointerDown={stop}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="thread-delete-title" aria-describedby="thread-delete-body"
           className="w-full max-w-sm rounded-lg border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 p-4 shadow-2xl">
        <h2 id="thread-delete-title" className="text-base font-semibold">Delete this chat?</h2>
        <p id="thread-delete-body" className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">
          <span className="font-medium text-neutral-900 dark:text-neutral-100 break-words">{title}</span> and every copy of it
          are removed, so {aname} can't bring it up again. This can't be undone.
        </p>
        {facts === null ? (
          <p className="mt-3 text-xs text-neutral-500">Checking what {aname} learned in it…</p>
        ) : facts === 'unknown' ? (
          <p className="mt-3 text-xs text-neutral-500">Couldn't check what {aname} learned in it: anything learned stays.</p>
        ) : facts.length > 0 && (
          <div className="mt-3 rounded-md border border-neutral-200 dark:border-neutral-700 p-2">
            <p className="text-xs text-neutral-600 dark:text-neutral-400">{aname} learned these in this chat:</p>
            <ul className="mt-1 text-sm list-disc pl-5 max-h-32 overflow-y-auto" data-thread-facts>
              {facts.map((f) => (
                <li key={`${f.store}:${f.key}`} className="break-words">
                  {f.label === 'note' ? f.value : <><span className="text-neutral-500">{f.label}:</span> {f.value}</>}
                </li>
              ))}
            </ul>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={forget} onChange={(e) => setForget(e.target.checked)} data-thread-forget />
              Forget these too
            </label>
          </div>
        )}
        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400" role="alert">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button ref={cancelRef} type="button" disabled={busy} onClick={onClose}
                  className="px-3 py-1.5 max-md:py-2.5 text-sm rounded-md border border-neutral-300 dark:border-neutral-600 hover:bg-neutral-100 dark:hover:bg-neutral-800">
            Cancel
          </button>
          <button type="button" disabled={busy || facts === null} onClick={() => void remove()} data-thread-delete-confirm
                  className="px-3 py-1.5 max-md:py-2.5 text-sm rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-60">
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
