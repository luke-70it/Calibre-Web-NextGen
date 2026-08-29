export interface NamedPreferenceMe {
  role?: { anonymous?: boolean };
  preferences?: Record<string, boolean | null>;
}

/** Pure decision core for useNamedPreference, kept separate so the first-render,
 * adoption, and guest/offline contracts have dependency-free unit coverage. */
export function resolveNamedPreferenceState(
  me: NamedPreferenceMe | null | undefined,
  name: string,
  localValue: boolean,
  storedValue: boolean | null,
) {
  const isGuest = !!me?.role?.anonymous;
  const hasServerSlot = !!me?.preferences
    && Object.prototype.hasOwnProperty.call(me.preferences, name);
  const serverValue = hasServerSlot ? me?.preferences?.[name] : undefined;
  const canPersist = !!me && !isGuest && hasServerSlot;

  return {
    isGuest,
    hasServerSlot,
    serverValue,
    value: (!isGuest && typeof serverValue === 'boolean')
      ? serverValue
      : localValue,
    canPersist,
    shouldAdopt: canPersist && serverValue === null && storedValue !== null,
  };
}
