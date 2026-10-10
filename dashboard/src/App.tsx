import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DashboardLayout } from './layouts/DashboardLayout';
import { RouteErrorBoundary } from './components/RouteErrorBoundary';
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
import { ASSISTANT_V2_ENABLED } from './lib/assistant/config';
import { indexPage } from './lib/features';
import NotFound from './components/NotFound';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchInterval: 10_000, staleTime: 5_000 } }
});


export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <RouteErrorBoundary label="app">
        <Routes>
          {/* Arturo home is the MAIN page — its own phone shell, no dashboard chrome (T4). A
              deployment without the Arturo service (runtime config features.arturo=false) opens
              the Overview here instead of a home whose every call 404s. */}
          <Route index element={indexPage() === 'arturo' ? <ArturoHome /> : <Navigate to="/overview" replace />} />

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
        </RouteErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
