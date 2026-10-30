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
import SignInLink from './SignInLink';
import {
  authedRuntimes, createAgent, fetchParents, freshRuntimes, nameError, openLoginShell, parentChoices, previewName, runtimeLabel,
  type ParentSeat, type RuntimeRow, type SeatKind,
} from '../lib/newAgent';
import { isViewHidden } from '../lib/features';

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
  // The General Manager is a different KIND of seat: tier T0, always-on, the gm prompt — the
  // web equivalent of `orchestra spawn <name> --gm`, which is what docs/INSTALL.md tells you to run.
  const [kind, setKind] = useState<SeatKind>('lead');
  const gm = kind === 'gm';
  // Who the new seat reports to. Never a made-up id: the server refuses anything that is not a
  // registered seat (it used to send 'dashboard', and every Worker was refused).
  const [seats, setSeats] = useState<ParentSeat[]>([]);
  const [parent, setParent] = useState('');
  const [error, setError] = useState('');
  const [created, setCreated] = useState<{ id: string; session: string; runtime?: string } | null>(null);
  const [shell, setShell] = useState<{ session: string; hint: string } | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    setPhase('probing'); setError(''); setCreated(null); setShell(null); setName(''); setTask(''); setRole(''); setKind('lead'); setParent('');
    void fetchParents().then((s) => { setSeats(s); const g = parentChoices('lead', s)[0]; setParent(g ? g.id : ''); });
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
    const r = await createAgent(name, task, runtime || undefined, role, gm, gm ? null : (parent || null));
    if (r.ok && r.id) {
      setCreated({ id: r.id, session: r.session || r.id, runtime: r.runtime });
      setPhase('spawned');
      onCreated?.(r.id);
      return;
    }
    if (r.reason === 'no_authed_runtime') { void probe(); return; }   // logged out between probe and submit
    setError(
      r.reason === 'name_taken' ? `"${clean}" already exists — pick another name.`
      : r.reason === 'unknown_parent' ? 'That lead is not a registered agent any more. Pick another.'
      : r.reason === 'spawn_failed' || r.reason === 'session_missing' ? `The spawn did not complete: ${(r.detail || '').slice(-300)}`
      : r.detail || r.reason || 'Something went wrong.',
    );
    setPhase('name');
  }

  if (!open) return null;

  const authed = authedRuntimes(rows);
  const clean = previewName(name);
  const nameProblem = nameError(name, taken || new Set());
  const choices = parentChoices(kind, seats);
  const needsLead = kind === 'worker';
  const canSubmit = !!clean && !nameProblem && phase !== 'spawning' && (!needsLead || !!parent);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="rounded-xl border border-tone-700 bg-tone-900 shadow-2xl w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="New agent"
      >
        {/* --- probing ------------------------------------------------------------- */}
        {phase === 'probing' && (
          <div className="p-6">
            <h3 className="text-lg font-semibold text-tone-100 mb-1">New Agent</h3>
            <p className="text-sm text-tone-400">Checking which AI providers you are signed in to…</p>
          </div>
        )}

        {/* --- name it ------------------------------------------------------------- */}
        {(phase === 'name' || phase === 'spawning') && (
          <div className="p-6">
            <h3 className="text-lg font-semibold text-tone-100 mb-1">New Agent</h3>
            <p className="text-sm text-tone-400 mb-4">
              {gm
                ? <>The manager seat: always on, tier T0, and it runs on the gm prompt. One per install.</>
                : <>What should it be called? It runs on {runtimeLabel(authed.find((r) => r.id === runtime) || authed[0])}, in a tmux session of its own.</>}
            </p>

            <div className="mb-3 flex gap-2">
              {([
                { v: 'lead', t: 'Lead', d: 'tier T1' },
                { v: 'worker', t: 'Worker', d: 'tier T2, under a lead' },
                { v: 'gm', t: 'General Manager', d: 'tier T0, always on' },
              ] as { v: SeatKind; t: string; d: string }[]).map((k) => {
                const noLead = k.v === 'worker' && parentChoices('worker', seats).length === 0;
                return (
                  <button
                    key={k.v}
                    type="button"
                    disabled={noLead}
                    title={noLead ? 'A worker reports to a lead. Start a lead first.' : undefined}
                    onClick={() => {
                      setKind(k.v);
                      if (k.v === 'gm' && !name.trim()) setName('gm');
                      const first = parentChoices(k.v, seats)[0];
                      setParent(first ? first.id : '');
                    }}
                    aria-pressed={kind === k.v}
                    className={`flex-1 px-3 py-2 rounded-lg border text-left transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${kind === k.v
                      ? 'border-blue-600 bg-blue-600/15 text-tone-100'
                      : 'border-tone-700 bg-tone-950 text-tone-400 hover:text-tone-200'}`}
                  >
                    <div className="text-sm font-medium">{k.t}</div>
                    <div className="text-[11px] text-tone-500">{k.d}</div>
                  </button>
                );
              })}
            </div>

            {kind === 'worker' && (
              <div className="mb-3">
                <label className="block text-xs uppercase tracking-wider text-tone-500 mb-1">Reports to</label>
                <select
                  value={parent}
                  onChange={(e) => setParent(e.target.value)}
                  aria-label="Reports to"
                  className="bg-tone-950 border border-tone-700 text-tone-300 text-sm rounded-lg px-3 py-2"
                >
                  {choices.map((s) => <option key={s.id} value={s.id}>{s.id}{s.tier ? ` (${s.tier})` : ''}</option>)}
                </select>
              </div>
            )}
            {kind === 'lead' && (
              <p className="mb-3 text-xs text-tone-500">
                {choices.length > 0 ? <>Reports to <code className="text-tone-300">{parent || choices[0].id}</code>.</> : <>No manager yet, so it starts on its own.</>}
              </p>
            )}

            <label className="block text-xs uppercase tracking-wider text-tone-500 mb-1">Name</label>
            <input
              ref={nameRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && canSubmit) void submit(); }}
              placeholder="docs writer"
              aria-label="Name"
              className="w-full px-3 py-2 rounded-lg bg-tone-950 border border-tone-700 text-tone-100 placeholder-tone-600 focus:outline-none focus:border-tone-500"
            />
            <div className="mt-1 h-5 text-xs">
              {nameProblem
                ? <span className="text-tone-red-400">{nameProblem}</span>
                : clean
                  ? <span className="text-tone-500">seat + tmux session: <code className="text-tone-300">{clean}</code></span>
                  : null}
            </div>

            <label className="block text-xs uppercase tracking-wider text-tone-500 mt-3 mb-1">Role <span className="normal-case tracking-normal text-tone-600">(optional)</span></label>
            <input
              value={role}
              onChange={(e) => setRole(e.target.value.slice(0, 120))}
              onKeyDown={(e) => { if (e.key === 'Enter' && canSubmit) void submit(); }}
              placeholder="docs writer for this repo"
              aria-label="Role"
              className="w-full px-3 py-2 rounded-lg bg-tone-950 border border-tone-700 text-tone-100 placeholder-tone-600 focus:outline-none focus:border-tone-500"
            />

            <label className="block text-xs uppercase tracking-wider text-tone-500 mt-3 mb-1">First task <span className="normal-case tracking-normal text-tone-600">(optional)</span></label>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              rows={2}
              placeholder="Read the repo and summarize what it does."
              className="w-full px-3 py-2 rounded-lg bg-tone-950 border border-tone-700 text-tone-100 placeholder-tone-600 focus:outline-none focus:border-tone-500 resize-none"
            />

            <div className="mt-3">
              <label className="block text-xs uppercase tracking-wider text-tone-500 mb-1">Runtime</label>
              <select
                value={runtime}
                onChange={(e) => setRuntime(e.target.value)}
                aria-label="Runtime"
                className="bg-tone-950 border border-tone-700 text-tone-300 text-sm rounded-lg px-3 py-2"
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

            {error && <p className="mt-3 text-sm text-tone-red-400 whitespace-pre-wrap">{error}</p>}

            <div className="flex gap-3 justify-end mt-5">
              <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-tone-800 text-tone-300 hover:bg-tone-700 transition-colors">Cancel</button>
              <button
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors disabled:opacity-40"
              >
                {phase === 'spawning' ? 'Starting…' : gm ? 'Create the manager' : 'Create agent'}
              </button>
            </div>
          </div>
        )}

        {/* --- spawned: its terminal, right here ------------------------------------ */}
        {phase === 'spawned' && created && (
          <div className="p-4">
            <div className="flex items-center justify-between mb-3 px-2">
              <div>
                <h3 className="text-lg font-semibold text-tone-100">{created.id} is up</h3>
                <p className="text-xs text-tone-500">registered seat{created.runtime ? ` · ${created.runtime}` : ''} · tmux {created.session}</p>
              </div>
              <div className="flex gap-2">
                {!isViewHidden('/agents') && <button
                  onClick={() => { onClose(); navigate('/agents'); }}
                  className="px-3 py-1.5 text-sm rounded-lg bg-tone-800 text-tone-300 hover:bg-tone-700 transition-colors"
                >
                  Agents list
                </button>}
                <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-lg bg-tone-800 text-tone-300 hover:bg-tone-700 transition-colors">Done</button>
              </div>
            </div>
            <div className="px-2 pb-3">
              <SignInLink session={created.session} />
            </div>
            <div className="h-80 rounded-lg overflow-hidden border border-tone-800">
              <WebTerminal session={created.session} machine="vps" />
            </div>
          </div>
        )}

        {/* --- not signed in: a blank shell that tells them how ---------------------- */}
        {phase === 'login' && shell && (
          <div className="p-4">
            <div className="flex items-center justify-between mb-3 px-2">
              <div>
                <h3 className="text-lg font-semibold text-tone-100">Sign in to a provider first</h3>
                <p className="text-xs text-tone-500">{shell.hint || 'Log in to your agent CLI in this terminal, then come back.'}</p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => { setPhase('probing'); void probe(); }}
                  className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors"
                >
                  I'm signed in now
                </button>
                <button onClick={onClose} className="px-3 py-1.5 text-sm rounded-lg bg-tone-800 text-tone-300 hover:bg-tone-700 transition-colors">Close</button>
              </div>
            </div>
            {/* The CLI prints its sign-in URL wrapped across a dozen lines; this is the one
                thing a person cannot do by hand from a terminal pane. */}
            <div className="px-2 pb-3">
              <SignInLink session={shell.session} />
            </div>
            <div className="h-80 rounded-lg overflow-hidden border border-tone-800">
              <WebTerminal session={shell.session} machine="vps" />
            </div>
          </div>
        )}

        {/* --- could not even open a shell ------------------------------------------ */}
        {phase === 'error' && (
          <div className="p-6">
            <h3 className="text-lg font-semibold text-tone-100 mb-2">Can't start an agent yet</h3>
            <p className="text-sm text-tone-400 whitespace-pre-wrap">{error}</p>
            <div className="flex gap-3 justify-end mt-5">
              <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-tone-800 text-tone-300 hover:bg-tone-700 transition-colors">Close</button>
              <button onClick={() => { setPhase('probing'); void probe(); }} className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors">Try again</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
