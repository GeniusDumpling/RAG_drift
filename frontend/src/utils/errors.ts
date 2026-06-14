export function formatErrorMessage(prefix: string, error: unknown): string {
  const detail = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  const trimmed = detail.trim();
  return trimmed ? `${prefix}: ${trimmed}` : prefix;
}
