import type {
  AnalyticsChartBlockV1,
  AnalyticsColumnV1,
  AnalyticsExplainerBlockV1,
  AnalyticsKpiV1,
  AnalyticsResponseV1,
  AnalyticsSeriesV1,
  AnalyticsTableBlockV1,
} from '@/api/types';

const MAX_ARTIFACT_BYTES = 49_152;
const UNSAFE_SCHEME = /(?:https?|javascript|data):/i;
const UNSAFE_TAG = /<\s*\/?\s*(?:script|iframe|svg|style|object|embed|link|meta)\b/i;
const COLUMN_KEY = /^[A-Za-z][A-Za-z0-9_]{0,39}$/;
const TRENDS = new Set(['up', 'down', 'flat', 'none']);
const COLUMN_FORMATS = new Set(['text', 'number', 'currency', 'percent', 'date']);

type JsonObject = Record<string, unknown>;

function objectValue(value: unknown): JsonObject | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null ? (value as JsonObject) : null;
}

function exactKeys(
  value: JsonObject,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) &&
    Object.keys(value).every((key) => allowed.has(key))
  );
}

function unsafeText(value: string): boolean {
  if (UNSAFE_SCHEME.test(value) || UNSAFE_TAG.test(value)) return true;
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if ((code <= 0x1f && code !== 0x09 && code !== 0x0a && code !== 0x0d) ||
        (code >= 0x7f && code <= 0x9f)) {
      return true;
    }
  }
  return false;
}

function textValue(value: unknown, maxLength: number, allowEmpty = false): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  const characterCount = [...normalized].length;
  if ((!allowEmpty && characterCount === 0) || characterCount > maxLength) return null;
  return unsafeText(normalized) ? null : normalized;
}

function nullableText(value: unknown, maxLength: number): string | null | undefined {
  if (value === null || value === undefined) return null;
  return textValue(value, maxLength) ?? undefined;
}

function markersValue(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 16) return null;
  if (
    value.some(
      (marker) =>
        typeof marker !== 'number' ||
        !Number.isInteger(marker) ||
        marker < 1 ||
        marker > 999,
    )
  ) {
    return null;
  }
  const markers = value as number[];
  return new Set(markers).size === markers.length ? [...markers] : null;
}

function stringList(
  value: unknown,
  { min, max, itemMax }: { min: number; max: number; itemMax: number },
): string[] | null {
  if (!Array.isArray(value) || value.length < min || value.length > max) return null;
  const parsed: string[] = [];
  for (const item of value) {
    const text = textValue(item, itemMax);
    if (text === null) return null;
    parsed.push(text);
  }
  return parsed;
}

function parseKpi(value: unknown): AnalyticsKpiV1 | null {
  const item = objectValue(value);
  if (
    !item ||
    !exactKeys(item, ['label', 'value', 'source_markers'], ['detail', 'trend'])
  ) {
    return null;
  }
  const label = textValue(item.label, 80);
  const displayValue = textValue(item.value, 80);
  const detail = nullableText(item.detail, 200);
  const trend = item.trend === undefined ? 'none' : item.trend;
  const sourceMarkers = markersValue(item.source_markers);
  if (
    label === null ||
    displayValue === null ||
    detail === undefined ||
    typeof trend !== 'string' ||
    !TRENDS.has(trend) ||
    sourceMarkers === null
  ) {
    return null;
  }
  return {
    label,
    value: displayValue,
    detail,
    trend: trend as AnalyticsKpiV1['trend'],
    source_markers: sourceMarkers,
  };
}

function parseSeries(value: unknown, categoryCount: number): AnalyticsSeriesV1 | null {
  const item = objectValue(value);
  if (!item || !exactKeys(item, ['name', 'values'])) return null;
  const name = textValue(item.name, 80);
  if (
    name === null ||
    !Array.isArray(item.values) ||
    item.values.length < 1 ||
    item.values.length > 50 ||
    item.values.length !== categoryCount
  ) {
    return null;
  }
  const values: (number | null)[] = [];
  for (const raw of item.values) {
    if (raw === null) {
      values.push(null);
    } else if (typeof raw === 'number' && Number.isFinite(raw)) {
      values.push(raw);
    } else {
      return null;
    }
  }
  return { name, values };
}

function parseChart(value: JsonObject): AnalyticsChartBlockV1 | null {
  if (
    !exactKeys(value, [
      'kind',
      'title',
      'x_label',
      'y_label',
      'categories',
      'series',
      'source_markers',
    ]) ||
    (value.kind !== 'bar_chart' && value.kind !== 'line_chart')
  ) {
    return null;
  }
  const title = textValue(value.title, 160);
  const xLabel = textValue(value.x_label, 80);
  const yLabel = textValue(value.y_label, 80);
  const categories = stringList(value.categories, {
    min: 1,
    max: 50,
    itemMax: MAX_ARTIFACT_BYTES,
  });
  const sourceMarkers = markersValue(value.source_markers);
  if (
    title === null ||
    xLabel === null ||
    yLabel === null ||
    categories === null ||
    new Set(categories).size !== categories.length ||
    sourceMarkers === null ||
    !Array.isArray(value.series) ||
    value.series.length < 1 ||
    value.series.length > 8
  ) {
    return null;
  }
  const series: AnalyticsSeriesV1[] = [];
  for (const raw of value.series) {
    const parsed = parseSeries(raw, categories.length);
    if (parsed === null) return null;
    series.push(parsed);
  }
  if (new Set(series.map((item) => item.name)).size !== series.length) return null;
  return {
    kind: value.kind,
    title,
    x_label: xLabel,
    y_label: yLabel,
    categories,
    series,
    source_markers: sourceMarkers,
  };
}

function parseColumn(value: unknown): AnalyticsColumnV1 | null {
  const item = objectValue(value);
  if (!item || !exactKeys(item, ['key', 'label'], ['format'])) return null;
  const key = textValue(item.key, 40);
  const label = textValue(item.label, 80);
  const format = item.format === undefined ? 'text' : item.format;
  if (
    key === null ||
    !COLUMN_KEY.test(key) ||
    label === null ||
    typeof format !== 'string' ||
    !COLUMN_FORMATS.has(format)
  ) {
    return null;
  }
  return { key, label, format: format as AnalyticsColumnV1['format'] };
}

function scalarValue(value: unknown): string | number | null | undefined {
  if (value === null) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value === 'string') {
    return textValue(value, MAX_ARTIFACT_BYTES, true) ?? undefined;
  }
  return undefined;
}

function parseTable(value: JsonObject): AnalyticsTableBlockV1 | null {
  if (
    !exactKeys(value, ['kind', 'title', 'columns', 'rows', 'source_markers']) ||
    value.kind !== 'table' ||
    !Array.isArray(value.columns) ||
    value.columns.length < 1 ||
    value.columns.length > 12 ||
    !Array.isArray(value.rows) ||
    value.rows.length > 200
  ) {
    return null;
  }
  const title = textValue(value.title, 160);
  const sourceMarkers = markersValue(value.source_markers);
  if (title === null || sourceMarkers === null) return null;
  const columns: AnalyticsColumnV1[] = [];
  for (const raw of value.columns) {
    const column = parseColumn(raw);
    if (column === null) return null;
    columns.push(column);
  }
  const keys = columns.map((column) => column.key);
  if (new Set(keys).size !== keys.length) return null;
  const expected = new Set(keys);
  const rows: Record<string, string | number | null>[] = [];
  for (const raw of value.rows) {
    const row = objectValue(raw);
    if (
      !row ||
      Object.keys(row).length !== expected.size ||
      Object.keys(row).some((key) => !expected.has(key))
    ) {
      return null;
    }
    const parsedRow: Record<string, string | number | null> = Object.create(null) as Record<
      string,
      string | number | null
    >;
    for (const key of keys) {
      if (!Object.prototype.hasOwnProperty.call(row, key)) return null;
      const scalar = scalarValue(row[key]);
      if (scalar === undefined) return null;
      parsedRow[key] = scalar;
    }
    rows.push(parsedRow);
  }
  return { kind: 'table', title, columns, rows, source_markers: sourceMarkers };
}

function parseExplainer(value: JsonObject): AnalyticsExplainerBlockV1 | null {
  if (
    !exactKeys(value, ['kind', 'title', 'body_markdown', 'source_markers']) ||
    value.kind !== 'explainer'
  ) {
    return null;
  }
  const title = textValue(value.title, 160);
  const body = textValue(value.body_markdown, 8_000);
  const sourceMarkers = markersValue(value.source_markers);
  return title === null || body === null || sourceMarkers === null
    ? null
    : {
        kind: 'explainer',
        title,
        body_markdown: body,
        source_markers: sourceMarkers,
      };
}

function parseBlock(
  value: unknown,
): AnalyticsChartBlockV1 | AnalyticsTableBlockV1 | AnalyticsExplainerBlockV1 | null {
  const block = objectValue(value);
  if (!block || typeof block.kind !== 'string') return null;
  if (block.kind === 'bar_chart' || block.kind === 'line_chart') return parseChart(block);
  if (block.kind === 'table') return parseTable(block);
  if (block.kind === 'explainer') return parseExplainer(block);
  return null;
}

function serializedSize(value: unknown): number | null {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength;
  } catch {
    return null;
  }
}

export function parseAnalyticsArtifact(value: unknown): AnalyticsResponseV1 | null {
  try {
    const root = objectValue(value);
    if (
      !root ||
      !exactKeys(
        root,
        ['schema_version', 'title', 'kpis', 'blocks', 'suggested_followups'],
        ['subtitle'],
      ) ||
      root.schema_version !== 'analytics.v1' ||
      !Array.isArray(root.kpis) ||
      root.kpis.length > 8 ||
      !Array.isArray(root.blocks) ||
      root.blocks.length < 1 ||
      root.blocks.length > 12
    ) {
      return null;
    }
    const title = textValue(root.title, 160);
    const subtitle = nullableText(root.subtitle, 240);
    const followups = stringList(root.suggested_followups, { min: 0, max: 5, itemMax: 240 });
    if (title === null || subtitle === undefined || followups === null) return null;
    const normalizedFollowups = followups.map((item) => item.split(/\s+/u).join(' ').toLowerCase());
    if (new Set(normalizedFollowups).size !== normalizedFollowups.length) return null;

    const kpis: AnalyticsKpiV1[] = [];
    for (const raw of root.kpis) {
      const kpi = parseKpi(raw);
      if (kpi === null) return null;
      kpis.push(kpi);
    }
    const blocks: AnalyticsResponseV1['blocks'] = [];
    for (const raw of root.blocks) {
      const block = parseBlock(raw);
      if (block === null) return null;
      blocks.push(block);
    }
    const artifact: AnalyticsResponseV1 = {
      schema_version: 'analytics.v1',
      title,
      subtitle,
      kpis,
      blocks,
      suggested_followups: followups,
    };
    const size = serializedSize(artifact);
    return size !== null && size <= MAX_ARTIFACT_BYTES ? artifact : null;
  } catch {
    return null;
  }
}

export function parseHistoricalAnalyticsArtifacts(value: unknown): AnalyticsResponseV1 | null {
  if (!Array.isArray(value) || value.length > 8) return null;
  let found: AnalyticsResponseV1 | null = null;
  for (const raw of value) {
    const item = objectValue(raw);
    if (!item || item.kind !== 'analytics' || item.schema_version !== 'analytics.v1') continue;
    if (found !== null) return null;
    const artifact = parseAnalyticsArtifact(item.artifact);
    if (!artifact) return null;
    found = artifact;
  }
  return found;
}
