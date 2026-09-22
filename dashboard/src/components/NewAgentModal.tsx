/**
 * NewAgentModal — Overview's "New Agent" button, both halves of it.
 *
 * Signed in to a provider (runtime catalog: installed AND authed):
 *     a modal asking what to call the agent (+ an optional first task, + which runtime
 *     when more than one is signed in) -> POST /api/agents/new -> a REAL registered seat
 *     through spawn-agent.sh, then the seat's terminal right here.
 * Not signed in:
 *     no form at all — POST /api/agents/login-shell opens a blank bash tmux session in
 *     the terminal view with the exact command to run (`claude`, then /login). "I'm
 *     signed in now" re-probes; when a provider answers, it flips to the name modal.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import WebTerminal from './WebTerminal';
import {
  authedRuntimes, createAgent, freshRuntimes, nameError, openLoginShell, previewName, runtimeLabel,
  type RuntimeRow,
} from '../lib/newAgent';

type Phase = 'probing' | 'name' | 'spawning' | 'spawned' | 'login' | 'error';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Names already in use, so a clash is caught before the round trip. */
  taken?: Set<string>;
  /** Called after a seat is created, so the caller can refetch its agent list. */
  onCreated?: (id: string) => void;
}

export default function NewAgentModal({ open, onClose, taken, onCreated }: Props) {
  const [phase, setPhase] = useState<Phase>('probing');
  const [rows, setRows] = useState<RuntimeRow[]>([]);
  const [runtime, setRuntime] = useState<string>('');
  const [name, setName] = useState('');
  const [task, setTask] = useState('');
  const [role, setRole] = useState('');
  const [error, setError] = useState('');
  const [created, setCreated] = useState<{ id: string; session: string; runtime?: string } | null>(null);
  const [shell, setShell] = useState<{ session: string; hint: string } | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    setPhase('probing'); setError(''); setCreated(null); setShell(null); setName(''); setTask(''); setRole('');
    void probe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => { if (phase === 'name') setTimeout(() => nameRef.current?.focus(), 30); }, [phase]);

  async function probe() {
    const probed = await freshRuntimes();
    setRows(probed);
    const authed = authedRuntimes(probed);
    if (authed.length > 0) {
      setRuntime(authed[0].id);
      setPhase('name');
      return;
    }
    const r = await openLoginShell();
    if (!r.ok || !r.session) {
      setError(r.detail || 'Could not open a terminal on the server.');
      setPhase('error');
      return;
    }
    setShell({ session: r.session, hint: r.hint || '' });
    setPhase('login');
  }

  async function submit() {
    const clean = previewName(name);
    if (!clean || phase === 'spawning') return;
    setPhase('spawning'); setError('');
    const r = await createAgent(name, task, runtime || undefined, role);
    if (r.ok && r.id) {
      setCreated({ id: r.id, session: r.session || r.id, runtime: r.runtime });
      setPhase('spawned');
      onCreated?.(r.id);
      return;
    }
    if (r.reason === 'no_authed_runtime') { void probe(); return; }   // logged out between probe and submit
    setError(
      r.reason === 'name_taken' ? `"${clean}" already exists — pick another name.`
      : r.reason === 'spawn_failed' || r.reason === 'session_missing' ? `The spawn did not complete: ${(r.detail || '').slice(-300)}`
      : r.detail || r.reason || 'Something went wrong.',
    );
    setPhase('name');
  }

  if (!open) return null;

  const authed = authedRuntimes(rows);
  const clean = previewName(name);
  const nameProblem = nameError(name, taken || new Set());
  const canSubmit = !!clean && !nameProblem && phase !== 'spawning';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="rounded-xl border border-neutral-700 bg-neutral-900 shadow-2xl w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="New agent"
      >
        {/* --- probing ------------------------------------------------------------- */}
        {phase === 'probing' && (
          <div className="p-6">
            <h3 className="text-lg font-semibold text-neutral-100 mb-1">New Agent</h3>
            <p className="text-sm text-neutral-400">Checking which AI providers you are signed in to…</p>
          </div>
        )}

        {/* --- name it ------------------------------------------------------------- */}
        {(phase === 'name' || phase === 'spawning') && (
          <div className="p-6">
            <h3 className="text-lg font-semibold text-neutral-100 mb-1">New Agent</h3>
            <p className="text-sm text-neutral-400 mb-4">
              What should it be called? It runs on {runtimeLabel(authed.find((r) => r.id === runtime) || authed[0])}, in a tmux session of its own.
            </p>

            <label className="block text-xs uppercase tracking-wider text-neutral-500 mb-1">Name</label>
            <input
              ref={nameRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && canSubmit) void submit(); }}
              placeholder="docs writer"
              className="w-full px-3 py-2 rounded-lg bg-neutral-950 border border-neutral-700 text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-neutral-500"
            />
            <div className="mt-1 h-5 text-xs">
              {nameProblem
                ? <span className="text-red-400">{nameProblem}</span>
                : clean
                  ? <span className="text-neutral-500">seat + tmux session: <code className="text-neutral-300">{clean}</code></span>
                  : null}
            </div>

            <label className="block text-xs uppercase tracking-wider text-neutral-500 mt-3 mb-1">Role <span className="normal-case tracking-normal text-neutral-600">(optional)</span></label>
            <input
              value={role}
              onChange={(e) => setRole(e.target.value.slice(0, 120))}
              onKeyDown={(e) => { if (e.key === 'Enter' && canSubmit) void submit(); }}
              placeholder="docs writer for this repo"
              aria-label="Role"
              className="w-full px-3 py-2 rounded-lg bg-neutral-950 border border-neutral-700 text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-neutral-500"
            />

            <label className="block text-xs uppercase tracking-wider text-neutral-500 mt-3 mb-1">First task <span className="normal-case tracking-normal text-neutral-600">(optional)</span></label>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              rows={2}
              placeholder="Read the repo and summarize what it does."
              className="w-full px-3 py-2 rounded-lg bg-neutral-950 border border-neutral-700 text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-neutral-500 resize-none"
            />

            <div className="mt-3">
              <label className="block text-xs uppercase tracking-wider text-neutral-500 mb-1">Runtime</label>
              <select
                value={runtime}
                onChange={(e) => setRuntime(e.target.value)}
                aria-label="Runtime"
                className="bg-neutral-950 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-3 py-2"
              >
                {rows.map((r) => {
                  const usable = r.installed && r.authed === true;
                  const why = !r.installed ? 'not installed' : r.authed === 'unverified' ? 'sign-in unverified' : 'not signed in';
                  return (
                    <option key={r.id} value={r.id} disabled={!usable}>
                      {runtimeLabel(r)}{usable ? '' : ` — ${why}`}
                    </option>
                  );
                })}
              </select>
            </div>

            {error && <p className="mt-3 text-sm text-red-400 whitespace-pre-wrap">{error}</p>}

            <div className="flex gap-3 justify-end mt-5">
              <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors">Cancel</button>
              <button
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors disabled:opacity-40"
              >
                {phase === 'spawning' ? 'Starting…' : 'Create agent'}
              </button>
            </div>
          </div>
        )}

        {/* --- spawned: its terminal, right here ------------------------------------ */}
        {phase === 'spawned' && created && (
          <div className="p-4">
            <div className="flex items-center justify-between mb-3 px-2">
              <div>
                <h3 className="text-lg font-semibold text-neutral-100">{created.id} is up</h3>
                <p className="text-xs text-neutral-500">registered seat{created.runtime ? ` · ${created.runtime}` : ''} · tmux {created.session}</p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => { onClose(); navigate('/agents'); }}
                  className="px-3 py-1.5 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors"
                >
                  Agents list
                </button>
                <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors">Done</button>
              </div>
            </div>
            <div className="h-80 rounded-lg overflow-hidden border border-neutral-800">
              <WebTerminal session={created.session} machine="vps" />
            </div>
          </div>
        )}

        {/* --- not signed in: a blank shell that tells them how ---------------------- */}
        {phase === 'login' && shell && (
          <div className="p-4">
            <div className="flex items-center justify-between mb-3 px-2">
              <div>
                <h3 className="text-lg font-semibold text-neutral-100">Sign in to a provider first</h3>
                <p className="text-xs text-neutral-500">{shell.hint || 'Log in to your agent CLI in this terminal, then come back.'}</p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => { setPhase('probing'); void probe(); }}
                  className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors"
                >
                  I'm signed in now
                </button>
                <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors">Close</button>
              </div>
            </div>
            <div className="h-80 rounded-lg overflow-hidden border border-neutral-800">
              <WebTerminal session={shell.session} machine="vps" />
            </div>
          </div>
        )}

        {/* --- could not even open a shell ------------------------------------------ */}
        {phase === 'error' && (
          <div className="p-6">
            <h3 className="text-lg font-semibold text-neutral-100 mb-2">Can't start an agent yet</h3>
            <p className="text-sm text-neutral-400 whitespace-pre-wrap">{error}</p>
            <div className="flex gap-3 justify-end mt-5">
              <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors">Close</button>
              <button onClick={() => { setPhase('probing'); void probe(); }} className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors">Try again</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
