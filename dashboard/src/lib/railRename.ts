/**
 * railRename.ts — the sidebar's rename field (AgentRail RenameField) as a small state machine, so every rule is
 * here and tested, and the component only forwards events and does what it is told.
 *
 * - Enter saves, and leaving the field (a click anywhere else, Tab) saves too, so a name typed and then clicked
 *   away from reaches the server. Escape cancels.
 * - A name left empty, or the same as before, is never sent: the field just closes.
 * - The window or tab losing focus (switching apps) is not leaving the field: nothing is saved, and the field is
 *   still there when you come back. Every name an agent ever had stays reserved for it, so a half-typed name saved
 *   by accident would be reserved for good.
 * - Once the field has acted, it ignores the blur that follows: Escape never turns into a save, and the blur from
 *   the input going disabled while it saves never sends a second save.
 * - A refused save puts the field back to editing (fix the name, try again). After a refused Enter, the field asks
 *   for focus back, since it lost focus while disabled; after a refused blur, focus stays where the person put it.
 */
export type RenameAction = { kind: 'save'; name: string } | { kind: 'cancel' } | { kind: 'none' };
export type RenameResult = 'close' | 'refocus' | 'stay';
type Phase = 'editing' | 'saving' | 'done';

export class RenameMachine {
  private phase: Phase = 'editing';
  private via: 'enter' | 'blur' = 'enter';
  private readonly initial: string;

  constructor(initial: string) { this.initial = initial; }

  get state(): Phase { return this.phase; }

  /** A key in the field: Enter saves (or closes, when there is nothing to send), Escape cancels, others do nothing. */
  key(key: string, text: string): RenameAction {
    if (this.phase !== 'editing') return { kind: 'none' };
    if (key === 'Escape') { this.phase = 'done'; return { kind: 'cancel' }; }
    if (key === 'Enter') return this.commit(text, 'enter');
    return { kind: 'none' };
  }

  /** The field lost focus. `windowFocused` false: the window or tab lost it (document.hasFocus()), not the field. */
  blur(text: string, windowFocused = true): RenameAction {
    if (this.phase !== 'editing' || !windowFocused) return { kind: 'none' };
    return this.commit(text, 'blur');
  }

  /** The server's answer to the save: close on success; on a refusal, back to editing (refocus after an Enter). */
  result(ok: boolean): RenameResult {
    if (this.phase !== 'saving') return 'stay';
    if (ok) { this.phase = 'done'; return 'close'; }
    this.phase = 'editing';
    return this.via === 'enter' ? 'refocus' : 'stay';
  }

  private commit(text: string, via: 'enter' | 'blur'): RenameAction {
    const name = text.trim();
    if (!name || name === this.initial.trim()) { this.phase = 'done'; return { kind: 'cancel' }; }
    this.phase = 'saving';
    this.via = via;
    return { kind: 'save', name };
  }
}
