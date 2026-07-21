import { ExternalLink, FlaskConical } from 'lucide-react';

import { Button } from '@/components/ui/button';

export const JUDGE_DEMO = {
  url: 'https://ragdemo.openalgo.in',
  loginUrl: 'https://ragdemo.openalgo.in/login',
  email: 'demo@openalgo.in',
  password: 'DemoOpen1234#',
} as const;

export function DemoAccessCard({
  onUseCredentials,
  compact = false,
}: {
  onUseCredentials?: () => void;
  compact?: boolean;
}) {
  return (
    <aside
      aria-label="Hackathon judge demo access"
      className={
        compact
          ? 'mb-5 rounded-[14px] border border-line-strong bg-raised p-4'
          : 'rounded-[24px] border border-line-strong bg-ink p-6 text-bg shadow-[0_20px_70px_rgba(0,0,0,0.14)] sm:p-8'
      }
    >
      <div className={compact ? 'flex items-center gap-2' : 'flex items-center gap-2.5'}>
        <FlaskConical className={compact ? 'h-4 w-4' : 'h-5 w-5'} />
        <p className="font-mono text-[10px] font-bold uppercase tracking-[0.14em]">
          Hackathon judge demo
        </p>
      </div>
      <p className={compact ? 'mt-2 text-[12px] leading-5 text-secondary' : 'mt-4 max-w-xl text-[14px] leading-6 text-bg/70'}>
        Public test account for the hosted VPS demo. Please do not upload confidential data.
      </p>
      <dl className={compact ? 'mt-3 grid gap-2 text-[12px]' : 'mt-6 grid gap-3 sm:grid-cols-3'}>
        {[
          ['URL', JUDGE_DEMO.url],
          ['Email', JUDGE_DEMO.email],
          ['Password', JUDGE_DEMO.password],
        ].map(([label, value]) => (
          <div
            key={label}
            className={
              compact
                ? 'flex items-center justify-between gap-3'
                : 'min-w-0 rounded-[14px] border border-bg/15 bg-bg/[0.06] px-4 py-3'
            }
          >
            <dt className={compact ? 'text-muted' : 'font-mono text-[9px] uppercase tracking-[0.12em] text-bg/50'}>
              {label}
            </dt>
            <dd
              className={
                compact
                  ? 'truncate font-mono font-semibold text-ink'
                  : 'mt-1 truncate font-mono text-[12px] font-semibold text-bg'
              }
              title={value}
            >
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <div className={compact ? 'mt-4 flex flex-wrap gap-2' : 'mt-6 flex flex-wrap gap-3'}>
        {onUseCredentials ? (
          <Button type="button" variant="primary" onClick={onUseCredentials}>
            Fill demo credentials
          </Button>
        ) : null}
        <a
          href={JUDGE_DEMO.loginUrl}
          target="_blank"
          rel="noreferrer"
          className={
            compact
              ? 'inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-line bg-bg px-3 text-[12px] font-medium text-ink hover:bg-subtle'
              : 'inline-flex h-10 items-center justify-center gap-2 rounded-full bg-bg px-5 text-[13px] font-bold text-ink transition-transform hover:-translate-y-0.5'
          }
        >
          Open hosted demo <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>
    </aside>
  );
}
