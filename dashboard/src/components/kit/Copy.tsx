import { clsx } from 'clsx';
import { Check, Copy as CopyIcon } from 'lucide-react';
import { useCopy } from '../../hooks/useCopy';

/** The thread kit's copy action: a code block's title bar, under a reply, on a card. A tick shows briefly once the
 *  words are on the clipboard; when the browser refuses the clipboard nothing is claimed. A press never reaches
 *  the card or row it sits on. */
export default function Copy({ text, label = 'Copy', title, size = 13, className }: {
  text: string; label?: string; title?: string; size?: number; className?: string;
}) {
  const { copied, copy } = useCopy(text);
  return (
    <button type="button" aria-label={copied ? 'Copied' : label} title={copied ? 'Copied' : (title ?? label)}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); void copy(); }}
            className={clsx('hit-44 inline-flex items-center justify-center rounded p-0.5', className)}>
      {copied ? <Check size={size} /> : <CopyIcon size={size} />}
    </button>
  );
}
