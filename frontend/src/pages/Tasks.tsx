import { ListChecks, X } from 'lucide-react';
import { Link } from 'wouter';
import { useTasks, useCancelTask } from '../lib/queries';
import type { TaskItem } from '../lib/api';
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

export function Tasks() {
  const { data, isLoading, error } = useTasks();
  const cancel = useCancelTask();
  const t = useT();

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
    </main>
  );
}
