import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { backupFetch, formatSize } from '../../../utils/backupApi';

interface DrJob {
  id?: string;
  status: 'pending' | 'running' | 'success' | 'failed';
  createdAt: string;
  fileSize?: number;
  filestoreSize?: number;
  backupPath?: string;
  logs?: string[];
}

interface DrStatusResponse {
  latest?: DrJob;
  jobs?: DrJob[];
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700',
  success: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  pending: 'bg-yellow-100 text-yellow-700',
};

export function DatabaseBackupPage() {
  const [latest, setLatest] = useState<DrJob | null>(null);
  const [history, setHistory] = useState<DrJob[]>([]);
  const [loading, setLoading] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const res = await backupFetch<DrStatusResponse>('/backup/database/dr-status');
      setLatest(res.latest ?? null);
      setHistory((res.jobs ?? []).slice(-10).reverse());
      if (res.latest?.status === 'running') {
        setTimeout(loadStatus, 2000);
      }
    } catch (_) {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const handleTrigger = async () => {
    setLoading(true);
    try {
      await backupFetch('/backup/database/dr-trigger', { method: 'POST' });
      toast.success('灾备任务已启动');
      loadStatus();
    } catch (e: unknown) {
      toast.error((e as Error).message || '启动失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">灾备备份</h1>
        <p className="text-sm text-gray-500 mt-0.5">数据库 + 文件存储一键备份，用于灾后重建 / 系统迁移</p>
      </div>

      {/* 操作区 */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">灾备备份（数据库 + 文件）</h2>
        <p className="text-sm text-gray-500">
          同一目录内同时包含 <code className="bg-gray-100 px-1 rounded">sharelatex_db.gz</code> 与{' '}
          <code className="bg-gray-100 px-1 rounded">filestore.tar.gz</code>
        </p>
        <div className="flex gap-3">
          <button
            onClick={handleTrigger}
            disabled={loading || latest?.status === 'running'}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
          >
            <AlertTriangle size={15} /> 立即灾备
          </button>
          <button
            onClick={loadStatus}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors"
          >
            <RefreshCw size={15} /> 刷新状态
          </button>
        </div>
      </div>

      {/* 当前进度 */}
      {latest && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <h2 className="font-semibold text-gray-800">灾备进度</h2>
          <div className="flex justify-between text-sm">
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[latest.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {latest.status}
            </span>
            <span className="text-gray-500">
              DB: {latest.fileSize ? formatSize(latest.fileSize) : '--'} / 文件: {latest.filestoreSize ? formatSize(latest.filestoreSize) : '--'}
            </span>
          </div>
          {latest.backupPath && (
            <p className="text-xs text-gray-400">目录: {latest.backupPath}</p>
          )}
          {latest.logs && latest.logs.length > 0 && (
            <pre className="bg-gray-900 text-gray-200 rounded-lg p-3 text-xs font-mono max-h-48 overflow-y-auto">
              {latest.logs.slice(-50).join('\n')}
            </pre>
          )}
        </div>
      )}

      {/* 历史 */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="font-semibold text-gray-800 mb-4">灾备历史</h2>
        {history.length === 0 ? (
          <p className="text-sm text-gray-400">暂无历史记录</p>
        ) : (
          <div className="space-y-2">
            {history.map((job, i) => (
              <div key={job.id ?? i} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg text-sm">
                <div className="flex items-center gap-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[job.status] ?? 'bg-gray-100 text-gray-600'}`}>
                    {job.status}
                  </span>
                  <span className="text-gray-500">{job.createdAt?.slice(0, 19)}</span>
                </div>
                <div className="text-right text-xs text-gray-400">
                  <div>{job.backupPath ?? '--'}</div>
                  <div>DB: {job.fileSize ? formatSize(job.fileSize) : '--'} / 文件: {job.filestoreSize ? formatSize(job.filestoreSize) : '--'}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
