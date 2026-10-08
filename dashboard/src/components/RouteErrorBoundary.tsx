import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';

// A render error anywhere under a router used to unmount the WHOLE tree: the screen went black
// and stayed black on every later page until a manual reload (operator finding #9, 2026-10-08).
// This catches it, shows what broke, and offers a reload. Navigating clears the error, so the
// next page renders normally. It RESETS on navigation rather than being keyed on it: a key
// would remount everything inside on every click (the whole layout, at the app level) and lose
// its state. A crash caught DURING a navigation clears at once and the subtree remounts fresh,
// so the operator gets the page; the error is still logged to the console.

type Props = { children: ReactNode; label: string };
type BoundaryProps = Props & { resetKey: string };
type State = { error: Error | null };

class Boundary extends Component<BoundaryProps, State> {
  state: State = { error: null };

  componentDidUpdate(prev: BoundaryProps) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.label}] render error`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="m-6 rounded-lg border border-red-900/60 bg-red-950/30 p-5 text-sm text-neutral-200">
        <p className="font-semibold text-red-300">This page hit an error and could not be shown.</p>
        <p className="mt-2 text-neutral-400">
          Other pages still work: pick one from the menu. Or reload this one.
        </p>
        <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-neutral-500">
          {this.state.error.message}
        </pre>
        <button
          onClick={() => window.location.reload()}
          className="mt-4 rounded-md bg-neutral-800 px-3 py-1.5 text-neutral-100 hover:bg-neutral-700"
        >
          Reload
        </button>
      </div>
    );
  }
}

/** Clears a caught error when the pathname changes; never remounts its children. */
export function RouteErrorBoundary({ children, label }: Props) {
  const location = useLocation();
  return (
    <Boundary resetKey={location.pathname} label={label}>
      {children}
    </Boundary>
  );
}
