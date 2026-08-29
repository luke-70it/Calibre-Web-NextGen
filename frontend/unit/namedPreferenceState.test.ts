import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveNamedPreferenceState } from '../src/lib/namedPreferenceState.ts';

test('an authenticated server boolean is authoritative on the first render', () => {
  assert.deepEqual(
    resolveNamedPreferenceState(
      { role: { anonymous: false }, preferences: { discover_hidden: false } },
      'discover_hidden', true, true,
    ),
    {
      isGuest: false,
      hasServerSlot: true,
      serverValue: false,
      value: false,
      canPersist: true,
      shouldAdopt: false,
    },
  );
});

test('an unset account adopts an explicit existing local value', () => {
  const state = resolveNamedPreferenceState(
    { role: { anonymous: false }, preferences: { show_hidden_books: null } },
    'show_hidden_books', true, true,
  );
  assert.equal(state.value, true);
  assert.equal(state.canPersist, true);
  assert.equal(state.shouldAdopt, true);
});

test('an absent local key does not adopt the fallback default', () => {
  const state = resolveNamedPreferenceState(
    { role: { anonymous: false }, preferences: { card_actions_hidden: null } },
    'card_actions_hidden', false, null,
  );
  assert.equal(state.value, false);
  assert.equal(state.canPersist, true);
  assert.equal(state.shouldAdopt, false);
});

test('a guest stays local-only even if /me exposes preference slots', () => {
  const state = resolveNamedPreferenceState(
    { role: { anonymous: true }, preferences: { discover_hidden: null } },
    'discover_hidden', true, true,
  );
  assert.equal(state.value, true);
  assert.equal(state.canPersist, false);
  assert.equal(state.shouldAdopt, false);
});

test('loading and older-server states stay local and never post', () => {
  for (const me of [undefined, { role: { anonymous: false } }]) {
    const state = resolveNamedPreferenceState(
      me, 'discover_hidden', true, true,
    );
    assert.equal(state.value, true);
    assert.equal(state.canPersist, false);
    assert.equal(state.shouldAdopt, false);
  }
});
