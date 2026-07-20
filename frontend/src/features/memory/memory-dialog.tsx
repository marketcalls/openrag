import { useEffect, useState, type FormEvent } from 'react';

import type { MemoryOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';

import { useCreateMemory, usePatchMemory } from './queries';

export function MemoryDialog({
  workspaceId,
  memory,
  open,
  onOpenChange,
}: {
  workspaceId: string | null;
  memory?: MemoryOut | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateMemory(workspaceId);
  const patch = usePatchMemory(workspaceId);
  const [key, setKey] = useState(memory?.canonical_key ?? '');
  const [content, setContent] = useState(memory?.content ?? '');
  const [type, setType] = useState<'semantic' | 'episodic'>(
    memory?.memory_type === 'episodic' ? 'episodic' : 'semantic',
  );
  const pending = create.isPending || patch.isPending;
  const error = create.error ?? patch.error;

  useEffect(() => {
    setKey(memory?.canonical_key ?? '');
    setContent(memory?.content ?? '');
    setType(memory?.memory_type === 'episodic' ? 'episodic' : 'semantic');
  }, [memory, open]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (memory) {
      patch.mutate(
        {
          memoryId: memory.id,
          body: {
            client_request_id: crypto.randomUUID(),
            content,
          },
        },
        { onSuccess: () => onOpenChange(false) },
      );
      return;
    }
    create.mutate(
      {
        client_request_id: crypto.randomUUID(),
        canonical_key: key,
        content,
        memory_type: type,
        scope: 'user_workspace',
        confidence: 1,
        importance: 0.5,
        sensitivity: 'internal',
      },
      { onSuccess: () => onOpenChange(false) },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={memory ? 'Edit memory' : 'Add memory'}
        description="Only facts and preferences you explicitly save are active."
      >
        <form onSubmit={submit}>
          <div>
            <Label htmlFor="memory-key">Memory key</Label>
            <Input
              id="memory-key"
              value={key}
              disabled={Boolean(memory)}
              required
              pattern="[a-z][a-z0-9_.-]*"
              maxLength={120}
              placeholder="response.style"
              onChange={(event) => setKey(event.target.value.toLowerCase())}
            />
          </div>
          {!memory ? (
            <div className="mt-3">
              <Label htmlFor="memory-type">Memory type</Label>
              <NativeSelect
                id="memory-type"
                value={type}
                onChange={(event) => setType(event.target.value as 'semantic' | 'episodic')}
              >
                <option value="semantic">Preference or durable fact</option>
                <option value="episodic">Past event or outcome</option>
              </NativeSelect>
            </div>
          ) : null}
          <div className="mt-3">
            <Label htmlFor="memory-content">What should OpenRAG remember?</Label>
            <textarea
              id="memory-content"
              value={content}
              required
              maxLength={4000}
              rows={5}
              className="w-full resize-y rounded-md border border-line bg-bg px-2.5 py-2 text-[13px] leading-5 text-ink placeholder:text-muted focus-visible:border-accent"
              placeholder="Prefer concise answers with a short summary first."
              onChange={(event) => setContent(event.target.value)}
            />
            <div className="mt-1 text-right font-mono text-[10px] text-muted">
              {content.length} / 4000
            </div>
          </div>
          {error ? (
            <p role="alert" className="mt-3 text-[12px] text-danger">
              {error.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {memory ? 'Save changes' : 'Save memory'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
