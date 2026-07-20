import { parseAnalyticsArtifact, parseHistoricalAnalyticsArtifacts } from './contract';

function validArtifact(): Record<string, unknown> {
  return {
    schema_version: 'analytics.v1',
    title: 'Revenue dashboard',
    subtitle: 'Grounded Q4 performance',
    kpis: [
      {
        label: 'Q4 revenue',
        value: '$4.83M',
        detail: 'Up from Q3',
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
        source_markers: [1, 2],
      },
      {
        kind: 'line_chart',
        title: 'Revenue trend',
        x_label: 'Month',
        y_label: 'Revenue ($M)',
        categories: ['October', 'November', 'December'],
        series: [{ name: 'Revenue', values: [1.42, null, 1.84] }],
        source_markers: [2],
      },
      {
        kind: 'table',
        title: 'Revenue summary',
        columns: [
          { key: 'month', label: 'Month', format: 'text' },
          { key: 'revenue', label: 'Revenue', format: 'currency' },
        ],
        rows: [
          { month: 'October', revenue: 1.42 },
          { month: 'November', revenue: 1.57 },
        ],
        source_markers: [1],
      },
      {
        kind: 'explainer',
        title: 'What changed',
        body_markdown: 'Revenue grew steadily across Q4 [1].',
        source_markers: [1],
      },
    ],
    suggested_followups: ['Break this down by product line'],
  };
}

function clone(): Record<string, unknown> {
  return structuredClone(validArtifact());
}

test('accepts every closed analytics block and returns a detached normalized value', () => {
  const input = validArtifact();
  const parsed = parseAnalyticsArtifact(input);

  expect(parsed?.schema_version).toBe('analytics.v1');
  expect(parsed?.blocks.map((block) => block.kind)).toEqual([
    'bar_chart',
    'line_chart',
    'table',
    'explainer',
  ]);
  expect(parsed).not.toBe(input);
});

test('fills only contract defaults for optional fields', () => {
  const value = clone();
  delete value.subtitle;
  const kpi = (value.kpis as Record<string, unknown>[])[0]!;
  delete kpi.detail;
  delete kpi.trend;
  const table = (value.blocks as Record<string, unknown>[])[2]!;
  delete (table.columns as Record<string, unknown>[])[0]!.format;

  const parsed = parseAnalyticsArtifact(value);

  expect(parsed?.subtitle).toBeNull();
  expect(parsed?.kpis[0]?.detail).toBeNull();
  expect(parsed?.kpis[0]?.trend).toBe('none');
  const parsedTable = parsed?.blocks[2];
  expect(parsedTable?.kind === 'table' ? parsedTable.columns[0]?.format : null).toBe('text');
});

test.each([
  ['unknown top-level field', (value: Record<string, unknown>) => (value.extra = true)],
  ['unknown block kind', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[0]!.kind = 'pie_chart'))],
  ['unknown nested field', (value: Record<string, unknown>) => (((value.kpis as Record<string, unknown>[])[0]!.href = '/x'))],
  ['unsafe URL', (value: Record<string, unknown>) => (value.subtitle = 'https://evil.example')],
  ['unsafe HTML', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[3]!.body_markdown = '<script>alert(1)</script>'))],
  ['control character', (value: Record<string, unknown>) => (value.title = 'Revenue\u0000')],
  ['invalid marker', (value: Record<string, unknown>) => (((value.kpis as Record<string, unknown>[])[0]!.source_markers = [0]))],
  ['duplicate marker', (value: Record<string, unknown>) => (((value.kpis as Record<string, unknown>[])[0]!.source_markers = [1, 1]))],
  ['duplicate follow-up', (value: Record<string, unknown>) => (value.suggested_followups = ['Next step', ' next   STEP '])],
  ['duplicate category', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[0]!.categories = ['October', 'October']))],
  ['duplicate series', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[0]!.series = [{ name: 'Revenue', values: [1.42, 1.57, 1.84] }, { name: 'Revenue', values: [1.42, 1.57, 1.84] }]))],
  ['chart shape mismatch', (value: Record<string, unknown>) => ((((value.blocks as Record<string, unknown>[])[0]!.series as Record<string, unknown>[])[0]!.values = [1]))],
  ['non-finite chart value', (value: Record<string, unknown>) => ((((value.blocks as Record<string, unknown>[])[0]!.series as Record<string, unknown>[])[0]!.values = [1.42, Number.NaN, 1.84]))],
  ['invalid column key', (value: Record<string, unknown>) => (((((value.blocks as Record<string, unknown>[])[2]!.columns as Record<string, unknown>[])[0]!.key = '__proto__')))],
  ['duplicate column', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[2]!.columns = [{ key: 'month', label: 'Month', format: 'text' }, { key: 'month', label: 'Again', format: 'text' }]))],
  ['table row mismatch', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[2]!.rows = [{ month: 'October', extra: 1 }]))],
  ['non-scalar table value', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[2]!.rows = [{ month: 'October', revenue: { nested: true } }]))],
  ['too many KPIs', (value: Record<string, unknown>) => (value.kpis = Array.from({ length: 9 }, () => (value.kpis as unknown[])[0]))],
  ['too many rows', (value: Record<string, unknown>) => (((value.blocks as Record<string, unknown>[])[2]!.rows = Array.from({ length: 201 }, () => ({ month: 'October', revenue: 1.42 }))))],
])('rejects %s without throwing', (_name, mutate) => {
  const value = clone();
  mutate(value);
  expect(() => parseAnalyticsArtifact(value)).not.toThrow();
  expect(parseAnalyticsArtifact(value)).toBeNull();
});

test('rejects an artifact over the UTF-8 byte limit', () => {
  const value = clone();
  (value.blocks as Record<string, unknown>[])[3]!.body_markdown = '界'.repeat(17_000);

  expect(parseAnalyticsArtifact(value)).toBeNull();
});

test.each([null, undefined, [], 'analytics.v1', 1, true])(
  'returns null for a non-object payload: %p',
  (value) => expect(parseAnalyticsArtifact(value)).toBeNull(),
);

test('revalidates a historical message artifact before replay', () => {
  const artifact = validArtifact();
  expect(
    parseHistoricalAnalyticsArtifacts([
      {
        kind: 'analytics',
        schema_version: 'analytics.v1',
        artifact,
      },
    ]),
  ).toEqual(parseAnalyticsArtifact(artifact));

  expect(
    parseHistoricalAnalyticsArtifacts([
      {
        kind: 'analytics',
        schema_version: 'analytics.v1',
        artifact: { ...artifact, renderer: '<iframe>' },
      },
    ]),
  ).toBeNull();
  expect(parseHistoricalAnalyticsArtifacts('not-an-array')).toBeNull();
});
