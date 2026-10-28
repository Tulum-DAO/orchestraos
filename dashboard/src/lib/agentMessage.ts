/**
 * agentMessage.ts — the rules behind a "Message from <sender>" line in an agent's chat
 * (components/chat/AgentMessage.tsx), kept here so they can be tested without a browser.
 */
export interface AgentLike {
  id: string;
  tmux_session?: string;
  display_name?: string;
  full_name?: string;
  name?: string;
  project?: { slug: string } | null;
}

const base = (id: string) => id.replace(/-(?:gen|g)\d+$/, '');

/** The sender's agent record, by its id, its session, or its seat (a later generation of it). None for a
 *  service such as approval-loop, which has no face and no chat to open. */
export function senderAgent<T extends AgentLike>(agents: T[], sender: string): T | undefined {
  return agents.find((a) => a.id === sender)
    ?? agents.find((a) => a.tmux_session === sender)
    ?? agents.find((a) => base(a.id) === base(sender));
}

/** The name the line shows, by the app's name rules: the short name when the sender is in the same project as
 *  the agent whose chat this is, its full name ("Project · name") anywhere else; the raw id for a service. */
export function senderName(sender: AgentLike | undefined, raw: string, viewer: AgentLike | undefined): string {
  if (!sender) return raw;
  const sameProject = !!sender.project && !!viewer?.project && sender.project.slug === viewer.project.slug;
  return (sameProject ? sender.display_name : sender.full_name) || sender.display_name || sender.name || sender.id;
}

