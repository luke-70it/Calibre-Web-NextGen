import { ListChecks, X } from 'lucide-react';
import { Link } from 'wouter';
import { useTasks, useCancelTask } from '../lib/queries';
import type { TaskItem } from '../lib/api';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Tasks.module.css';

function taskMessageText(task: TaskItem) {
  const parts = task.taskMessageParts;
  return parts
    ? `${parts.prefix}${parts.book.title}${parts.suffix}`
    : task.taskMessage;
}

function TaskMessage({ task }: { task: TaskItem }) {
  const parts = task.taskMessageParts;
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
