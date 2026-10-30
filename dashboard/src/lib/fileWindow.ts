/**
 * Where a floating file window (components/kit/FileWindow.tsx) reads its file from: a project's file or a library
 * item. Kept apart from the component so the page-level helpers can be shared without a component file exporting
 * plain functions.
 */
import { fetchProjectFile, projectFileRawUrl, type ProjectFileContent } from './api';

export interface FileSource {
  /** The query key for its content: the window's body and its copy button share one fetch. */
  key: readonly unknown[];
  /** Its path, for how text is shown (by extension) and the image's alt text. */
  path: string;
  load: () => Promise<ProjectFileContent>;
  /** The file itself, for an image or a PDF. */
  rawUrl: string;
}

export const projectFileSource = (slug: string, path: string): FileSource => ({
  key: ['project-file', slug, path], path, load: () => fetchProjectFile(slug, path), rawUrl: projectFileRawUrl(slug, path),
});

export function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
