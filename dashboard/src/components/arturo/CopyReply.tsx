import { useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';

/** The copy button under one of Arturo's replies. A tick shows briefly once the words are on the clipboard;
 *  when the browser refuses the clipboard nothing is claimed. */
export default function CopyReply({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch { /* the browser refused the clipboard */ }
  };
  return (
    <div className="reply-actions">
      <button type="button" className="call-icon" aria-label={copied ? 'Copied' : 'Copy reply'} title={copied ? 'Copied' : 'Copy'}
              onClick={() => { void copy(); }}>
        {copied ? <Check size={15} /> : <Copy size={15} />}
      </button>
    </div>
  );
}
