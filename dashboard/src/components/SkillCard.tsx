import { useState } from 'react';
import { X, Plus, Package, Puzzle } from 'lucide-react';
import { clsx } from 'clsx';

interface Plugin {
  id: string;
  name: string;
  marketplace: string;
  version: string;
  description: string;
  installed: boolean;
  skills: { id: string; name: string; description: string; trigger: string }[];
}

interface PluginCardProps {
  plugin: Plugin;
  agentSkills: Record<string, string[]>;
  agents: { id: string; name: string }[];
  onAssign: (skillId: string, agentId: string) => void;
  onUnassign: (skillId: string, agentId: string) => void;
}

const MARKETPLACE_COLORS: Record<string, string> = {
  'superpowers-marketplace': 'bg-purple-500/15 text-purple-400',
  'claude-plugins-official': 'bg-blue-500/15 text-blue-400',
  'claude-code-plugins': 'bg-amber-500/15 text-amber-400',
};

export function PluginCard({ plugin, agentSkills, agents, onAssign, onUnassign }: PluginCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectedSkill, setSelectedSkill] = useState('');

  // Find which agents have any skill from this plugin
  const pluginSkillIds = new Set(plugin.skills.map(s => s.id));
  const agentsWithPlugin: Record<string, string[]> = {};
  for (const [agentId, skills] of Object.entries(agentSkills)) {
    const matching = skills.filter(s => pluginSkillIds.has(s));
    if (matching.length > 0) agentsWithPlugin[agentId] = matching;
  }
  const assignedAgentCount = Object.keys(agentsWithPlugin).length;

  function handleAssign() {
    if (!selectedSkill || !selectedAgent) return;
    onAssign(selectedSkill, selectedAgent);
    setSelectedAgent('');
    setSelectedSkill('');
  }

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden hover:border-neutral-700 transition">
      {/* Plugin header */}
      <button onClick={() => setExpanded(!expanded)} className="w-full p-5 text-left">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-10 h-10 rounded-lg bg-neutral-800 flex items-center justify-center shrink-0">
              <Package size={20} className="text-neutral-400" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-neutral-100 font-semibold truncate">{plugin.name}</span>
                <span className="text-[10px] text-neutral-600">v{plugin.version}</span>
              </div>
              <span className={clsx('text-[10px] font-medium px-1.5 py-0.5 rounded-full', MARKETPLACE_COLORS[plugin.marketplace] || 'bg-neutral-800 text-neutral-500')}>
                {plugin.marketplace.replace('-marketplace', '').replace('claude-', '')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-neutral-500">{plugin.skills.length} skills</span>
            {assignedAgentCount > 0 && (
              <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-green-500/15 text-green-400">
                {assignedAgentCount} agents
              </span>
            )}
          </div>
        </div>
        <p className="text-sm text-neutral-500 mt-2 line-clamp-2">{plugin.description}</p>
      </button>

      {/* Expanded: skills list + agent assignments */}
      {expanded && (
        <div className="border-t border-neutral-800 p-5 space-y-4">
          {/* Skills list */}
          <div>
            <span className="text-[11px] uppercase tracking-wider text-neutral-600 block mb-2">Skills ({plugin.skills.length})</span>
            <div className="space-y-2">
              {plugin.skills.map(skill => (
                <div key={skill.id} className="p-3 rounded-lg bg-neutral-800/50 border border-neutral-800">
                  <div className="flex items-center gap-2">
                    <Puzzle size={14} className="text-neutral-500" />
                    <span className="text-sm font-medium text-neutral-200">{skill.name}</span>
                  </div>
                  <p className="text-xs text-neutral-500 mt-1">{skill.description}</p>
                  {skill.trigger && (
                    <p className="text-[10px] text-neutral-600 mt-1 italic">Trigger: {skill.trigger}</p>
                  )}
                  {/* Agents with this skill */}
                  {Object.entries(agentSkills).filter(([, skills]) => skills.includes(skill.id)).length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {Object.entries(agentSkills).filter(([, skills]) => skills.includes(skill.id)).map(([agentId]) => (
                        <span key={agentId} className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-neutral-700 text-neutral-300">
                          {agentId}
                          <button onClick={(e) => { e.stopPropagation(); onUnassign(skill.id, agentId); }} className="text-neutral-500 hover:text-red-400">
                            <X size={10} />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Assign skill to agent */}
          {plugin.skills.length > 0 && (
            <div className="pt-3 border-t border-neutral-800">
              <span className="text-[11px] uppercase tracking-wider text-neutral-600 block mb-2">Assign to Agent</span>
              <div className="flex items-center gap-2">
                <select value={selectedSkill} onChange={(e) => setSelectedSkill(e.target.value)}
                  className="flex-1 bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-2 py-1.5 focus:outline-none">
                  <option value="">Select skill...</option>
                  {plugin.skills.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}
                  className="flex-1 bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-2 py-1.5 focus:outline-none">
                  <option value="">Select agent...</option>
                  {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
                <button onClick={handleAssign} disabled={!selectedSkill || !selectedAgent}
                  className={clsx('inline-flex items-center gap-1 text-sm font-medium px-3 py-1.5 rounded-lg transition',
                    selectedSkill && selectedAgent ? 'bg-green-600 text-white hover:bg-green-500' : 'bg-neutral-800 text-neutral-600 cursor-not-allowed')}>
                  <Plus size={14} /> Assign
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
