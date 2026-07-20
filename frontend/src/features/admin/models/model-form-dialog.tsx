import { useEffect, useState, type FormEvent } from 'react';

import type { ModelCreate, ModelOut, ModelPatch, ReasoningEffort } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useCreateModel, usePatchModel } from './queries';

type ProviderKind = ModelCreate['provider_kind'];

const NEEDS_BASE_URL: ProviderKind[] = ['ollama', 'openai_compatible'];
const NEEDS_KEY: ProviderKind[] = ['openai', 'openai_compatible'];

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
  const [supportsChatCompletion, setSupportsChatCompletion] = useState(true);
  const [supportsStructuredJson, setSupportsStructuredJson] = useState(false);
  const [supportsVerifier, setSupportsVerifier] = useState(false);
  const [supportsReasoning, setSupportsReasoning] = useState(false);
  const [defaultReasoningEffort, setDefaultReasoningEffort] =
    useState<ReasoningEffort>('off');
  const editing = model !== null && model !== undefined;

  useEffect(() => {
    if (!open) return;
    setDisplayName(model?.display_name ?? '');
    setProvider(model?.provider_kind ?? 'openai');
    setModelId(model?.litellm_model_name ?? '');
    setBaseUrl(model?.base_url ?? '');
    setApiKey('');
    setSupportsChatCompletion(model?.supports_chat_completion ?? true);
    setSupportsStructuredJson(model?.supports_structured_json ?? false);
    setSupportsVerifier(model?.supports_verifier ?? false);
    setSupportsReasoning(model?.supports_reasoning ?? false);
    setDefaultReasoningEffort(model?.default_reasoning_effort ?? 'off');
  }, [model, open]);

  const close = (next: boolean): void => {
    if (!next) {
      setDisplayName('');
      setProvider('openai');
      setModelId('');
      setBaseUrl('');
      setApiKey('');
      setSupportsChatCompletion(true);
      setSupportsStructuredJson(false);
      setSupportsVerifier(false);
      setSupportsReasoning(false);
      setDefaultReasoningEffort('off');
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
        ...(NEEDS_BASE_URL.includes(provider) ? { base_url: baseUrl.trim() } : {}),
        ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
        supports_chat_completion: supportsChatCompletion,
        supports_structured_json: supportsStructuredJson,
        supports_verifier: supportsVerifier,
        supports_reasoning: supportsReasoning,
        default_reasoning_effort: supportsReasoning ? defaultReasoningEffort : 'off',
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
      ...(NEEDS_BASE_URL.includes(provider) && baseUrl.trim()
        ? { base_url: baseUrl.trim() }
        : {}),
      ...(NEEDS_KEY.includes(provider) && apiKey ? { api_key: apiKey } : {}),
      supports_chat_completion: supportsChatCompletion,
      supports_structured_json: supportsStructuredJson,
      supports_verifier: supportsVerifier,
      supports_reasoning: supportsReasoning,
      default_reasoning_effort: supportsReasoning ? defaultReasoningEffort : 'off',
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
              <option value="ollama">Ollama via LiteLLM</option>
              <option value="openai_compatible">OpenAI-compatible via LiteLLM</option>
            </NativeSelect>
          </div>
          <div>
            <Label htmlFor="model-id">Model id</Label>
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
          {NEEDS_BASE_URL.includes(provider) ? (
            <div>
              <Label htmlFor="model-base-url">Base URL</Label>
              <Input
                id="model-base-url"
                required
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
          <fieldset className="rounded-md border border-line bg-subtle/50 p-3">
            <legend className="px-1 text-[12px] font-semibold text-ink">
              Model capabilities
            </legend>
            <div className="grid gap-2 sm:grid-cols-3">
              <label className="flex items-center gap-2 text-[12px] text-ink">
                <input
                  type="checkbox"
                  aria-label="Chat completion"
                  checked={supportsChatCompletion}
                  onChange={(event) => {
                    setSupportsChatCompletion(event.target.checked);
                    if (!event.target.checked) {
                      setSupportsStructuredJson(false);
                      setSupportsVerifier(false);
                    }
                  }}
                />
                Chat completion
              </label>
              <label className="flex items-center gap-2 text-[12px] text-ink">
                <input
                  type="checkbox"
                  aria-label="Structured JSON"
                  checked={supportsStructuredJson}
                  onChange={(event) => {
                    setSupportsStructuredJson(event.target.checked);
                    if (event.target.checked) setSupportsChatCompletion(true);
                    else setSupportsVerifier(false);
                  }}
                />
                Structured JSON
              </label>
              <label className="flex items-center gap-2 text-[12px] text-ink">
                <input
                  type="checkbox"
                  aria-label="Verifier / judge"
                  checked={supportsVerifier}
                  onChange={(event) => {
                    setSupportsVerifier(event.target.checked);
                    if (event.target.checked) {
                      setSupportsChatCompletion(true);
                      setSupportsStructuredJson(true);
                    }
                  }}
                />
                Verifier / judge
              </label>
            </div>
            <p className="mt-2 text-[11px] text-muted">
              Verifier models must support structured JSON; structured output requires chat completion.
            </p>
          </fieldset>
          <div className="rounded-md border border-line bg-subtle/50 p-3">
            <label className="flex items-center gap-2 text-[13px] font-medium text-ink">
              <input
                type="checkbox"
                aria-label="Supports reasoning effort"
                checked={supportsReasoning}
                onChange={(event) => {
                  setSupportsReasoning(event.target.checked);
                  if (!event.target.checked) setDefaultReasoningEffort('off');
                }}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Supports reasoning effort
            </label>
            <p className="mt-1 text-[11px] text-muted">
              Enable only when the provider model accepts LiteLLM reasoning effort.
            </p>
            {supportsReasoning ? (
              <div className="mt-3">
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
              </div>
            ) : null}
          </div>
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
