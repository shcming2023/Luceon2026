import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Pause, Play, RefreshCw, RotateCcw, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { BatchItemStatus, Material, ServerBatchJob } from '../../store/types';
 
type FilterKey = 'all' | 'pending' | 'processing' | 'failed' | 'completed';
 
function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
 
function formatStage(status: BatchItemStatus) {
  if (status === 'uploading') return '上传中';
  if (status === 'pending') return '待处理';
  if (status === 'uploaded') return '文件已就绪';
  if (status === 'mineru') return 'MinerU 解析';
  if (status === 'ai') return 'AI 分析';
  if (status === 'completed') return '已完成';
  if (status === 'error') return '失败';
  if (status === 'skipped') return '已取消';
  return status;
}
 
function isProcessingStatus(status: BatchItemStatus) {
  return status === 'uploaded' || status === 'mineru' || status === 'ai';
}
 
export function WorkspacePage() {
  const { state, dispatch } = useAppStore();
  const queue = state.serverBatchQueue;
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
 
  const materialById = useMemo(() => {
    const map = new Map<number, Material>();
    for (const m of state.materials) map.set(m.id, m);
    return map;
  }, [state.materials]);
 
  const jobs = useMemo(() => {
    const list = Array.isArray(queue?.items) ? queue.items : [];
    if (filter === 'all') return list;
    if (filter === 'pending') return list.filter((j) => j.status === 'pending' || j.status === 'uploading');
    if (filter === 'processing') return list.filter((j) => isProcessingStatus(j.status));
    if (filter === 'failed') return list.filter((j) => j.status === 'error');
    if (filter === 'completed') return list.filter((j) => j.status === 'completed');
    return list;
  }, [queue?.items, filter]);
 
  const pendingIds = useMemo(() => {
    const list = Array.isArray(queue?.items) ? queue.items : [];
    return list.filter((j) => j.status === 'pending').map((j) => j.id);
  }, [queue?.items]);
 
  const refresh = async () => {
    try {
      const res = await fetch('/__proxy/upload/batch/status', { signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      dispatch({ type: 'SERVER_BATCH_SYNC', payload: data });
    } catch (e) {
      toast.error(`刷新失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
 
  const openUpload = () => {
    dispatch({ type: 'BATCH_SET_UI_OPEN', payload: { uiOpen: true } });
  };
 
  const pauseOrResume = async () => {
    if (!queue) return;
    try {
      if (queue.running && queue.paused) {
        await fetch('/__proxy/upload/batch/resume', { method: 'POST' });
        toast.success('队列已恢复');
      } else if (queue.running && !queue.paused) {
        await fetch('/__proxy/upload/batch/pause', { method: 'POST' });
        toast.info('队列已暂停');
      } else {
        await fetch('/__proxy/upload/batch/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ autoMinerU: true, autoAI: true }),
        });
        toast.success('队列已启动');
      }
      await refresh();
    } catch (e) {
      toast.error(`操作失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
 
  const retryFailed = async () => {
    try {
      const res = await fetch('/__proxy/upload/batch/retry-failed', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      toast.success(`已重试 ${data.retried || 0} 个失败任务`);
      await refresh();
    } catch (e) {
      toast.error(`重试失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
 
  const clearSelection = () => setSelectedIds(new Set());
 
  const removeSelected = async () => {
    if (!queue) return;
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    try {
      const byId = new Map(queue.items.map((j) => [j.id, j]));
      const tasks = ids.map(async (id) => {
        const job = byId.get(id);
        if (!job) return;
        if (isProcessingStatus(job.status)) {
          await fetch(`/__proxy/upload/batch/cancel/${encodeURIComponent(id)}`, { method: 'POST' });
          return;
        }
        await fetch(`/__proxy/upload/batch/job/${encodeURIComponent(id)}`, { method: 'DELETE' });
      });
      await Promise.all(tasks);
      toast.success('已处理所选任务');
      clearSelection();
      await refresh();
    } catch (e) {
      toast.error(`删除/取消失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
 
  const reorderPendingBySwap = async (jobId: string, direction: 'up' | 'down') => {
    if (!queue) return;
    const idx = pendingIds.findIndex((id) => id === jobId);
    if (idx === -1) return;
    const next = pendingIds.slice();
    const swapWith = direction === 'up' ? idx - 1 : idx + 1;
    if (swapWith < 0 || swapWith >= next.length) return;
    [next[idx], next[swapWith]] = [next[swapWith], next[idx]];
 
    try {
      const res = await fetch('/__proxy/upload/batch/reorder-pending', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jobIds: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      await refresh();
    } catch (e) {
      toast.error(`调整顺序失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
 
  const toggleSelectAll = (checked: boolean) => {
    if (!checked) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(jobs.map((j) => j.id)));
  };
 
  const toggleOne = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };
 
  const summary = useMemo(() => {
    if (!queue) return null;
    const uploading = queue.uploading ?? queue.items.filter((j) => j.status === 'uploading').length;
    return `队列 ${queue.total} · 上传中 ${uploading} · 待处理 ${queue.pending} · 处理中 ${queue.processing} · 失败 ${queue.errors} · 完成 ${queue.completed}`;
  }, [queue]);
 
  return (
    <div className="p-6 space-y-5 max-w-[1400px] mx-auto">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">工作台</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {summary || '后端队列状态加载中...'}
            {queue?.memory && (
              <span className={`ml-2 ${queue.memory.pressure ? 'text-red-600 font-semibold' : ''}`}>
                · 内存 {queue.memory.freeMB}MB 空闲{queue.memory.pressure ? '（压力过大，已暂停）' : ''}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={openUpload}
            className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700"
          >
            <Upload size={16} /> 批量上传
          </button>
          <button
            onClick={pauseOrResume}
            disabled={!queue}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
          >
            {queue?.running && !queue?.paused ? <Pause size={16} /> : <Play size={16} />}
            {queue?.running ? (queue?.paused ? '恢复' : '暂停') : '启动'}
          </button>
          <button
            onClick={retryFailed}
            disabled={!queue || (queue?.errors ?? 0) === 0}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
          >
            <RotateCcw size={16} /> 重试失败
          </button>
          <button
            onClick={refresh}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-gray-200 bg-white hover:bg-gray-50"
          >
            <RefreshCw size={16} /> 刷新
          </button>
        </div>
      </div>
 
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          {([
            { key: 'all', label: '全部' },
            { key: 'pending', label: '待处理' },
            { key: 'processing', label: '处理中' },
            { key: 'failed', label: '失败' },
            { key: 'completed', label: '已完成' },
          ] as const).map((t) => (
            <button
              key={t.key}
              onClick={() => { setFilter(t.key); clearSelection(); }}
              className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                filter === t.key ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={removeSelected}
            disabled={selectedIds.size === 0}
            className="flex items-center gap-1.5 px-3 py-2 text-sm text-red-600 rounded-lg border border-red-200 bg-white hover:bg-red-50 disabled:opacity-50"
          >
            <Trash2 size={16} /> 删除/取消 ({selectedIds.size})
          </button>
        </div>
      </div>
 
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="w-10 px-4 py-3 text-left">
                <input
                  type="checkbox"
                  checked={jobs.length > 0 && selectedIds.size === jobs.length}
                  onChange={(e) => toggleSelectAll(e.target.checked)}
                  className="rounded"
                />
              </th>
              <th className="w-12 px-2 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">序号</th>
              <th className="px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">资料名称</th>
              <th className="w-28 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">类型</th>
              <th className="w-24 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">大小</th>
              <th className="w-24 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">上传用户</th>
              <th className="w-44 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">上传时间</th>
              <th className="w-28 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">当前阶段</th>
              <th className="w-40 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {!queue && (
              <tr>
                <td colSpan={9} className="text-center py-14 text-gray-400">后端队列加载中...</td>
              </tr>
            )}
            {queue && jobs.length === 0 && (
              <tr>
                <td colSpan={9} className="text-center py-14 text-gray-400">暂无任务</td>
              </tr>
            )}
            {jobs.map((job, idx) => {
              const material = job.materialId ? materialById.get(job.materialId) : undefined;
              const uploadTs = material?.uploadTimestamp || job.createdAt || 0;
              const type = material?.metadata?.format || material?.type || '-';
              const uploader = material?.uploader || '-';
              const canMove = job.status === 'pending' && pendingIds.length > 1;
              const canMoveUp = canMove && pendingIds[0] !== job.id;
              const canMoveDown = canMove && pendingIds[pendingIds.length - 1] !== job.id;
              return (
                <tr key={job.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(job.id)}
                      onChange={() => toggleOne(job.id)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-2 py-3 text-gray-500">{idx + 1}</td>
                  <td className="px-4 py-3">
                    <div className="min-w-0">
                      <div className="text-gray-900 font-medium truncate" title={job.path || job.fileName}>
                        {job.path || job.fileName}
                      </div>
                      {job.message && (
                        <div className={`text-xs truncate ${job.status === 'error' ? 'text-red-600' : 'text-gray-500'}`} title={job.message}>
                          {job.message}
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{type}</td>
                  <td className="px-4 py-3 text-gray-600">{formatBytes(job.fileSize)}</td>
                  <td className="px-4 py-3 text-gray-600">{uploader}</td>
                  <td className="px-4 py-3 text-gray-600">{uploadTs ? new Date(uploadTs).toLocaleString() : '-'}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-1 rounded-full border ${
                      job.status === 'completed'
                        ? 'bg-green-50 text-green-700 border-green-200'
                        : job.status === 'error'
                          ? 'bg-red-50 text-red-700 border-red-200'
                          : isProcessingStatus(job.status)
                            ? 'bg-blue-50 text-blue-700 border-blue-200'
                            : 'bg-gray-50 text-gray-700 border-gray-200'
                    }`}>
                      {formatStage(job.status)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => reorderPendingBySwap(job.id, 'up')}
                        disabled={!canMoveUp}
                        className="p-2 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
                        title="上移（仅 pending）"
                        type="button"
                      >
                        <ArrowUp size={14} />
                      </button>
                      <button
                        onClick={() => reorderPendingBySwap(job.id, 'down')}
                        disabled={!canMoveDown}
                        className="p-2 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
                        title="下移（仅 pending）"
                        type="button"
                      >
                        <ArrowDown size={14} />
                      </button>
                      <button
                        onClick={async () => {
                          try {
                            if (isProcessingStatus(job.status)) {
                              await fetch(`/__proxy/upload/batch/cancel/${encodeURIComponent(job.id)}`, { method: 'POST' });
                            } else {
                              await fetch(`/__proxy/upload/batch/job/${encodeURIComponent(job.id)}`, { method: 'DELETE' });
                            }
                            await refresh();
                          } catch (e) {
                            toast.error(`操作失败：${e instanceof Error ? e.message : String(e)}`);
                          }
                        }}
                        className="p-2 rounded border border-red-200 bg-white text-red-600 hover:bg-red-50"
                        title={isProcessingStatus(job.status) ? '取消' : '删除'}
                        type="button"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

