import { useState, useEffect } from 'react';
import { AlertCircle, Check, Loader2 } from 'lucide-react';
import { factsApi } from '../../lib/factsApi';

interface Fact {
  id: number;
  text: string;
  source: 'session' | 'facts_db' | 'context_layer';
  verified_at: string | null;
  freshness: {
    age_days: number | null;
    stale: boolean;
    threshold_days: number;
  };
}

export function FactsPane() {
  const [facts, setFacts] = useState<Fact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState('');
  const [savingId, setSavingId] = useState<number | null>(null);
  const [markingFreshId, setMarkingFreshId] = useState<number | null>(null);

  // Load facts on mount
  useEffect(() => {
    loadFacts();
  }, []);

  const loadFacts = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await factsApi.getFacts();
      setFacts(data.facts || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load facts');
      console.error('Error loading facts:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleEditStart = (fact: Fact) => {
    setEditingId(fact.id);
    setEditText(fact.text);
  };

  const handleEditCancel = () => {
    setEditingId(null);
    setEditText('');
  };

  const handleEditSave = async (id: number) => {
    if (!editText.trim()) {
      setError('Fact text cannot be empty');
      return;
    }

    try {
      setSavingId(id);
      await factsApi.editFact(id, editText);
      setEditingId(null);
      setEditText('');
      await loadFacts();
    } catch (err: any) {
      setError(err.message || 'Failed to save fact');
    } finally {
      setSavingId(null);
    }
  };

  const handleMarkFresh = async (id: number) => {
    try {
      setMarkingFreshId(id);
      await factsApi.markFresh(id);
      await loadFacts();
    } catch (err: any) {
      setError(err.message || 'Failed to mark fact as fresh');
    } finally {
      setMarkingFreshId(null);
    }
  };

  const getFreshnessDisplay = (fact: Fact) => {
    const { age_days, stale, threshold_days } = fact.freshness;

    if (stale) {
      if (age_days === null) {
        return {
          text: `STALE(unknown, threshold ${threshold_days}d)`,
          bgColor: 'bg-amber-900/50',
          textColor: 'text-amber-300',
        };
      }
      return {
        text: `STALE(${age_days}d, threshold ${threshold_days}d)`,
        bgColor: 'bg-amber-900/50',
        textColor: 'text-amber-300',
      };
    }

    return {
      text: `fresh (threshold ${threshold_days}d)`,
      bgColor: 'bg-green-900/50',
      textColor: 'text-green-300',
    };
  };

  const getSourceLabel = (source: string) => {
    switch (source) {
      case 'session':
        return '📝 Session';
      case 'facts_db':
        return '💾 Database';
      case 'context_layer':
        return '🧠 Context';
      default:
        return source;
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        <Loader2 className="animate-spin mr-2 h-5 w-5" />
        Loading facts...
      </div>
    );
  }

  if (error && facts.length === 0) {
    return (
      <div className="p-4 bg-red-900/50 border border-red-700 rounded text-red-300 text-sm flex items-start gap-2">
        <AlertCircle className="h-5 w-5 flex-shrink-0 mt-0.5" />
        <div>
          <p className="font-semibold">Facts store unavailable</p>
          <p className="text-xs mt-1">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 max-h-96 overflow-y-auto">
      {error && (
        <div className="p-3 bg-red-900/50 border border-red-700 rounded text-red-300 text-sm flex items-start gap-2">
          <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {facts.length === 0 ? (
        <p className="text-center text-gray-500 py-8">No facts yet</p>
      ) : (
        facts.map((fact) => {
          const freshness = getFreshnessDisplay(fact);
          const isEditing = editingId === fact.id;
          const isSaving = savingId === fact.id;
          const isMarkingFresh = markingFreshId === fact.id;

          return (
            <div
              key={fact.id}
              className="p-3 border border-gray-700 rounded bg-gray-900/50 hover:border-gray-600 transition-colors text-sm"
            >
              {isEditing ? (
                // Edit mode
                <div className="space-y-2">
                  <textarea
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    className="w-full px-2 py-1 bg-gray-800 border border-gray-600 rounded text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-none min-h-[60px]"
                    placeholder="Fact text..."
                  />
                  <div className="flex gap-2 justify-end">
                    <button
                      onClick={handleEditCancel}
                      className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-white"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => handleEditSave(fact.id)}
                      disabled={isSaving}
                      className="px-2 py-1 text-xs rounded bg-blue-700 hover:bg-blue-600 text-white disabled:opacity-50"
                    >
                      {isSaving ? <Loader2 className="inline h-3 w-3 animate-spin" /> : 'Save'}
                    </button>
                  </div>
                </div>
              ) : (
                // Display mode
                <>
                  <p className="text-gray-200 break-words">{fact.text}</p>
                  <div className="mt-2 flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className="text-xs px-2 py-1 rounded bg-gray-800 text-gray-300">
                        {getSourceLabel(fact.source)}
                      </span>
                      <span
                        className={`text-xs px-2 py-1 rounded font-semibold ${freshness.bgColor} ${freshness.textColor}`}
                      >
                        {freshness.text}
                      </span>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => handleEditStart(fact)}
                        className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 text-white transition-colors"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleMarkFresh(fact.id)}
                        disabled={isMarkingFresh}
                        className="px-2 py-1 text-xs rounded bg-green-800 hover:bg-green-700 text-white transition-colors disabled:opacity-50 flex items-center gap-1"
                      >
                        {isMarkingFresh ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Check className="h-3 w-3" />
                        )}
                        Fresh
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
