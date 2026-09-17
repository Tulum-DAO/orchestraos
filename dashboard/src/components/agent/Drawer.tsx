import { Link } from 'react-router-dom';

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

const routes = [
  { path: '/inbox', label: 'Inbox' },
  { path: '/tasks', label: 'Tasks' },
  { path: '/projects', label: 'Projects' },
  { path: '/roadmaps', label: 'Roadmaps' },
];

export function Drawer({ isOpen, onClose }: DrawerProps) {
  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50"
          onClick={onClose}
        />
      )}

      {/* Slide-in panel */}
      <div
        className={`fixed inset-y-0 left-0 z-40 w-64 transform transition-transform duration-200 ease-in-out bg-card border-r border-border safe-top safe-bottom ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="p-4 space-y-2">
          <nav className="space-y-1">
            {routes.map((route) => (
              <Link
                key={route.path}
                to={route.path}
                onClick={onClose}
                className="block px-3 py-2 text-sm rounded-lg text-foreground hover:bg-muted transition-colors"
              >
                {route.label}
              </Link>
            ))}
          </nav>
        </div>
      </div>
    </>
  );
}
