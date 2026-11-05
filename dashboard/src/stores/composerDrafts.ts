/**
 * composerDrafts.ts — what you have typed to an agent but not sent: the text, held pastes and attached files.
 *
 * Kept here, keyed by agent, instead of inside the composer: switching an agent's page to Dev (or to another
 * agent) unmounts the composer, and a draft held in its own state was lost on the way back. In memory only, so a
 * reload still starts empty; File objects cannot be stored anyway.
 */
import { create } from 'zustand';

export interface DraftFile { id: number; file: File; preview: string | null }
export interface DraftPaste { id: number; content: string; lines: number; chars: number }
export interface Draft { text: string; files: DraftFile[]; pastes: DraftPaste[] }

export const EMPTY_DRAFT: Draft = Object.freeze({ text: '', files: [], pastes: [] }) as Draft;

type Update<T> = T | ((prev: T) => T);
const apply = <T,>(u: Update<T>, prev: T): T => (typeof u === 'function' ? (u as (p: T) => T)(prev) : u);

interface DraftStore {
  drafts: Record<string, Draft>;
  update: (agentId: string, change: (d: Draft) => Draft) => void;
}

export const useComposerDrafts = create<DraftStore>((set) => ({
  drafts: {},
  update: (agentId, change) => set((s) => {
    const next = change(s.drafts[agentId] ?? EMPTY_DRAFT);
    const drafts = { ...s.drafts };
    // An emptied draft is dropped, so the store holds only drafts that have something in them.
    if (!next.text && !next.files.length && !next.pastes.length) delete drafts[agentId];
    else drafts[agentId] = next;
    return { drafts };
  }),
}));

export function readDraft(agentId: string): Draft {
  return useComposerDrafts.getState().drafts[agentId] ?? EMPTY_DRAFT;
}

/** Setters with React's shape (a value or an updater), each writing one part of this agent's draft. */
export function draftSetters(agentId: string) {
  const update = useComposerDrafts.getState().update;
  return {
    setText: (u: Update<string>) => update(agentId, (d) => ({ ...d, text: apply(u, d.text) })),
    setFiles: (u: Update<DraftFile[]>) => update(agentId, (d) => ({ ...d, files: apply(u, d.files) })),
    setPastes: (u: Update<DraftPaste[]>) => update(agentId, (d) => ({ ...d, pastes: apply(u, d.pastes) })),
  };
}

/** The next free id in a list of drafted items (ids are only unique within one agent's draft). */
export function nextDraftId(items: readonly { id: number }[]): number {
  return items.reduce((m, x) => Math.max(m, x.id), 0) + 1;
}

/** This agent's draft and its setters, for a component. */
export function useComposerDraft(agentId: string) {
  const draft = useComposerDrafts((s) => s.drafts[agentId] ?? EMPTY_DRAFT);
  return { draft, ...draftSetters(agentId) };
}
