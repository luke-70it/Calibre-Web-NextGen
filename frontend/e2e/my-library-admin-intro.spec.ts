import { test, expect } from '@playwright/test';

/*
 * The server-wide "Try My Library" intro card on /app/admin.
 *
 * State hygiene: enabling deliberately mutates EVERY non-guest account —
 * including the shared admin session and any account another worker just
 * created — so this spec asserts through the intro endpoint's own payload
 * (snapshot_accounts / restored_accounts) rather than other users' rows, keeps
 * each enabled window to a few hundred milliseconds, and ALWAYS ends at
 * not_enabled via a finally-guarded undo (which also clears dismissal).
 * Per-account snapshot/restore semantics (role bits both directions, dormant
 * selections, Guest exclusion) are owned by tests/unit/test_my_library_admin_intro.py.
 * A crashed run self-heals: the arrange step undoes leftover enabled state.
 */

async function csrf(page: import('@playwright/test').Page) {
  const res = await page.request.get('/api/v1/auth/csrf');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function introState(page: import('@playwright/test').Page) {
  const res = await page.request.get('/api/v1/admin/my-library/intro');
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as {
    status: string; dismissed: boolean; snapshot_accounts: number;
  };
}

async function undoIfEnabled(page: import('@playwright/test').Page) {
  if ((await introState(page)).status !== 'enabled') return;
  const res = await page.request.post('/api/v1/admin/my-library/intro/undo', {
    headers: { 'X-CSRFToken': await csrf(page) },
  });
  expect(res.ok()).toBeTruthy();
}

test.describe('My Library admin intro card', () => {
  test('try → enabled with undo, undo restores, close dismisses permanently', async ({ page }) => {
    await undoIfEnabled(page);
    // While this spec holds the shared admin in personal mode (the two enable
    // windows), the announcement queue would promote library-intro-v1 above
    // the banners other specs assert on. The queue entry requires the admin's
    // own per-user intro to be undismissed, so dismissing it here (one-way,
    // per-account, and no spec depends on the shared admin seeing it) keeps
    // this spec's global mutation invisible to the banner lane.
    await page.request.post('/api/v1/account/my-library-intro/dismiss', {
      headers: { 'X-CSRFToken': await csrf(page) },
    });

    try {
      await page.goto('/app/admin');
      const card = page.getByRole('region', { name: 'New Feature!' });
      await expect(card).toBeVisible();

      // NOT-ENABLED: full pitch, disabled Undo preview, NO close affordance.
      await expect(card.getByRole('button', { name: 'Try My Library' })).toBeVisible();
      await expect(card.getByRole('button', { name: 'Undo' })).toBeDisabled();
      await expect(card.getByRole('button', { name: 'Close' })).toHaveCount(0);
      await expect(card.getByRole('button', { name: 'Dismiss introduction' })).toHaveCount(0);

      // Try → ENABLED: copy swaps, Undo activates, x-mark appears, and the
      // server reports a snapshot covering every non-guest account.
      await card.getByRole('button', { name: 'Try My Library' }).click();
      await expect(card).toContainText('Explore the changes, you can always undo later.');
      await expect(card.getByRole('button', { name: 'Undo' })).toBeEnabled();
      await expect(card.getByRole('button', { name: 'Close' })).toBeVisible();
      await expect(card.getByRole('button', { name: 'Dismiss introduction' })).toBeVisible();
      const enabled = await introState(page);
      expect(enabled.status).toBe('enabled');
      expect(enabled.snapshot_accounts).toBeGreaterThan(0);

      // Undo → NOT-ENABLED again, snapshot consumed, close affordances gone.
      await card.getByRole('button', { name: 'Undo' }).click();
      await expect(card.getByRole('button', { name: 'Try My Library' })).toBeVisible();
      await expect(card.getByRole('button', { name: 'Close' })).toHaveCount(0);
      const undone = await introState(page);
      expect(undone).toMatchObject({ status: 'not_enabled', dismissed: false, snapshot_accounts: 0 });

      // Enable once more, then Close dismisses permanently (survives reload).
      await card.getByRole('button', { name: 'Try My Library' }).click();
      await expect(card).toContainText('Explore the changes, you can always undo later.');
      await card.getByRole('button', { name: 'Close' }).click();
      await expect(page.getByRole('region', { name: 'New Feature!' })).toHaveCount(0);
      await page.reload();
      await expect(page.getByRole('region', { name: 'New Feature!' })).toHaveCount(0);
      expect((await introState(page)).dismissed).toBe(true);
    } finally {
      // Restore the shared default for the rest of the suite: not_enabled,
      // undismissed, every account's prior mode/role restored server-side.
      await undoIfEnabled(page);
      expect(await introState(page))
        .toMatchObject({ status: 'not_enabled', dismissed: false });
    }
  });
});
