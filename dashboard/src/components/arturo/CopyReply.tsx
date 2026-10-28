import { Check, Copy } from 'lucide-react';
import { useCopy } from '../../hooks/useCopy';

/** The copy button under one of Arturo's replies. A tick shows briefly once the words are on the clipboard;
 *  when the browser refuses the clipboard nothing is claimed. */
export default function CopyReply({ text }: { text: string }) {
  const { copied, copy } = useCopy(text);
  return (
    <div className="reply-actions">
      <button type="button" className="call-icon" aria-label={copied ? 'Copied' : 'Copy reply'} title={copied ? 'Copied' : 'Copy'}
              onClick={() => { void copy(); }}>
        {copied ? <Check size={15} /> : <Copy size={15} />}
      </button>
    </div>
  );
}
