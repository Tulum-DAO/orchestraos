import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardLayout } from './layouts/DashboardLayout';
import Overview from './pages/Overview';
import Agents from './pages/Agents';
import Tasks from './pages/Tasks';
import ActivityPage from './pages/Activity';
import Voice from './pages/Voice';
import Approvals from './pages/Approvals';
import Workflows from './pages/Workflows';
import Analytics from './pages/Analytics';
import Skills from './pages/Skills';
import Projects from './pages/Projects';
import Insights from './pages/Insights';
import Questionnaires from './pages/Questionnaires';
import CommandCenter from './pages/CommandCenter';
import Inbox from './pages/Inbox';
import RoadmapDetail from './pages/RoadmapDetail';
import ChatHistory from './pages/ChatHistory';
import Assistant from './pages/Assistant';
import AgentPage from './pages/AgentPage';
import ArturoHome from './pages/ArturoHome';
import ConnectScreen from './components/ConnectScreen';
import { useGatewayConfig } from './stores/gatewayConfig';
import { probeSameOriginSession } from './lib/gatewayConnect';
import { ASSISTANT_V2_ENABLED } from './lib/assistant/config';
import { useEffect, useState } from 'react';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchInterval: 10_000, staleTime: 5_000 } }
});

// There was no catch-all, so ANY unmatched path rendered an empty layout instead
// of a 404 — a guessable deep link like /agents/gm silently rendered nothing,
// which is indistinguishable from a page that loaded and had nothing to show
// (gm, 2026-08-19). Same law as the dead attention feed: a failure the operator cannot
// see is worse than one he can. This states the route does not exist; it does
// NOT invent an agent-detail page, which would be a product decision, not a fix.
function NotFound() {
  return (
    <div className="p-8">
      <h1 className="text-lg font-semibold text-neutral-100 mb-2">Page not found</h1>
      <p className="text-sm text-neutral-400 mb-4">
        <code className="text-neutral-300">{window.location.pathname}</code> is not a route in
        this dashboard. Before this message existed, it rendered a blank page.
      </p>
      <a href="/" className="text-sm text-blue-400 hover:underline">Back to Arturo</a>
    </div>
  );
}

export default function App() {
  // Web first-run gate: until the dashboard is pointed at a gateway (URL + token), show the
  // connect screen. Same-origin default means an operator on the gateway host only pastes a
  // token. Unconfigured = same-origin fallback in api.ts, so this is the only thing that gates.
  //
  // Section-B ruling (devex-review, contract owner): the gate applies to unconfigured sessions
  // with NO existing session ONLY. A same-origin session with an existing valid session
  // auto-connects silently — the live connection IS the evidence, and the dashboard on the
  // projector must never regress into a connect screen. So on boot, before gating, we probe the
  // same-origin authenticated endpoint once; a live session marks configured with no screen.
  const configured = useGatewayConfig((s) => s.configured);
  const [autoChecked, setAutoChecked] = useState(false);

  useEffect(() => {
    if (configured) {
      setAutoChecked(true);
      return;
    }
    let cancelled = false;
    probeSameOriginSession().then((live) => {
      if (cancelled) return;
      if (live) useGatewayConfig.getState().markSameOriginConnected();
      setAutoChecked(true);
    });
    return () => {
      cancelled = true;
    };
    // Boot-once: this runs on mount. `configured` flips true via the store after a live probe or
    // an explicit connect, which re-renders and passes the gate below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Neutral splash while the boot probe is in flight — deliberately NOT ConnectScreen, so a
  // same-origin operator never sees even a flash of the connect screen (the ruling's invariant).
  if (!autoChecked) {
    return (
      <div className="min-h-screen bg-neutral-950" aria-busy="true" aria-label="Loading" />
    );
  }
  if (!configured) return <ConnectScreen />;

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Arturo home is the MAIN page — its own phone shell, no dashboard chrome (T4) */}
          <Route index element={<ArturoHome />} />

          {/* Internal dashboard */}
          <Route element={<DashboardLayout />}>
            <Route path="overview" element={<Overview />} />
            <Route path="command-center" element={<CommandCenter />} />
            <Route path="agents" element={<Agents />} />
            <Route path="tasks" element={<Tasks />} />
            <Route path="activity" element={<ActivityPage />} />
            <Route path="voice" element={<Voice />} />
            <Route path="approvals" element={<Approvals />} />
            <Route path="workflows" element={<Workflows />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="skills" element={<Skills />} />
            <Route path="projects" element={<Projects />} />
            <Route path="inbox" element={<Inbox />} />
            <Route path="insights" element={<Insights />} />
            <Route path="questionnaires" element={<Questionnaires />} />
            <Route path="chat-history" element={<ChatHistory />} />
            {/* V2 assistant (B1) — additive, feature-flagged. Rollback: build without VITE_ASSISTANT_V2. */}
            {ASSISTANT_V2_ENABLED && <Route path="assistant" element={<Assistant />} />}
            <Route path="agent/:id?" element={<AgentPage />} />
            <Route path="roadmaps/:projectSlug" element={<RoadmapDetail />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
