import type { ReactNode } from 'react';

interface StatCardProps {
  title: string;
  value: string | number;
  icon?: ReactNode;
  color?: 'blue' | 'green' | 'orange' | 'purple' | 'red' | 'gray';
  subtitle?: string;
}

const COLOR_MAP: Record<string, string> = {
  blue:   'bg-blue-50 border-blue-200 text-blue-600',
  green:  'bg-green-50 border-green-200 text-green-600',
  orange: 'bg-orange-50 border-orange-200 text-orange-600',
  purple: 'bg-purple-50 border-purple-200 text-purple-600',
  red:    'bg-red-50 border-red-200 text-red-600',
  gray:   'bg-gray-50 border-gray-200 text-gray-600',
};

export function StatCard({ title, value, icon, color = 'blue', subtitle }: StatCardProps) {
  const colorClass = COLOR_MAP[color] ?? COLOR_MAP.blue;
  return (
    <div className={`rounded-xl border p-5 flex items-center gap-4 ${colorClass}`}>
      {icon && (
        <div className="flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-lg bg-white/60">
          {icon}
        </div>
      )}
      <div className="min-w-0">
        <p className="text-sm font-medium opacity-80 truncate">{title}</p>
        <p className="text-2xl font-bold mt-0.5">{value}</p>
        {subtitle && <p className="text-xs opacity-60 mt-0.5">{subtitle}</p>}
      </div>
    </div>
  );
}
