import { VoiceAgentCard } from '../components/VoiceAgentCard';
import { useVoiceAgents } from '../hooks/useVoiceAgents';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { Phone } from 'lucide-react';

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
  const activeCall = useOrchestraStore((s) => s.activeCall);
  const startCall = useOrchestraStore((s) => s.startCall);

  const agents: VoiceAgent[] = data?.agents ?? [];

  if (isLoading) {
    return <div className="text-neutral-500">Loading voice agents...</div>;
  }

  return (
    <div>
      {/* md:mr-28: the layout's fixed top-right Arturo + bell cluster (DashboardLayout) floats over this corner on desktop */}
      <div className="flex items-center justify-between mb-6 md:mr-28">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <Phone size={24} />
            Voice Agents
          </h1>
          <p className="text-neutral-500 text-sm mt-1">
            Talk to your GM or any PM directly from the browser
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {agents
          .sort((a, b) => (a.pm_id === 'gm' ? -1 : b.pm_id === 'gm' ? 1 : 0))
          .map(agent => (
            <VoiceAgentCard
              key={agent.pm_id}
              pmId={agent.pm_id}
              name={agent.name}
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
