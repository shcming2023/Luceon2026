import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { Save } from 'lucide-react';
import { backupFetch } from '../../../utils/backupApi';

interface SchedulerConfig {
  databaseBackupTime: string;
}

export function SchedulerPage() {
  const [drTime, setDrTime] = useState('02:00');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    backupFetch<SchedulerConfig>('/scheduler/config')
      .then((res) => {
        if (res.databaseBackupTime) setDrTime(res.databaseBackupTime);
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setLoading(true);
    try {
      await backupFetch('/scheduler/config', {
        method: 'POST',
        body: JSON.stringify({ databaseBackupTime: drTime }),
      });
      toast.success('调度配置已保存');
    } catch (e: unknown) {
      toast.error((e as Error).message || '保存失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-5 max-w-xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">定时调度</h1>
        <p className="text-sm text-gray-500 mt-0.5">配置自动灾备备份的执行时间</p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-5">
        <h2 className="font-semibold text-gray-800">调度配置</h2>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">灾备备份时间（每日）</label>
          <input
            type="time"
            value={drTime}
            onChange={(e) => setDrTime(e.target.value)}
            className="px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
          <p className="text-xs text-gray-400 mt-1">系统将在每天该时间自动触发灾备备份任务</p>
        </div>

        <div>
          <button
            onClick={handleSave}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <Save size={15} /> 保存配置
          </button>
        </div>
      </div>
    </div>
  );
}
