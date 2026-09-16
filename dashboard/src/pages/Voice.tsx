import { VoiceAgentCard } from '../components/VoiceAgentCard';
import { useVoiceAgents, useSyncPrompts } from '../hooks/useVoiceAgents';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { RefreshCw, Phone } from 'lucide-react';
import { clsx } from 'clsx';

interface VoiceAgent {
  pm_id: string;
  agent_id: string;
  name: string;
  voice: string;
  phone_number: string;
  updated_at: string;
}

export default function Voice() {
  const { data, isLoading } = useVoiceAgents();
  const syncMutation = useSyncPrompts();
  const activeCall = useOrchestraStore((s) => s.activeCall);
  const startCall = useOrchestraStore((s) => s.startCall);

  const agents: VoiceAgent[] = data?.agents ?? [];

  if (isLoading) {
    return <div className="text-neutral-500">Loading voice agents...</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <Phone size={24} />
            Voice Agents
          </h1>
          <p className="text-neutral-500 text-sm mt-1">
            Talk to your GM or any PM directly from the browser
          </p>
        </div>
        <button
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
          className={clsx(
            'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border border-neutral-700 hover:bg-neutral-800 transition-colors',
            syncMutation.isPending && 'opacity-50'
          )}
        >
          <RefreshCw size={14} className={clsx(syncMutation.isPending && 'animate-spin')} />
          Sync Prompts
        </button>
      </div>

      {syncMutation.isSuccess && (
        <div className="mb-4 p-3 rounded-lg bg-green-900/20 border border-green-800 text-green-400 text-sm">
          Prompts synced with live system state. Voice agents now have current data.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {agents
          .sort((a, b) => (a.pm_id === 'gemini-gm' || a.pm_id === 'gm' ? -1 : b.pm_id === 'gemini-gm' || b.pm_id === 'gm' ? 1 : 0))
          .map(agent => (
            <VoiceAgentCard
              key={agent.pm_id}
              pmId={agent.pm_id}
              agentId={agent.agent_id}
              voice={agent.voice}
              updatedAt={agent.updated_at}
              isInCall={activeCall?.pmId === agent.pm_id}
              onCall={() => startCall(agent.pm_id, agent.agent_id)}
            />
          ))}
      </div>
    </div>
  );
}
