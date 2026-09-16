import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Lightbulb, Check, X } from 'lucide-react';
import { getPendingInsights, respondToInsight } from '../lib/api';

export default function CoachingToast() {
  const queryClient = useQueryClient();
  const [visible, setVisible] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const { data: insights } = useQuery({
    queryKey: ['pending-insights'],
    queryFn: () => getPendingInsights(),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  // Pick the oldest pending insight with confidence > 0.7
  const insight = (insights ?? []).find(
    (i: any) => i.confidence != null ? i.confidence > 0.7 : true
  );

  // Show/hide with animation
  useEffect(() => {
    if (insight && !dismissed) {
      const timer = setTimeout(() => setVisible(true), 50);
      return () => clearTimeout(timer);
    } else {
      setVisible(false);
    }
  }, [insight, dismissed]);

  // Auto-hide after 30 seconds
  useEffect(() => {
    if (!insight || dismissed) return;
    const timer = setTimeout(() => setDismissed(true), 30_000);
    return () => clearTimeout(timer);
  }, [insight, dismissed]);

  // Reset dismissed state when a new insight arrives
  useEffect(() => {
    if (insight) setDismissed(false);
  }, [insight?.id]);

  const respond = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'accept' | 'dismiss' }) =>
      respondToInsight('operator', id, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending-insights'] });
      queryClient.invalidateQueries({ queryKey: ['all-insights'] });
      queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    },
  });

  const handleAction = useCallback(
    (action: 'accept' | 'dismiss') => {
      if (!insight) return;
      respond.mutate({ id: insight.id, action });
    },
    [insight, respond]
  );

  if (!insight || dismissed) return null;

  return (
    <div
      className={`mx-4 mt-2 transition-all duration-300 ease-out overflow-hidden ${
        visible ? 'max-h-32 opacity-100 translate-y-0' : 'max-h-0 opacity-0 -translate-y-2'
      }`}
    >
      <div className="bg-neutral-800 border border-neutral-700 rounded-lg p-4 flex items-start gap-3">
        <Lightbulb className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
        <p className="flex-1 text-sm text-neutral-200 leading-relaxed">
          {insight.description || insight.text || 'New coaching insight available'}
        </p>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => handleAction('accept')}
            disabled={respond.isPending}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md bg-green-600/20 text-green-400 hover:bg-green-600/30 transition-colors disabled:opacity-50"
          >
            <Check className="w-3.5 h-3.5" />
            Accept
          </button>
          <button
            onClick={() => handleAction('dismiss')}
            disabled={respond.isPending}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md bg-neutral-700 text-neutral-400 hover:bg-neutral-600 hover:text-neutral-300 transition-colors disabled:opacity-50"
          >
            <X className="w-3.5 h-3.5" />
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
