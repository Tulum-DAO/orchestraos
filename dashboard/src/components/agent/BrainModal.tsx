import { useState } from 'react';
import { useAgentSettings } from '../../stores/agentSettings';
import { FactsPane } from './FactsPane';

interface BrainModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type Tab = 'facts' | 'brain' | 'settings';

// SVG icons as React components
function FactsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M3 3h18a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm0 2v2h18V5H3zm0 4v2h18V9H3zm0 4v6h18v-6H3z" />
    </svg>
  );
}

function BrainIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="12" r="2" />
      <circle cx="7" cy="7" r="1.5" />
      <circle cx="17" cy="7" r="1.5" />
      <circle cx="7" cy="17" r="1.5" />
      <circle cx="17" cy="17" r="1.5" />
      <line x1="12" y1="12" x2="7" y2="7" stroke="currentColor" strokeWidth="1" />
      <line x1="12" y1="12" x2="17" y2="7" stroke="currentColor" strokeWidth="1" />
      <line x1="12" y1="12" x2="7" y2="17" stroke="currentColor" strokeWidth="1" />
      <line x1="12" y1="12" x2="17" y2="17" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="12" r="2" />
      <path d="M12 1v6m0 6v4m6.5-8.5h-6m-4 0h-6m10.5-4l-4.24 4.24m5.66 5.66l-4.24-4.24m0 5.66l4.24 4.24m-5.66-5.66l4.24 4.24"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
      <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

export function BrainModal({ isOpen, onClose }: BrainModalProps) {
  const [activeTab, setActiveTab] = useState<Tab>('facts');
  const settings = useAgentSettings();

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md mx-4 bg-card rounded-lg border border-border max-h-[80vh] overflow-auto">
        {/* Header */}
        <div className="sticky top-0 bg-card border-b border-border p-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">Brain</h2>
          <button
            onClick={onClose}
            className="text-foreground/60 hover:text-foreground"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Tab buttons */}
        <div className="flex border-b border-border p-2 gap-1">
          <button
            onClick={() => setActiveTab('facts')}
            className={`flex-1 flex items-center justify-center p-2 rounded-lg transition-colors ${
              activeTab === 'facts'
                ? 'bg-muted text-foreground'
                : 'text-foreground/60 hover:bg-muted/50'
            }`}
            aria-label="Facts"
          >
            <FactsIcon className="w-5 h-5" />
          </button>
          <button
            onClick={() => setActiveTab('brain')}
            className={`flex-1 flex items-center justify-center p-2 rounded-lg transition-colors ${
              activeTab === 'brain'
                ? 'bg-muted text-foreground'
                : 'text-foreground/60 hover:bg-muted/50'
            }`}
            aria-label="Second Brain"
          >
            <BrainIcon className="w-5 h-5" />
          </button>
          <button
            onClick={() => setActiveTab('settings')}
            className={`flex-1 flex items-center justify-center p-2 rounded-lg transition-colors ${
              activeTab === 'settings'
                ? 'bg-muted text-foreground'
                : 'text-foreground/60 hover:bg-muted/50'
            }`}
            aria-label="Settings"
          >
            <SettingsIcon className="w-5 h-5" />
          </button>
        </div>

        {/* Tab content */}
        <div className="p-4">
          {activeTab === 'facts' && (
            <div className="space-y-3">
              <FactsPane />
            </div>
          )}

          {activeTab === 'brain' && (
            <div className="space-y-3">
              <iframe
                src="http://localhost:7373/?embed=1&source=facts"
                className="w-full h-96 border border-border rounded-lg"
                title="Second Brain"
                sandbox="allow-same-origin allow-scripts"
              />
              <p className="text-xs text-foreground/50">
                If the graph fails to load, check that the second-brain server is running on :7373
              </p>
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Assistant Name
                </label>
                <input
                  type="text"
                  value={settings.assistantName}
                  onChange={(e) => settings.setAssistantName(e.target.value)}
                  className="w-full px-3 py-2 bg-background border border-border rounded-lg text-foreground text-sm focus:outline-none focus:border-ring"
                  placeholder="Arturo"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Accent Voice Color
                </label>
                <div className="flex gap-2">
                  <input
                    type="color"
                    value={settings.accentVoice}
                    onChange={(e) => settings.setAccentVoice(e.target.value)}
                    className="w-12 h-10 rounded border border-border cursor-pointer"
                  />
                  <input
                    type="text"
                    value={settings.accentVoice}
                    onChange={(e) => settings.setAccentVoice(e.target.value)}
                    className="flex-1 px-3 py-2 bg-background border border-border rounded-lg text-foreground text-sm focus:outline-none focus:border-ring"
                    placeholder="#000000"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Transcript Display
                </label>
                <select
                  value={settings.transcriptMode}
                  onChange={(e) => settings.setTranscriptMode(e.target.value as any)}
                  className="w-full px-3 py-2 bg-background border border-border rounded-lg text-foreground text-sm focus:outline-none focus:border-ring"
                >
                  <option value="all-at-once">All at once</option>
                  <option value="as-spoken">As spoken</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Mic Mode
                </label>
                <select
                  value={settings.micMode}
                  onChange={(e) => settings.setMicMode(e.target.value as any)}
                  className="w-full px-3 py-2 bg-background border border-border rounded-lg text-foreground text-sm focus:outline-none focus:border-ring"
                >
                  <option value="hold">Hold to talk</option>
                  <option value="toggle">Toggle</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
