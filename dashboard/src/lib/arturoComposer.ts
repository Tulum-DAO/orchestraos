/**
 * A picked file is already an intentional Arturo request.  Keep that intent
 * sendable when the operator does not add a separate prompt in either composer.
 */
export function arturoComposerText(draft: string, attachmentCount: number): string {
  const text = draft.trim();
  if (text) return text;
  if (attachmentCount <= 0) return '';
  return `Please review the attached ${attachmentCount === 1 ? 'file' : 'files'}.`;
}
