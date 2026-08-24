import { expect, test, type Page } from '@playwright/test';
import { assertNoHorizontalOverflow, assertNoPageErrors, collectPageErrors } from './utils';

interface MailSettings {
  mail_server: string;
  mail_port: number;
  mail_use_ssl: number;
  mail_login: string;
  mail_from: string;
  mail_size_mb: number;
  mail_server_type: number;
  has_password: boolean;
}

interface TaskItem {
  task_id: string;
  taskMessage: string;
  status?: string;
  starttime?: string;
  error?: string | null;
}

async function csrfToken(page: Page): Promise<string> {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok(), 'CSRF token request should succeed').toBeTruthy();
  return ((await response.json()) as { csrf_token: string }).csrf_token;
}

/**
 * F-57c90e / F-b19131 — this is deliberately one real, serial flow. It changes
 * SMTP settings, queues a genuine conversion and send, waits for both workers,
 * then renders those same tasks in both UIs. Running one copy avoids racing the
 * shared seeded admin's settings and formats between Playwright projects.
 */
test('convert and failed send expose SPA-native book links in both task UIs', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one real worker flow covers both UIs and the 375px viewport');

  await page.goto('/app');
  const errors = collectPageErrors(page);
  const csrf = await csrfToken(page);
  const headers = { 'X-CSRFToken': csrf };

  const originalResponse = await page.request.get('/api/v1/admin/mailsettings');
  expect(originalResponse.ok(), 'reading the original SMTP settings should succeed').toBeTruthy();
  const original = (await originalResponse.json()) as MailSettings;
  let convertedBookId: number | undefined;

  try {
    const configure = await page.request.post('/api/v1/admin/mailsettings', {
      headers,
      data: {
        mail_server: 'smtp-probe.invalid',
        mail_port: 25,
        mail_use_ssl: 0,
        mail_login: '',
        mail_from: 'probe@example.invalid',
        mail_size_mb: original.mail_size_mb,
        mail_server_type: 0,
      },
    });
    expect(configure.ok(), await configure.text()).toBeTruthy();

    const booksResponse = await page.request.get('/api/v1/books?per_page=200');
    expect(booksResponse.ok(), 'book seed request should succeed').toBeTruthy();
    const books = (await booksResponse.json()) as {
      items: Array<{ id: number; title: string; formats: string[] }>;
    };
    const book = books.items.find((item) => {
      const formats = item.formats.map((format) => format.toLowerCase());
      return formats.includes('epub') && !formats.includes('mobi');
    });
    expect(book, 'the e2e seed must contain an EPUB-only book for the real conversion').toBeTruthy();
    convertedBookId = book!.id;

    const convert = await page.request.post(`/api/v1/books/${book!.id}/convert`, {
      headers,
      data: { from: 'EPUB', to: 'MOBI' },
    });
    expect(convert.ok(), await convert.text()).toBeTruthy();

    const queued = await page.request.post(`/api/v1/books/${book!.id}/send`, {
      headers,
      data: { format: 'epub', emails: 'probe@example.invalid' },
    });
    expect(queued.ok(), await queued.text()).toBeTruthy();

    let convertTask: TaskItem | undefined;
    let failedTask: TaskItem | undefined;
    await expect.poll(async () => {
      const response = await page.request.get('/api/v1/tasks');
      if (!response.ok()) return `HTTP ${response.status()}`;
      const body = (await response.json()) as { items: TaskItem[] };
      convertTask = body.items.find((item) =>
        item.taskMessage.includes('EPUB -> MOBI') && item.taskMessage.includes(`/book/${book!.id}`));
      failedTask = body.items.find((item) =>
        item.taskMessage.includes('send to eReader') && item.taskMessage.includes(`/book/${book!.id}`));
      return `${convertTask?.status}/${failedTask?.status}`;
    }, {
      message: 'the real conversion should finish and SMTP task should fail in the worker',
      timeout: 30_000,
      intervals: [250, 500, 1_000],
    }).toBe('Finished/Failed');

    expect(failedTask?.starttime, 'failed task payload should carry its start time').toBeTruthy();
    expect(failedTask?.error, 'failed task payload should carry its failure reason')
      .toMatch(/Socket Error sending e-mail:.*(Name or service not known|nodename nor servname)/i);

    await page.goto('/app/tasks');
    const convertSpaRow = page.getByRole('row').filter({ hasText: 'EPUB -> MOBI' }).last();
    const sendSpaRow = page.getByRole('row').filter({ hasText: 'Failed' }).last();
    await expect(convertSpaRow).toBeVisible();
    await expect(sendSpaRow).toBeVisible();
    const convertBookLink = convertSpaRow.getByRole('link', { name: book!.title });
    const sendBookLink = sendSpaRow.getByRole('link', { name: book!.title });
    await expect(convertBookLink).toHaveAttribute('href', new RegExp(`/app/book/${book!.id}$`));
    await expect(sendBookLink).toHaveAttribute('href', new RegExp(`/app/book/${book!.id}$`));
    await expect(sendSpaRow).toContainText(failedTask!.starttime!);
    await expect(sendSpaRow).toContainText(failedTask!.error!);
    await expect(convertSpaRow).not.toContainText('<a href=');
    await expect(sendSpaRow).not.toContainText('<a href=');

    await convertBookLink.click();
    await expect(page).toHaveURL(new RegExp(`/app/book/${book!.id}$`));
    await expect(page.getByRole('heading', { name: book!.title }).first()).toBeVisible();

    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/app/tasks');
    await expect(page.getByRole('row').filter({ hasText: 'EPUB -> MOBI' }).last()).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: 'Failed' }).last()).toBeVisible();
    await assertNoHorizontalOverflow(page);

    await page.context().addCookies([{
      name: 'cwng_prefer_spa',
      value: '0',
      url: new URL(page.url()).origin,
    }]);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
    const convertClassicRow = page.locator('#tasktable tbody tr').filter({ hasText: 'EPUB -> MOBI' }).last();
    const sendClassicRow = page.locator('#tasktable tbody tr').filter({ hasText: 'Failed' }).last();
    await expect(convertClassicRow).toBeVisible();
    await expect(sendClassicRow).toBeVisible();
    const convertClassicLink = convertClassicRow.getByRole('link', { name: book!.title });
    const sendClassicLink = sendClassicRow.getByRole('link', { name: book!.title });
    await expect(convertClassicLink).toHaveAttribute('href', `/book/${book!.id}`);
    await expect(sendClassicLink).toHaveAttribute('href', `/book/${book!.id}`);
    await expect(sendClassicRow).toContainText(failedTask!.starttime!);
    await expect(sendClassicRow).toContainText(failedTask!.error!);

    await convertClassicLink.click();
    await expect(page).toHaveURL(new RegExp(`/book/${book!.id}$`));
    await expect(page.getByText(book!.title, { exact: true }).first()).toBeVisible();
    await page.waitForLoadState('networkidle');

    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
    await page.locator('#tasktable tbody tr').filter({ hasText: 'Failed' }).last()
      .getByRole('link', { name: book!.title }).click();
    await expect(page).toHaveURL(new RegExp(`/book/${book!.id}$`));
    await expect(page.getByText(book!.title, { exact: true }).first()).toBeVisible();
    await page.waitForLoadState('networkidle');

    assertNoPageErrors(errors);
  } finally {
    if (convertedBookId !== undefined) {
      await page.request.post(`/api/v1/books/${convertedBookId}/formats/MOBI/delete`, { headers });
    }
    await page.request.post('/api/v1/admin/mailsettings', {
      headers,
      data: {
        mail_server: original.mail_server,
        mail_port: original.mail_port,
        mail_use_ssl: original.mail_use_ssl,
        mail_login: original.mail_login,
        mail_from: original.mail_from,
        mail_size_mb: original.mail_size_mb,
        mail_server_type: original.mail_server_type,
      },
    });
  }
});
