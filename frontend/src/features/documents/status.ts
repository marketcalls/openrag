import type { DocumentOut } from '@/api/types';
import type { StatusTone } from '@/components/ui/status-pill';

export function statusPresentation(document: Pick<DocumentOut, 'status'>): {
  tone: StatusTone;
  label: string;
} {
  switch (document.status) {
    case 'indexed':
      return { tone: 'success', label: 'Indexed' };
    case 'failed':
      return { tone: 'danger', label: 'Failed' };
    default:
      return { tone: 'accent', label: 'Processing' };
  }
}

export function shouldPoll(documents: readonly DocumentOut[] | undefined): boolean {
  return (documents ?? []).some(
    (document) => document.status === 'queued' || document.status === 'processing',
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
