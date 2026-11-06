import { useEffect, type Dispatch, type SetStateAction } from 'react';
import { applyThreadEdit, subscribeThreadEdits } from './arturoThreadEdits.ts';

type Titled = { id: string; title: string; title_custom?: boolean };

/** Keep a list of Arturo's chats in step with every rename and delete made anywhere in this tab
 *  (lib/arturoThreadEdits.ts). A list not loaded yet (null) stays so. */
export function useFollowThreadEdits<T extends Titled>(setList: Dispatch<SetStateAction<T[]>> | Dispatch<SetStateAction<T[] | null>>): void {
  useEffect(() => subscribeThreadEdits((e) => {
    (setList as Dispatch<SetStateAction<T[] | null>>)((l) => (l ? applyThreadEdit(l, e) : l));
  }), [setList]);
}
