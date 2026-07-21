import { useEffect, useState, type FormEvent } from 'react';

import type { ModelCreate, ModelOut, ModelPatch, ReasoningEffort } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';
import { CatalogPicker } from '@/features/admin/model-catalog/catalog-picker';

import { useCreateModel, usePatchModel } from './queries';

type ProviderKind = ModelCreate['provider_kind'];

const NEEDS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible'];
const NEEDS_KEY: ProviderKind[] = ['openai', 'openai_compatible', 'litellm'];
const SHOWS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible', 'litellm'];

export function ModelFormDialog({
  open,
  onOpenChange,
  model,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  model?: ModelOut | null;
}) {
  const create = useCreateModel();
  const patch = usePatchModel();
  const [displayName, setDisplayName] = useState('');
  const [provider, setProvider] = useState<ProviderKind>('openai');
  const [modelId, setModelId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [defaultReasoningEffort, setDefaultReasoningEffort] =
    useState<ReasoningEffort>('off');
  const [catalogOpen, setCatalogOpen] = useState(false);
  const editing = model !== null && model !== undefined;

  useEffect(() => {
    if (!open) return;
    setDisplayName(model?.display_name ?? '');
    setProvider(model?.provider_kind ?? 'openai');
    setModelId(model?.litellm_model_name ?? '');
    setBaseUrl(model?.base_url ?? '');
    setApiKey('');
    setDefaultReasoningEffort(model?.default_reasoning_effort ?? 'off');
  }, [model, open]);

  const close = (next: boolean): void => {
    if (!next) {
      setDisplayName('');
      setProvider('openai');
      setModelId('');
      setBaseUrl('');
      setApiKey('');
      setDefaultReasoningEffort('off');
      setCatalogOpen(false);
      create.reset();
      patch.reset();
    }
    onOpenChange(next);
  };

  const onSubmit = (event: FormEvent): void => {
    event.preventDefault();

    if (model) {
      const body: ModelPatch = {
        display_name: displayName.trim(),
        ...(SHOWS_BASE_URL.includes(provider) && baseUrl.trim()
          ? { base_url: baseUrl.trim() }
          : {}),
        ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
        ...(model.supports_reasoning
          ? { default_reasoning_effort: defaultReasoningEffort }
          : {}),
      };
      patch.mutate(
        { modelId: model.id, body },
        {
          onSuccess: () => {
            toast.success('Model updated — secret remains write-only');
            close(false);
          },
        },
      );
      return;
    }

    const body: ModelCreate = {
      display_name: displayName.trim(),
      litellm_model_name: modelId.trim(),
      provider_kind: provider,
      ...(SHOWS_BASE_URL.includes(provider) && baseUrl.trim()
        ? { base_url: baseUrl.trim() }
        : {}),
      ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
    };

    create.mutate(body, {
      onSuccess: () => {
        toast.success('Model added — secret stored securely');
        close(false);
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title={editing ? 'Edit model' : 'Add model'}
        description={
          editing
            ? 'Update model metadata or rotate its write-only provider key.'
            : 'Register a model with OpenRAG for the in-process LiteLLM runtime.'
        }
      >
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <Label htmlFor="model-display">Display name</Label>
            <Input
              id="model-display"
              required
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="model-provider">Provider</Label>
            <NativeSelect
              id="model-provider"
              value={provider}
              disabled={editing}
              onChange={(event) => setProvider(event.target.value as ProviderKind)}
            >
              <option value="openai">OpenAI via LiteLLM</option>
              <option value="litellm">Native LiteLLM provider</option>
              <option value="ollama">Ollama via LiteLLM</option>
              <option value="openai_compatible">OpenAI-compatible via LiteLLM</option>
            </NativeSelect>
          </div>
          <div>
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="model-id">Model id</Label>
              {!editing ? (
                <Button size="sm" variant="ghost" onClick={() => setCatalogOpen(!catalogOpen)}>
                  {catalogOpen ? 'Hide catalog' : 'Browse catalog'}
                </Button>
              ) : null}
            </div>
            <Input
              id="model-id"
              required
              disabled={editing}
              autoComplete="off"
              placeholder="e.g. gpt-4o-mini"
              value={modelId}
              onChange={(event) => setModelId(event.target.value)}
            />
          </div>
          {catalogOpen && !editing ? (
            <CatalogPicker
              capability="chat"
              onSelect={(entry) => {
                setDisplayName(`${entry.model_id} · ${entry.provider}`);
                setProvider(entry.provider_kind);
                setModelId(entry.litellm_model_name);
                setBaseUrl(entry.suggested_base_url ?? '');
                setCatalogOpen(false);
              }}
            />
          ) : null}
          {SHOWS_BASE_URL.includes(provider) ? (
            <div>
              <Label htmlFor="model-base-url">
                Base URL{provider === 'litellm' ? ' (optional)' : ''}
              </Label>
              <Input
                id="model-base-url"
                required={NEEDS_BASE_URL.includes(provider)}
                type="url"
                autoComplete="url"
                placeholder="http://ollama:11434"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </div>
          ) : null}
          {NEEDS_KEY.includes(provider) ? (
            <div>
              <Label htmlFor="model-api-key">API key</Label>
              <Input
                id="model-api-key"
                type="password"
                autoComplete="off"
                placeholder={
                  editing
                    ? 'Leave blank to keep the current key'
                    : 'Write-only — never returned by the API'
                }
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
              />
              {editing && model.key_fingerprint ? (
                <p className="mt-1 font-mono text-[11px] text-muted">
                  Stored key: {model.key_fingerprint}
                </p>
              ) : null}
            </div>
          ) : null}
          <div className="rounded-md border border-line bg-subtle/50 p-3 text-[11px] text-secondary">
            Chat, streaming, structured JSON, tool-calling, vision, context, and verifier capabilities are measured automatically through bounded LiteLLM probes. A model stays unavailable until its connection probe passes.
          </div>
          {editing && model.supports_reasoning ? (
            <div className="rounded-md border border-line bg-subtle/50 p-3">
              <Label htmlFor="model-default-reasoning">Default reasoning effort</Label>
              <NativeSelect
                id="model-default-reasoning"
                value={defaultReasoningEffort}
                onChange={(event) =>
                  setDefaultReasoningEffort(event.target.value as ReasoningEffort)
                }
              >
                <option value="off">Off</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </NativeSelect>
              <p className="mt-1 text-[11px] text-muted">
                Available because the live LiteLLM probe verified reasoning support.
              </p>
            </div>
          ) : null}
          {create.isError || patch.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              {(create.error ?? patch.error)?.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button
              type="submit"
              variant="primary"
              disabled={create.isPending || patch.isPending}
            >
              {editing
                ? patch.isPending
                  ? 'Saving…'
                  : 'Save changes'
                : create.isPending
                  ? 'Adding…'
                  : 'Add model'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
