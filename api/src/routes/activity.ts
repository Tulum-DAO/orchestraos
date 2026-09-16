import { Router, type Request, type Response } from 'express';
import { getRecentActivity } from '../services/state-reader.js';
import { addSSEClient } from '../services/activity-stream.js';

const router = Router();

router.get('/', (req: Request, res: Response) => {
  // SSE mode
  if (req.headers.accept?.includes('text/event-stream')) {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    res.write('data: {"type":"connected"}\n\n');
    addSSEClient(res);
    return;
  }

  // JSON mode
  try {
    const limit = parseInt(req.query.limit as string, 10) || 50;
    const agentFilter = req.query.agent as string | undefined;
    const eventFilter = req.query.event as string | undefined;

    let events = getRecentActivity(limit * 5); // over-fetch to account for filtering

    if (agentFilter) {
      events = events.filter((e) => (e.agent as string) === agentFilter);
    }
    if (eventFilter) {
      events = events.filter((e) => (e.event as string) === eventFilter || (e.type as string) === eventFilter);
    }

    events = events.slice(0, limit);
    res.json({ events, total: events.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load activity', detail: String(err) });
  }
});

export default router;
