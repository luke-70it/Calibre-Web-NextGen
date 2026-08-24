const SPA_READABLE = new Set(['epub', 'kepub']);
const SERVER_READABLE = new Set([
  'pdf', 'txt', 'djvu', 'djv', 'cbz', 'cbr', 'cbt',
  'mp3', 'mp4', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav', 'aac',
]);

export function getPrimaryReadTarget(
  id: number | string,
  formats: string[],
  canRead: boolean,
): string | null {
  if (!canRead) return null;
  const normalized = formats.map((format) => format.toLowerCase());
  if (normalized.some((format) => SPA_READABLE.has(format))) return `/read/${id}`;
  const fallback = normalized.find((format) => SERVER_READABLE.has(format));
  return fallback ? `/view/${id}/${fallback}` : null;
}

export function isReadableFormat(format: string): boolean {
  const normalized = format.toLowerCase();
  return SPA_READABLE.has(normalized) || SERVER_READABLE.has(normalized);
}
