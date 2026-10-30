/**
 * RefText — withRefs: plain text with every card id in it drawn as its chip (components/kit/CardChip.tsx), so no message, mail
 * line, receipt or old message ever shows a person an id. Text with none is returned as it is.
 */
import { Fragment, type ReactNode } from 'react';
import CardChip from './CardChip';
import type { Skin } from './Markdown';
import { hasRefs, splitRefs } from '../../lib/cardRefs';

export function withRefs(text: string, skin: Skin = 'bubbles', keyBase = 'r', inert = false): ReactNode {
  if (!hasRefs(text)) return text;
  return splitRefs(text).map((p, i) => (p.t === 'ref'
    ? <CardChip key={`${keyBase}-${i}`} id={p.ref.id} skin={skin} inert={inert} />
    : <Fragment key={`${keyBase}-${i}`}>{p.text}</Fragment>));
}
