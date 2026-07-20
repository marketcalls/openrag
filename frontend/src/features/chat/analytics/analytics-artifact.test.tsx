import { fireEvent, render, screen } from '@testing-library/react';

import type { AnalyticsResponseV1, AnalyticsTableBlockV1 } from '@/api/types';

import { CitationProvider } from '../citation-context';
import { AnalyticsArtifact } from './analytics-artifact';
import { analyticsJson, downloadText, tableCsv } from './export';

const artifact: AnalyticsResponseV1 = {
  schema_version: 'analytics.v1',
  title: 'Revenue dashboard',
  subtitle: 'Q4 performance grounded in approved finance documents',
  kpis: [
    {
      label: 'Q4 revenue',
      value: '$4.83M',
      detail: '12.4% quarter-over-quarter growth',
      trend: 'up',
      source_markers: [1],
    },
  ],
  blocks: [
    {
      kind: 'bar_chart',
      title: 'Monthly revenue',
      x_label: 'Month',
      y_label: 'Revenue ($M)',
      categories: ['October', 'November', 'December'],
      series: [{ name: 'Revenue', values: [1.42, 1.57, 1.84] }],
      source_markers: [1],
    },
    {
      kind: 'line_chart',
      title: 'Monthly growth',
      x_label: 'Month',
      y_label: 'Growth (%)',
      categories: ['October', 'November', 'December'],
      series: [{ name: 'Growth', values: [4.1, 10.6, 17.2] }],
      source_markers: [1],
    },
    {
      kind: 'table',
      title: 'Revenue summary',
      columns: [
        { key: 'month', label: 'Month', format: 'text' },
        { key: 'revenue', label: 'Revenue ($M)', format: 'currency' },
      ],
      rows: [
        { month: 'October', revenue: 1.42 },
        { month: 'November', revenue: 1.57 },
      ],
      source_markers: [1],
    },
    {
      kind: 'explainer',
      title: 'Executive readout',
      body_markdown: '**Revenue accelerated** throughout the quarter [1].',
      source_markers: [1],
    },
  ],
  suggested_followups: ['Break this down by product line'],
};

test('renders the closed analytics registry accessibly and sends a grounded follow-up', () => {
  const onFollowup = vi.fn();
  const onCitation = vi.fn();
  render(
    <CitationProvider onCitationClick={onCitation}>
      <AnalyticsArtifact artifact={artifact} onFollowup={onFollowup} />
    </CitationProvider>,
  );

  expect(screen.getByRole('heading', { name: 'Revenue dashboard' })).toBeVisible();
  expect(screen.getByText('$4.83M')).toBeVisible();
  expect(screen.getByRole('img', { name: 'Monthly revenue' })).toBeVisible();
  expect(screen.getByRole('img', { name: 'Monthly growth' })).toBeVisible();
  expect(screen.getByRole('table', { name: 'Revenue summary' })).toBeVisible();
  expect(screen.getByText('Revenue accelerated')).toBeVisible();
  expect(screen.queryByText(/<script/i)).not.toBeInTheDocument();

  fireEvent.click(screen.getAllByRole('button', { name: 'Citation 1' })[0]!);
  expect(onCitation).toHaveBeenCalledWith(1);

  fireEvent.click(
    screen.getByRole('button', { name: 'Ask: Break this down by product line' }),
  );
  expect(onFollowup).toHaveBeenCalledWith('Break this down by product line');
  expect(screen.getByRole('button', { name: 'Export Revenue summary as CSV' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Export Revenue dashboard as JSON' })).toBeVisible();
});

test('provides a screen-reader data table for each chart', () => {
  render(<AnalyticsArtifact artifact={artifact} />);

  const dataTable = screen.getByRole('table', { name: 'Monthly revenue data' });
  expect(dataTable).toHaveTextContent('October');
  expect(dataTable).toHaveTextContent('1.42');
});

test('CSV is deterministic RFC 4180 output and neutralizes spreadsheet formulas', () => {
  const table: AnalyticsTableBlockV1 = {
    kind: 'table',
    title: 'Risk export',
    columns: [
      { key: 'name', label: 'Name', format: 'text' },
      { key: 'note', label: 'Note', format: 'text' },
    ],
    rows: [
      { name: '=HYPERLINK("bad")', note: 'comma, quote " and newline\nvalue' },
      { name: '+SUM(1,2)', note: '@command' },
      { name: '-10+20', note: '\tformula' },
    ],
    source_markers: [1],
  };

  const csv = tableCsv(table);

  expect(csv).toContain('"\'=HYPERLINK(""bad"")"');
  expect(csv).toContain('"\'+SUM(1,2)"');
  expect(csv).toContain('"\'-10+20"');
  expect(csv).toContain('"\'@command"');
  expect(csv).toContain('"\'\tformula"');
  expect(csv).toContain('\r\n');
});

test('JSON export contains exactly the validated analytics object', () => {
  expect(JSON.parse(analyticsJson(artifact))).toEqual(artifact);
  expect(analyticsJson(artifact)).not.toContain('content_hash');
});

test('analytical Markdown never creates model-controlled links', () => {
  const linked: AnalyticsResponseV1 = structuredClone(artifact);
  const explainer = linked.blocks.find((block) => block.kind === 'explainer');
  if (!explainer || explainer.kind !== 'explainer') throw new Error('Expected explainer');
  explainer.body_markdown = 'Review the [internal report](/relative-path) [1].';

  render(<AnalyticsArtifact artifact={linked} />);

  expect(screen.getByText('internal report')).toBeVisible();
  expect(screen.queryByRole('link', { name: 'internal report' })).not.toBeInTheDocument();
});

test('download uses and revokes a local Blob URL', () => {
  const createObjectURL = vi.fn(() => 'blob:openrag-export');
  const revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });

  downloadText('Revenue dashboard', 'json', 'application/json', '{}');

  expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
  expect(click).toHaveBeenCalledOnce();
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:openrag-export');
  click.mockRestore();
});
