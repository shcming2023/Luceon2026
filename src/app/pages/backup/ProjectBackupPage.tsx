import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Play, Search, RefreshCw } from 'lucide-react';
import { backupFetch, formatSize } from '../../../utils/backupApi';

interface BackupAnalysis {
  totalProjects: number;
  tagsCount: number;
  estimatedTime: string;
}

interface BackupJob {
  id: string;
  status: 'pending' | 'running' | 'success' | 'failed';
  createdAt: string;
  totalProjects?: number;
  processedCount?: number;
  progressPercent?: number;
  eta?: string;
  logs?: string[];
}

interface BackupStatus {
  job?: BackupJob;
}

interface BackupJobsResponse {
  jobs: BackupJob[];
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700',
  success: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  pending: 'bg-yellow-100 text-yellow-700',
};

export function ProjectBackupPage() {
  const [url, setUrl] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [scope, setScope] = useState('system');
  const [filter, setFilter] = useState('');
  const [organizeTags, setOrganizeTags] = useState(false);

  const [analysis, setAnalysis] = useState<BackupAnalysis | null>(null);
  const [currentJob, setCurrentJob] = useState<BackupJob | null>(null);
  const [history, setHistory] = useState<BackupJob[]>([]);
  const [loading, setLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    try {
      const res = await backupFetch<BackupJobsResponse>('/overleaf-backup/jobs');
      setHistory((res.jobs ?? []).slice(-10).reverse());
    } catch (_) {
      // ignore
    }
  }, []);

  const pollStatus = useCallback(async () => {
    try {
      const res = await backupFetch<BackupStatus>('/backup/status');
      if (res.job) {
        setCurrentJob(res.job);
        if (res.job.status === 'running') {
          setTimeout(pollStatus, 2000);
        } else {
          loadHistory();
        }
      }
    } catch (_) {
      // ignore
    }
  }, [loadHistory]);

  useEffect(() => {
    loadHistory();
    // 加载系统设置预填表单
    backupFetch<Record<string, string>>('/settings').then((cfg) => {
      if (cfg.overleafBaseUrl) setUrl(cfg.overleafBaseUrl);
      if (cfg.adminEmail) setEmail(cfg.adminEmail);
    }).catch(() => {});
  }, [loadHistory]);

  const handleAnalyze = async () => {
    setLoading(true);
    try {
      const res = await backupFetch<{ success: boolean; data: BackupAnalysis }>('/backup/analyze', {
        method: 'POST',
        body: JSON.stringify({
          overleafBaseUrl: url,
          adminEmail: email,
          adminPassword: password,
          manualCookie: '',
          manualCsrf: '',
        }),
      });
      if (res.success) setAnalysis(res.data);
    } catch (e: unknown) {
      toast.error((e as Error).message || '分析失败');
    } finally {
      setLoading(false);
    }
  };

  const handleBackup = async () => {
    setLoading(true);
    try {
      await backupFetch('/overleaf-backup/create', {
        method: 'POST',
        body: JSON.stringify({
          overleafBaseUrl: url,
          adminEmail: email,
          adminPassword: password,
          backupScope: scope,
          scopeFilter: filter,
          organizeByTags: organizeTags,
          backupType: 'system_full',
        }),
      });
      toast.success('备份任务已创建');
      pollStatus();
    } catch (e: unknown) {
      toast.error((e as Error).message || '备份启动失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">项目备份</h1>
        <p className="text-sm text-gray-500 mt-0.5">备份 Overleaf 所有项目到本地目录</p>
      </div>

      {/* 备份配置 */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h2 className="font-semibold text-gray-800">备份配置</h2>
        <div className="grid grid-cols-1 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Overleaf 地址</label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="http://host.docker.internal:8080"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">管理员邮箱</label>
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@example.com"
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">管理员密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="***"
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">备份范围</label>
              <select
                value={scope}
                onChange={(e) => setScope(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
              >
                <option value="system">全部项目</option>
                <option value="tag">按标签</option>
                <option value="path">按路径前缀</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">筛选条件</label>
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="标签名称或路径前缀"
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-700">
            <input
              type="checkbox"
              checked={organizeTags}
              onChange={(e) => setOrganizeTags(e.target.checked)}
              className="w-4 h-4 rounded"
            />
            按标签组织目录结构
          </label>
        </div>
        <div className="flex gap-3 pt-2">
          <button
            onClick={handleAnalyze}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 disabled:opacity-50 transition-colors"
          >
            <Search size={15} /> 分析项目
          </button>
          <button
            onClick={handleBackup}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <Play size={15} /> 立即备份
          </button>
        </div>
      </div>

      {/* 分析结果 */}
      {analysis && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-800 mb-4">分析结果</h2>
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: '总项目数', value: analysis.totalProjects },
              { label: '标签数量', value: analysis.tagsCount },
              { label: '预计耗时', value: analysis.estimatedTime },
            ].map((s) => (
              <div key={s.label} className="bg-gray-50 rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-blue-600">{s.value}</div>
                <div className="text-xs text-gray-500 mt-1">{s.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 进度 */}
      {currentJob && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-gray-800">备份进度</h2>
            <button onClick={pollStatus} className="text-gray-400 hover:text-gray-600"><RefreshCw size={15} /></button>
          </div>
          <div className="flex justify-between text-sm text-gray-600">
            <span>{currentJob.status}</span>
            <span>{currentJob.progressPercent ?? 0}%</span>
          </div>
          <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 transition-all"
              style={{ width: `${currentJob.progressPercent ?? 0}%` }}
            />
          </div>
          {currentJob.eta && (
            <p className="text-xs text-gray-400">ETA: {currentJob.eta}</p>
          )}
          {currentJob.logs && currentJob.logs.length > 0 && (
            <pre className="bg-gray-900 text-gray-200 rounded-lg p-3 text-xs font-mono max-h-48 overflow-y-auto">
              {currentJob.logs.slice(-20).join('\n')}
            </pre>
          )}
        </div>
      )}

      {/* 历史任务 */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-gray-800">历史任务</h2>
          <button onClick={loadHistory} className="text-gray-400 hover:text-gray-600"><RefreshCw size={15} /></button>
        </div>
        {history.length === 0 ? (
          <p className="text-sm text-gray-400">暂无历史记录</p>
        ) : (
          <div className="space-y-2">
            {history.map((job) => (
              <div key={job.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg text-sm">
                <div className="flex items-center gap-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[job.status] ?? 'bg-gray-100 text-gray-600'}`}>
                    {job.status}
                  </span>
                  <span className="text-gray-500">{job.createdAt?.slice(0, 19)}</span>
                </div>
                <span className="text-gray-500">{job.totalProjects ?? 0} 项目，{job.processedCount ?? 0} 完成</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
