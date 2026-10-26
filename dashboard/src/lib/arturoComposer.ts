/**
 * A picked file is already an intentional Arturo request.  Keep that intent
 * sendable when the operator does not add a separate prompt in either composer.
 */
/** The request sent for attachments with nothing typed. Arturo reads it; the operator never typed it, so it
 *  is never shown as their words. */
const FILLER_RE = /^Please review the attached files?\.$/;
export function isAttachmentFiller(text: string): boolean {
  return FILLER_RE.test((text || '').trim());
}

/** What the operator's bubble shows for a send: what they typed, never the filler. */
export function arturoShownText(draft: string): string {
  return draft.trim();
}

export function arturoComposerText(draft: string, attachmentCount: number): string {
  const text = draft.trim();
  if (text) return text;
  if (attachmentCount <= 0) return '';
  return `Please review the attached ${attachmentCount === 1 ? 'file' : 'files'}.`;
}
