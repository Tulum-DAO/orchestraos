import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
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
import ProjectFiles from './pages/ProjectFiles';
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
import { SettingsShell } from './components/settings/SettingsShell';
import { useOpenSettings } from './components/settings/useOpenSettings';
import { backgroundPath, isMacPlatform, isSettingsShortcut, settingsSectionOf, settingsStateOf } from './lib/settingsRoute';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchInterval: 10_000, staleTime: 5_000 } }
});


/** Every route, plus Settings drawn OVER the page it was opened from (the "background location"): while /settings/… is
 *  showing, the routes below keep rendering the page behind it, so opening Settings never unloads that page. */
function AppRoutes() {
  const location = useLocation();
  const section = settingsSectionOf(location.pathname);
  const inSettings = section !== undefined;
  const nav = settingsStateOf(location.state);
  // A deep link (/settings/voice typed or reloaded with no page behind it) shows the home page behind: the index
  // route itself when it is a page, else where it redirects, since a <Navigate> behind Settings would close it.
  const pageLocation = !inSettings ? location : nav.settingsBg ? backgroundPath(nav) : indexPage() === 'arturo' ? '/' : '/overview';
  const openSettings = useOpenSettings();
  // ⌘, on a Mac, Ctrl+, elsewhere.
  useEffect(() => {
    const mac = isMacPlatform(navigator.platform || '');
    const onKey = (e: KeyboardEvent) => {
      if (!isSettingsShortcut(e, mac)) return;
      e.preventDefault();
      if (settingsSectionOf(window.location.pathname) === undefined) openSettings('general');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openSettings]);
  return (
    <>
      <RouteErrorBoundary label="app">
        <Routes location={pageLocation}>
          {/* Internal dashboard */}
          <Route element={<DashboardLayout />}>
            {/* Arturo home is the MAIN page, inside the same shell as every other page so the sidebar
                is there too. A deployment without the Arturo service (runtime config
                features.arturo=false) opens the Overview here instead of a home whose every call 404s. */}
            <Route index element={indexPage() === 'arturo' ? <ArturoHome /> : <Navigate to="/overview" replace />} />
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
            <Route path="projects/:slug/files" element={<ProjectFiles />} />
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
      {/* Its own boundary: a throw inside Settings shows there and never unmounts the app behind it. */}
      {inSettings && <RouteErrorBoundary label="settings"><SettingsShell section={section} /></RouteErrorBoundary>}
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
