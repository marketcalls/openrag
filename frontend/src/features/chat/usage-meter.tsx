import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import { cn } from '@/lib/cn';

function compact(value: number): string {
  return Intl.NumberFormat('en', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

export function UsageMeter() {
  const usage = useQuery({
    queryKey: ['usage', 'me'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/usage/me');
      if (error) throw new Error('Failed to load token usage');
      return data;
    },
    refetchInterval: 30_000,
  });

  if (!usage.data) return null;
  const { used_tokens, allocated_tokens, resets_at, warning, blocked } = usage.data;
  const reset = new Date(resets_at).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
  const label =
    allocated_tokens == null
      ? `${compact(used_tokens)} tokens this month`
      : `${compact(used_tokens)} / ${compact(allocated_tokens)} tokens · resets ${reset}`;

  return (
    <span
      className={cn(
        'text-[12px] tabular-nums',
        warning || blocked ? 'text-danger' : 'text-muted',
      )}
      title={
        allocated_tokens == null
          ? `Tokens used in the current period; resets ${reset}`
          : `Monthly token usage; resets ${reset}`
      }
    >
      {label}
    </span>
  );
}
