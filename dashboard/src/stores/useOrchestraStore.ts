import { create } from 'zustand';

interface ActivityEvent { timestamp: string; agent: string; event: string; detail: string; task_id?: string; }

interface ActiveCall { pmId: string; agentId: string; }

/**
 * 2D Agents View §9/§11: view state kept in ONE place so every surface reads the same
 * selection, the same moment and the same window. §9's goal is that switching views feels
 * like turning the camera on one scene; that only holds if there is a single source.
 *
 * HONEST LIMIT, do not read more into this than it does: there is no 3D view in this
 * codebase (zero react-three-fiber in dashboard/src, nothing named field/3d, `three` absent
 * from package.json). gm saw the 3D surface via an operator screenshot and it is very likely
 * a separate service. So this is the 2D half of the shared state plus the seam a future 3D
 * consumer plugs into — NOT verified sharing, and step 11 is not done.
 */
interface ViewState {
  /** How far back counts, thickness and the ticker look (spec §7). */
  windowHours: number;
  setWindowHours: (h: number) => void;
  /** The moment being viewed as ISO, or null for live. §7's scrubber. */
  asof: string | null;
  setAsof: (iso: string | null) => void;
  /** Which agent's panel is open. */
  selectedAgentId: string | null;
  /** Which connection's conversation is open, as an ordered display pair. */
  selectedConnection: [string, string] | null;
  /** One panel at a time (§3) — these two setters clear each other, deliberately. */
  openAgentPanel: (id: string) => void;
  openConversation: (a: string, b: string) => void;
  closePanel: () => void;
  search: string;
  setSearch: (q: string) => void;
  /** Agents highlighted by picking a repo or prompt in search (spec §8). Distinct from
   *  `selectedAgentId`, which opens a panel — this highlights a SET and opens nothing, which
   *  is what "highlights the agents that use it" actually asks for. `null` means no highlight
   *  at all, deliberately different from an empty array (a repo with zero agents). */
  highlight: { label: string; agentIds: string[] } | null;
  setHighlight: (h: { label: string; agentIds: string[] } | null) => void;
}

interface OrchestraState extends ViewState {
  activity: ActivityEvent[];
  addActivity: (event: ActivityEvent) => void;
  activeCall: ActiveCall | null;
  startCall: (pmId: string, agentId: string) => void;
  endCall: () => void;
  // Legacy compat
  activeCallAgent: string | null;
  setActiveCallAgent: (agent: string | null) => void;
}

export const useOrchestraStore = create<OrchestraState>((set) => ({
  windowHours: 24,
  setWindowHours: (h) => set({ windowHours: h }),
  asof: null,
  setAsof: (iso) => set({ asof: iso }),
  selectedAgentId: null,
  selectedConnection: null,
  openAgentPanel: (id) => set({ selectedAgentId: id, selectedConnection: null }),
  openConversation: (a, b) => set({ selectedConnection: [a, b], selectedAgentId: null }),
  closePanel: () => set({ selectedAgentId: null, selectedConnection: null }),
  search: '',
  setSearch: (q) => set({ search: q }),
  highlight: null,
  setHighlight: (h) => set({ highlight: h }),
  activity: [],
  addActivity: (event) => set((state) => ({ activity: [event, ...state.activity.slice(0, 499)] })),
  activeCall: null,
  startCall: (pmId, agentId) => set({ activeCall: { pmId, agentId }, activeCallAgent: pmId }),
  endCall: () => set({ activeCall: null, activeCallAgent: null }),
  activeCallAgent: null,
  setActiveCallAgent: (agent) => set({ activeCallAgent: agent }),
}));
