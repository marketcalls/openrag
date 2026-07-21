import { Check, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { DocumentOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { StatusPill } from '@/components/ui/status-pill';
import { TD, TR } from '@/components/ui/table';

import { formatBytes, statusPresentation } from './status';

export function DocumentRow({
  document,
  onDelete,
  deleting,
  canApprove,
  approving,
  onApprove,
}: {
  document: DocumentOut;
  onDelete: () => void;
  deleting: boolean;
  canApprove: boolean;
  approving: boolean;
  onApprove: () => void;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { tone, label } = statusPresentation(document);
  return (
    <TR>
      <TD className="max-w-[320px] truncate font-medium">
        {document.filename ?? 'Untitled document'}
      </TD>
      <TD className="text-secondary">{formatBytes(document.size_bytes)}</TD>
      <TD className="text-secondary">{document.page_count ?? '—'}</TD>
      <TD>
        {document.status === 'failed' ? (
          <Popover>
            <PopoverTrigger asChild>
              <button type="button" aria-label="Show failure reason">
                <StatusPill tone={tone}>{label}</StatusPill>
              </button>
            </PopoverTrigger>
            <PopoverContent>
              <p className="font-medium text-danger">Ingestion failed</p>
              <p className="mt-1 text-secondary">
                {document.error_code ?? 'processing_failed'}
              </p>
            </PopoverContent>
          </Popover>
        ) : (
          <StatusPill tone={tone}>{label}</StatusPill>
        )}
      </TD>
      <TD className="text-muted">{new Date(document.created_at).toLocaleDateString()}</TD>
      <TD className="text-right">
        {document.status === 'review' && canApprove ? (
          <Button
            size="sm"
            aria-label={`Approve ${document.filename ?? 'untitled document'}`}
            disabled={approving}
            onClick={onApprove}
          >
            <Check className="h-3.5 w-3.5" aria-hidden /> Approve
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete ${document.filename ?? 'untitled document'}`}
          onClick={() => setConfirmOpen(true)}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </Button>
        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent
            title="Delete document"
            description={`“${document.filename ?? 'Untitled document'}” and all its indexed chunks will be removed.`}
          >
            <DialogFooter>
              <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
              <Button
                variant="danger"
                disabled={deleting}
                onClick={() => {
                  onDelete();
                  setConfirmOpen(false);
                }}
              >
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </TD>
    </TR>
  );
}
