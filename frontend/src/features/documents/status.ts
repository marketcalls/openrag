import type { DocumentOut } from '@/api/types';
import type { StatusTone } from '@/components/ui/status-pill';

export function statusPresentation(document: Pick<DocumentOut, 'status'>): {
  tone: StatusTone;
  label: string;
} {
  switch (document.status) {
    case 'indexed':
      return { tone: 'success', label: 'Indexed' };
    case 'review':
      return { tone: 'warning', label: 'Awaiting approval' };
    case 'rejected':
      return { tone: 'danger', label: 'Rejected' };
    case 'obsolete':
      return { tone: 'warning', label: 'Obsolete' };
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

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
