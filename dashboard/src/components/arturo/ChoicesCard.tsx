import { useState } from 'react';
import { toggleChoice } from '../../lib/arturo';

/** The multi-select card, with options the brain wrote (ask_choices multi). Its picks are local
 *  until Continue: nothing is sent per tap. `note` is the server's own line, shown above. */
export default function ChoicesCard({ options, note, exclusive, onSubmit }: { options: string[]; note?: string; exclusive?: string; onSubmit: (picked: string[]) => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  return (
    <div className="decision-card multi" role="group" aria-label="Pick all that apply">
      {note && <div className="decision-note">{note}</div>}
      {options.map((o) => {
        const on = picked.includes(o);
        return (
          <button key={o} className={`decision-opt${on ? ' is-on' : ''}`} aria-pressed={on}
            onClick={() => setPicked((p) => toggleChoice(options, p, o, exclusive))}>
            {o}<span className="r" />
          </button>
        );
      })}
      <button className="decision-go" disabled={picked.length === 0} onClick={() => onSubmit(picked)}>Continue</button>
      <div className="decision-freetext">✎ Or type your own answer below…</div>
    </div>
  );
}
