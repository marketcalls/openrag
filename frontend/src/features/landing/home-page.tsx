import {
  ArrowRight,
  Check,
  FileSearch,
  Github,
  LockKeyhole,
  Network,
  ScanText,
  ShieldCheck,
} from 'lucide-react';
import { Link } from 'react-router-dom';

import { DemoAccessCard } from '@/features/auth/demo-access';

const capabilities = [
  {
    number: '01',
    title: 'Every answer carries evidence',
    copy: 'Document, version, section, and page citations stay attached to the claims they support.',
    icon: FileSearch,
  },
  {
    number: '02',
    title: 'Agentic retrieval, not one search',
    copy: 'OpenRAG routes, gathers, reranks, validates, and refuses when the evidence is not sufficient.',
    icon: Network,
  },
  {
    number: '03',
    title: 'Messy documents are welcome',
    copy: 'Parse office files, tables, and scanned PDFs with asynchronous OCR and background indexing.',
    icon: ScanText,
  },
  {
    number: '04',
    title: 'Private by architecture',
    copy: 'Self-hosted storage, role-based access, audit trails, and governed model access through LiteLLM.',
    icon: LockKeyhole,
  },
] as const;

const formats = ['PDF', 'Word', 'Excel', 'PowerPoint', 'Text', 'Scanned PDF + OCR'];

function EvidencePreview() {
  return (
    <div className="relative mx-auto w-full max-w-[560px]" aria-label="OpenRAG answer preview">
      <div className="absolute -inset-6 -z-10 rounded-[40px] bg-gradient-to-b from-ink/[0.05] to-transparent blur-2xl" />
      <div className="overflow-hidden rounded-[24px] border border-line-strong bg-bg shadow-[0_24px_80px_rgba(0,0,0,0.10)]">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-success" />
            <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-secondary">
              Evidence pipeline live
            </span>
          </div>
          <span className="rounded-full border border-line px-2.5 py-1 font-mono text-[10px] text-muted">
            3.2s
          </span>
        </div>
        <div className="space-y-5 p-5 sm:p-7">
          <div className="ml-auto max-w-[84%] rounded-[18px] rounded-br-[5px] bg-subtle px-4 py-3 text-[13px] leading-relaxed text-ink">
            What is the outstanding amount on the latest approved invoice?
          </div>
          <div className="space-y-3">
            <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              <Network className="h-3.5 w-3.5" />
              Routed · hybrid search · verified
            </div>
            <p className="text-[15px] leading-7 text-ink">
              The outstanding amount is <strong className="font-extrabold">₹5,90,000</strong>, due
              on 14 July 2026. <span className="rounded-md bg-subtle px-1.5 py-1 font-mono text-[11px]">1</span>
            </p>
            <div className="rounded-[14px] border border-line bg-raised p-3.5">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-[10px] text-bg">
                  1
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[12px] font-semibold text-ink">Invoice — approved.pdf</p>
                  <p className="mt-1 font-mono text-[10px] text-muted">VERSION 3 · PAYMENT STATUS · PAGE 1</p>
                </div>
                <ShieldCheck className="ml-auto h-4 w-4 shrink-0 text-success" />
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="absolute -bottom-5 -left-3 hidden items-center gap-2 rounded-full border border-line-strong bg-bg px-3.5 py-2 shadow-soft sm:flex">
        <Check className="h-3.5 w-3.5" />
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.1em]">Claim validated</span>
      </div>
    </div>
  );
}

export function HomePage() {
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="sticky top-0 z-40 border-b border-line/80 bg-bg/90 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between gap-5 px-5 sm:px-8">
          <Link to="/" aria-label="OpenRAG home">
            <span className="text-[18px] font-extrabold tracking-[-0.03em]">OpenRAG</span>
          </Link>
          <nav className="hidden items-center gap-1 rounded-full border border-line bg-bg px-1.5 py-1.5 shadow-soft md:flex">
            <a className="rounded-full px-3.5 py-1.5 text-[13px] font-medium text-secondary hover:text-ink" href="#why">
              Why OpenRAG
            </a>
            <a className="rounded-full px-3.5 py-1.5 text-[13px] font-medium text-secondary hover:text-ink" href="#formats">
              Formats
            </a>
            <a
              className="flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-medium text-secondary hover:text-ink"
              href="https://github.com/marketcalls/openrag"
              target="_blank"
              rel="noreferrer"
            >
              <Github className="h-3.5 w-3.5" /> GitHub
            </a>
          </nav>
          <Link
            to="/login"
            className="inline-flex h-9 items-center justify-center rounded-full bg-ink px-5 text-[13px] font-semibold text-bg transition-transform hover:-translate-y-0.5"
          >
            Log in
          </Link>
        </div>
      </header>

      <main>
        <section className="openrag-grid relative overflow-hidden px-5 py-20 sm:px-8 sm:py-28 lg:py-32">
          <div className="mx-auto grid max-w-[1240px] items-center gap-16 lg:grid-cols-[1.02fr_0.98fr] lg:gap-20">
            <div>
              <div className="home-reveal home-reveal-1 mb-8 inline-flex items-center gap-2.5 rounded-full border border-line-strong bg-bg px-4 py-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-ink" />
                <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-secondary">
                  Production-grade agentic RAG
                </span>
              </div>
              <h1 className="home-reveal home-reveal-2 max-w-[720px] text-[46px] font-extrabold leading-[1.02] tracking-[-0.055em] sm:text-[64px] lg:text-[72px]">
                Ask your company.
                <span className="block text-secondary/70">Get evidence, not guesses.</span>
              </h1>
              <p className="home-reveal home-reveal-3 mt-7 max-w-[620px] text-[17px] leading-8 text-secondary sm:text-[18px]">
                OpenRAG turns private documents into a secure AI knowledge system—combining OCR,
                hybrid retrieval, agentic routing, and claim-level citations you can audit.
              </p>
              <div className="home-reveal home-reveal-4 mt-9 flex flex-col gap-3 sm:flex-row">
                <Link
                  to="/login"
                  className="inline-flex h-12 items-center justify-center gap-2 rounded-full bg-ink px-7 text-[14px] font-semibold text-bg transition-transform hover:-translate-y-0.5"
                >
                  Open your workspace <ArrowRight className="h-4 w-4" />
                </Link>
                <a
                  href="https://github.com/marketcalls/openrag"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-12 items-center justify-center gap-2 rounded-full border border-line-strong bg-bg px-7 text-[14px] font-semibold transition-colors hover:bg-subtle"
                >
                  <Github className="h-4 w-4" /> View source
                </a>
              </div>
              <div className="home-reveal home-reveal-5 mt-10 flex flex-wrap gap-x-8 gap-y-4">
                {[
                  ['100%', 'open source'],
                  ['LiteLLM', 'model gateway'],
                  ['Self-hosted', 'data control'],
                ].map(([value, label]) => (
                  <div key={value} className="flex items-baseline gap-2">
                    <span className="text-[16px] font-extrabold tracking-tight">{value}</span>
                    <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted">{label}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="home-reveal home-reveal-4 lg:pt-5">
              <EvidencePreview />
            </div>
          </div>
        </section>

        <section aria-labelledby="judge-demo-title" className="border-t border-line bg-bg px-5 py-10 sm:px-8 sm:py-14">
          <div className="mx-auto grid max-w-[1100px] gap-6 lg:grid-cols-[0.72fr_1.28fr] lg:items-center">
            <div>
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted">
                Live on the OpenRAG VPS
              </p>
              <h2 id="judge-demo-title" className="mt-3 text-[28px] font-extrabold tracking-[-0.04em] sm:text-[34px]">
                Test the submission now.
              </h2>
              <p className="mt-3 max-w-md text-[14px] leading-6 text-secondary">
                No installation is needed for judges. Use the shared public account to explore the complete hosted workflow.
              </p>
            </div>
            <DemoAccessCard />
          </div>
        </section>

        <section id="why" className="border-y border-line bg-raised px-5 py-20 sm:px-8 sm:py-24">
          <div className="mx-auto max-w-[1040px]">
            <div className="mx-auto mb-14 max-w-2xl text-center">
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted">Why OpenRAG</p>
              <h2 className="mt-4 text-[36px] font-extrabold leading-tight tracking-[-0.04em] sm:text-[48px]">
                Knowledge you can trust,
                <span className="block text-secondary/70">on infrastructure you control.</span>
              </h2>
            </div>
            <div className="divide-y divide-line">
              {capabilities.map(({ number, title, copy, icon: Icon }) => (
                <article key={number} className="grid gap-5 py-8 sm:grid-cols-[52px_1fr_1fr] sm:items-start sm:gap-8">
                  <span className="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong bg-bg font-mono text-[10px] font-semibold">
                    {number}
                  </span>
                  <div className="flex items-center gap-3">
                    <Icon className="h-5 w-5" />
                    <h3 className="text-[19px] font-bold tracking-[-0.02em]">{title}</h3>
                  </div>
                  <p className="leading-7 text-secondary">{copy}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="formats" className="px-5 py-20 sm:px-8 sm:py-24">
          <div className="mx-auto max-w-[1100px] rounded-[28px] border border-line-strong bg-bg p-7 sm:p-12">
            <div className="grid gap-10 lg:grid-cols-[0.85fr_1.15fr] lg:items-end">
              <div>
                <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted">Bring the whole knowledge base</p>
                <h2 className="mt-4 text-[34px] font-extrabold leading-tight tracking-[-0.04em] sm:text-[42px]">
                  From scanned pages to structured answers.
                </h2>
              </div>
              <div className="flex flex-wrap gap-2.5 lg:justify-end">
                {formats.map((format) => (
                  <span key={format} className="rounded-full border border-line-strong bg-raised px-4 py-2 font-mono text-[10px] font-semibold uppercase tracking-[0.08em] text-secondary">
                    {format}
                  </span>
                ))}
              </div>
            </div>
            <div className="mt-10 flex flex-col items-start justify-between gap-5 border-t border-line pt-8 sm:flex-row sm:items-center">
              <div className="flex items-center gap-3 text-[13px] text-secondary">
                <ShieldCheck className="h-5 w-5 text-ink" />
                RBAC · encryption · audit logs · grounded refusals
              </div>
              <Link to="/login" className="inline-flex items-center gap-2 text-[14px] font-bold hover:underline">
                Sign in to OpenRAG <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line px-5 py-9 sm:px-8">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-4 text-[12px] text-muted sm:flex-row sm:items-center sm:justify-between">
          <div className="text-ink">
            <span className="font-bold">OpenRAG</span>
          </div>
          <p>Open-source, self-hosted knowledge infrastructure.</p>
          <a className="font-medium text-secondary hover:text-ink" href="https://github.com/marketcalls/openrag" target="_blank" rel="noreferrer">
            GitHub ↗
          </a>
        </div>
      </footer>
    </div>
  );
}
