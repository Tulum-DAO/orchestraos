/**
 * AssistantInput — minimal converse text input for the V2 assistant.
 *
 * Deliberately NOT the agent ChatInput (that's coupled to tmux inject /
 * inbox / file-upload). Converse just submits text to the streaming hook.
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import { Send, Square } from 'lucide-react';

interface Props {
  onSend: (text: string) => void;
  onCancel: () => void;
  streaming: boolean;
  disabled?: boolean;
}

export default function AssistantInput({ onSend, onCancel, streaming, disabled }: Props) {
  const [text, setText] = useState('');

  const submit = () => {
    const t = text.trim();
    if (!t || streaming || disabled) return;
    onSend(t);
    setText('');
  };

  return (
    <div className="px-3 py-2 border-t border-neutral-800 shrink-0">
      <div className="flex gap-2 items-end">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={streaming ? 'Assistant is responding…' : 'Ask the assistant…'}
          rows={2}
          disabled={disabled}
          className="flex-1 bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-neutral-300 placeholder-neutral-600 resize-none focus:outline-none focus:border-neutral-600 disabled:opacity-50"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {streaming ? (
          <button
            onClick={onCancel}
            className="flex items-center gap-1 px-4 py-2 min-h-[44px] rounded-lg text-sm font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors self-end"
            title="Stop"
          >
            <Square size={13} /> Stop
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!text.trim() || disabled}
            className={clsx(
              'flex items-center gap-1 px-4 py-2 min-h-[44px] rounded-lg text-sm font-medium transition-colors self-end',
              !text.trim() || disabled
                ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed'
                : 'bg-blue-500/15 text-blue-400 hover:bg-blue-500/25',
            )}
          >
            <Send size={14} /> Send
          </button>
        )}
      </div>
      <p className="text-[10px] text-neutral-600 mt-1">⌘/Ctrl+Enter to send · read-only V1</p>
    </div>
  );
}
