import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { execFileSync } from 'node:child_process';
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

interface ScheduledSeed {
  sendId: number;
  operationId: number;
}

const SCHEDULE_CONTAINER = process.env.E2E_CONTAINER_NAME;

function containerPython(source: string, ...args: string[]): string {
  if (!SCHEDULE_CONTAINER) {
    throw new Error('E2E_CONTAINER_NAME must name the isolated app container for scheduled-queue tests');
  }
  return execFileSync('docker', [
    'exec', '-w', '/app/calibre-web-automated', SCHEDULE_CONTAINER,
    'python3', '-c', source, ...args,
  ], { encoding: 'utf8' });
}

function markedJson<T>(output: string, marker: string): T {
  const line = output.split(/\r?\n/).find((candidate) => candidate.startsWith(marker));
  if (!line) throw new Error(`container helper did not emit ${marker}:\n${output}`);
  return JSON.parse(line.slice(marker.length)) as T;
}

function seedScheduledQueues(bookId: number, userId: number, username: string): ScheduledSeed {
  const output = containerPython(`
import json, sys
from datetime import datetime, timedelta, timezone
from cps.cwa_db_loader import load_cwa_db

db = load_cwa_db().CWA_DB()
run_at = (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat().replace('+00:00', 'Z')
send_id = db.scheduled_add_autosend(int(sys.argv[1]), int(sys.argv[2]), run_at, sys.argv[3], 'Scheduled queue E2E send')
operation_id = db.scheduled_add_job('epub_fixer', run_at, username=sys.argv[3], title='Scheduled queue E2E operation')
print('SCHEDULE_SEED=' + json.dumps({'sendId': send_id, 'operationId': operation_id}))
`, String(bookId), String(userId), username);
  return markedJson<ScheduledSeed>(output, 'SCHEDULE_SEED=');
}

function scheduledState(id: number): string | null {
  const output = containerPython(`
import json, sys
from cps.cwa_db_loader import load_cwa_db
row = load_cwa_db().CWA_DB().scheduled_get_by_id(int(sys.argv[1]))
print('SCHEDULE_STATE=' + json.dumps(None if row is None else row.get('state')))
`, String(id));
  return markedJson<string | null>(output, 'SCHEDULE_STATE=');
}

function removeScheduledSeeds(ids: number[]): void {
  containerPython(`
import sys
from cps.cwa_db_loader import load_cwa_db
db = load_cwa_db().CWA_DB()
db.cur.executemany('DELETE FROM cwa_scheduled_jobs WHERE id=?', [(int(value),) for value in sys.argv[1:]])
db.con.commit()
`, ...ids.map(String));
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

/**
 * F-f61640 — one serial, real-stack flow owns the shared cwa.db fixture. Rows
 * are inserted through the same CWA_DB methods used by the scheduler, then all
 * assertions go through the real HTTP/UI surfaces. The post-cancel state is
 * read independently from cwa.db so a 200 response or a filtered-out row can
 * never masquerade as proof that cancellation persisted.
 *
 * The docker exec is intentional fixture setup, not a product-layer shortcut.
 * The production HTTP scheduling path is localhost-gated and is unreachable in
 * this harness, so the spec seeds the job's own isolated container through the
 * exact CWA_DB.scheduled_add_autosend / scheduled_add_job methods production
 * scheduling uses. Product behaviour is still exercised through the browser
 * and real HTTP endpoints; the matching CWA_DB read only independently proves
 * that cancellation persisted instead of trusting a 200 or a missing row.
 */
test('admin can inspect and cancel persisted scheduled queues; non-admin cannot', async ({ page, browser, baseURL }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'one persisted scheduler flow covers desktop and 375px');
  if (!SCHEDULE_CONTAINER) {
    const reason = 'requires E2E_CONTAINER_NAME for isolated cwa.db seeding; refusing to guess a developer container';
    console.log(`[scheduled-queues] SKIP: ${reason}`);
    test.skip(true, reason);
  }

  await page.goto('/app');
  const errors = collectPageErrors(page);
  const csrf = await csrfToken(page);
  const headers = { 'X-CSRFToken': csrf };

  const meResponse = await page.request.get('/api/v1/auth/me');
  expect(meResponse.ok(), 'admin identity request should succeed').toBeTruthy();
  const me = (await meResponse.json()) as { id: number; name: string; role: { admin?: boolean } };
  expect(me.role.admin, 'the seeded Playwright account must be an admin').toBe(true);

  const booksResponse = await page.request.get('/api/v1/books?per_page=1');
  expect(booksResponse.ok(), 'book seed request should succeed').toBeTruthy();
  const books = (await booksResponse.json()) as { items: Array<{ id: number }> };
  expect(books.items.length, 'the e2e seed must contain a book for the scheduled send').toBeGreaterThan(0);

  const username = `scheduled-e2e-${Date.now()}`;
  const password = 'CWNG-scheduled-E2E-42!';
  const created = await page.request.post('/api/v1/admin/users', {
    headers,
    data: {
      name: username,
      email: `${username}@example.test`,
      password,
      roles: { viewer: true, download: true, admin: false },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const nonAdmin = (await created.json()) as { id: number };

  let seeded: ScheduledSeed | undefined;
  let nonAdminContext: Awaited<ReturnType<typeof browser.newContext>> | undefined;

  try {
    seeded = seedScheduledQueues(books.items[0].id, me.id, me.name);
    nonAdminContext = await browser.newContext({ baseURL });
    const nonAdminPage = await nonAdminContext.newPage();

    const sends = await page.request.get('/cwa-scheduled/upcoming');
    const operations = await page.request.get('/cwa-scheduled/upcoming-ops');
    expect(sends.ok(), await sends.text()).toBeTruthy();
    expect(operations.ok(), await operations.text()).toBeTruthy();
    expect((await sends.json()).items).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: seeded.sendId, state: 'scheduled' }),
    ]));
    expect((await operations.json()).items).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: seeded.operationId, state: 'scheduled', job_type: 'epub_fixer' }),
    ]));

    await page.goto('/app/tasks');
    const sendsSection = page.getByRole('region', { name: 'Upcoming scheduled sends' });
    const operationsSection = page.getByRole('region', { name: 'Upcoming scheduled operations' });
    await expect(sendsSection).toBeVisible();
    await expect(operationsSection).toBeVisible();
    const sendRow = sendsSection.getByRole('row').filter({ hasText: 'Scheduled queue E2E send' });
    const operationRow = operationsSection.getByRole('row').filter({ hasText: 'Scheduled queue E2E operation' });
    await expect(sendRow).toContainText('scheduled');
    await expect(operationRow).toContainText('scheduled');
    await expect(sendRow.locator('time')).not.toHaveText('');
    await expect(operationRow.locator('time')).not.toHaveText('');
    const accessibility = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze();
    expect(
      accessibility.violations
        .filter((violation) => ['critical', 'serious'].includes(violation.impact ?? ''))
        .map((violation) => `${violation.id}: ${violation.help}`),
      'populated scheduled queues must have no critical/serious accessibility violations',
    ).toEqual([]);

    await page.context().addCookies([{
      name: 'cwng_prefer_spa',
      value: '0',
      url: new URL(page.url()).origin,
    }]);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#upcomingtable tbody tr').filter({ hasText: 'Scheduled queue E2E send' })).toContainText('scheduled');
    await expect(page.locator('#upcomingopstable tbody tr').filter({ hasText: 'Scheduled queue E2E operation' })).toContainText('scheduled');

    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/app/tasks');
    await assertNoHorizontalOverflow(page);

    page.once('dialog', async (dialog) => {
      expect(dialog.type()).toBe('confirm');
      expect(dialog.message()).toContain('Scheduled queue E2E send');
      expect(dialog.message()).toContain('cannot be undone');
      await dialog.dismiss();
    });
    await sendRow.getByRole('button', { name: /cancel/i }).click();
    expect(scheduledState(seeded.sendId), 'dismissing confirmation must not mutate the row').toBe('scheduled');
    await expect(sendRow).toContainText('scheduled');

    page.once('dialog', async (dialog) => {
      expect(dialog.type()).toBe('confirm');
      await dialog.accept();
    });
    await sendRow.getByRole('button', { name: /cancel/i }).click();
    await expect.poll(() => scheduledState(seeded.sendId), {
      message: 'cancellation should persist the send state transition in cwa.db',
    }).toBe('cancelled');
    await expect(sendRow).toHaveCount(0);
    await expect(operationRow).toContainText('scheduled');

    const nonAdminCsrf = await csrfToken(nonAdminPage);
    const login = await nonAdminPage.request.post('/api/v1/auth/login', {
      headers: { 'X-CSRFToken': nonAdminCsrf },
      data: { username, password },
    });
    expect(login.ok(), await login.text()).toBeTruthy();

    const forbiddenSends = await nonAdminPage.request.get('/cwa-scheduled/upcoming');
    const forbiddenOperations = await nonAdminPage.request.get('/cwa-scheduled/upcoming-ops');
    expect(forbiddenSends.status(), 'non-admin scheduled-send data must be server-forbidden').toBe(403);
    expect(forbiddenOperations.status(), 'non-admin scheduled-operation data must be server-forbidden').toBe(403);
    const forbiddenCancel = await nonAdminPage.request.post('/cwa-scheduled/cancel', {
      data: { id: seeded.operationId },
    });
    expect(forbiddenCancel.status(), 'non-admin scheduled cancellation must be server-forbidden').toBe(403);
    expect(scheduledState(seeded.operationId), 'a forbidden cancellation must not mutate the row').toBe('scheduled');

    await nonAdminPage.goto('/app/tasks');
    await expect(nonAdminPage.getByRole('region', { name: 'Upcoming scheduled sends' })).toHaveCount(0);
    await expect(nonAdminPage.getByRole('region', { name: 'Upcoming scheduled operations' })).toHaveCount(0);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#upcomingtable tbody tr').filter({ hasText: 'Scheduled queue E2E send' })).toHaveCount(0);
    await expect(page.locator('#upcomingopstable tbody tr').filter({ hasText: 'Scheduled queue E2E operation' })).toContainText('scheduled');

    assertNoPageErrors(errors);
  } finally {
    await nonAdminContext?.close();
    if (seeded) removeScheduledSeeds([seeded.sendId, seeded.operationId]);
    const deleted = await page.request.post(`/api/v1/admin/users/${nonAdmin.id}/delete`, { headers });
    expect(deleted.status(), await deleted.text()).toBe(204);
  }
});
