import type { AssetStatus, AiStatus, MinerUStatus } from '../../store/types';

type BadgeStatus = AssetStatus | AiStatus | MinerUStatus;

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  // AssetStatus
  processing: { label: '处理中', className: 'bg-blue-100 text-blue-700 border-blue-200' },
  completed:  { label: '已完成', className: 'bg-green-100 text-green-700 border-green-200' },
  pending:    { label: '待处理', className: 'bg-gray-100 text-gray-600 border-gray-200' },
  failed:     { label: '失败',   className: 'bg-red-100 text-red-700 border-red-200' },
  reviewing:  { label: '审核中', className: 'bg-yellow-100 text-yellow-700 border-yellow-200' },
  // AiStatus
  analyzed:   { label: 'AI已分析', className: 'bg-purple-100 text-purple-700 border-purple-200' },
  analyzing:  { label: 'AI分析中', className: 'bg-indigo-100 text-indigo-700 border-indigo-200' },
};

interface StatusBadgeProps {
  status: BadgeStatus;
  className?: string;
}

export function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? { label: status, className: 'bg-gray-100 text-gray-600 border-gray-200' };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${config.className} ${className}`}
    >
      {config.label}
    </span>
  );
}
