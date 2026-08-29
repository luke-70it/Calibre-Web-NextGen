import { Link } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Server } from 'lucide-react';
import { apiGet } from '../lib/api';
import { useT } from '../lib/i18n';
import { type Device } from '../components/DeviceInventory';
import { DeviceSummary } from '../components/DeviceSummary';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import styles from './AdminDevices.module.css';

interface AdminDevice extends Device {
  user: { id: number; name: string };
}

export function AdminDevices() {
  const t = useT();
  const { data, isLoading, error } = useQuery<{ devices: AdminDevice[] }>({
    queryKey: ['admin-devices'],
    queryFn: () => apiGet('/api/admin/devices'),
  });
  if (isLoading) return <SpinnerCentered size={40} />;
  const devices = data?.devices ?? [];
  return (
    <main className={styles.container}>
      <Link href="/admin" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Admin')}
      </Link>
      <div className={styles.heading}>
        <Server aria-hidden="true" focusable={false} />
        <h1>{t('Device administration')}</h1>
      </div>
      {error ? (
        <EmptyState message={t('Could not load the device board.')} />
      ) : devices.length === 0 ? (
        <EmptyState message={t('No registered devices.')} />
      ) : (
        <ul className={styles.list} role="list">
          {devices.map((device) => (
            <li key={device.public_id} className={styles.card}>
              <header>
                <div>
                  <h2>{device.label}</h2>
                  <p>{t('Account: {name}', { name: device.user.name })}</p>
                </div>
                <span>{device.active ? t('Active') : t('Inactive')}</span>
              </header>
              <DeviceSummary device={device} />
              <p>{t('Last seen: {when}', { when: device.last_seen || t('Never') })}</p>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
