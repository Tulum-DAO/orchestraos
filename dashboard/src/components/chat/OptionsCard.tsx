/**
 * OptionsCard — renders numbered options as tappable buttons.
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import type { OptionsEvent } from '../../lib/claude-code-protocol';

interface Props {
  event: OptionsEvent;
  onSelect: (value: string) => void;
}

export default function OptionsCard({ event, onSelect }: Props) {
  const [selected, setSelected] = useState<string | null>(null);

  const handleSelect = (value: string) => {
    if (selected) return;
    setSelected(value);
    onSelect(value);
  };

  return (
    <div className="flex justify-start">
      <div className="flex flex-wrap gap-1.5 max-w-[90%]">
        {event.options.map(opt => (
          <button
            key={opt.value}
            onClick={() => handleSelect(opt.value)}
            disabled={selected !== null}
            className={clsx(
              'text-xs px-3 py-1.5 min-h-[44px] rounded-lg transition-colors text-left',
              selected === opt.value
                ? 'bg-blue-600/20 text-blue-300 ring-1 ring-blue-500/40'
                : selected !== null
                  ? 'bg-neutral-800/50 text-neutral-600 cursor-not-allowed'
                  : 'bg-neutral-800 text-neutral-300 hover:text-neutral-100 hover:bg-neutral-700'
            )}
          >
            <span className="text-neutral-500 mr-1.5 font-mono">{opt.value}.</span>
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
