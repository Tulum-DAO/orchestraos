/**
 * /assistant — OrchestraOS V2 converse page (B1, feature-flagged).
 * Only registered when VITE_ASSISTANT_V2 === "1" (see App.tsx / config.ts).
 */
import AssistantView from '../components/assistant/AssistantView';

export default function Assistant() {
  return <AssistantView channel="page" />;
}
