import { expect, test, type Page } from '@playwright/test';

type SelectedBook = {
  id: number;
  title: string;
};

function libraryBookLinks(page: Page) {
  // Quick-edit links end in /edit. The remaining book links are the cards whose
  // accessible names become "Select <title>" when multi-select mode is active.
  return page.locator('main a[href*="/book/"]:not([href$="/edit"])');
}

test('bulk delete reports and renders the exact partial-failure split (F-567c75, #1831)', async ({ page }) => {
  // Discover can repeat library books outside the catalog grid. Keeping it out
  // makes every captured accessible name belong to exactly one selectable card.
  await page.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));

  const simulatedDeletedIds = new Set<number>();
  await page.route('**/api/v1/books?**', async (route) => {
    const requestUrl = new URL(route.request().url());
    if (route.request().method() !== 'GET' || requestUrl.pathname !== '/api/v1/books') {
      await route.continue();
      return;
    }

    const response = await route.fetch();
    if (!response.ok() || simulatedDeletedIds.size === 0) {
      await route.fulfill({ response });
      return;
    }

    const body = await response.json() as {
      items?: Array<{ id: number }>;
      total?: number;
    };
    const removedFromPage = (body.items ?? []).filter((book) => simulatedDeletedIds.has(book.id)).length;
    body.items = (body.items ?? []).filter((book) => !simulatedDeletedIds.has(book.id));
    if (typeof body.total === 'number') body.total = Math.max(0, body.total - removedFromPage);
    await route.fulfill({ response, json: body });
  });

  await page.goto('/app');
  const links = libraryBookLinks(page);
  await expect(links.first()).toBeVisible();

  const visibleBooks = await links.evaluateAll((bookLinks) => bookLinks.map((link) => {
    const href = (link as HTMLAnchorElement).getAttribute('href') ?? '';
    const id = Number(href.match(/\/book\/(\d+)/)?.[1]);
    const accessibleName = link.getAttribute('aria-label') ?? '';
    return { id, title: accessibleName.replace(/^Open details for /, '') };
  }).filter((book) => Number.isInteger(book.id) && book.title));

  // Prefer three books, but do not pin the spec to a seed's total book count.
  // Repeated titles are excluded because their role/name selector is ambiguous.
  const titleCounts = new Map<string, number>();
  for (const book of visibleBooks) titleCounts.set(book.title, (titleCounts.get(book.title) ?? 0) + 1);
  const selectedBooks = visibleBooks.filter((book) => titleCounts.get(book.title) === 1).slice(0, 3);
  test.skip(selectedBooks.length < 2, 'the seed needs at least two uniquely named visible books');

  await page.getByRole('button', { name: 'Select', exact: true }).click();
  for (const book of selectedBooks) {
    await page.getByRole('button', { name: `Select ${book.title}`, exact: true }).click();
  }
  await expect(page.getByRole('region', { name: `${selectedBooks.length} selected` })).toBeVisible();

  const failedBook = selectedBooks[1];
  const succeededBooks = selectedBooks.filter((book) => book.id !== failedBook.id);
  const deleteCallIds: number[] = [];
  await page.route('**/api/v1/books/*/delete', async (route) => {
    const id = Number(new URL(route.request().url()).pathname.match(/\/books\/(\d+)\/delete$/)?.[1]);
    if (route.request().method() !== 'POST' || !selectedBooks.some((book) => book.id === id)) {
      await route.continue();
      return;
    }

    deleteCallIds.push(id);
    if (id === failedBook.id) {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'Injected partial bulk-delete failure' }),
      });
      return;
    }

    simulatedDeletedIds.add(id);
    await route.fulfill({ status: 204, body: '' });
  });

  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('button', { name: 'Delete', exact: true }).click();

  await expect
    .poll(() => deleteCallIds.length, { message: 'one delete request is issued for every selected book' })
    .toBe(selectedBooks.length);
  expect(new Set(deleteCallIds), 'each selected book is attempted exactly once').toEqual(
    new Set(selectedBooks.map((book) => book.id)),
  );

  const deleteAnnouncement = page.locator('[aria-live]').filter({ hasText: /book\(s\) deleted/ });
  await expect(deleteAnnouncement).toHaveText(
    `${succeededBooks.length} book(s) deleted; 1 failed.`,
  );

  // The failed card remains selected in the current list. Successful cards are
  // absent from that same live view; no page reload is used to reach this state.
  await expect(page.getByRole('button', { name: `Deselect ${failedBook.title}`, exact: true })).toBeVisible();
  for (const book of succeededBooks) {
    await expect(page.getByRole('button', { name: `Select ${book.title}`, exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: `Deselect ${book.title}`, exact: true })).toHaveCount(0);
  }
});
