/**
 * headlessDev.ts — Dev for a HEADLESS seat: the seat runs no TUI (its supervisor drives a headless CLI), so the
 * terminal has to be switched on: the headless process stops and the seat's session resumes in its pane, and Back
 * to chat reverses it (POST /api/agents/:id/dev {on}). A tmux seat's Dev is the terminal as it always was; none of
 * this applies to it.
 *
 * The rules (pure, so they are tested on their own):
 *  - choosing Dev on an idle seat switches at once ("Switching to the terminal…");
 *  - a page that OPENS in a saved Dev style while the seat is mid-turn shows the chat with "Switches to the terminal
 *    when this turn ends", and switches then: opening a page never interrupts a turn;
 *  - CHOOSING Dev mid-turn asks: "<seat> is working. Switching now would stop this turn." with
 *    [Switch when it finishes] (the default) and [Stop and switch now] (stop the turn, then switch);
 *  - a page that opens in a chat style while the seat's terminal is open leaves it open (only Back to chat chosen
 *    on the page closes it);
 *  - Back to chat shows "Starting…" while the headless process comes back; a terminal still in use says
 *    "The terminal is in use." with [Try again];
 *  - anything else that goes wrong says what, with [Try again].
 * Nothing here forces a switch over a busy seat: [Stop and switch now] stops the turn first, then switches.
 */

export type DevPhase =
  | 'none' | 'switching' | 'waitTurn' | 'askBusy' | 'stopping' | 'starting' | 'terminalBusy' | 'error';

export interface DevInputs {
  /** The seat runs headless (null while not known yet: treated as not headless, today's behaviour). */
  headless: boolean | null;
  /** Its TUI holds the pane now. */
  actualDev: boolean;
  /** The chat style asks for Dev. */
  wantDev: boolean;
  /** Mid-turn (working, or a question waiting): switching would stop it. */
  busy: boolean;
}

export interface DevMemory {
  phase: DevPhase;
  /** How Dev came to be wanted: the page opened in it, or it was chosen here. */
  origin: 'open' | 'choose';
  /** [Switch when it finishes] was pressed. */
  waitChosen: boolean;
  error?: string;
}

export type DevAction = 'none' | 'switchOn' | 'switchOff';

export const INITIAL: DevMemory = { phase: 'none', origin: 'open', waitChosen: false };

const IN_FLIGHT: ReadonlySet<DevPhase> = new Set(['switching', 'starting']);
const BUSY_STATES = new Set(['working', 'thinking', 'waiting_permission', 'waiting', 'stalled']);

/** Mid-turn for a switch: a turn running, or a question it is waiting on. */
export function isBusy(status: string | undefined): boolean {
  return BUSY_STATES.has(String(status || ''));
}

/** What to draw: the terminal only once the seat's TUI holds the pane; until then (and while coming back), the chat. */
export function viewOf(inp: DevInputs): 'dev' | 'chat' {
  if (!inp.headless) return inp.wantDev ? 'dev' : 'chat';
  return inp.wantDev && inp.actualDev ? 'dev' : 'chat';
}

/** One evaluation: the next memory and what to send now. Called whenever an input changes. */
export function step(inp: DevInputs, mem: DevMemory): { mem: DevMemory; action: DevAction } {
  if (!inp.headless || IN_FLIGHT.has(mem.phase)) return { mem: inp.headless ? mem : { ...mem, phase: 'none' }, action: 'none' };
  if (inp.wantDev === inp.actualDev) return { mem: { ...mem, phase: 'none', error: undefined }, action: 'none' };
  if (inp.wantDev) {
    if (mem.phase === 'error') return { mem, action: 'none' };
    if (mem.phase === 'stopping') {
      return inp.busy ? { mem, action: 'none' } : { mem: { ...mem, phase: 'switching' }, action: 'switchOn' };
    }
    if (inp.busy) {
      const wait = mem.origin === 'open' || mem.waitChosen;
      return { mem: { ...mem, phase: wait ? 'waitTurn' : 'askBusy' }, action: 'none' };
    }
    return { mem: { ...mem, phase: 'switching', error: undefined }, action: 'switchOn' };
  }
  if (mem.phase === 'terminalBusy' || mem.phase === 'error') return { mem, action: 'none' };
  // A page that opens in a chat style while the seat's terminal is open (left in Dev elsewhere) leaves it open:
  // only Back to chat chosen here closes it.
  if (mem.origin !== 'choose') return { mem: { ...mem, phase: 'none' }, action: 'none' };
  return { mem: { ...mem, phase: 'starting', error: undefined }, action: 'switchOff' };
}

/** The chat style changed here (not on page open). */
export function styleChanged(mem: DevMemory): DevMemory {
  if (IN_FLIGHT.has(mem.phase)) return { ...mem, origin: 'choose', waitChosen: false };
  return { phase: 'none', origin: 'choose', waitChosen: false };
}

/** [Switch when it finishes]. */
export function chooseWait(mem: DevMemory): DevMemory {
  return { ...mem, phase: 'waitTurn', waitChosen: true };
}

/** [Stop and switch now]: the caller stops the turn; the switch follows once the seat is idle. */
export function chooseStop(mem: DevMemory): DevMemory {
  return { ...mem, phase: 'stopping', waitChosen: false };
}

/** [Try again]. */
export function retry(mem: DevMemory): DevMemory {
  return { ...mem, phase: 'none', error: undefined };
}

export interface SwitchResult { status: number; body: { ok?: boolean; reason?: string; error?: string; state?: string } | null }

/** The route's answer to a switch. `headless` false when the seat turned out not to be headless (404). */
export function afterSwitch(mem: DevMemory, on: boolean, r: SwitchResult): { mem: DevMemory; headless?: false } {
  if (r.status === 200 && r.body?.ok) return { mem: { ...mem, phase: 'none', error: undefined } };
  if (r.status === 404) return { mem: { ...mem, phase: 'none' }, headless: false };
  if (r.status === 409) {
    if (!on) return { mem: { ...mem, phase: 'terminalBusy' } };
    const wait = mem.origin === 'open' || mem.waitChosen;
    return { mem: { ...mem, phase: wait ? 'waitTurn' : 'askBusy' } };
  }
  const why = r.status === 503 ? "The agent's process isn't running."
    : r.status === 0 ? "Couldn't reach the server."
      : `The switch didn't go through${r.body?.reason || r.body?.error ? `: ${r.body.reason || r.body.error}` : '.'}`;
  return { mem: { ...mem, phase: 'error', error: why } };
}

export interface DevBanner {
  text: string;
  tone: 'info' | 'warn';
  /** Buttons, in order; the first is the default. */
  actions: Array<{ id: 'wait' | 'stop' | 'retry'; label: string }>;
  busyIndicator: boolean;
}

/** What the page says for a phase (null: nothing to say). `seatName` comes from the seat's own data. */
export function bannerFor(mem: DevMemory, seatName: string): DevBanner | null {
  switch (mem.phase) {
    case 'switching': return { text: 'Switching to the terminal…', tone: 'info', actions: [], busyIndicator: true };
    case 'waitTurn': return { text: 'Switches to the terminal when this turn ends', tone: 'info', actions: [], busyIndicator: false };
    case 'askBusy': return {
      text: `${seatName} is working. Switching now would stop this turn.`, tone: 'warn', busyIndicator: false,
      actions: [{ id: 'wait', label: 'Switch when it finishes' }, { id: 'stop', label: 'Stop and switch now' }],
    };
    case 'stopping': return { text: 'Stopping this turn, then switching to the terminal…', tone: 'info', actions: [], busyIndicator: true };
    case 'starting': return { text: 'Starting…', tone: 'info', actions: [], busyIndicator: true };
    case 'terminalBusy': return { text: 'The terminal is in use.', tone: 'warn', actions: [{ id: 'retry', label: 'Try again' }], busyIndicator: false };
    case 'error': return { text: mem.error || "The switch didn't go through.", tone: 'warn', actions: [{ id: 'retry', label: 'Try again' }], busyIndicator: false };
    default: return null;
  }
}

/** The note under the Dev option, for a headless seat only. */
export const DEV_NOTE = 'Uses the terminal. Switching back takes a few seconds.';
