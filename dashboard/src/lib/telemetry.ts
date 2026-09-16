const BASE = '/api';
const USER_ID = 'operator';

interface TelemetryEvent {
  action: string;
  agent_id?: string;
  detail?: string;
  element?: string;
  dwell_ms?: number;
  session_context?: Record<string, any>;
}

let eventQueue: TelemetryEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function flush() {
  if (eventQueue.length === 0) return;
  const batch = [...eventQueue];
  eventQueue = [];
  for (const event of batch) {
    fetch(`${BASE}/adaptive/${USER_ID}/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(event),
    }).catch(() => {});
  }
}

export function trackEvent(event: TelemetryEvent) {
  eventQueue.push(event);
  if (!flushTimer) {
    flushTimer = setTimeout(() => {
      flush();
      flushTimer = null;
    }, 2000);
  }
}

export function initAutoDiscovery() {
  document.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;

    // Check for data-track attribute first
    const tracked = target.closest('[data-track]') as HTMLElement | null;
    if (tracked) {
      const agentCard = tracked.closest('[data-agent-id]') as HTMLElement | null;
      trackEvent({
        action: 'button.click',
        agent_id: agentCard?.dataset.agentId,
        element: tracked.dataset.track || tracked.textContent?.slice(0, 50) || 'unknown',
      });
      return;
    }

    // Auto-capture clicks on buttons/links within agent cards
    const clickable = target.closest('button, a, [role="button"]') as HTMLElement | null;
    const agentCard = target.closest('[data-agent-id]') as HTMLElement | null;
    if (clickable && agentCard) {
      trackEvent({
        action: 'button.click',
        agent_id: agentCard.dataset.agentId,
        element: clickable.textContent?.trim().slice(0, 50) || 'unknown',
      });
    }
  });
}

// Card dwell tracking
const dwellTimers = new Map<string, number>();

export function trackCardOpen(agentId: string) {
  trackEvent({ action: 'card.open', agent_id: agentId });
  dwellTimers.set(agentId, Date.now());
}

export function trackCardClose(agentId: string) {
  const openTime = dwellTimers.get(agentId);
  if (openTime) {
    trackEvent({
      action: 'card.dwell',
      agent_id: agentId,
      dwell_ms: Date.now() - openTime,
    });
    dwellTimers.delete(agentId);
  }
}

export function trackViewSwitch(view: string) {
  trackEvent({ action: 'view.switch', detail: view });
}
