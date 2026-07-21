import { RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';

interface RouteErrorPageProps {
  reload?: () => void;
}

export function RouteErrorPage({
  reload = () => window.location.reload(),
}: RouteErrorPageProps) {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg px-6 py-12">
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-[radial-gradient(circle_at_18%_18%,var(--accent-soft),transparent_34%),radial-gradient(circle_at_82%_72%,var(--subtle),transparent_38%)]"
      />
      <section className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-line bg-bg/95 p-8 shadow-[0_24px_80px_rgba(16,21,20,0.12)] backdrop-blur">
        <div className="mb-8 text-[13px] font-semibold tracking-tight text-ink">
          OpenRAG
        </div>
        <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.18em] text-secondary">
          Connection refresh required
        </p>
        <h1 className="max-w-md text-balance text-3xl font-semibold tracking-[-0.035em] text-ink">
          OpenRAG needs a fresh connection
        </h1>
        <p className="mt-4 max-w-md text-[14px] leading-6 text-secondary">
          The application was updated while this tab was open. Reload to connect to the
          latest version. Your chats and documents are safely stored.
        </p>
        <Button
          className="mt-7 h-10 rounded-lg px-4"
          variant="primary"
          onClick={reload}
        >
          <RefreshCw aria-hidden="true" className="h-4 w-4" />
          Reload OpenRAG
        </Button>
      </section>
    </main>
  );
}
