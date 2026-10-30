import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Brain, Lightbulb, Check, X, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';
import { clsx } from 'clsx';
import { getAllInsights, respondToInsight, getUserProfile, updateUserProfile } from '../lib/api';
import { operatorUserId } from '../lib/runtimeConfig';

const WEIGHT_KEYS = ['recency', 'frequency', 'active_context', 'time_of_day', 'sequence'] as const;

const WEIGHT_LABELS: Record<string, string> = {
  recency: 'Recency',
  frequency: 'Frequency',
  active_context: 'Active Context',
  time_of_day: 'Time of Day',
  sequence: 'Sequence',
};

export default function Insights() {
  const queryClient = useQueryClient();

  const { data: insights, isLoading: insightsLoading } = useQuery({
    queryKey: ['all-insights'],
    queryFn: () => getAllInsights(),
    staleTime: 10_000,
  });

  const { data: profile, isLoading: profileLoading } = useQuery({
    queryKey: ['user-profile'],
    queryFn: () => getUserProfile(),
    staleTime: 10_000,
  });

  const respond = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'accept' | 'dismiss' }) =>
      respondToInsight(operatorUserId(), id, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['all-insights'] });
      queryClient.invalidateQueries({ queryKey: ['pending-insights'] });
      queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    },
  });

  const updateProfile = useMutation({
    mutationFn: (updates: any) => updateUserProfile(operatorUserId(), updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    },
  });

  const deleteRule = useMutation({
    mutationFn: (ruleIndex: number) => {
      const rules = [...(profile?.coaching_rules ?? [])];
      rules.splice(ruleIndex, 1);
      return updateUserProfile(operatorUserId(), { coaching_rules: rules });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    },
  });

  const toggleRule = useMutation({
    mutationFn: (ruleIndex: number) => {
      const rules = [...(profile?.coaching_rules ?? [])].map((r: any, i: number) =>
        i === ruleIndex ? { ...r, enabled: !r.enabled } : r
      );
      return updateUserProfile(operatorUserId(), { coaching_rules: rules });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    },
  });

  if (insightsLoading || profileLoading) {
    return <p className="text-tone-500 p-8">Loading...</p>;
  }

  const coachingRules = profile?.coaching_rules ?? [];
  const scoringWeights = profile?.scoring_weights ?? {};
  const allInsights = insights ?? [];
  const pending = allInsights.filter((i: any) => i.status === 'pending');
  const history = allInsights.filter((i: any) => i.status === 'accepted' || i.status === 'dismissed');

  const handleWeightChange = (key: string, value: number) => {
    updateProfile.mutate({
      scoring_weights: { ...scoringWeights, [key]: value },
    });
  };

  return (
    <div className="p-6 space-y-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Brain className="w-7 h-7 text-tone-purple-400" />
        <h1 className="text-2xl font-bold text-foreground">Insights</h1>
      </div>

      {/* Active Coaching Rules */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-tone-200">Active Coaching Rules</h2>
        {coachingRules.length === 0 ? (
          <p className="text-sm text-tone-500">No coaching rules configured.</p>
        ) : (
          <div className="space-y-2">
            {coachingRules.map((rule: any, idx: number) => (
              <div
                key={idx}
                className={clsx(
                  'bg-tone-800 border rounded-lg p-4 flex items-start gap-3',
                  rule.enabled !== false ? 'border-tone-700' : 'border-tone-800 opacity-60'
                )}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-tone-200">{rule.description}</p>
                  <div className="mt-1.5 flex items-center gap-3 text-xs text-tone-500">
                    {rule.type && (
                      <span className="px-2 py-0.5 rounded bg-tone-700 text-tone-300">
                        {rule.type}
                      </span>
                    )}
                    {rule.created_at && (
                      <span>{new Date(rule.created_at).toLocaleDateString()}</span>
                    )}
                    {rule.confidence != null && (
                      <span>Confidence: {(rule.confidence * 100).toFixed(0)}%</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => toggleRule.mutate(idx)}
                    disabled={toggleRule.isPending}
                    className="p-1.5 text-tone-400 hover:text-tone-white transition-colors"
                    title={rule.enabled !== false ? 'Disable' : 'Enable'}
                  >
                    {rule.enabled !== false ? (
                      <ToggleRight className="w-5 h-5 text-tone-green-400" />
                    ) : (
                      <ToggleLeft className="w-5 h-5" />
                    )}
                  </button>
                  <button
                    onClick={() => deleteRule.mutate(idx)}
                    disabled={deleteRule.isPending}
                    className="p-1.5 text-tone-500 hover:text-tone-red-400 transition-colors"
                    title="Delete rule"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Pending Insights */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-tone-200">Pending Insights</h2>
          {pending.length > 0 && (
            <span className="bg-amber-600 text-white text-xs font-bold px-2 py-0.5 rounded-full">
              {pending.length}
            </span>
          )}
        </div>
        {pending.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-tone-500">
            <Lightbulb className="w-10 h-10 mb-2 text-tone-600" />
            <p className="text-sm">No pending insights</p>
          </div>
        ) : (
          <div className="space-y-2">
            {pending.map((insight: any) => (
              <div
                key={insight.id}
                className="bg-tone-800 border border-tone-700 rounded-lg p-4 flex items-start gap-3"
              >
                <Lightbulb className="w-5 h-5 text-tone-amber-400 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-tone-200">
                    {insight.description || insight.text}
                  </p>
                  {insight.confidence != null && (
                    <p className="mt-1 text-xs text-tone-500">
                      Confidence: {(insight.confidence * 100).toFixed(0)}%
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => respond.mutate({ id: insight.id, action: 'accept' })}
                    disabled={respond.isPending}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md bg-green-600/20 text-tone-green-400 hover:bg-green-600/30 transition-colors disabled:opacity-50"
                  >
                    <Check className="w-3.5 h-3.5" />
                    Accept
                  </button>
                  <button
                    onClick={() => respond.mutate({ id: insight.id, action: 'dismiss' })}
                    disabled={respond.isPending}
                    className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md bg-tone-700 text-tone-400 hover:bg-tone-600 hover:text-tone-300 transition-colors disabled:opacity-50"
                  >
                    <X className="w-3.5 h-3.5" />
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* History */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-tone-200">History</h2>
        {history.length === 0 ? (
          <p className="text-sm text-tone-500">No resolved insights yet.</p>
        ) : (
          <div className="space-y-2">
            {history.map((insight: any) => (
              <div
                key={insight.id}
                className="bg-tone-900 border border-tone-800 rounded-lg p-4 flex items-start gap-3 opacity-70"
              >
                <Lightbulb className="w-5 h-5 text-tone-600 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-tone-400">
                    {insight.description || insight.text}
                  </p>
                  <div className="mt-1 flex items-center gap-3 text-xs text-tone-600">
                    <span
                      className={clsx(
                        'px-2 py-0.5 rounded',
                        insight.status === 'accepted'
                          ? 'bg-tone-green-900/30 text-green-500'
                          : 'bg-tone-800 text-tone-500'
                      )}
                    >
                      {insight.status}
                    </span>
                    {insight.responded_at && (
                      <span>{new Date(insight.responded_at).toLocaleDateString()}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Scoring Weights */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-tone-200">Scoring Weights</h2>
        <div className="bg-tone-800 border border-tone-700 rounded-lg p-5 space-y-5">
          {WEIGHT_KEYS.map((key) => {
            const value = scoringWeights[key] ?? 0.5;
            return (
              <div key={key} className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-sm text-tone-300">{WEIGHT_LABELS[key]}</label>
                  <span className="text-xs text-tone-500 tabular-nums">
                    {value.toFixed(2)}
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={value}
                  onChange={(e) => handleWeightChange(key, parseFloat(e.target.value))}
                  className="w-full h-1.5 rounded-full appearance-none bg-tone-700 accent-purple-500 cursor-pointer"
                />
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
