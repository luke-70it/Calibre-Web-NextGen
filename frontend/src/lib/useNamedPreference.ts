import { useCallback, useEffect, useRef } from 'react';
import { useMe, useUpdateNamedPreferences } from './queries';
import { usePersistentBool } from './usePersistentBool';

function readStoredBool(key: string): boolean | null {
  try {
    const value = localStorage.getItem(key);
    return value === null ? null : value === '1';
  } catch {
    return null;
  }
}

interface NamedPreferenceOptions {
  onError?: () => void;
}

/** A boolean preference that follows authenticated accounts while retaining a
 * localStorage fallback for guests, offline state, and one-time adoption.
 *
 * /me is loaded before Catalog mounts, so an authoritative server value drives
 * the first render directly. If the server returns null, an explicit existing
 * local value drives that first render and is adopted once. Guests never call
 * the mutation endpoint.
 */
export function useNamedPreference(
  name: string,
  localStorageKey: string,
  fallback: boolean,
  options: NamedPreferenceOptions = {},
) {
  const { data: me } = useMe();
  const update = useUpdateNamedPreferences();
  const [localValue, setLocalValue] = usePersistentBool(localStorageKey, fallback);
  const storedAtMount = useRef<boolean | null>(readStoredBool(localStorageKey));
  const adoptionAttempted = useRef(false);

  const isGuest = !!me?.role?.anonymous;
  const hasServerSlot = !!me?.preferences
    && Object.prototype.hasOwnProperty.call(me.preferences, name);
  const serverValue = hasServerSlot ? me?.preferences?.[name] : undefined;

  // Keep the fallback current for offline use without letting it override the
  // server on an authenticated render.
  useEffect(() => {
    if (typeof serverValue === 'boolean') setLocalValue(serverValue);
  }, [serverValue, setLocalValue]);

  // One-time adoption. An absent local key is not a preference and therefore
  // does not create a pointless write of the fallback default.
  useEffect(() => {
    if (!me || isGuest || !hasServerSlot || serverValue !== null
        || storedAtMount.current === null || adoptionAttempted.current) return;
    adoptionAttempted.current = true;
    update.mutate(
      { [name]: storedAtMount.current },
      {
        onError: () => {
          adoptionAttempted.current = false;
          options.onError?.();
        },
      },
    );
  }, [hasServerSlot, isGuest, me, name, options, serverValue, update]);

  const value = (!isGuest && typeof serverValue === 'boolean')
    ? serverValue
    : localValue;

  const setValue = useCallback((next: boolean) => {
    const previous = value;
    setLocalValue(next);
    if (!me || isGuest || !hasServerSlot) return;
    update.mutate(
      { [name]: next },
      {
        onError: () => {
          setLocalValue(previous);
          options.onError?.();
        },
      },
    );
  }, [hasServerSlot, isGuest, me, name, options, setLocalValue, update, value]);

  return [value, setValue, update.isPending] as const;
}
