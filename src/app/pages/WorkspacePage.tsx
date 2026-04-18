import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, Eye, FolderPlus, Pause, Play, RotateCcw, Settings, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { BatchItemStatus, Material } from '../../store/types';
import { Link } from 'react-router-dom';
import { DropdownMenu } from '../components/DropdownMenu';
import { useFileUpload } from '../hooks/useFileUpload';
 
type FilterKey = 'all' | 'pending' | 'processing' | 'failed' | 'completed';
 
function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
 
function formatElapsed(ms: number) {
  if (!Number.isFinite(ms) || ms < 0) return '-';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}分${s % 60}秒`;
  const h = Math.floor(m / 60);
  return `${h}小时${m % 60}分`;
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
 
function isCancellable(status: BatchItemStatus) {
  return status === 'uploading' || status === 'uploaded' || status === 'mineru' || status === 'ai';
}
 
export function WorkspacePage() {
  const { state, dispatch } = useAppStore();
  const queue = state.serverBatchQueue;
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => Date.now());
  const pollTimerRef = useRef<number | null>(null);
  const unmountedRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const { upload, uploading, progress } = useFileUpload();
 
  const materialById = useMemo(() => {
    const map = new Map<number, Material>();
    for (const m of state.materials) map.set(m.id, m);
    return map;
  }, [state.materials]);
 
  const hasActive = useMemo(() => {
    if (!queue) return false;
    const items = Array.isArray(queue.items) ? queue.items : [];
    return items.some(
      (j) => j.status === 'uploading' || j.status === 'pending' || isProcessingStatus(j.status),
    );
  }, [queue]);
 
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
 
  const counts = useMemo(() => {
    const all = Array.isArray(queue?.items) ? queue.items : [];
    const pending = all.filter((j) => j.status === 'pending' || j.status === 'uploading').length;
    const processing = all.filter((j) => isProcessingStatus(j.status)).length;
    const failed = all.filter((j) => j.status === 'error').length;
    const completed = all.filter((j) => j.status === 'completed').length;
    return { all: all.length, pending, processing, failed, completed };
  }, [queue?.items]);
 
  const refresh = useCallback(async (opts: { silent?: boolean } = {}) => {
    const silent = opts.silent ?? true;
    try {
      const res = await fetch('/__proxy/upload/batch/status', { signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      dispatch({ type: 'SERVER_BATCH_SYNC', payload: data });
    } catch (e) {
      if (!silent) toast.error(`刷新失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }, [dispatch]);
 
  const handlePickFiles = () => {
    fileInputRef.current?.click();
  };
 
  const handlePickFolder = () => {
    folderInputRef.current?.click();
  };
 
  const onFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = '';
    void upload(files);
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
        if (isCancellable(job.status)) {
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
 
  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      if (pollTimerRef.current !== null) {
        window.clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);
 
  useEffect(() => {
    if (!hasActive) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [hasActive]);
 
  useEffect(() => {
    const scheduleNext = (delayMs: number) => {
      if (unmountedRef.current) return;
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = window.setTimeout(() => {
        void poll();
      }, delayMs);
    };
 
    const poll = async () => {
      if (unmountedRef.current) return;
      if (document.hidden) {
        scheduleNext(30_000);
        return;
      }
      await refresh({ silent: true });
      scheduleNext(hasActive ? 5_000 : 30_000);
    };
 
    scheduleNext(0);
    const onVisibilityChange = () => {
      if (!document.hidden) scheduleNext(0);
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      if (pollTimerRef.current !== null) {
        window.clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [hasActive, refresh]);
 
  useEffect(() => {
    const el = folderInputRef.current as unknown as { webkitdirectory?: boolean; directory?: boolean; setAttribute?: (k: string, v: string) => void } | null;
    if (!el) return;
    el.webkitdirectory = true;
    el.directory = true;
    el.setAttribute?.('webkitdirectory', '');
    el.setAttribute?.('directory', '');
  }, []);
 
  return (
    <div className="p-6 space-y-5 max-w-[1400px] mx-auto">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={onFileInputChange}
        accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.jpg,.jpeg,.png"
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={onFileInputChange}
        accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.jpg,.jpeg,.png"
      />
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
          {progress && (
            <div className="text-sm text-gray-500 mt-1">
              上传进度：{progress.done}/{progress.total}
              {progress.failed > 0 && <span className="text-red-500">（失败 {progress.failed}）</span>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex bg-blue-600 rounded-lg overflow-hidden text-white text-sm">
            <button
              disabled={uploading}
              onClick={handlePickFiles}
              className="flex items-center gap-1.5 px-4 py-2 hover:bg-blue-700 disabled:opacity-60"
              type="button"
            >
              <Upload size={16} /> 上传文件
            </button>
            <div className="w-px bg-blue-500 my-2" />
            <button
              disabled={uploading}
              onClick={handlePickFolder}
              className="flex items-center gap-1.5 px-3 py-2 hover:bg-blue-700 disabled:opacity-60"
              type="button"
            >
              <FolderPlus size={16} /> 文件夹
            </button>
          </div>
          <DropdownMenu
            trigger={({ open, setOpen }) => (
              <button
                className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-gray-200 bg-white hover:bg-gray-50"
                type="button"
                onClick={() => setOpen(!open)}
              >
                <Settings size={16} /> 队列控制 <ChevronDown size={14} />
              </button>
            )}
            items={[
              {
                kind: 'item',
                label: queue?.running ? (queue.paused ? '恢复队列' : '暂停队列') : '启动队列',
                onClick: pauseOrResume,
                disabled: !queue,
              },
              {
                kind: 'item',
                label: `重试失败 (${queue?.errors ?? 0})`,
                onClick: retryFailed,
                disabled: !queue || (queue?.errors ?? 0) === 0,
              },
              { kind: 'divider' },
              {
                kind: 'item',
                label: `清理已结束 (${(queue?.errors ?? 0) + (queue?.completed ?? 0)})`,
                onClick: async () => {
                  try {
                    const res = await fetch('/__proxy/upload/batch/clear-completed', { method: 'POST' });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
                    toast.success(`已清理 ${(data as { removed?: number }).removed || 0} 个任务`);
                    await refresh();
                  } catch (e) {
                    toast.error(`清理失败：${e instanceof Error ? e.message : String(e)}`);
                  }
                },
                disabled: !queue || (queue?.completed ?? 0) + (queue?.errors ?? 0) === 0,
              },
              {
                kind: 'item',
                label: '清空全部',
                danger: true,
                onClick: async () => {
                  try {
                    const res = await fetch('/__proxy/upload/batch/clear-all', { method: 'POST' });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
                    toast.success('已清空后端队列');
                    await refresh();
                  } catch (e) {
                    toast.error(`清空失败：${e instanceof Error ? e.message : String(e)}`);
                  }
                },
                disabled: !queue || (queue?.total ?? 0) === 0,
              },
            ]}
          />
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
              <span className="inline-flex items-center gap-2">
                <span>{t.label}</span>
                <span className="text-[11px] px-2 h-5 leading-5 rounded-full bg-gray-100 text-gray-600 border border-gray-200">
                  {t.key === 'all'
                    ? counts.all
                    : t.key === 'pending'
                      ? counts.pending
                      : t.key === 'processing'
                        ? counts.processing
                        : t.key === 'failed'
                          ? counts.failed
                          : counts.completed}
                </span>
              </span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {(counts.failed > 0 || counts.completed > 0) && (
            <button
              type="button"
              onClick={async () => {
                try {
                  const res = await fetch('/__proxy/upload/batch/clear-completed', { method: 'POST' });
                  const data = await res.json().catch(() => ({}));
                  if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
                  toast.success(`已清理 ${(data as { removed?: number }).removed || 0} 个任务`);
                  await refresh();
                } catch (e) {
                  toast.error(`清理失败：${e instanceof Error ? e.message : String(e)}`);
                }
              }}
              className="text-xs text-gray-500 hover:text-red-500"
            >
              清理已结束 ({counts.failed + counts.completed})
            </button>
          )}
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
              <th className="px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">资料名称</th>
              <th className="w-28 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">类型</th>
              <th className="w-28 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">处理阶段</th>
              <th className="w-56 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">AI 识别结果</th>
              <th className="w-44 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">创建时间</th>
              <th className="w-44 px-4 py-3 text-left font-semibold text-gray-600 text-xs uppercase tracking-wide">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {!queue && (
              <tr>
                <td colSpan={7} className="text-center py-14 text-gray-400">后端队列加载中...</td>
              </tr>
            )}
            {queue && jobs.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center py-14 text-gray-400">暂无任务</td>
              </tr>
            )}
            {jobs.map((job) => {
              const material = job.materialId ? materialById.get(job.materialId) : undefined;
              const type = material?.metadata?.format || material?.type || '-';
              const canMove = job.status === 'pending' && pendingIds.length > 1;
              const canMoveUp = canMove && pendingIds[0] !== job.id;
              const canMoveDown = canMove && pendingIds[pendingIds.length - 1] !== job.id;
              const progress = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0))));
              const lastUpdated = job.updatedAt || 0;
              const elapsedFrom = job.status === 'mineru' && job.mineruSubmittedAt ? job.mineruSubmittedAt : job.createdAt || lastUpdated || 0;
              const showTiming = isCancellable(job.status);
              const createdAt = job.createdAt || 0;
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
                  <td className="px-4 py-3">
                    <div className="min-w-0">
                      {job.materialId && job.status === 'completed' ? (
                        <Link
                          to={`/asset/${job.materialId}`}
                          className="text-blue-600 hover:underline font-medium truncate block"
                          title={job.path || job.fileName}
                        >
                          {job.path || job.fileName}
                        </Link>
                      ) : (
                        <div className="text-gray-900 font-medium truncate" title={job.path || job.fileName}>
                          {job.path || job.fileName}
                        </div>
                      )}
                      <div className="text-xs text-gray-400 mt-0.5">{formatBytes(job.fileSize)}</div>
                      {job.message && (
                        <div className={`text-xs truncate ${job.status === 'error' ? 'text-red-600' : 'text-gray-500'}`} title={job.message}>
                          {job.message}
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{type}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col gap-1">
                      <span className={`text-xs px-2 py-1 rounded-full border w-fit ${
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
                      {showTiming && (
                        <div className="text-[11px] text-gray-500">
                          <span>进度 {progress}%</span>
                          {elapsedFrom > 0 && <span className="ml-2">已等待 {formatElapsed(now - elapsedFrom)}</span>}
                          {lastUpdated > 0 && <span className="ml-2">最近更新 {formatElapsed(now - lastUpdated)}</span>}
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
                      if (!material) return <span className="text-xs text-gray-300">-</span>;
                      if (job.status === 'ai') return <span className="text-xs text-blue-500">识别中...</span>;
                      if (job.status !== 'completed') return <span className="text-xs text-gray-300">-</span>;
                      const subject = material.metadata?.subject;
                      const grade = material.metadata?.grade;
                      const tags = material.tags ?? [];
                      if (!subject && !grade && tags.length === 0) return <span className="text-xs text-gray-400">无标签</span>;
                      return (
                        <div className="flex flex-col gap-1">
                          {(subject || grade) && (
                            <span className="text-xs text-gray-600">
                              {[subject, grade].filter(Boolean).join(' · ')}
                            </span>
                          )}
                          {tags.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                              {tags.slice(0, 3).map((tag) => (
                                <span key={tag} className="text-[11px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
                                  {tag}
                                </span>
                              ))}
                              {tags.length > 3 && <span className="text-[11px] text-gray-400">+{tags.length - 3}</span>}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{createdAt ? new Date(createdAt).toLocaleString() : '-'}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      {filter === 'pending' && job.status === 'pending' && pendingIds.length > 1 && (
                        <>
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
                        </>
                      )}
                      {job.materialId && job.status === 'completed' && (
                        <Link
                          to={`/asset/${job.materialId}`}
                          className="p-2 rounded border border-gray-200 bg-white hover:bg-blue-50 text-blue-600"
                          title="查看详情"
                        >
                          <Eye size={14} />
                        </Link>
                      )}
                      <button
                        onClick={async () => {
                          try {
                            if (isCancellable(job.status)) {
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
                        title={isCancellable(job.status) ? '取消' : '删除'}
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
