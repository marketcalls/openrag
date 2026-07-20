import type { LucideIcon } from 'lucide-react';

export function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}) {
  const toneClass = {
    neutral: 'bg-accent-soft text-accent-on-soft',
    success: 'bg-success-soft text-success',
    warning: 'bg-warning-soft text-warning',
    danger: 'bg-danger-soft text-danger',
  }[tone];

  return (
    <article className="relative overflow-hidden rounded-xl border border-line bg-bg p-4 shadow-sm">
      <div aria-hidden className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-line-strong to-transparent" />
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">{label}</p>
          <p className="mt-2 text-[24px] font-semibold tracking-[-0.04em] text-ink">{value}</p>
        </div>
        <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${toneClass}`}>
          <Icon className="h-4 w-4" aria-hidden />
        </span>
      </div>
      <p className="mt-2 text-[11px] text-secondary">{detail}</p>
    </article>
  );
}
