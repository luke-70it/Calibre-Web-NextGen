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
 * SMTP settings, queues a genuine send, waits for the worker's DNS failure,
 * then renders that same task in both UIs. Running one copy avoids racing the
 * shared seeded admin's settings between Playwright projects.
 */
test('failed send exposes its reason, start time, and SPA-native book link in both task UIs', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one real failed-send flow covers both UIs and the 375px viewport');

  await page.goto('/app');
  const errors = collectPageErrors(page);
  const csrf = await csrfToken(page);
  const headers = { 'X-CSRFToken': csrf };

  const originalResponse = await page.request.get('/api/v1/admin/mailsettings');
  expect(originalResponse.ok(), 'reading the original SMTP settings should succeed').toBeTruthy();
  const original = (await originalResponse.json()) as MailSettings;

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
    const book = books.items.find((item) =>
      item.formats.some((format) => format.toLowerCase() === 'epub'));
    expect(book, 'the e2e seed must contain an EPUB book for the real send failure').toBeTruthy();

    const queued = await page.request.post(`/api/v1/books/${book!.id}/send`, {
      headers,
      data: { format: 'epub', emails: 'probe@example.invalid' },
    });
    expect(queued.ok(), await queued.text()).toBeTruthy();

    let failedTask: TaskItem | undefined;
    await expect.poll(async () => {
      const response = await page.request.get('/api/v1/tasks');
      if (!response.ok()) return `HTTP ${response.status()}`;
      const body = (await response.json()) as { items: TaskItem[] };
      failedTask = body.items.find((item) => item.taskMessage.includes(`/book/${book!.id}`));
      return failedTask?.status;
    }, {
      message: 'the real SMTP task should fail in the worker',
      timeout: 20_000,
      intervals: [250, 500, 1_000],
    }).toBe('Failed');

    expect(failedTask?.starttime, 'failed task payload should carry its start time').toBeTruthy();
    expect(failedTask?.error, 'failed task payload should carry its failure reason')
      .toMatch(/Socket Error sending e-mail:.*(Name or service not known|nodename nor servname)/i);

    await page.goto('/app/tasks');
    const spaRow = page.getByRole('row').filter({ hasText: 'Failed' }).last();
    await expect(spaRow).toBeVisible();
    const bookLink = spaRow.getByRole('link', { name: book!.title });
    await expect(bookLink).toBeVisible();
    await expect(bookLink).toHaveAttribute('href', new RegExp(`/app/book/${book!.id}$`));
    await expect(spaRow).toContainText(failedTask!.starttime!);
    await expect(spaRow).toContainText(failedTask!.error!);
    await expect(spaRow).not.toContainText('<a href=');

    await bookLink.click();
    await expect(page).toHaveURL(new RegExp(`/app/book/${book!.id}$`));
    await expect(page.getByRole('heading', { name: book!.title }).first()).toBeVisible();

    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/app/tasks');
    await expect(page.getByRole('row').filter({ hasText: 'Failed' }).last()).toBeVisible();
    await assertNoHorizontalOverflow(page);

    await page.context().addCookies([{
      name: 'cwng_prefer_spa',
      value: '0',
      url: new URL(page.url()).origin,
    }]);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
    const classicRow = page.locator('#tasktable tbody tr').filter({ hasText: book!.title });
    await expect(classicRow).toBeVisible();
    await expect(classicRow.getByRole('link', { name: book!.title })).toHaveAttribute(
      'href',
      `/book/${book!.id}`,
    );
    await expect(classicRow).toContainText(failedTask!.starttime!);
    await expect(classicRow).toContainText(failedTask!.error!);

    assertNoPageErrors(errors);
  } finally {
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
