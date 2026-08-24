import { ListChecks, X } from 'lucide-react';
import { Link } from 'wouter';
import {
  useTasks, useCancelTask, useMe, useScheduledQueues, useCancelScheduledItem,
} from '../lib/queries';
import type { ScheduledOperation, ScheduledSend, TaskItem } from '../lib/api';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Tasks.module.css';

type TaskMessageParts = NonNullable<TaskItem['taskMessageParts']>;

function decodeTaskEntities(text: string) {
  return text.replace(/&(#(?:x[0-9a-f]+|\d+)|amp|lt|gt|quot|apos);/gi, (entity, token: string) => {
    if (token.startsWith('#')) {
      const hex = token[1]?.toLowerCase() === 'x';
      const codePoint = Number.parseInt(token.slice(hex ? 2 : 1), hex ? 16 : 10);
      if (Number.isInteger(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff) {
        try { return String.fromCodePoint(codePoint); } catch { return entity; }
      }
      return entity;
    }
    return ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" } as Record<string, string>)[token.toLowerCase()] ?? entity;
  });
}

function taskMessageParts(task: TaskItem): TaskMessageParts | undefined {
  if (task.taskMessageParts) return task.taskMessageParts;

  // Rolling upgrades and the PR E2E rig can briefly pair this SPA with a
  // server that only has the Classic HTML field. Accept one known, local book
  // anchor surrounded by plain text; arbitrary or additional markup stays
  // visible as plain text.
  const match = /^([^<]*)<a href="\/(?:[^"<>:/]+\/)*book\/(\d+)">([^<]*)<\/a>([^<]*)$/.exec(task.taskMessage);
  if (!match) return undefined;
  return {
    prefix: decodeTaskEntities(match[1]),
    book: { id: match[2], title: decodeTaskEntities(match[3]) },
    suffix: decodeTaskEntities(match[4]),
  };
}

function taskMessageText(task: TaskItem) {
  const parts = taskMessageParts(task);
  return parts
    ? `${parts.prefix}${parts.book.title}${parts.suffix}`
    : task.taskMessage;
}

function TaskMessage({ task }: { task: TaskItem }) {
  const parts = taskMessageParts(task);
  if (!parts) return <>{task.taskMessage}</>;
  return (
    <>
      {parts.prefix}
      <Link href={`/book/${parts.book.id}`} className={styles.bookLink}>{parts.book.title}</Link>
      {parts.suffix}
    </>
  );
}

function scheduledTime(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function operationType(item: ScheduledOperation, t: ReturnType<typeof useT>) {
  if (item.job_type === 'convert_library') return t('Convert Library');
  if (item.job_type === 'epub_fixer') return t('EPUB Fixer');
  return item.job_type;
}

interface ScheduledSectionProps {
  id: string;
  title: string;
  items: Array<ScheduledSend | ScheduledOperation>;
  kind: 'send' | 'operation';
  cancelling: boolean;
  onCancel: (item: ScheduledSend | ScheduledOperation) => void;
}

function ScheduledSection({ id, title, items, kind, cancelling, onCancel }: ScheduledSectionProps) {
  const t = useT();
  return (
    <section className={styles.scheduledSection} aria-labelledby={id}>
      <div className={styles.sectionHeader}>
        <h2 id={id} className={styles.sectionTitle}>{title}</h2>
        <span className={styles.count}>{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className={styles.scheduledEmpty}>
          {kind === 'send' ? t('No upcoming scheduled sends.') : t('No upcoming scheduled operations.')}
        </p>
      ) : (
        <table className={`${styles.table} ${styles.scheduledTable}`}>
          <thead>
            <tr>
              {kind === 'operation' && <th>{t('Type')}</th>}
              <th>{t('Title')}</th>
              <th>{t('User')}</th>
              {kind === 'send' && <th>{t('Book ID')}</th>}
              <th>{t('State')}</th>
              <th aria-label={t('Cancel')} />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                {kind === 'operation' && (
                  <td className={styles.metaCell}>
                    <span className={styles.mobileLabel}>{t('Type')}</span>
                    {operationType(item as ScheduledOperation, t)}
                  </td>
                )}
                <td className={`${styles.taskCell} ${styles.taskCellCancellable}`}>
                  <div className={styles.taskMsg}>{item.title}</div>
                  <div className={styles.taskDetails}>
                    <span className={styles.scheduledTimeLabel}>{t('Scheduled Time')}</span>
                    <time className={styles.taskStart} dateTime={item.run_at_utc}>
                      {scheduledTime(item.run_at_utc)}
                    </time>
                  </div>
                </td>
                <td className={styles.metaCell}>
                  <span className={styles.mobileLabel}>{t('User')}</span>
                  {item.username}
                </td>
                {kind === 'send' && (
                  <td className={styles.metaCell}>
                    <span className={styles.mobileLabel}>{t('Book ID')}</span>
                    {(item as ScheduledSend).book_id}
                  </td>
                )}
                <td className={styles.metaCell}>
                  <span className={styles.mobileLabel}>{t('State')}</span>
                  <span className={styles.state}>{item.state}</span>
                </td>
                <td className={styles.cancelCell}>
                  <button
                    type="button"
                    className={styles.cancelBtn}
                    onClick={() => onCancel(item)}
                    disabled={cancelling}
                    aria-label={t('Cancel {task}', { task: item.title })}
                  >
                    <X size={15} aria-hidden="true" focusable={false} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function Tasks() {
  const { data, isLoading, error } = useTasks();
  const cancel = useCancelTask();
  const { data: me } = useMe();
  const isAdmin = !!me?.role?.admin;
  const scheduled = useScheduledQueues(isAdmin);
  const cancelScheduled = useCancelScheduledItem();
  const t = useT();
  const scheduledFailure = scheduled.error ?? cancelScheduled.error;

  const confirmScheduledCancel = (item: ScheduledSend | ScheduledOperation) => {
    if (!window.confirm(t('Cancel scheduled item "{title}"? This cannot be undone.', {
      title: item.title,
    }))) return;
    cancelScheduled.mutate(item.id);
  };

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return (
      <main className={styles.container}>
        <EmptyState message={error instanceof Error ? error.message : t('Could not load tasks.')} />
      </main>
    );
  }

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <ListChecks size={22} className={styles.headerIcon} aria-hidden="true" focusable={false} />
        <h1 className={styles.title}>{t('Tasks')}</h1>
        <span className={styles.count}>{data.items.length}</span>
      </div>

      {data.items.length === 0 ? (
        <EmptyState message={t('No tasks running.')} />
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>{t('Task')}</th>
              <th>{t('User')}</th>
              <th>{t('Status')}</th>
              <th>{t('Progress')}</th>
              <th>{t('Run time')}</th>
              <th aria-label={t('Cancel')} />
            </tr>
          </thead>
          <tbody>
            {data.items.map((task) => (
              <tr key={String(task.task_id)}>
                <td className={`${styles.taskCell} ${task.is_cancellable ? styles.taskCellCancellable : ''}`}>
                  <div className={styles.taskMsg}><TaskMessage task={task} /></div>
                  {(task.starttime || task.error) && (
                    <div className={styles.taskDetails}>
                      {task.starttime && (
                        <time className={styles.taskStart} dateTime={task.starttime.replace(' ', 'T')}>
                          {task.starttime}
                        </time>
                      )}
                      {task.error && (
                        <div className={styles.taskError} role="alert">{task.error}</div>
                      )}
                    </div>
                  )}
                </td>
                <td className={styles.metaCell}>
                  <span className={styles.mobileLabel}>{t('User')}</span>
                  {task.user}
                </td>
                <td className={styles.metaCell}>
                  <span className={styles.mobileLabel}>{t('Status')}</span>
                  {task.status ?? '—'}
                </td>
                <td className={styles.metaCell}>
                  <span className={styles.mobileLabel}>{t('Progress')}</span>
                  {task.progress}
                </td>
                <td className={styles.metaCell}>
                  <span className={styles.mobileLabel}>{t('Run time')}</span>
                  {task.runtime ?? '—'}
                </td>
                <td className={styles.cancelCell}>
                  {task.is_cancellable && (
                    <button className={styles.cancelBtn}
                      onClick={() => cancel.mutate(task.task_id)}
                      disabled={cancel.isPending}
                      aria-label={t('Cancel {task}', { task: taskMessageText(task) })}>
                      <X size={15} aria-hidden="true" focusable={false} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {isAdmin && (
        <div className={styles.scheduledQueues}>
          {scheduledFailure ? (
            <p className={styles.scheduledError} role="alert">
              {scheduledFailure instanceof Error
                ? scheduledFailure.message
                : t('Could not load scheduled tasks.')}
            </p>
          ) : scheduled.data ? (
            <>
              <ScheduledSection
                id="upcoming-scheduled-sends"
                title={t('Upcoming scheduled sends')}
                items={scheduled.data.sends}
                kind="send"
                cancelling={cancelScheduled.isPending}
                onCancel={confirmScheduledCancel}
              />
              <ScheduledSection
                id="upcoming-scheduled-operations"
                title={t('Upcoming scheduled operations')}
                items={scheduled.data.operations}
                kind="operation"
                cancelling={cancelScheduled.isPending}
                onCancel={confirmScheduledCancel}
              />
            </>
          ) : (
            <SpinnerCentered size={28} />
          )}
        </div>
      )}
    </main>
  );
}
