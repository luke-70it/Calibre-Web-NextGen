/** Browser-local identity for reading-data attribution (#1942 M1).
 *
 * The opaque installation id never appears in an API response and the server
 * stores only its keyed HMAC. If storage or randomUUID is unavailable, callers
 * omit the header and the server deliberately uses the legacy web-reader
 * bucket instead.
 */

export const WEBREADER_INSTALLATION_STORAGE_KEY = 'cwng.webreader.installation-id.v1';
export const WEBREADER_INSTALLATION_HEADER = 'X-CWNG-Webreader-Installation-Id';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function webreaderInstallationId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.localStorage.getItem(WEBREADER_INSTALLATION_STORAGE_KEY);
    if (stored && UUID_RE.test(stored)) return stored;
    if (typeof window.crypto?.randomUUID !== 'function') return null;
    const minted = window.crypto.randomUUID();
    window.localStorage.setItem(WEBREADER_INSTALLATION_STORAGE_KEY, minted);
    return minted;
  } catch {
    // Storage can be denied in hardened/private contexts. Falling back is part
    // of the server contract and must not prevent the reading-data write.
    return null;
  }
}

export function webreaderDeviceHeaders(base?: HeadersInit): Headers {
  const headers = new Headers(base);
  const installationId = webreaderInstallationId();
  if (installationId) headers.set(WEBREADER_INSTALLATION_HEADER, installationId);
  return headers;
}
