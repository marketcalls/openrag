import type { AnalyticsResponseV1, AnalyticsTableBlockV1 } from '@/api/types';

import { parseAnalyticsArtifact } from './contract';

function spreadsheetSafe(value: string): string {
  return /^[=+\-@\t\r]/u.test(value) ? `'${value}` : value;
}

function csvCell(value: string | number | null): string {
  const text = spreadsheetSafe(value === null ? '' : String(value));
  return `"${text.replaceAll('"', '""')}"`;
}

export function tableCsv(table: AnalyticsTableBlockV1): string {
  const header = table.columns.map((column) => csvCell(column.label)).join(',');
  const rows = table.rows.map((row) =>
    table.columns.map((column) => csvCell(row[column.key] ?? null)).join(','),
  );
  return [header, ...rows].join('\r\n');
}

export function analyticsJson(artifact: AnalyticsResponseV1): string {
  const validated = parseAnalyticsArtifact(artifact);
  if (!validated) throw new Error('Invalid analytics artifact');
  return JSON.stringify(validated, null, 2);
}

function safeFilename(value: string): string {
  const filename = value
    .normalize('NFKD')
    .replace(/[^A-Za-z0-9]+/gu, '-')
    .replace(/^-|-$/gu, '')
    .toLowerCase()
    .slice(0, 80);
  return filename || 'openrag-analytics';
}

export function downloadText(
  title: string,
  extension: 'csv' | 'json',
  contentType: string,
  content: string,
): void {
  const url = URL.createObjectURL(new Blob([content], { type: contentType }));
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${safeFilename(title)}.${extension}`;
    anchor.rel = 'noopener';
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}
