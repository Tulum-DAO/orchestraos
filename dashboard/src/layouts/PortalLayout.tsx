import { Outlet, useParams, Link, useLocation } from 'react-router-dom';
import { clsx } from 'clsx';

export function PortalLayout() {
  const { clientId } = useParams();
  const location = useLocation();

  const navItems = [
    { label: 'Dashboard', path: `/portal/${clientId}` },
    { label: 'Projects', path: `/portal/${clientId}/projects` },
    { label: 'Updates', path: `/portal/${clientId}/updates` },
    { label: 'Campaigns', path: `/portal/${clientId}/campaigns` },
    { label: 'Systems', path: `/portal/${clientId}/ecosystem` },
  ];

  function isActive(path: string) {
    if (path === `/portal/${clientId}`) return location.pathname === path;
    return location.pathname.startsWith(path);
  }

  return (
    <div className="min-h-screen bg-white text-neutral-900">
      {/* Header */}
      <header className="border-b border-neutral-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-neutral-900">OrchestraOS</h1>
          <p className="text-xs text-neutral-500">Client Portal — {clientId}</p>
        </div>
        <nav className="flex gap-4 text-sm">
          {navItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={clsx(
                'transition-colors',
                isActive(item.path)
                  ? 'text-neutral-900 font-medium'
                  : 'text-neutral-500 hover:text-neutral-900'
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>
      {/* Content */}
      <main className="max-w-5xl mx-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
