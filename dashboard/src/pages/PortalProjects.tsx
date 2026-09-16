import { useParams } from 'react-router-dom';

const PROJECTS = [
  {
    name: 'Marketing Website',
    status: 'live',
    description: 'Primary marketing site with CMS, blog, and lead capture forms.',
    stack: ['Next.js', 'Sanity CMS', 'Vercel'],
    lastDeploy: 'Mar 28, 2026',
  },
  {
    name: 'CRM Integration',
    status: 'deploying',
    description: 'Salesforce bi-directional sync with custom field mapping and deduplication.',
    stack: ['Node.js', 'Salesforce API', 'Cloud Run'],
    lastDeploy: 'Mar 27, 2026',
  },
  {
    name: 'Email Automation',
    status: 'live',
    description: 'Automated drip campaigns, transactional emails, and re-engagement flows.',
    stack: ['SendGrid', 'Cloud Functions', 'BigQuery'],
    lastDeploy: 'Mar 25, 2026',
  },
  {
    name: 'Analytics Dashboard',
    status: 'staging',
    description: 'Custom reporting dashboard with real-time KPIs and attribution modeling.',
    stack: ['React', 'Looker', 'BigQuery'],
    lastDeploy: 'Mar 24, 2026',
  },
];

const STATUS_STYLES: Record<string, string> = {
  live: 'bg-green-100 text-green-700',
  deploying: 'bg-amber-100 text-amber-700',
  staging: 'bg-blue-100 text-blue-700',
};

export default function PortalProjects() {
  const { clientId } = useParams();

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-neutral-900">Your Projects</h2>
        <p className="text-neutral-500 mt-1">{PROJECTS.length} projects for {clientId}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {PROJECTS.map((project) => (
          <div
            key={project.name}
            className="rounded-xl border border-neutral-200 bg-white p-5 hover:border-neutral-300 transition"
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-semibold text-neutral-900">{project.name}</h3>
              <span
                className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_STYLES[project.status] || 'bg-neutral-100 text-neutral-600'}`}
              >
                {project.status}
              </span>
            </div>
            <p className="text-sm text-neutral-600 mb-3">{project.description}</p>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {project.stack.map((tech) => (
                <span
                  key={tech}
                  className="text-xs bg-neutral-100 text-neutral-600 px-2 py-0.5 rounded"
                >
                  {tech}
                </span>
              ))}
            </div>
            <p className="text-xs text-neutral-400">Last deploy: {project.lastDeploy}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
