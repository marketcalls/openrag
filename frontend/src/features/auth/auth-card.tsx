import { type ReactNode } from 'react';
import { Link } from 'react-router-dom';

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sidebar px-4">
      <div className="w-full max-w-sm rounded-lg border border-line bg-bg p-6 shadow-soft">
        <div className="mb-5 flex items-center gap-2">
          <span aria-hidden className="h-5 w-5 rounded-[6px] bg-ink" />
          <Link to="/" className="text-[16px] font-semibold tracking-[-0.01em] text-ink">
            OpenRAG
          </Link>
        </div>
        <h1 className="mb-4 text-[18px] font-semibold tracking-[-0.01em] text-ink">{title}</h1>
        {children}
      </div>
    </div>
  );
}
