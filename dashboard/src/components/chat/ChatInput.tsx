/**
 * ChatInput — text input with send button, inject/inbox toggle, and file upload.
 */
import { useState, useRef } from 'react';
import { clsx } from 'clsx';
import { Send, Paperclip, X, ClipboardList } from 'lucide-react';
import { injectAgentVerified, messageAgent, type InjectResult } from '../../lib/api';
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
   * (qa-harness.tsx, AgentCard.tsx, ChatView.tsx) — behavior for them is
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
  ) => Promise<{ ok: boolean; note?: string; queued?: boolean; held?: boolean }>;
}

async function uploadImage(file: File): Promise<string> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/uploads', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  const data = await res.json();
  return data.path;
}

export default function ChatInput({ agentId, disabled, placeholder, attachSupported = true, draft, onDraftChange, onSend }: Props) {
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
    if (force && !busy?.attemptText) return;
    logAction(injectMode ? 'chat.inject' : 'chat.message', agentId, text.trim().slice(0, 100));
    setSending(true);
    setResult(null);
    try {
      // Expand any held large pastes into the fenced grammar at their position.
      let messageText = force ? busy!.attemptText : serializeForSend(text).trim();

      if (onSend) {
        // B1 send-bridge seam: the caller (e.g. Composer.tsx's sendToAgent
        // bridge) owns delivery + upload — pass pendingImage through as a
        // raw File attachment (instead of this file's own uploadImage() +
        // inline [IMAGE:]/[FILE:] marker) and let onSend do the upload and
        // P3 state mapping. The internal inject/inbox path below is
        // untouched and only runs when onSend is absent.
        const attachments = !force && pendingImage ? [pendingImage] : [];
        if (!force && pendingImage) clearImage();
        const res = await onSend({ text: messageText, attachments }, { force });
        if (res.ok) {
          setBusy(null);
          setResult(res.note || 'Sent');
          setText('');
          setPastes([]);
          pasteIdRef.current = 1;
        } else if (res.queued || res.held) {
          // Reuse the existing busy/queued affordance below (reason/state/
          // activity/attemptText — same shape the 409 branch already fills).
          setBusy({
            reason: res.note,
            state: res.held ? 'held' : 'queued',
            activity: res.note,
            attemptText: messageText,
          });
          setResult(null);
        } else {
          setResult(res.note || 'Failed');
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
          setResult(null);
        } else if (res.status === 502) {
          setResult('Delivery unverified — try again');
        } else {
          setResult('Failed' + (res.error ? ': ' + res.error : ''));
        }
      } else {
        await messageAgent(agentId, messageText);
        setResult('Sent');
        setText('');
        setPastes([]);
        pasteIdRef.current = 1;
      }
    } catch (err: any) {
      setResult('Error: ' + (err.message || 'unknown'));
    } finally {
      setSending(false);
      setTimeout(() => setResult(null), 3000);
    }
  };

  const canSend = (text.trim() || pendingImage) && !sending && !disabled;

  return (
    <div
      className={clsx('px-3 py-2 border-t shrink-0 transition-colors', isDragging ? 'border-blue-500 bg-blue-500/5' : 'border-neutral-800')}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {pendingImage && (
        <div className="flex items-center gap-2 px-3 py-1.5 mb-1.5 bg-neutral-800 rounded-lg border border-neutral-700">
          {imagePreview
            ? <img src={imagePreview} alt="preview" className="h-10 w-10 rounded object-cover" />
            : <Paperclip size={16} className="text-neutral-400 shrink-0" />
          }
          <span className="text-xs text-neutral-400 truncate flex-1">{pendingImage.name}</span>
          <button
            onClick={clearImage}
            className="text-neutral-500 hover:text-red-400 transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      )}
      {/* Held large pastes — chips show WHAT is attached; the token in the text
          shows WHERE. Only render chips whose token still survives in the text. */}
      {pastes.filter((p) => text.includes(pasteToken(p))).length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-1.5">
          {pastes.filter((p) => text.includes(pasteToken(p))).map((p) => (
            <span key={p.id} className="inline-flex items-center gap-1.5 px-2 py-1 bg-neutral-800 rounded-lg border border-neutral-700 text-xs text-neutral-300">
              <ClipboardList size={13} className="text-neutral-400 shrink-0" />
              Pasted text · {p.lines} lines
              <button onClick={() => removePaste(p.id)} className="text-neutral-500 hover:text-red-400 transition-colors">
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <div className="flex-1 flex items-end gap-1">
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={!attachSupported}
            className={clsx(
              'p-1.5 mb-1.5 rounded transition-colors',
              !attachSupported
                ? 'text-neutral-700 cursor-not-allowed'
                : 'text-neutral-400 hover:text-neutral-200'
            )}
            title={attachSupported ? 'Attach file (image, PDF, CSV, audio…)' : 'Attachments not supported for this agent yet'}
          >
            <Paperclip size={16} />
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
            rows={2}
            disabled={disabled}
            className="flex-1 bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-neutral-300 placeholder-neutral-600 resize-none focus:outline-none focus:border-neutral-600 disabled:opacity-50"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
        </div>
        <div className="flex flex-col gap-1 self-end">
          <button
            onClick={() => handleSend()}
            disabled={!canSend}
            className={clsx(
              'flex items-center gap-1 px-4 py-2 min-h-[44px] rounded-lg text-sm font-medium transition-colors',
              !canSend
                ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed'
                : 'bg-blue-500/15 text-blue-400 hover:bg-blue-500/25'
            )}
          >
            <Send size={14} />
            {sending ? '...' : injectMode ? 'Inject' : 'Send'}
          </button>
          <button
            onClick={() => setInjectMode(!injectMode)}
            className={clsx(
              'text-[10px] px-1.5 py-0.5 rounded transition-colors text-center',
              injectMode ? 'text-amber-400 bg-amber-500/10' : 'text-neutral-600 hover:text-neutral-400'
            )}
          >
            {injectMode ? 'inject' : 'inbox'}
          </button>
        </div>
      </div>
      {busy && (
        <div className="mt-1.5 rounded-lg border border-amber-700/50 bg-amber-500/5 px-2.5 py-1.5">
          <div className="text-[11px] text-amber-300">
            Not delivered — {busy.activity || busy.state || 'agent busy'}
            {busy.state ? <span className="text-neutral-500"> ({busy.state})</span> : null}
          </div>
          {busy.composer_text && (
            <div className="text-[10px] text-neutral-500 mt-0.5 truncate">
              would overwrite typed draft: "{busy.composer_text}"
            </div>
          )}
          {busy.stranded?.text && (
            <div className="text-[10px] text-neutral-500 mt-0.5 truncate">
              agent has an unsent draft: "{busy.stranded.text}"
            </div>
          )}
          <div className="flex items-center gap-2 mt-1">
            {busy.activity !== 'Active turn' ? (
              <button
                onClick={() => handleSend(true)}
                disabled={sending}
                className="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 disabled:opacity-50"
              >
                {busy.composer_text ? 'Overwrite draft & send' : 'Send anyway'}
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
        <span className={clsx('text-[10px] mt-1 block', result === 'Sent' ? 'text-green-500' : 'text-red-400')}>
          {result}
        </span>
      )}
    </div>
  );
}
