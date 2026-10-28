import { clsx } from 'clsx';
import { Check, Copy } from 'lucide-react';
import { useCopy } from '../hooks/useCopy';

/** A small copy icon for a title bar (a code block's, a file window's): a tick once copied. */
export default function CopyButton({ text, label = 'Copy', size = 13, className }: {
  text: string; label?: string; size?: number; className?: string;
}) {
  const { copied, copy } = useCopy(text);
  return (
    <button type="button" aria-label={copied ? 'Copied' : label} title={copied ? 'Copied' : label}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); void copy(); }}
            className={clsx('hit-44 inline-flex items-center justify-center rounded p-0.5', className)}>
      {copied ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}
