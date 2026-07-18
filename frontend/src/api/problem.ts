export function problemDetail(body: unknown, fallback = 'Request failed'): string {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
    return body.detail || fallback;
  }
  return fallback;
}
