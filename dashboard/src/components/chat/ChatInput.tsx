/**
 * ChatInput — the floating pill composer: attach (+), text, inject/inbox toggle, caller slots
 * (model, voice) and a round send button that becomes Stop while the seat is mid-turn.
 */
import { useState, useRef, useLayoutEffect, useEffect, useId, type ReactNode } from 'react';
import { clsx } from 'clsx';
import { ArrowUp, Square, Loader2, Plus, Paperclip, X, ClipboardList, MoreHorizontal, Check } from 'lucide-react';
import { injectAgentVerified, type InjectResult } from '../../lib/api';
import { sendToAgent, isDelivered, isQueued, isHeld, describeSendState } from '../../lib/agentSend';
import { sendPanelHeadline, shouldSendPhoto, clearsComposer, retryText, canForceRetry, composerKeyAction } from '../../lib/composerGate';
import { logAction } from '../../lib/user-actions';
import { isLargePaste, fencePaste } from '../../lib/pastedText';

// A held large paste: kept out of the textarea (which shows a compact token at
// the paste's position) and serialized into the fenced grammar on submit.
interface HeldPaste { id: number; content: string; lines: number; chars: number }
// Token the composer shows in-place for a held paste. Parseable back to the id;
// user can delete it to drop the paste, and it marks WHERE the paste sits.
const pasteToken = (p: HeldPaste) => `〖paste ${p.id} · ${p.lines} lines〗`;
const TOKEN_RE = /〖paste (\d+) · \d+ lines〗/g;

interface Props {
  agentId: string;
  disabled?: boolean;
  placeholder?: string;
  /**
   * Whether file attach is supported for this agent. Attach/upload is ALWAYS
   * allowed regardless of busy state — only the SEND is gated (the gateway 409
   * handles busy). Attach is only disabled for genuinely unsupported targets
   * (machine != vps, SPEC_ios-attach §B.5). Default true.
   */
  attachSupported?: boolean;
  /**
   * B1/B3 controlled-draft seam (Agent Page v1, DEC-1789508247033721) — ALL
   * new props are optional and additive. When BOTH `draft` and
   * `onDraftChange` are supplied, the textarea is controlled by the caller
   * (e.g. Composer.tsx binding it to VoiceControls' onPartial/onFinal)
   * instead of the internal `text` state. Omitted by every other caller
   * (qa-harness.tsx, AgentCard.tsx) — behavior for them is
   * byte-for-byte unchanged.
   */
  draft?: string;
  onDraftChange?: (t: string) => void;
  /**
   * When supplied, handleSend calls this INSTEAD of the internal
   * injectAgentVerified/messageAgent path (e.g. Composer.tsx's
   * sendToAgent bridge). Omitted by every other caller — internal path runs
   * unchanged for them.
   */
  onSend?: (
    payload: { text: string; attachments: File[] },
    opts: { force: boolean }
  ) => Promise<{ ok: boolean; note?: string; queued?: boolean; held?: boolean; refused?: boolean;
    composer_text?: string; stranded?: { text?: string; age_s?: number } }>;
  /** Rendered INSIDE the pill's right cluster, after the delivery mode and before `trailing`
      (the agent page puts the model chip here). Secondary controls belong on this baseline
      rather than stacked underneath it — a second row of controls under Send reads as a
      junk drawer, which is the defect these two slots exist to prevent. */
  leading?: ReactNode;
  /** Rendered inside the pill, immediately before Send/Stop (the agent page's voice controls). */
  trailing?: ReactNode;
  /** May Stop be offered right now? The caller decides with composerGate.canStopTurn (mid-turn
   *  AND no pending menu): Stop presses Esc, and Esc at a prompt ANSWERS it (#334). */
  canStop?: boolean;
  /** Extra rows for the narrow-phone overflow menu (below 480px the delivery mode and the
   *  caller's secondary controls fold into one "more" button so the text gets the row).
   *  `close` dismisses the menu. Callers that pass nothing still get the delivery-mode rows. */
  menuItems?: (close: () => void) => ReactNode;
  /** Interrupt the agent's turn (the same Esc key the Dev-mode ActionBar sends). Absent = no Stop. */
  onStop?: () => Promise<unknown> | void;
}

async function uploadImage(file: File): Promise<string> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/uploads', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  const data = await res.json();
  return data.path;
}

export default function ChatInput({ agentId, disabled, placeholder, attachSupported = true, draft, onDraftChange, onSend, leading, trailing, canStop, onStop, menuItems }: Props) {
  const [internalText, setInternalText] = useState('');
  // Single accessor pair every read/clear/paste/send path goes through, so
  // there is exactly one send path regardless of controlled vs internal
  // mode. `controlled` only turns true when BOTH props are supplied by the
  // caller — omitting either (as the 3 other callers do) keeps this on the
  // internal-state branch, unchanged from before.
  const controlled = draft !== undefined && onDraftChange !== undefined;
  const getText = () => (controlled ? (draft as string) : internalText);
  const setTextAll = (next: string) => {
    if (controlled) onDraftChange!(next);
    else setInternalText(next);
  };
  const text = getText();
  const setText = (updater: string | ((prev: string) => string)) => {
    const prev = getText();
    const next = typeof updater === 'function' ? (updater as (p: string) => string)(prev) : updater;
    setTextAll(next);
  };
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  // Success is a BOOLEAN. The styling below tested `result === 'Sent'`, which held only while
  // every success said exactly that — the durable path also reports "Queued — agent is busy"
  // and "Held — will deliver at the next turn boundary", both SUCCESSES, both rendered RED.
  // Same regression as d3cd511 in AgentCard, missed here. Found by the eslint selector rule
  // added in this commit, in seconds, after a grep and two readings had not.
  const [resultOk, setResultOk] = useState(false);
  const [injectMode, setInjectMode] = useState(true);
  const [busy, setBusy] = useState<{
    reason?: string; state?: string; activity?: string;
    composer_text?: string; stranded?: { text?: string; age_s?: number };
    attemptText: string;
  } | null>(null);
  const [pendingImage, setPendingImage] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const [pastes, setPastes] = useState<HeldPaste[]>([]);
  const pasteIdRef = useRef(1);

  // Intercept a LARGE paste: keep the content as an object, drop a compact token
  // at the caret so its position in the message is preserved, and show a chip.
  // Small pastes fall through to the browser's normal inline paste.
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const clip = e.clipboardData?.getData('text') ?? '';
    if (!clip || !isLargePaste(clip)) return; // small paste → default inline
    e.preventDefault();
    const id = pasteIdRef.current++;
    const held: HeldPaste = { id, content: clip, lines: clip.split('\n').length, chars: clip.length };
    const el = textAreaRef.current;
    const start = el ? el.selectionStart : text.length;
    const end = el ? el.selectionEnd : text.length;
    const token = pasteToken(held);
    const next = text.slice(0, start) + token + text.slice(end);
    setText(next);
    setPastes((prev) => [...prev, held]);
    // restore caret after the token on next tick
    requestAnimationFrame(() => {
      const e2 = textAreaRef.current;
      if (e2) { const pos = start + token.length; e2.selectionStart = e2.selectionEnd = pos; e2.focus(); }
    });
  };

  const removePaste = (id: number) => {
    const p = pastes.find((x) => x.id === id);
    if (p) setText((t) => t.replace(pasteToken(p), ''));
    setPastes((prev) => prev.filter((x) => x.id !== id));
  };

  // Serialize the composed text: expand each surviving token (in message order)
  // into a fenced paste block, renumbering #1..#k by position. Tokens the user
  // deleted are dropped. Returns the agent-facing string.
  const serializeForSend = (raw: string): string => {
    const byId = new Map(pastes.map((p) => [p.id, p]));
    let n = 0;
    return raw.replace(TOKEN_RE, (_m, idStr) => {
      const p = byId.get(Number(idStr));
      if (!p) return '';           // token for a dropped paste
      n += 1;
      return fencePaste(n, p.content);
    });
  };

  const clearImage = () => {
    setPendingImage(null);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImagePreview(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleFileUpload = (file: File) => {
    setPendingImage(file);
    if (file.type.startsWith('image/')) {
      setImagePreview(URL.createObjectURL(file));
    } else {
      setImagePreview(null);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };
  const handleDragLeave = () => setIsDragging(false);
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (!attachSupported) return;
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  };

  // send(force): force re-sends the retained text through the gateway's human
  // override (skips composer/stranded gates; NEVER the active-turn gate).
  const handleSend = async (force = false) => {
    if (disabled) return;
    // On a normal send we compose from the input; on a force retry we reuse the
    // text the gateway just refused (retained in busy.attemptText).
    if (!force && !text.trim() && !pendingImage) return;
    if (force && !canForceRetry(busy?.attemptText, busy?.state, !!pendingImage)) return;
    logAction(injectMode ? 'chat.inject' : 'chat.message', agentId, text.trim().slice(0, 100));
    setSending(true);
    setResultOk(false);
    setResult(null);
    let failed = false;
    try {
      // Expand any held large pastes into the fenced grammar at their position.
      // A forced retry sends what is in the box NOW: the operator may have fixed the text after the
      // refusal, and re-sending the stale attemptText silently discarded that edit (review of #198).
      let messageText = retryText(force, serializeForSend(text).trim(), busy?.attemptText);

      if (onSend) {
        // B1 send-bridge seam: the caller (e.g. Composer.tsx's sendToAgent
        // bridge) owns delivery + upload — pass pendingImage through as a
        // raw File attachment (instead of this file's own uploadImage() +
        // inline [IMAGE:]/[FILE:] marker) and let onSend do the upload and
        // P3 state mapping. The internal inject/inbox path below is
        // untouched and only runs when onSend is absent.
        // A forced retry normally carries text only (the photo of a queued/held send was already
        // uploaded and cleared). A forced retry of a REFUSED send must carry the photo the refusal
        // kept, or the operator's screenshot is silently dropped (review of #198).
        const sendPhoto = shouldSendPhoto(!!pendingImage, force, busy?.state);
        const attachments = sendPhoto ? [pendingImage!] : [];
        // THE PHOTO IS NOT CLEARED UNTIL THE SEND SUCCEEDS (P1 2026-10-06: the API was down for
        // an hour and every send failed; clearing here threw the attachment away on the way to a
        // failure, so the operator lost it and had to re-pick it). Nothing is destroyed before
        // the thing that could fail has not failed.
        const res = await onSend({ text: messageText, attachments }, { force });
        if (res.ok) {
          if (sendPhoto) clearImage();
          setBusy(null);
          setResultOk(true);
          setResult(res.note || 'Sent');
          setText('');
          setPastes([]);
          pasteIdRef.current = 1;
        } else if (res.refused) {
          // NOT delivered: the agent's input box already holds text. Keep the photo (nothing was
          // sent) and offer the explicit overwrite through the same panel as a gateway 409.
          setBusy({
            reason: res.note,
            state: 'stranded',
            activity: res.note,
            attemptText: messageText,
            // SHOW the agent's draft before offering to overwrite it (review of #198): without
            // these the panel said "Send anyway" and a force-send destroyed text nobody had seen.
            composer_text: res.composer_text,
            stranded: res.stranded,
          });
          setResultOk(false);
          setResult(null);
        } else if (res.queued || res.held) {
          // QUEUED/HELD IS AS DELIVERED AS IT GETS, so the photo clears here too. It does not
          // share the failure branch's reason for being kept: the upload and the send both
          // HAPPENED and the attachment reached the server — it is waiting, not lost.
          //
          // Leaving it attached was actively harmful rather than merely confusing. The retry
          // affordance below re-sends with force: true, and `!force && pendingImage` drops the
          // attachment on a forced send — so the thumbnail sat in the composer implying it had
          // not been sent, and the one gesture offered for sending it carried text only.
          if (sendPhoto) clearImage();
          // ...and so does the TEXT. It reached the server; leaving it in the box invited the same
          // instruction to be sent twice, now that the panel rightly offers no "Send anyway"
          // after a send that already landed (review of #198).
          if (clearsComposer(res)) {
            setText('');
            setPastes([]);
            pasteIdRef.current = 1;
          }
          // Reuse the existing busy/queued affordance below (reason/state/
          // activity/attemptText — same shape the 409 branch already fills).
          setBusy({
            reason: res.note,
            state: res.held ? 'held' : 'queued',
            activity: res.note,
            attemptText: messageText,
          });
          setResultOk(false);
          setResult(null);
        } else {
          setResultOk(false);
          setResult(res.note || 'Failed');
          failed = true;
        }
        return;
      }

      if (!force && pendingImage) {
        const filePath = await uploadImage(pendingImage);
        const tag = pendingImage.type.startsWith('image/') ? 'IMAGE' : 'FILE';
        messageText = `[${tag}: ${filePath}]${messageText ? ' ' + messageText : ''}`;
        clearImage();
      }

      if (injectMode) {
        const res: InjectResult = await injectAgentVerified(agentId, messageText, force);
        if (res.status === 200 && res.injected) {
          setBusy(null);
          setResultOk(true);
          setResult('Sent');
          setText('');
          setPastes([]);
          pasteIdRef.current = 1;
        } else if (res.status === 409) {
          // Refused — surface why + retain the text for a possible force retry.
          setBusy({
            reason: res.reason,
            state: res.state,
            activity: res.activity,
            composer_text: res.composer_text,
            stranded: res.stranded,
            attemptText: messageText,
          });
          setResultOk(false);
          setResult(null);
        } else if (res.status === 502) {
          setResultOk(false);
          setResult('Delivery unverified — try again');
        } else {
          setResultOk(false);
          setResult('Failed' + (res.error ? ': ' + res.error : ''));
        }
      } else {
        // Durable path — see the note in AgentCard.tsx. messageAgent() wrote to queue/inbox/,
        // which nothing reads, and still reported success.
        const res2 = await sendToAgent(agentId, { text: messageText });
        if (isDelivered(res2) || isQueued(res2) || isHeld(res2)) {
          setResultOk(true);
          setResult(describeSendState(res2) || 'Sent');
          setText('');
          setPastes([]);
          pasteIdRef.current = 1;
        } else {
          setResultOk(false);
          setResult('Failed' + (res2.error ? ': ' + res2.error : ''));
        }
      }
    } catch (err: any) {
      setResultOk(false);
      setResult('Error: ' + (err.message || 'unknown'));
      failed = true;
    } finally {
      setSending(false);
      // Only a SUCCESS notice is transient. A failure stays until the next attempt — it is the
      // only thing telling the operator their message did not go, and it outlived a 3s timeout
      // by about an hour during the P1.
      if (!failed) setTimeout(() => setResult(null), 3000);
    }
  };

  const canSend = (text.trim() || pendingImage) && !sending && !disabled;
  // The round button is STOP only while the seat is mid-turn with no menu open (the caller's
  // canStop, see composerGate.canStopTurn) AND there is nothing to send: text typed mid-turn is
  // still a send, because the CLI queues it.
  const showStop = !!onStop && !!canStop && !text.trim() && !pendingImage && !sending;

  // Stop presses Esc in the seat's pane. A ref, not state, guards it: two clicks in one frame
  // both read the same `stopping` state, and Esc pressed twice can land on whatever the first
  // one surfaced.
  const stoppingRef = useRef(false);
  const [stopping, setStopping] = useState(false);
  const handleStop = async () => {
    if (!onStop || !canStop || stoppingRef.current) return;
    stoppingRef.current = true;
    setStopping(true);
    logAction('chat.stop', agentId, '');
    try {
      await onStop();
    } catch (err: unknown) {
      setResultOk(false);
      setResult('Stop failed: ' + ((err instanceof Error && err.message) || 'unknown'));
    } finally {
      stoppingRef.current = false;
      setStopping(false);
    }
  };

  // Grow upward with the text (1 line -> ~8, then scroll). Layout effect so dictation and
  // programmatic clears resize before paint.
  useLayoutEffect(() => {
    const el = textAreaRef.current;
    if (!el) return;
    // Empty: one row, so a long placeholder clips instead of growing the pill on a phone.
    if (!text) { el.style.height = ''; return; }
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [text]);

  const heldChips = pastes.filter((p) => text.includes(pasteToken(p)));
  const roundBtn = 'shrink-0 h-9 w-9 rounded-full flex items-center justify-center transition-colors';

  // The narrow-phone overflow menu. Closes on an outside press or Esc; Esc here never reaches
  // the textarea's stop path, because focus is on the menu while it is open.
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const deliveryLabelId = useId();
  const closeMenu = () => setMenuOpen(false);
  const menuItemsEls = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);
  // Arrow keys / Home / End move between the rows (WAI-ARIA menu pattern).
  const onMenuKeyDown = (e: React.KeyboardEvent) => {
    const items = menuItemsEls();
    if (!items.length) return;
    const i = items.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); items[(n + items.length) % items.length].focus(); };
    if (e.key === 'ArrowDown') go(i + 1);
    else if (e.key === 'ArrowUp') go(i - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(items.length - 1);
  };
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    // Esc closes and hands focus back to the trigger. Capture phase + stopPropagation, so it never
    // reaches the textarea's onKeyDown (which would press Esc into the agent: Stop).
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setMenuOpen(false); menuButtonRef.current?.focus(); }
    };
    // Focus the first row on open, so the keyboard lands in the menu.
    requestAnimationFrame(() => menuItemsEls()[0]?.focus());
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey, true);
    // The Arturo pill (z-75, fixed) would sit on top of this menu; arturo.css hides it while open.
    document.documentElement.setAttribute('data-composer-menu', 'open');
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey, true);
      document.documentElement.removeAttribute('data-composer-menu');
    };
  }, [menuOpen]);
  const menuRow = 'w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-left text-foreground hover:bg-muted rounded-lg';

  return (
    <div
      className="px-3 pt-1 pb-3 shrink-0"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Send outcome and the refusal/queued panel sit ABOVE the pill, so the pill never moves
          under the operator's thumb when one appears. */}
      {busy && (
        <div className="mb-1.5 mx-2 rounded-xl border border-amber-700/50 bg-card px-2.5 py-1.5 shadow-sm">
          <div className="text-[11px] text-amber-300">
            {sendPanelHeadline(busy)}
            {busy.state ? <span className="text-neutral-500"> ({busy.state})</span> : null}
          </div>
          {busy.composer_text && (
            <div className="text-[10px] text-neutral-500 mt-0.5 truncate">
              the agent's box already holds: "{busy.composer_text}" — sending now submits it together with yours
            </div>
          )}
          {busy.stranded?.text && (
            <div className="text-[10px] text-neutral-500 mt-0.5 truncate">
              agent has an unsent draft: "{busy.stranded.text}"
            </div>
          )}
          <div className="flex items-center gap-2 mt-1">
            {/* A queued/held send already REACHED the server: offering "Send anyway" would send the
                same instruction twice (review of #198). Only a real refusal gets the force path. */}
            {busy.state === 'queued' || busy.state === 'held' ? null : busy.activity !== 'Active turn' ? (
              <button
                onClick={() => handleSend(true)}
                disabled={sending}
                className="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 disabled:opacity-50"
              >
                {/* the gateway's force APPENDS to the box and submits; it never clears it (review of #198) */}
                {busy.composer_text ? 'Send together with the draft' : 'Send anyway'}
              </button>
            ) : (
              <span className="text-[10px] text-neutral-600">retry when the agent finishes its turn</span>
            )}
            <button
              onClick={() => { setBusy(null); setResult(null); }}
              className="text-[10px] px-2 py-0.5 rounded text-neutral-500 hover:text-neutral-300"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
      {result && (
        <span className={clsx('text-[10px] mb-1 px-3 block', resultOk ? 'text-green-500' : 'text-red-400')}>
          {result}
        </span>
      )}
      {/* ONE FLOATING PILL (Shaw 2026-10-09: "floating pill CLI in chat mode with a model
          selector, microphone, and a round send/stop button and a plus button on the left for
          attaching"). Left: attach. Middle: the text. Right: delivery mode, the caller's slots
          (model, voice), then send/stop. Attachments and held pastes ride INSIDE it, above the
          text, so what is attached sits with what is being sent. */}
      <div
        data-testid="composer-pill"
        className={clsx(
          'rounded-[26px] border bg-card shadow-lg shadow-black/20 transition-colors',
          isDragging ? 'border-blue-500 bg-blue-500/5' : 'border-border focus-within:border-foreground/25'
        )}
      >
        {(pendingImage || heldChips.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2.5">
            {pendingImage && (
              <span className="inline-flex items-center gap-2 max-w-full pl-1 pr-2 py-1 bg-muted rounded-xl border border-border">
                {imagePreview
                  ? <img src={imagePreview} alt="preview" className="h-9 w-9 rounded-lg object-cover" />
                  : <Paperclip size={16} className="text-muted-foreground shrink-0 mx-1" />
                }
                <span className="text-xs text-muted-foreground truncate max-w-[180px]">{pendingImage.name}</span>
                <button onClick={clearImage} aria-label="Remove attachment" className="text-muted-foreground hover:text-red-400 transition-colors">
                  <X size={14} />
                </button>
              </span>
            )}
            {/* Held large pastes — chips show WHAT is attached; the token in the text shows
                WHERE. Only chips whose token still survives in the text. */}
            {heldChips.map((p) => (
              <span key={p.id} className="inline-flex items-center gap-1.5 px-2 py-1 bg-muted rounded-xl border border-border text-xs text-foreground/80">
                <ClipboardList size={13} className="text-muted-foreground shrink-0" />
                Pasted text · {p.lines} lines
                <button onClick={() => removePaste(p.id)} aria-label="Remove pasted text" className="text-muted-foreground hover:text-red-400 transition-colors">
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-end gap-1 p-1.5">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={!attachSupported}
            aria-label="Attach a file"
            className={clsx(
              roundBtn,
              !attachSupported
                ? 'text-muted-foreground/40 cursor-not-allowed'
                : 'text-foreground/70 hover:text-foreground hover:bg-muted'
            )}
            title={attachSupported ? 'Attach file (image, PDF, CSV, audio…)' : 'Attachments not supported for this agent yet'}
          >
            <Plus size={20} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.csv,.txt,.md,.json,.html,.htm,.vtt,.srt,.docx,.xlsx,.pptx,.zip,.m4a,.mp3,.wav,image/png,image/jpeg,image/gif,image/webp,application/pdf,text/csv,application/json,text/html,audio/mpeg,audio/mp4"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFileUpload(file);
            }}
          />
          <textarea
            ref={textAreaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onPaste={handlePaste}
            placeholder={placeholder || 'Message your agent...'}
            rows={1}
            disabled={disabled}
            aria-label="Message"
            className="flex-1 min-w-0 self-center bg-transparent px-1.5 py-2 text-sm leading-5 text-foreground placeholder:text-muted-foreground placeholder:whitespace-nowrap placeholder:text-ellipsis resize-none focus:outline-none disabled:opacity-50"
            onKeyDown={(e) => {
              // BEHAVIOUR CHANGE (2026-10-09): Enter sends, Shift+Enter is a newline. It used to be
              // Cmd/Ctrl+Enter only, which still sends. Touch keyboards keep Enter as a newline.
              const action = composerKeyAction({
                key: e.key, shiftKey: e.shiftKey, metaKey: e.metaKey, ctrlKey: e.ctrlKey,
                isComposing: e.nativeEvent.isComposing,
                coarse: typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches,
                canStop: !!onStop && !!canStop,
              });
              if (action === 'send') { e.preventDefault(); handleSend(); }
              else if (action === 'stop') { e.preventDefault(); void handleStop(); }
            }}
          />
          {/* The delivery mode is a REAL control, not debug output — it chooses between typing
              into the agent's terminal now and queuing to its inbox. Kept, restyled to sit in the
              pill's right cluster without competing with send. */}
          <button
            type="button"
            onClick={() => setInjectMode(!injectMode)}
            title={injectMode
              ? 'Delivering NOW, straight into the agent’s terminal. Click to queue to its inbox instead.'
              : 'Queuing to the agent’s INBOX, read at its next turn. Click to deliver now instead.'}
            className="shrink-0 h-9 px-1.5 text-[11px] rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition-colors max-[479px]:hidden"
          >
            {injectMode ? 'now' : 'inbox'}
          </button>
          {leading}
          {/* BELOW 480px: ONE overflow control instead of the delivery toggle + the caller's
              secondary controls, so the text gets the row (at 390px it showed ~13 characters).
              The non-default "inbox" mode shows on the button itself, so folding it never hides
              where a message will go. */}
          <div ref={menuRef} className="relative shrink-0 min-[480px]:hidden">
            <button
              ref={menuButtonRef}
              type="button"
              onClick={() => setMenuOpen((o) => !o)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label={injectMode ? 'More options' : 'More options (queuing to inbox)'}
              title="More options"
              className={clsx(roundBtn, 'relative text-foreground/70 hover:text-foreground hover:bg-muted', menuOpen && 'bg-muted text-foreground')}
            >
              <MoreHorizontal size={20} />
              {!injectMode && <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-amber-400" aria-hidden />}
            </button>
            {menuOpen && (
              <div role="menu" aria-label="More options" onKeyDown={onMenuKeyDown}
                className="absolute bottom-full right-0 mb-2 w-60 rounded-2xl border border-border bg-card p-1.5 shadow-xl shadow-black/30 z-20">
                <div role="group" aria-labelledby={deliveryLabelId}>
                <div id={deliveryLabelId} role="presentation" className="px-3 pt-1.5 pb-1 text-[11px] text-muted-foreground">Delivery</div>
                <button type="button" role="menuitemradio" aria-checked={injectMode} className={menuRow}
                  onClick={() => { setInjectMode(true); closeMenu(); }}>
                  <Check size={15} className={injectMode ? 'opacity-100' : 'opacity-0'} />
                  <span>Now <span className="text-muted-foreground">· into its terminal</span></span>
                </button>
                <button type="button" role="menuitemradio" aria-checked={!injectMode} className={menuRow}
                  onClick={() => { setInjectMode(false); closeMenu(); }}>
                  <Check size={15} className={!injectMode ? 'opacity-100' : 'opacity-0'} />
                  <span>Inbox <span className="text-muted-foreground">· read next turn</span></span>
                </button>
                </div>
                {menuItems && <div role="separator" className="my-1 border-t border-border" />}
                {menuItems?.(closeMenu)}
              </div>
            )}
          </div>
          {trailing}
          {showStop ? (
            <button
              type="button"
              onClick={() => void handleStop()}
              disabled={stopping}
              aria-label={stopping ? 'Stopping' : 'Stop the agent'}
              title={stopping ? 'Stopping…' : 'Stop — interrupt the agent’s turn (Esc)'}
              className={clsx(roundBtn, 'bg-foreground text-background hover:opacity-90 disabled:opacity-60')}
            >
              {stopping ? <Loader2 size={16} className="animate-spin" /> : <Square size={13} fill="currentColor" />}
            </button>
          ) : (
            /* "Send" is what a person does. "Inject" is plumbing. */
            <button
              type="button"
              onClick={() => handleSend()}
              disabled={!canSend}
              aria-label="Send"
              title="Send (Enter)"
              className={clsx(
                roundBtn,
                !canSend
                  ? 'bg-muted text-muted-foreground cursor-not-allowed'
                  : 'bg-foreground text-background hover:opacity-90'
              )}
            >
              {sending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={18} strokeWidth={2.5} />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
