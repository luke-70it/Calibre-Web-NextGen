import { test, expect } from './fixtures';

async function csrfHeaders(page: import('@playwright/test').Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  const payload = await response.json() as { csrf_token: string };
  return { 'X-CSRFToken': payload.csrf_token };
}

test('Discover adopts local hidden state once and follows the account across browsers', async ({
  secondaryUser, browser, baseURL,
}) => {
  const { page, context, username, password } = secondaryUser;
  await expect(page.getByTestId('discover-section')).toBeVisible();

  // The observer starts before React. If Discover ever mounts before /me's
  // server/local preference decision, this catches the visible-then-hide flash.
  await page.addInitScript(() => {
    const windowWithFlag = window as typeof window & { __discoverMounted?: boolean };
    const start = () => {
      const mark = () => {
        if (document.querySelector('[data-testid="discover-section"]')) {
          windowWithFlag.__discoverMounted = true;
        }
      };
      mark();
      new MutationObserver(mark).observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  });
  await page.evaluate(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));

  const adoption = page.waitForResponse((response) =>
    response.url().includes('/api/v1/account/preferences')
    && response.request().method() === 'POST');
  await page.reload();
  expect((await adoption).ok()).toBeTruthy();
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  expect(await page.evaluate(() =>
    (window as typeof window & { __discoverMounted?: boolean }).__discoverMounted ?? false,
  )).toBe(false);

  const adoptedMe = await page.request.get('/api/v1/auth/me');
  expect((await adoptedMe.json() as {
    preferences: { discover_hidden: boolean | null };
  }).preferences.discover_hidden).toBe(true);

  // Browser B gets only the same account cookies, never browser A's storage.
  const browserB = await browser.newContext({ baseURL });
  try {
    await browserB.addCookies(await context.cookies());
    const pageB = await browserB.newPage();
    await pageB.goto('/app');
    await expect(pageB.getByTestId('discover-section')).toHaveCount(0);
    await expect.poll(() => pageB.evaluate(() =>
      localStorage.getItem('cwng_discover_hidden_v1'))).toBe('1');

    // The gear checkbox writes false and makes the section visible.
    await pageB.getByTestId('catalog-view-settings').click();
    const showDiscover = pageB.getByTestId('show-discover-section');
    await expect(showDiscover).not.toBeChecked();
    const showSaved = pageB.waitForResponse((response) =>
      response.url().includes('/api/v1/account/preferences')
      && response.request().method() === 'POST');
    await showDiscover.click();
    expect((await showSaved).ok()).toBeTruthy();
    await expect(showDiscover).toBeChecked();
    await expect(pageB.getByTestId('discover-section')).toBeVisible();

    // Browser A sees Browser B's choice after local storage is removed.
    await page.evaluate(() => localStorage.removeItem('cwng_discover_hidden_v1'));
    await page.reload();
    await expect(page.getByTestId('discover-section')).toBeVisible();

    // The section's × writes through too.
    const hideSaved = page.waitForResponse((response) =>
      response.url().includes('/api/v1/account/preferences')
      && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Hide Discover section' }).click();
    expect((await hideSaved).ok()).toBeTruthy();
    await expect(page.getByTestId('discover-section')).toHaveCount(0);

    // Logout, clear local storage, log the same account back in: server state wins.
    const logout = await browserB.request.post('/api/v1/auth/logout', {
      headers: await csrfHeaders(pageB),
    });
    expect(logout.status()).toBe(204);
    await pageB.goto('/app');
    await pageB.evaluate(() => localStorage.removeItem('cwng_discover_hidden_v1'));
    const login = await browserB.request.post('/api/v1/auth/login', {
      headers: await csrfHeaders(pageB),
      data: { username, password, remember: false },
    });
    expect(login.ok(), await login.text()).toBeTruthy();
    await pageB.goto('/app');
    await expect(pageB.getByTestId('discover-section')).toHaveCount(0);
    await expect.poll(() => pageB.evaluate(() =>
      localStorage.getItem('cwng_discover_hidden_v1'))).toBe('1');
  } finally {
    await browserB.close();
  }
});

test('guest Discover stays local and never posts an account preference', async ({ page }) => {
  let preferencePosts = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/account/preferences')) {
      preferencePosts += 1;
    }
  });
  await page.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.role = { ...(me.role ?? {}), anonymous: true };
    me.preferences = { discover_hidden: null };
    await route.fulfill({ response, json: me });
  });

  await page.goto('/app');
  await expect(page.getByTestId('discover-section')).toHaveCount(0);
  await page.getByTestId('catalog-view-settings').click();
  await page.getByTestId('show-discover-section').check();
  await expect(page.getByTestId('discover-section')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('cwng_discover_hidden_v1'))).toBe('0');
  await expect.poll(() => preferencePosts).toBe(0);
});
