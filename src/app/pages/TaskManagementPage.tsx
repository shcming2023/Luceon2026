import { useEffect, useState } from 'react';
// Card component from shadcn UI not present; using native Tailwind divs
import { Loader2, RefreshCw, FileText, Play, Download, Trash, Eye } from 'lucide-react';
import { toast } from 'sonner';

interface ParseTask {
  id: string;
  materialId?: string;
  engine?: string;
  stage?: string;
  state?: string;
  progress?: number;
  message?: string;
  createdAt?: string;
}

export function TaskManagementPage() {
  const [tasks, setTasks] = useState<ParseTask[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchTasks = async () => {
    setLoading(true);
    try {
      // 通过 proxy 访问 db-server
      const res = await fetch('/cms/__proxy/db/tasks');
      if (!res.ok) throw new Error('提取任务失败');
      const data = await res.json();
      setTasks(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error('无法获取任务列表', { description: String(err) });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, []);

  return (
    <div className="p-6 h-full flex flex-col space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">任务管理</h1>
          <p className="text-sm text-slate-500 mt-1">查看和管理 MinerU 解析与 AI 元数据识别任务。</p>
        </div>
        <button
          onClick={fetchTasks}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 rounded-lg text-sm font-medium hover:bg-slate-50 transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          刷新状态
        </button>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col shadow-sm border border-slate-200 rounded-lg bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-slate-500 bg-slate-50 uppercase border-b border-slate-200">
              <tr>
                <th className="px-6 py-4 font-semibold">任务 ID</th>
                <th className="px-6 py-4 font-semibold">引擎</th>
                <th className="px-6 py-4 font-semibold">阶段</th>
                <th className="px-6 py-4 font-semibold">状态</th>
                <th className="px-6 py-4 font-semibold">创建时间</th>
                <th className="px-6 py-4 font-semibold text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {tasks.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-500">
                    {loading ? (
                      <div className="flex flex-col items-center gap-2">
                        <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
                        <p>加载中...</p>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center gap-2">
                        <FileText className="w-8 h-8 text-slate-300" />
                        <p>暂无解析任务</p>
                      </div>
                    )}
                  </td>
                </tr>
              ) : (
                tasks.map((t) => (
                  <tr key={t.id} className="bg-white hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4 font-medium text-slate-900">
                      <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 text-slate-400" />
                        {t.id}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2 py-1 rounded bg-slate-100 text-slate-600 text-xs font-medium">
                        {t.engine || 'pipeline'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-600">{t.stage || '—'}</td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2 py-1 flex-shrink-0 text-xs font-medium rounded-full ${
                        t.state === 'success' ? 'bg-green-100 text-green-700' :
                        t.state === 'failed' ? 'bg-red-100 text-red-700' :
                        'bg-blue-100 text-blue-700'
                      }`}>
                        {t.state || 'pending'}
                      </span>
                      {t.message && (
                        <p className="text-[10px] text-slate-400 mt-1 max-w-[200px] truncate" title={t.message}>
                          {t.message}
                        </p>
                      )}
                    </td>
                    <td className="px-6 py-4 text-slate-500">
                      {t.createdAt ? new Date(t.createdAt).toLocaleString() : '—'}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex justify-end gap-2">
                        <button className="p-1.5 text-slate-400 hover:text-blue-600 rounded transition-colors" title="查看详情">
                          <Eye className="w-4 h-4" />
                        </button>
                        <button className="p-1.5 text-slate-400 hover:text-green-600 rounded transition-colors" title="下载结果">
                          <Download className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
