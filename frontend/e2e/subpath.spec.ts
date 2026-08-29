import { test, expect } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Reverse-proxy sub-path — the v4.1.1 (reader CSS 404) and #571 (white page)
 * class. Runs only when E2E_SUBPATH_URL points at the cwn-nginx-571 rig, via the
 * `subpath` project. Asserts the app boots under a base path with assets served
 * (no 404s / white page) and navigation stays inside the prefix.
 */
test('SPA boots and serves assets under a reverse-proxy sub-path', async ({ page }) => {
  const errors = collectPageErrors(page);
  const bad404s: string[] = [];
  page.on('response', (r) => {
    if (r.status() === 404 && /\.(js|css|png|svg|woff2?)(\?|$)/.test(r.url())) bad404s.push(r.url());
  });

  // Relative navigation preserves the baseURL's /cwa/ mount prefix. A leading
  // slash would silently test the domain root instead of the sub-path rig.
  await page.goto('./app');
  await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();

  // Nav links must carry the base prefix, not resolve to bare root.
  const href = await page.locator('a[href*="/book/"]').first().getAttribute('href');
  expect(href, 'book link lost the sub-path prefix').toContain('/app/book/');

  expect(bad404s, `static assets 404'd under sub-path:\n${bad404s.join('\n')}`).toEqual([]);
  assertNoPageErrors(errors);
});

test('JavaScript-disabled browser self-heals to prefixed Classic', async ({ browser, baseURL }) => {
  const context = await browser.newContext({
    baseURL,
    javaScriptEnabled: false,
    storageState: { cookies: [], origins: [] },
  });
  try {
    const page = await context.newPage();
    await page.goto('./app');
    await expect(page).toHaveURL(/\/cwa\/?\?cwng_feedback=newui$/);
    await expect(page.locator('#books').first()).toBeVisible();
    const classic = (await context.cookies()).find((cookie) =>
      cookie.name === 'cwng_prefer_classic');
    expect(classic?.value).toBe('1');
    expect(classic?.path).toBe('/cwa');
  } finally {
    await context.close();
  }
});

test('Sign out preserves the reverse-proxy sub-path', async ({ page }) => {
  await page.route('**/cwa/logout', (route) => route.fulfill({
    status: 200,
    contentType: 'text/html',
    body: '<title>Logout captured</title>',
  }));
  await page.goto('./app');
  await page.getByRole('button', { name: /account:/i }).click();
  await page.getByText('Sign out', { exact: true }).click();
  await expect(page).toHaveURL(/\/cwa\/logout$/);
});

test('admin hybrid links keep their intended UI and reverse-proxy prefix (#909)', async ({ page }) => {
  await page.goto('./app/admin');

  const duplicates = page.getByRole('link', { name: 'Duplicates', exact: true });
  await expect(duplicates).toHaveAttribute('href', /\/cwa\/app\/duplicates$/);
  await expect(duplicates).not.toContainText('Opens in classic view');

  const classic = page.getByRole('link', { name: /Basic configuration/ });
  await expect(classic).toHaveAttribute('href', '/cwa/admin/config');
  await expect(classic).toContainText('Opens in classic view');
});

test('stale prefixed login honors a prefixed next destination', async ({ page }) => {
  await page.goto(`./app/login?next=${encodeURIComponent('/cwa/')}`);
  await expect(page).toHaveURL(/\/cwa\/app\/?$/);
  await expect(page.locator('a[href*="/cwa/app/book/"]').first()).toBeVisible();
  await expect(page.getByText("This page doesn't exist here.", { exact: true })).toHaveCount(0);
});

test('Classic prefixed login bridge carries a safe next into the SPA', async ({ page }) => {
  await page.goto('./app');
  const bookHref = await page.locator('a[href*="/cwa/app/book/"]').first().getAttribute('href');
  expect(bookHref).toBeTruthy();
  await page.context().clearCookies();

  await page.goto(`./login?next=${encodeURIComponent(bookHref!)}`);
  const bridged = new URL(page.url());
  expect(bridged.pathname).toBe('/cwa/app/');
  expect(bridged.searchParams.get('next')).toBe(bookHref);
  await page.locator('input[autocomplete="username"]').fill(process.env.E2E_USER || 'admin');
  await page.locator('input[autocomplete="current-password"]').fill(process.env.E2E_PASS || 'admin123');
  await page.getByRole('button', { name: /sign in/i }).click();

  await expect(page).toHaveURL(new RegExp(`${bookHref!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/?$`));
  await expect(page.locator('main h1')).toBeVisible();
});

test('prefixed login rejects a same-origin path outside its mount', async ({ page }) => {
  await page.goto(`./app/login?next=${encodeURIComponent('/admin/config')}`);
  await expect(page).toHaveURL(/\/cwa\/app\/?$/);
  await expect(page.locator('a[href*="/cwa/app/book/"]').first()).toBeVisible();
});
