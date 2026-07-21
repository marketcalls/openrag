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
import { CatalogPicker } from '@/features/admin/model-catalog/catalog-picker';

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
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [dimension, setDimension] = useState('1024');
  const [maxTokens, setMaxTokens] = useState('8192');
  const [batchSize, setBatchSize] = useState('32');
  const [catalogOpen, setCatalogOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(profile?.name ?? '');
    setProvider(profile?.provider_kind ?? 'litellm');
    setModelName(profile?.model_name ?? '');
    setBaseUrl(profile?.base_url ?? '');
    setApiKey('');
    setDimension(String(profile?.dimension ?? 1024));
    setMaxTokens(String(profile?.max_input_tokens ?? 8192));
    setBatchSize(String(profile?.batch_size ?? 32));
  }, [open, profile]);

  const close = (next: boolean): void => {
    if (!next) {
      setCatalogOpen(false);
      create.reset();
      patch.reset();
    }
    onOpenChange(next);
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (profile) {
      const body: EmbeddingProfilePatch = {
        name: name.trim(),
        ...(apiKey ? { api_key: apiKey } : {}),
      };
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
        ...(provider === 'litellm' && baseUrl.trim()
          ? { base_url: baseUrl.trim() }
          : {}),
        ...(provider === 'litellm' && apiKey ? { api_key: apiKey } : {}),
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
                <option value="litellm">LiteLLM library</option>
                <option value="tei">Local TEI service</option>
                <option value="hash">Deterministic hash — development only</option>
              </NativeSelect>
            </div>
            <div>
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="embedding-model">Model identifier</Label>
                {!editing && provider === 'litellm' ? (
                  <Button size="sm" variant="ghost" onClick={() => setCatalogOpen(!catalogOpen)}>
                    {catalogOpen ? 'Hide catalog' : 'Browse catalog'}
                  </Button>
                ) : null}
              </div>
              <Input
                id="embedding-model"
                required
                maxLength={200}
                autoComplete="off"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="openai/text-embedding-3-small"
              />
              <p className="mt-1 text-[11px] text-muted">
                Use a LiteLLM model identifier such as openai/text-embedding-3-small or ollama/nomic-embed-text.
              </p>
            </div>
            {catalogOpen && provider === 'litellm' ? (
              <CatalogPicker
                capability="embedding"
                onSelect={(entry) => {
                  setName(`${entry.model_id} · ${entry.provider}`);
                  setModelName(
                    entry.provider_kind === 'openai_compatible'
                      ? `openai/${entry.model_id}`
                      : entry.litellm_model_name,
                  );
                  setBaseUrl(entry.suggested_base_url ?? '');
                  if (entry.max_tokens) setMaxTokens(String(entry.max_tokens));
                  if (entry.litellm_model_name === 'openai/text-embedding-3-small') {
                    setDimension('1536');
                  } else if (
                    entry.litellm_model_name === 'openai/text-embedding-3-large'
                  ) {
                    setDimension('3072');
                  } else if (
                    entry.litellm_model_name === 'openai/text-embedding-ada-002'
                  ) {
                    setDimension('1536');
                  }
                  setCatalogOpen(false);
                }}
              />
            ) : null}
            {provider === 'litellm' ? (
              <div>
                <Label htmlFor="embedding-base-url">Base URL (optional)</Label>
                <Input
                  id="embedding-base-url"
                  type="url"
                  required={modelName.trim().startsWith('ollama/')}
                  maxLength={2048}
                  autoComplete="off"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="https://provider.example/v1"
                />
                <p className="mt-1 text-[11px] text-muted">
                  Required for Ollama and private OpenAI-compatible endpoints. Production URLs must use HTTPS.
                </p>
              </div>
            ) : null}
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
          {provider === 'litellm' ? (
            <div>
              <Label htmlFor="embedding-api-key">
                API key{editing ? ' (leave blank to keep current)' : ''}
              </Label>
              <Input
                id="embedding-api-key"
                type="password"
                required={!editing && !modelName.trim().startsWith('ollama/')}
                maxLength={8192}
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={editing && profile?.key_fingerprint ? profile.key_fingerprint : 'Write-only credential'}
              />
              <p className="mt-1 text-[11px] text-muted">
                Encrypted at rest and sent only to the selected provider for this embedding request.
              </p>
            </div>
          ) : null}
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
