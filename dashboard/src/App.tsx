import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardLayout } from './layouts/DashboardLayout';
import { PortalLayout } from './layouts/PortalLayout';
import Overview from './pages/Overview';
import Agents from './pages/Agents';
import Tasks from './pages/Tasks';
import ActivityPage from './pages/Activity';
import Strategy from './pages/Strategy';
import Voice from './pages/Voice';
import Approvals from './pages/Approvals';
import Workflows from './pages/Workflows';
import Analytics from './pages/Analytics';
import Experiments from './pages/Experiments';
import Skills from './pages/Skills';
import Clients from './pages/Clients';
import Projects from './pages/Projects';
import Insights from './pages/Insights';
import Questionnaires from './pages/Questionnaires';
import CommandCenter from './pages/CommandCenter';
import Inbox from './pages/Inbox';
import Learning from './pages/Learning';
import RoadmapDetail from './pages/RoadmapDetail';
import People from './pages/People';
import Portal from './pages/Portal';
import PortalProjects from './pages/PortalProjects';
import PortalEcosystem from './pages/PortalEcosystem';
import Campaigns from './pages/Campaigns';
import ChatHistory from './pages/ChatHistory';
import PortalCampaigns from './pages/PortalCampaigns';
import Assistant from './pages/Assistant';
import AgentPage from './pages/AgentPage';
import { ASSISTANT_V2_ENABLED } from './lib/assistant/config';

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
      <a href="/" className="text-sm text-blue-400 hover:underline">Back to Overview</a>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Internal dashboard */}
          <Route element={<DashboardLayout />}>
            <Route index element={<Overview />} />
            <Route path="command-center" element={<CommandCenter />} />
            <Route path="agents" element={<Agents />} />
            <Route path="tasks" element={<Tasks />} />
            <Route path="activity" element={<ActivityPage />} />
            <Route path="strategy" element={<Strategy />} />
            <Route path="voice" element={<Voice />} />
            <Route path="approvals" element={<Approvals />} />
            <Route path="workflows" element={<Workflows />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="experiments" element={<Experiments />} />
            <Route path="skills" element={<Skills />} />
            <Route path="clients" element={<Clients />} />
            <Route path="people" element={<People />} />
            <Route path="projects" element={<Projects />} />
            <Route path="inbox" element={<Inbox />} />
            <Route path="learning" element={<Learning />} />
            <Route path="insights" element={<Insights />} />
            <Route path="questionnaires" element={<Questionnaires />} />
            <Route path="campaigns" element={<Campaigns />} />
            <Route path="chat-history" element={<ChatHistory />} />
            {/* V2 assistant (B1) — additive, feature-flagged. Rollback: build without VITE_ASSISTANT_V2. */}
            {ASSISTANT_V2_ENABLED && <Route path="assistant" element={<Assistant />} />}
            <Route path="agent/:id?" element={<AgentPage />} />
            <Route path="roadmaps/:projectSlug" element={<RoadmapDetail />} />
            <Route path="*" element={<NotFound />} />
          </Route>

          {/* Client portal — separate layout, no sidebar */}
          <Route path="portal/:clientId" element={<PortalLayout />}>
            <Route index element={<Portal />} />
            <Route path="projects" element={<PortalProjects />} />
            <Route path="ecosystem" element={<PortalEcosystem />} />
            <Route path="campaigns" element={<PortalCampaigns />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
