import { useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';
import { useWorkspace } from '@/features/workspaces/workspace-context';
import { hasPermission } from '@/lib/jwt';
import { useClaims } from '@/lib/use-claims';

import { DocumentRow } from './document-row';
import { Dropzone } from './dropzone';
import { useApproveDocument, useDeleteDocument, useDocuments } from './queries';
import { uploadDocuments } from './upload';

interface UploadItem {
  key: string;
  names: string;
  percentage: number;
}

export function DocumentsPage() {
  const { workspaceId } = useWorkspace();
  const claims = useClaims();
  const documents = useDocuments(workspaceId);
  const deleteDocument = useDeleteDocument(workspaceId);
  const approveDocument = useApproveDocument(workspaceId);
  const canApprove = claims ? hasPermission(claims, 'document.approve') : false;
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  const onFiles = (files: File[]) => {
    if (!workspaceId) return;
    const key = crypto.randomUUID();
    const names = files.map((file) => file.name).join(', ');
    setUploads((current) => [...current, { key, names, percentage: 0 }]);
    void uploadDocuments(workspaceId, files, (percentage) =>
      setUploads((current) =>
        current.map((upload) =>
          upload.key === key ? { ...upload, percentage } : upload,
        ),
      ),
    )
      .then(() => {
        toast.success(files.length === 1 ? 'Document uploaded' : 'Documents uploaded');
        return documents.refetch();
      })
      .catch((error: Error) => toast.error(error.message))
      .finally(() =>
        setUploads((current) => current.filter((upload) => upload.key !== key)),
      );
  };

  return (
    <>
      <TopBar title="Documents" />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl space-y-4">
          <Dropzone onFiles={onFiles} disabled={!workspaceId} />
          {uploads.map((upload) => (
            <div key={upload.key} className="rounded-md border border-line bg-raised px-3 py-2">
              <div className="mb-1 flex justify-between text-[12px]">
                <span className="truncate text-secondary">Uploading {upload.names}</span>
                <span className="tabular-nums text-muted">{upload.percentage}%</span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-subtle">
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: `${upload.percentage}%` }}
                />
              </div>
            </div>
          ))}
          {documents.isPending && workspaceId ? <Spinner label="Loading documents…" /> : null}
          {documents.isError ? (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger">
              Unable to load documents.
            </p>
          ) : null}
          {documents.data?.length ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Size</TH>
                  <TH>Pages</TH>
                  <TH>Status</TH>
                  <TH>Uploaded</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {documents.data.map((document) => (
                  <DocumentRow
                    key={document.id}
                    document={document}
                    deleting={deleteDocument.isPending}
                    canApprove={canApprove}
                    approving={approveDocument.isPending}
                    onApprove={() =>
                      approveDocument.mutate(document.id, {
                        onSuccess: () => toast.success('Document approved and searchable'),
                        onError: (error) => toast.error(error.message),
                      })
                    }
                    onDelete={() =>
                      deleteDocument.mutate(document.id, {
                        onError: (error) => toast.error(error.message),
                      })
                    }
                  />
                ))}
              </TBody>
            </Table>
          ) : null}
          {documents.data?.length === 0 && uploads.length === 0 ? (
            <p className="pt-4 text-center text-[13px] text-secondary">
              No documents yet—upload some to make them searchable.
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}
