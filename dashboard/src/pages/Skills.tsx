import { useState } from 'react';
import { clsx } from 'clsx';
import { useSkills, useAssignSkill, useUnassignSkill } from '../hooks/useSkills';
import { useAgents } from '../hooks/useAgents';
import { PluginCard } from '../components/SkillCard';
import { StatCard } from '../components/StatCard';

type Tab = 'all' | 'plugins' | 'by-agent';

export default function Skills() {
  const { data, isLoading } = useSkills();
  const { data: agentsData } = useAgents();
  const assignMutation = useAssignSkill();
  const unassignMutation = useUnassignSkill();
  const [tab, setTab] = useState<Tab>('all');

  if (isLoading) {
    return <div><h1 className="text-2xl font-bold">Skills</h1><p className="text-neutral-500 mt-2">Loading...</p></div>;
  }

  const plugins = data?.plugins || [];
  const agentSkills: Record<string, string[]> = data?.agent_skills || {};
  const allSkills = data?.all_skills || [];
  const agents = (agentsData?.agents || []).map((a: any) => ({ id: a.id, name: a.name || a.id }));

  const tabs: { key: Tab; label: string }[] = [
    { key: 'all', label: `All Plugins (${plugins.length})` },
    { key: 'plugins', label: `Skills (${allSkills.length})` },
    { key: 'by-agent', label: `By Agent (${Object.keys(agentSkills).length})` },
  ];

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Skills</h1>
        <p className="text-neutral-500 text-sm mt-1">Browse plugins and manage skill assignments across your agent fleet</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard label="Plugins Installed" value={data?.installed_plugins || 0} />
        <StatCard label="Total Skills" value={data?.total_skills || 0} />
        <StatCard label="Agents with Skills" value={data?.agents_with_skills || 0} />
        <StatCard label="Marketplaces" value={3} sub="official, community, superpowers" />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-neutral-800 mb-6">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx('px-4 py-2 text-sm font-medium transition border-b-2 -mb-px',
              tab === t.key ? 'border-neutral-100 text-neutral-100' : 'border-transparent text-neutral-500 hover:text-neutral-300')}>
            {t.label}
          </button>
        ))}
      </div>

      {/* All Plugins tab */}
      {tab === 'all' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {plugins.map((plugin: any) => (
            <PluginCard key={plugin.id} plugin={plugin} agentSkills={agentSkills} agents={agents}
              onAssign={(skillId, agentId) => assignMutation.mutate({ skillId, agentId })}
              onUnassign={(skillId, agentId) => unassignMutation.mutate({ skillId, agentId })} />
          ))}
        </div>
      )}

      {/* Flat skills tab */}
      {tab === 'plugins' && (
        <div className="space-y-2">
          {allSkills.map((skill: any) => (
            <div key={`${skill.plugin_id}-${skill.id}`} className="p-4 rounded-xl border border-neutral-800 bg-neutral-900">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">{skill.name}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-500">{skill.plugin_name}</span>
              </div>
              <p className="text-xs text-neutral-500 mt-1">{skill.description}</p>
              {skill.trigger && <p className="text-[10px] text-neutral-600 mt-1 italic">Trigger: {skill.trigger}</p>}
              {/* Agents with this skill */}
              <div className="flex flex-wrap gap-1 mt-2">
                {Object.entries(agentSkills).filter(([, skills]) => (skills as string[]).includes(skill.id)).map(([agentId]) => (
                  <span key={agentId} className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">{agentId}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* By Agent tab */}
      {tab === 'by-agent' && (
        <div className="space-y-4">
          {Object.entries(agentSkills).sort(([a], [b]) => a.localeCompare(b)).map(([agentId, skills]) => (
            <div key={agentId} className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-7 h-7 rounded-full bg-neutral-700 flex items-center justify-center text-xs font-bold">
                  {agentId[0].toUpperCase()}
                </div>
                <span className="font-medium">{agentId}</span>
                <span className="text-xs text-neutral-500">{(skills as string[]).length} skills</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(skills as string[]).map(skillId => {
                  const skillInfo = allSkills.find((s: any) => s.id === skillId);
                  return (
                    <span key={skillId} className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md bg-neutral-800 text-neutral-300"
                      title={skillInfo?.description || skillId}>
                      {skillInfo?.name || skillId}
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
