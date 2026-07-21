import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';

import type { CatalogCapability, ModelCatalogItemOut } from '@/api/types';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';

import { useModelCatalog } from './queries';

export function CatalogPicker({
  capability,
  onSelect,
}: {
  capability: CatalogCapability;
  onSelect: (entry: ModelCatalogItemOut) => void;
}) {
  const catalog = useModelCatalog(capability, true);
  const [query, setQuery] = useState('');
  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const items = catalog.data?.items ?? [];
    if (!normalized) return items.slice(0, 80);
    return items
      .filter((entry) =>
        `${entry.provider} ${entry.model_id} ${entry.litellm_model_name}`
          .toLocaleLowerCase()
          .includes(normalized),
      )
      .slice(0, 80);
  }, [catalog.data?.items, query]);

  return (
    <section className="space-y-2 rounded-lg border border-line bg-subtle/40 p-3">
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-muted"
        />
        <Input
          aria-label={`Search ${capability} model catalog`}
          className="pl-8"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={`Search ${capability} models or providers`}
        />
      </div>
      {catalog.isPending ? <Spinner label="Loading model presets…" /> : null}
      {catalog.isError ? (
        <p role="alert" className="text-[12px] text-danger">
          {catalog.error.message}
        </p>
      ) : null}
      {catalog.data ? (
        <div className="space-y-1 text-[11px] text-muted">
          <p>
            {catalog.data.total.toLocaleString()} {capability} presets · showing{' '}
            {visible.length.toLocaleString()}
          </p>
          <p>
            Native presets use a LiteLLM provider adapter. Compatible presets use a provider
            base URL. Both must pass OpenRAG&apos;s live probe before use.
          </p>
        </div>
      ) : null}
      <div className="max-h-56 space-y-1 overflow-y-auto" aria-label={`${capability} presets`}>
        {visible.map((entry, index) => (
          <button
            key={`${entry.provider}:${entry.model_id}:${index}`}
            type="button"
            className="flex w-full items-center justify-between gap-3 rounded-md border border-transparent px-2.5 py-2 text-left hover:border-line hover:bg-bg"
            onClick={() => onSelect(entry)}
          >
            <span className="min-w-0">
              <span className="block truncate text-[12px] font-medium text-ink">
                {entry.model_id}
              </span>
              <span className="block truncate text-[10px] text-muted">
                {entry.provider} · {entry.capabilities.join(' · ')}
              </span>
            </span>
            <span className="shrink-0 text-right text-[10px] text-secondary">
              <span className="block rounded-full bg-accent-soft px-1.5 py-0.5 font-medium text-accent">
                {entry.provider_kind === 'litellm' ? 'Native LiteLLM' : 'Compatible API'}
              </span>
              <span className="mt-1 block font-mono text-muted">
                {entry.max_tokens ? `${Math.round(entry.max_tokens / 1000)}k` : '—'}
              </span>
            </span>
          </button>
        ))}
        {catalog.data && visible.length === 0 ? (
          <p className="py-4 text-center text-[12px] text-secondary">No matching presets.</p>
        ) : null}
      </div>
    </section>
  );
}
