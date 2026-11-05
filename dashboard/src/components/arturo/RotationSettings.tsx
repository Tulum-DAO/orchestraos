/**
 * RotationSettings — the compaction rows in an agent's Setup (the drawer from the top bar's gear): "Auto-compact" (the
 * main switch, off by default: whether the agent's conversation is compacted on its own), under it "Compact at" (the
 * share of the context window at which that happens, 40–85 %; dimmed while Auto-compact is off, its value kept) and
 * "Show compaction messages" (the chat's "Compacting the chat…" / "The chat was compacted" row). Per agent, read and saved through
 * /api/agents/:id/settings (lib/seatSettings.ts); a change applies from the agent's next turn. Saving, saved and
 * refusals are said in words.
 * Arturo's conversation is not compacted by us yet, so his drawer shows neither unless the server says otherwise.
 * Mount it keyed by the agent, so another agent's drawer starts afresh.
 */
import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Minus, Plus } from 'lucide-react';
import { compactNowView, type CompactAsk } from '../../lib/compactNow';
import { describeSendState, sendToAgent } from '../../lib/agentSend';
import { useCompactionNotices } from '../../stores/compactionNotices';
import { checkCompactAt, fetchSeatSettings, saveSeatSettings, showsRotationSettings, stepCompactAt,
         COMPACT_MAX, COMPACT_MIN, type SeatSettings, type SettingsChange } from '../../lib/seatSettings';
import { Switch } from './PanelRows';
import './rotationSettings.css';

type Status = { kind: 'idle' } | { kind: 'saving' } | { kind: 'saved' } | { kind: 'error'; text: string };

export function RotationSettings({ agentId, isArturo }: { agentId: string; isArturo: boolean }) {
  const [settings, setSettings] = useState<SeatSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const stepTimer = useRef<number | null>(null);
  const saveSeq = useRef(0);
  // "Compact now": the last tap, and the agent's newest compaction notice as its chat shows it (lib/compactNow.ts).
  const [ask, setAsk] = useState<CompactAsk>({ phase: 'idle' });
  const notice = useCompactionNotices((s) => s.byAgent[agentId] ?? null);

  useEffect(() => {
    let alive = true;
    fetchSeatSettings(agentId).then((s) => { if (alive) { setSettings(s); setDraft(String(s.compact_at)); } })
      .catch((e: unknown) => { if (alive) setLoadError(e instanceof Error ? e.message : 'Could not read the settings.'); });
    return () => { alive = false; if (stepTimer.current) window.clearTimeout(stepTimer.current); };
  }, [agentId]);

  // Arturo shows these only once the server says his conversation is compacted too; a failed read shows nothing for him.
  if (isArturo && !showsRotationSettings(true, settings)) return null;
  if (settings && !showsRotationSettings(isArturo, settings)) return null;

  const save = async (change: SettingsChange, before: SeatSettings) => {
    const mine = ++saveSeq.current;
    setStatus({ kind: 'saving' });
    try {
      const saved = await saveSeatSettings(agentId, change);
      if (mine !== saveSeq.current) return;
      const next = saved ?? { ...before, ...change };
      setSettings(next); setDraft(String(next.compact_at));
      setStatus({ kind: 'saved' });
    } catch (e) {
      if (mine !== saveSeq.current) return;
      setSettings(before); setDraft(String(before.compact_at));        // what is really in effect
      setStatus({ kind: 'error', text: e instanceof Error ? e.message : 'Could not save.' });
    }
  };

  /** A switch: shown at once, put back with the reason if the save is refused. */
  const toggle = (field: 'auto_compact' | 'show_compaction') => {
    if (!settings) return;
    const before = settings;
    setSettings({ ...settings, [field]: !settings[field] });
    void save({ [field]: !settings[field] }, before);
  };

  /** A typed value: saved when it is valid and new, else the reason under it. */
  const commit = (text: string) => {
    if (!settings) return;
    const c = checkCompactAt(text);
    if (!c.ok) { setStatus({ kind: 'error', text: c.error }); return; }
    setDraft(String(c.value));
    if (c.value !== settings.compact_at) void save({ compact_at: c.value }, settings);
    else if (status.kind === 'error') setStatus({ kind: 'idle' });
  };

  /** The stepper: shown at once, saved once the taps stop. */
  const step = (by: number) => {
    if (!settings) return;
    const base = checkCompactAt(draft);
    const value = stepCompactAt(base.ok ? base.value : settings.compact_at, by);
    setDraft(String(value));
    if (stepTimer.current) window.clearTimeout(stepTimer.current);
    stepTimer.current = window.setTimeout(() => commit(String(value)), 500);
  };

  const shown = checkCompactAt(draft);
  const guard = settings?.guard_pct ?? 90;
  /** Exactly what typing /compact in the chat sends; a refusal is shown in the server's own words. */
  const compactNow = async () => {
    const at = Date.now();
    setAsk({ phase: 'sending', at });
    const r = await sendToAgent(agentId, { text: '/compact' });
    if (r.httpStatus >= 200 && r.httpStatus < 300) setAsk({ phase: 'sent', at });
    else setAsk({ phase: 'refused', text: describeSendState(r), final: (r as { reason?: string }).reason === 'compact_refused' });
  };

  const auto = settings?.auto_compact ?? false;
  const cv = compactNowView({ canCompact: settings?.compact_now, ask, notice, showNotices: settings?.show_compaction ?? true });
  return (
    <div className="rotation-settings" data-rotation-settings>
      <div className={`panel-field${settings && settings.compact_now === false ? ' is-disabled' : ''}`} data-rotation-compact-now-row>
        <span id={`compact-now-${agentId}`}>Compact now</span>
        <button type="button" className="rotation-compact" aria-describedby={`compact-now-line-${agentId}`}
                disabled={!settings || !cv.enabled} onClick={() => void compactNow()} data-rotation-compact-now>
          {cv.busy ? <LoaderCircle size={14} className="animate-spin" aria-label="working" /> : 'Compact'}
        </button>
      </div>
      {cv.line && (
        <p id={`compact-now-line-${agentId}`} className={`rotation-status rotation-compact-line is-${cv.tone}`}
           role={cv.tone === 'error' ? 'alert' : 'status'} data-rotation-compact-line={cv.tone}>{cv.line}</p>
      )}
      <div className="panel-field">
        <span id={`auto-cmp-${agentId}`}>Auto-compact</span>
        <Switch on={auto} labelledBy={`auto-cmp-${agentId}`} disabled={!settings} onToggle={() => toggle('auto_compact')} data="auto" />
      </div>
      <p className="panel-note rotation-note">You can also type /compact in the chat at any time.</p>
      <div className={`panel-field${auto ? '' : ' is-disabled'}`} data-rotation-compact-row>
        <label htmlFor={`compact-at-${agentId}`}>Compact at</label>
        <span className="rotation-stepper">
          <button type="button" className="rotation-step" aria-label="Lower"
                  disabled={!settings || !auto || (shown.ok && shown.value <= COMPACT_MIN)} onClick={() => step(-1)}><Minus size={14} /></button>
          <span className="rotation-number">
            <input id={`compact-at-${agentId}`} inputMode="numeric" value={draft} disabled={!settings || !auto} data-rotation-compact-at
                   aria-describedby={`compact-note-${agentId}`} aria-invalid={draft !== '' && !shown.ok}
                   onChange={(e) => setDraft(e.target.value)} onBlur={(e) => commit(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') commit((e.target as HTMLInputElement).value); }} />
            <span aria-hidden>%</span>
          </span>
          <button type="button" className="rotation-step" aria-label="Raise"
                  disabled={!settings || !auto || (shown.ok && shown.value >= COMPACT_MAX)} onClick={() => step(1)}><Plus size={14} /></button>
        </span>
      </div>
      <p className={`panel-note rotation-note${auto ? '' : ' is-disabled'}`} id={`compact-note-${agentId}`}>
        {auto
          ? <>Compacts when the conversation fills this share of the context window ({COMPACT_MIN}–{COMPACT_MAX}%). A safety limit at {guard}% can't be changed.</>
          : <>Applies while Auto-compact is on ({COMPACT_MIN}–{COMPACT_MAX}%). A safety limit at {guard}% can't be changed.</>}
      </p>
      <div className="panel-field">
        <span id={`show-cmp-${agentId}`}>Show compaction messages</span>
        <Switch on={settings?.show_compaction ?? true} labelledBy={`show-cmp-${agentId}`} disabled={!settings}
                onToggle={() => toggle('show_compaction')} data="show" />
      </div>
      {loadError ? <p className="panel-error" role="alert">{loadError}</p>
        : status.kind === 'error' ? <p className="panel-error" role="alert" data-rotation-error>{status.text}</p>
        : status.kind === 'saving' ? <p className="rotation-status" role="status">Saving…</p>
        : status.kind === 'saved' ? <p className="rotation-status" role="status" data-rotation-saved>Saved. Applies from the next turn.</p>
        : null}
    </div>
  );
}
