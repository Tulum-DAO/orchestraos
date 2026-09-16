import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { CheckCircle2, Clock, FolderOpen, MessageSquare, Send } from 'lucide-react';

const SAMPLE_PROJECTS = [
  { name: 'Marketing Website', status: 'live', description: 'Main site with CMS integration', lastUpdate: '2 hours ago' },
  { name: 'CRM Integration', status: 'deploying', description: 'Salesforce sync pipeline', lastUpdate: '1 day ago' },
  { name: 'Email Automation', status: 'live', description: 'Drip campaigns and transactional', lastUpdate: '3 days ago' },
];

const SAMPLE_UPDATES = [
  { title: 'Consent checkboxes deployed', time: '2 hours ago', type: 'deploy' },
  { title: 'SMS A2P registration approved', time: '1 day ago', type: 'milestone' },
  { title: 'New landing page variant pushed to staging', time: '2 days ago', type: 'deploy' },
  { title: 'Monthly analytics report generated', time: '5 days ago', type: 'report' },
];

const STATUS_STYLES: Record<string, string> = {
  live: 'bg-green-100 text-green-700',
  deploying: 'bg-amber-100 text-amber-700',
  staging: 'bg-blue-100 text-blue-700',
};

export default function Portal() {
  const { clientId } = useParams();
  const [chatInput, setChatInput] = useState('');

  return (
    <div className="space-y-8">
      {/* Welcome */}
      <div>
        <h2 className="text-2xl font-bold text-neutral-900">Welcome back</h2>
        <p className="text-neutral-500 mt-1">Here is your operations dashboard for <span className="font-medium text-neutral-700">{clientId}</span>.</p>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-sm">
            <FolderOpen size={14} />
            Active Projects
          </div>
          <p className="text-2xl font-bold text-neutral-900 mt-1">3</p>
        </div>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-sm">
            <Clock size={14} />
            Pending Items
          </div>
          <p className="text-2xl font-bold text-neutral-900 mt-1">2</p>
        </div>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-sm">
            <CheckCircle2 size={14} />
            Last Update
          </div>
          <p className="text-2xl font-bold text-neutral-900 mt-1">2h ago</p>
        </div>
      </div>

      {/* Production Projects */}
      <section>
        <h3 className="text-lg font-semibold text-neutral-900 mb-3">Production Projects</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {SAMPLE_PROJECTS.map((project) => (
            <div key={project.name} className="rounded-xl border border-neutral-200 bg-white p-4 hover:border-neutral-300 transition">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-neutral-900">{project.name}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_STYLES[project.status] || 'bg-neutral-100 text-neutral-600'}`}>
                  {project.status}
                </span>
              </div>
              <p className="text-sm text-neutral-500">{project.description}</p>
              <p className="text-xs text-neutral-400 mt-2">Updated {project.lastUpdate}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Recent Updates */}
      <section>
        <h3 className="text-lg font-semibold text-neutral-900 mb-3">Recent Updates</h3>
        <div className="rounded-xl border border-neutral-200 bg-white divide-y divide-neutral-100">
          {SAMPLE_UPDATES.map((update, i) => (
            <div key={i} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-3">
                <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
                <span className="text-sm text-neutral-800">{update.title}</span>
              </div>
              <span className="text-xs text-neutral-400">{update.time}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Chat Section */}
      <section>
        <h3 className="text-lg font-semibold text-neutral-900 mb-3">Talk to your AI assistant</h3>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-5">
          <div className="space-y-3 mb-4">
            <div className="flex gap-3">
              <div className="w-7 h-7 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center shrink-0">
                <MessageSquare size={14} />
              </div>
              <div className="bg-white rounded-xl rounded-tl-none border border-neutral-200 px-4 py-2.5 text-sm text-neutral-700 max-w-md">
                Hi! I can help you check project status, review recent deployments, or answer questions about your systems. What would you like to know?
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Ask about your projects..."
              className="flex-1 rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-sm text-neutral-900 placeholder:text-neutral-400 focus:outline-none focus:border-blue-300 focus:ring-1 focus:ring-blue-200"
            />
            <button className="rounded-lg bg-blue-600 text-white px-4 py-2.5 hover:bg-blue-700 transition flex items-center gap-1.5 text-sm font-medium">
              <Send size={14} />
              Send
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
