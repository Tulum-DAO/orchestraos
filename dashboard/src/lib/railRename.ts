/**
 * railRename.ts — what the sidebar's rename field (AgentRail RenameField) does on a key or when it is left.
 *
 * Enter saves and leaving the field (a click anywhere else, Tab) saves too, so a name typed and then clicked away
 * from reaches the server; Escape cancels. A name left empty, or the same as before, is never sent: the field just
 * closes. Once the field has acted (a save on its way, or a cancel), a blur that follows is ignored, so Escape
 * never turns into a save and a save is never sent twice.
 */
export type RenameEvent = 'enter' | 'escape' | 'blur';
/** save: send the trimmed name; cancel: close without sending; none: do nothing (the field already acted). */
export type RenameAction = 'save' | 'cancel' | 'none';

export function renameAction(event: RenameEvent, text: string, initial: string, settled = false): RenameAction {
  if (settled) return 'none';
  if (event === 'escape') return 'cancel';
  const next = text.trim();
  return next && next !== initial.trim() ? 'save' : 'cancel';
}
