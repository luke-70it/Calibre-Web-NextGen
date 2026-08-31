import assert from 'node:assert/strict';
import test from 'node:test';

import { koboConfigLine, latestPairingDevice } from '../src/lib/koboPairing.ts';

test('stock Kobo config uses the server-provided token URL verbatim', () => {
  const url = 'https://books.example.test/kobo/0123456789abcdef';
  assert.equal(koboConfigLine(url), `api_endpoint=${url}`);
});

test('device-seen confirmation chooses the newest Kobo or KOReader check-in', () => {
  const newest = latestPairingDevice([
    { label: 'Browser', type: 'webreader', last_seen: '2026-08-30T15:00:00Z' },
    { label: 'Old Kobo', type: 'kobo', last_seen: '2026-08-28T15:00:00Z' },
    { label: 'Kitchen KOReader', type: 'koreader', last_seen: '2026-08-30T14:00:00Z' },
    { label: 'Never synced', type: 'kobo', last_seen: null },
  ]);
  assert.equal(newest?.label, 'Kitchen KOReader');
});

test('device-seen confirmation stays waiting for missing or invalid check-ins', () => {
  assert.equal(latestPairingDevice([]), null);
  assert.equal(latestPairingDevice([
    { label: 'Kobo', type: 'kobo', last_seen: 'not-a-date' },
    { label: 'Browser', type: 'webreader', last_seen: '2026-08-30T15:00:00Z' },
  ]), null);
});
