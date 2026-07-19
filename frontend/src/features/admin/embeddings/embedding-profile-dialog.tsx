import { useEffect, useState, type FormEvent } from 'react';

import type {
  EmbeddingProfileCreate,
  EmbeddingProfileOut,
  EmbeddingProfilePatch,
} from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useCreateEmbeddingProfile, usePatchEmbeddingProfile } from './queries';

type Provider = EmbeddingProfileCreate['provider_kind'];

export function EmbeddingProfileDialog({
  open,
  onOpenChange,
  profile,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profile?: EmbeddingProfileOut | null;
}) {
  const create = useCreateEmbeddingProfile();
  const patch = usePatchEmbeddingProfile();
  const editing = profile !== null && profile !== undefined;
  const [name, setName] = useState('');
  const [provider, setProvider] = useState<Provider>('litellm');
  const [modelName, setModelName] = useState('');
  const [dimension, setDimension] = useState('1024');
  const [maxTokens, setMaxTokens] = useState('8192');
  const [batchSize, setBatchSize] = useState('32');

  useEffect(() => {
    if (!open) return;
    setName(profile?.name ?? '');
    setProvider(profile?.provider_kind ?? 'litellm');
    setModelName(profile?.model_name ?? '');
    setDimension(String(profile?.dimension ?? 1024));
    setMaxTokens(String(profile?.max_input_tokens ?? 8192));
    setBatchSize(String(profile?.batch_size ?? 32));
  }, [open, profile]);

  const close = (next: boolean): void => {
    if (!next) {
      create.reset();
      patch.reset();
    }
    onOpenChange(next);
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (profile) {
      const body: EmbeddingProfilePatch = { name: name.trim() };
      patch.mutate(
        { profileId: profile.id, body },
        {
          onSuccess: () => {
            toast.success('Embedding profile renamed');
            close(false);
          },
        },
      );
      return;
    }
    create.mutate(
      {
        name: name.trim(),
        provider_kind: provider,
        model_name: modelName.trim(),
        dimension: Number(dimension),
        max_input_tokens: Number(maxTokens),
        batch_size: Number(batchSize),
      },
      {
        onSuccess: () => {
          toast.success('Embedding profile registered');
          close(false);
        },
      },
    );
  };

  const pending = create.isPending || patch.isPending;
  const error = create.error ?? patch.error;

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={editing ? 'Rename embedding profile' : 'Register embedding profile'}
        description={
          editing
            ? 'Vector identity fields are immutable. Create a new profile to change provider, model, or dimension.'
            : 'Define the exact vector contract OpenRAG will validate before a generation can be reindexed.'
        }
      >
        <form className="space-y-3" onSubmit={submit}>
          <div>
            <Label htmlFor="embedding-name">Profile name</Label>
            <Input
              id="embedding-name"
              required
              maxLength={120}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Production BGE"
            />
          </div>
          <fieldset disabled={editing} className="space-y-3 disabled:opacity-65">
            <div>
              <Label htmlFor="embedding-provider">Provider path</Label>
              <NativeSelect
                id="embedding-provider"
                value={provider}
                onChange={(event) => setProvider(event.target.value as Provider)}
              >
                <option value="litellm">LiteLLM gateway</option>
                <option value="tei">Local TEI service</option>
                <option value="hash">Deterministic hash — development only</option>
              </NativeSelect>
            </div>
            <div>
              <Label htmlFor="embedding-model">Model identifier</Label>
              <Input
                id="embedding-model"
                required
                maxLength={200}
                autoComplete="off"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="huggingface/BAAI/bge-m3"
              />
              <p className="mt-1 text-[11px] text-muted">
                LiteLLM profiles use the gateway model alias. No provider key is stored here.
              </p>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <Label htmlFor="embedding-dimension">Dimensions</Label>
                <Input
                  id="embedding-dimension"
                  required
                  type="number"
                  min={1}
                  max={32768}
                  value={dimension}
                  onChange={(event) => setDimension(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="embedding-tokens">Max tokens</Label>
                <Input
                  id="embedding-tokens"
                  required
                  type="number"
                  min={1}
                  max={2000000}
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="embedding-batch">Batch size</Label>
                <Input
                  id="embedding-batch"
                  required
                  type="number"
                  min={1}
                  max={1024}
                  value={batchSize}
                  onChange={(event) => setBatchSize(event.target.value)}
                />
              </div>
            </div>
          </fieldset>
          {error ? (
            <p role="alert" className="text-[12px] text-danger">
              {error.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={pending}>
              {pending ? 'Saving…' : editing ? 'Save name' : 'Register profile'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
