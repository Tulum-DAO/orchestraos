/**
 * The thread kit's Markdown (components/kit/Markdown.tsx), under the name its older callers import. `size` is the
 * kit's skin: "bubbles" (the agent chat) or "modern" (the size and colour of the reply it sits in).
 */
import KitMarkdown from '../kit/Markdown';

export default function Markdown({ text, size = 'bubbles' }: { text: string; size?: 'bubbles' | 'modern' }) {
  return <KitMarkdown text={text} skin={size} />;
}
