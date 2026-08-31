export interface PairingDevice {
  label: string;
  type: string;
  last_seen: string | null;
}

/** Newest Kobo/KOReader check-in that can confirm the pairing reached CWNG. */
export function latestPairingDevice<T extends PairingDevice>(devices: T[]): T | null {
  const eligible = devices.filter((device) => (
    (device.type === 'kobo' || device.type === 'koreader')
    && device.last_seen
    && Number.isFinite(Date.parse(device.last_seen))
  ));
  return eligible.sort((left, right) => (
    Date.parse(right.last_seen as string) - Date.parse(left.last_seen as string)
  ))[0] ?? null;
}

export function koboConfigLine(syncUrl: string): string {
  return `api_endpoint=${syncUrl}`;
}
