/**
 * SpawnedAgentCard — the way INTO an agent Arturo just created.
 *
 * Shaw, 2026-09-22: when Arturo spawns an agent, that turn should carry a "go to agent" button
 * rather than leaving the operator to find the seat on another page. The seat id is known only
 * to the tool call, so it rides the /text reply as the additive `spawned` list; a FAILED spawn
 * records nothing, so this never links to a seat that does not exist.
 */
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';

export default function SpawnedAgentCard({ ids }: { ids?: string[] }) {
  if (!ids || ids.length === 0) return null;
  return (
    <div className="spawned-card" data-testid="spawned-card">
      {ids.map((id) => (
        <Link key={id} to={`/agent/${encodeURIComponent(id)}`} className="spawned-go" aria-label={`Go to ${id}`}>
          <span className="spawned-name">{id}</span>
          <span className="spawned-cta">Go to agent <ArrowRight size={12} /></span>
        </Link>
      ))}
    </div>
  );
}
