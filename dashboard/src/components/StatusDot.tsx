import { clsx } from 'clsx';
import { STATUS_DOT } from '../lib/constants';

export function StatusDot({ status }: { status: string }) {
  return <span className={clsx('inline-block w-2 h-2 rounded-full', STATUS_DOT[status] || STATUS_DOT.unknown)} />;
}
