import type { ModelPublic } from '@/api/types';
import { NativeSelect } from '@/components/ui/select';

export function ModelSelector({
  models,
  value,
  onChange,
  loading = false,
  error = false,
}: {
  models: ModelPublic[];
  value: string | null;
  onChange: (id: string) => void;
  loading?: boolean;
  error?: boolean;
}) {
  if (loading) return <span className="text-[12px] text-muted">Loading models…</span>;
  if (error) {
    return (
      <span className="text-[12px] text-danger" role="status">
        Models unavailable
      </span>
    );
  }
  if (!models.length) {
    return (
      <span
        className="text-[12px] text-muted"
        title="Configured models appear here after their capability probe passes"
      >
        No ready models
      </span>
    );
  }
  return (
    <NativeSelect
      aria-label="Model"
      className="h-7 w-auto min-w-[140px] text-[12px]"
      value={value ?? models[0]?.id ?? ''}
      onChange={(event) => onChange(event.target.value)}
    >
      {models.map((model) => (
        <option key={model.id} value={model.id}>
          {model.display_name}
        </option>
      ))}
    </NativeSelect>
  );
}
