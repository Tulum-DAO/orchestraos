/**
 * What an approval card shows under its title, and who it is from.
 * The unified feed sends the asking seat's summary as `description` and the
 * seat as `agent_id`; the cards used to render neither.
 */
export interface ApprovalCardFields {
  title?: string | null;
  description?: string | null;
  agent_id?: string | null;
}

/** The card body: the summary, or '' when it is empty or just repeats the title. */
export function approvalCardBody(item: ApprovalCardFields): string {
  const body = (item.description || '').trim();
  if (!body || body === (item.title || '').trim()) return '';
  return body;
}

/** The asking seat's name, or '' when the row does not name one. */
export function approvalCardAgent(item: ApprovalCardFields): string {
  return (item.agent_id || '').trim();
}
