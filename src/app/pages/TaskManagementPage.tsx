import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
// Card component from shadcn UI not present; using native Tailwind divs
import { Loader2, RefreshCw, FileText, Play, Download, Trash2, Eye, Clock } from 'lucide-react';
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
  const [filter, setFilter] = useState<'all' | 'pending' | 'processing' | 'failed' | 'completed'>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const res = await fetch('/__proxy/db/tasks');
      if (!res.ok) throw new Error(`提取任务失败: HTTP ${res.status}`);
      const data = await res.json();
      setTasks(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error('无法获取任务列表', { description: String(err) });
      setTasks([]);
    } finally {
      setLoading(false);
    }
  };

  const deleteTask = async (id: string) => {
    try {
      const res = await fetch('/__proxy/db/tasks', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [id] })
      });
      if (!res.ok) throw new Error(`删除失败: HTTP ${res.status}`);
      toast.success('任务已删除');
      fetchTasks();
    } catch (err) {
      toast.error('删除失败', { description: String(err) });
    }
  };

  const filteredTasks = tasks.filter(t => {
    if (filter === 'all') return true;
    if (filter === 'processing') return t.state === 'running' || t.state === 'result-store';
    if (filter === 'completed') return t.state === 'success' || t.state === 'ai-pending' || t.state === 'completed';
    return t.state === filter;
  });

  useEffect(() => {
    fetchTasks();
  }, []);

  return (
    <div className="p-6 h-full flex flex-col space-y-5 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">任务管理</h1>
          <p className="text-sm text-gray-500 mt-1">监控文档解析与 AI 元数据提取的全生命周期。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchTasks}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新列表
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 border-b border-gray-200 pb-px overflow-x-auto no-scrollbar">
        {(['all', 'pending', 'processing', 'completed', 'failed'] as const).map((key) => {
          const labelMap = { all: '全部', pending: '等待中', processing: '处理中', completed: '已完成', failed: '已失败' };
          const active = filter === key;
          const count = tasks.filter(t => {
            if (key === 'all') return true;
            if (key === 'processing') return t.state === 'running' || t.state === 'result-store';
            if (key === 'completed') return t.state === 'success' || t.state === 'ai-pending' || t.state === 'completed';
            return t.state === key;
          }).length;
          return (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-4 py-2.5 text-sm font-medium transition-all relative ${
                active ? 'text-blue-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <div className="flex items-center gap-2">
                {labelMap[key]}
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                  active ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                }`}>
                  {count}
                </span>
              </div>
              {active && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-600 rounded-full" />}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-hidden flex flex-col bg-white border border-gray-200 rounded-xl shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-6 py-4 font-semibold text-gray-600 text-xs uppercase tracking-wider">任务信息</th>
                <th className="px-6 py-4 font-semibold text-gray-600 text-xs uppercase tracking-wider">处理引擎</th>
                <th className="px-6 py-4 font-semibold text-gray-600 text-xs uppercase tracking-wider">当前状态</th>
                <th className="px-6 py-4 font-semibold text-gray-600 text-xs uppercase tracking-wider">创建时间</th>
                <th className="px-6 py-4 font-semibold text-gray-600 text-xs uppercase tracking-wider text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filteredTasks.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-16 text-center text-gray-400">
                    <div className="flex flex-col items-center gap-3">
                      {loading ? (
                        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
                      ) : (
                        <FileText className="w-10 h-10 opacity-20" />
                      )}
                      <p>{loading ? '正在加载数据...' : '暂无符合条件的任务'}</p>
                    </div>
                  </td>
                </tr>
              ) : (
                filteredTasks.map((t) => (
                  <tr key={t.id} className="hover:bg-gray-50/80 transition-colors group">
                    <td className="px-6 py-4">
                      <div className="flex flex-col gap-1">
                        <button
                          onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}`)}
                          className="font-semibold text-blue-600 hover:underline text-left truncate max-w-[240px]"
                        >
                          {t.id}
                        </button>
                        <div className="flex items-center gap-1.5 text-xs text-gray-400">
                          <Clock size={12} />
                          {t.stage || '准备中'}
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-md bg-gray-100 text-gray-600 text-[11px] font-bold uppercase tracking-tight">
                        {t.engine || 'mineru-local'}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-2">
                          <span className={`inline-flex items-center px-2 py-0.5 text-xs font-bold rounded-full ${
                            t.state === 'success' || t.state === 'ai-pending' || t.state === 'completed' ? 'bg-green-50 text-green-700 border border-green-100' :
                            t.state === 'failed' ? 'bg-red-50 text-red-700 border border-red-100' :
                            t.state === 'running' || t.state === 'result-store' ? 'bg-blue-50 text-blue-700 border border-blue-100 animate-pulse' :
                            'bg-gray-50 text-gray-600 border border-gray-100'
                          }`}>
                            {t.state === 'ai-pending' ? '等待 AI' : (t.state === 'result-store' ? '产物落库' : t.state || 'pending')}
                          </span>
                          {(t.state === 'running' || t.state === 'result-store') && (
                            <span className="text-[11px] font-mono font-medium text-blue-600">{t.progress || 0}%</span>
                          )}
                        </div>
                        {(t.state === 'running' || t.state === 'result-store') && (
                          <div className="w-32 h-1 bg-gray-100 rounded-full overflow-hidden">
                            <div 
                              className="h-full bg-blue-500 transition-all duration-700 ease-in-out" 
                              style={{ width: `${t.progress || 0}%` }}
                            />
                          </div>
                        )}
                        {t.message && (
                          <p className="text-[11px] text-gray-400 line-clamp-1 max-w-[200px]" title={t.message}>
                            {t.message}
                          </p>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-gray-500 font-mono text-[11px]">
                      {t.createdAt ? new Date(t.createdAt).toLocaleString('zh-CN', { hour12: false }) : '—'}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex justify-end gap-1.5">
                        <button 
                          onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}`)} 
                          className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-all" 
                          title="查看日志"
                        >
                          <Eye size={16} />
                        </button>
                        <button 
                          className="p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-all disabled:opacity-30" 
                          disabled={t.state !== 'completed' && t.state !== 'success' && t.state !== 'ai-pending'}
                          title="下载解析结果 (ZIP)"
                        >
                          <Download size={16} />
                        </button>
                        <button 
                          onClick={() => { if(window.confirm('确定要删除此任务记录吗？')) deleteTask(t.id); }}
                          className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-all opacity-0 group-hover:opacity-100" 
                          title="删除任务"
                        >
                          <Trash2 size={16} />
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