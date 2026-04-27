import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2,
  RefreshCw,
  FileText,
  Download,
  Trash2,
  Eye,
  Clock,
  RotateCw,
  Sparkles,
  XCircle,
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  Upload,
  FolderPlus,
} from 'lucide-react';
import { toast } from 'sonner';
import { deriveTaskBucket, ParseTask, TaskBucket } from '../utils/taskView';
import { useFileUpload } from '../hooks/useFileUpload';

/**
 * TaskManagementPage — 任务管理
 *
 * 按 PRD v0.4 §6.3「展示桶」规范：
 *   queued     ← uploading, pending, ai-pending
 *   processing ← running, result-store, ai-running
 *   reviewing  ← review-pending
 *   completed  ← completed
 *   failed     ← failed
 *   canceled   ← canceled
 */

type BucketKey = 'all' | TaskBucket;

const BUCKET_LABELS: Record<BucketKey, string> = {
  all: '全部',
  queued: '等待中',
  processing: '处理中',
  reviewing: '待审核',
  completed: '已完成',
  failed: '已失败',
  canceled: '已取消',
  unknown: '未知',
};

function bucketOf(state: string | undefined, stage?: string): BucketKey {
  return deriveTaskBucket(state, stage);
}

function stateBadgeClass(state: string | undefined, stage?: string): string {
  const b = bucketOf(state, stage);
  if (b === 'completed') return 'bg-green-50 text-green-700 border border-green-100';
  if (b === 'failed') return 'bg-red-50 text-red-700 border border-red-100';
  if (b === 'canceled') return 'bg-gray-100 text-gray-500 border border-gray-200';
  if (b === 'reviewing') return 'bg-amber-50 text-amber-700 border border-amber-100';
  if (b === 'processing') return 'bg-blue-50 text-blue-700 border border-blue-100 animate-pulse';
  return 'bg-gray-50 text-gray-600 border border-gray-100';
}

function zhLabelForState(state: string | undefined): string {
  switch (state) {
    case 'uploading': return '上传中';
    case 'pending': return '等待中';
    case 'running': return '解析中';
    case 'result-store': return '产物落库';
    case 'ai-pending': return '等待中';
    case 'ai-running': return 'AI 分析中';
    case 'review-pending': return '待审核';
    case 'completed': return '已完成';
    case 'failed': return '失败';
    case 'canceled': return '已取消';
    default: return state || 'pending';
  }
}

export function TaskManagementPage() {
  const [tasks, setTasks] = useState<ParseTask[]>([]);
  const [materials, setMaterials] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<BucketKey>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const sseRef = useRef<EventSource | null>(null);
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  // P1 Patch: 复用 useFileUpload hook，任务管理页直接发起上传
  const { upload, uploading } = useFileUpload();

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const [tResp, mResp] = await Promise.all([
        fetch('/__proxy/db/tasks'),
        fetch('/__proxy/db/materials'),
      ]);
      if (!tResp.ok) throw new Error(`提取任务失败: HTTP ${tResp.status}`);
      const tData = await tResp.json();
      setTasks(Array.isArray(tData) ? tData : []);
      
      if (mResp.ok) {
        const mData = await mResp.json();
        setMaterials(Array.isArray(mData) ? mData : []);
      }
    } catch (err) {
      toast.error('无法获取任务列表', { description: String(err) });
      setTasks([]);
    } finally {
      setLoading(false);
    }
  };

  const diagnoseStatus = (t: ParseTask) => {
    const m = materials.find(mat => String(mat.id) === String(t.materialId));
    if (!m) return { label: '需审计', color: 'text-amber-600 bg-amber-50 border-amber-100', icon: AlertTriangle };

    const ts = t.state;
    const ms = m.status;
    const mins = m.mineruStatus;
    const ais = m.aiStatus;

    if (ts === 'review-pending' && ms === 'reviewing' && mins === 'completed' && (ais === 'analyzed' || ais === 'failed')) {
      return { label: '待复核', color: 'text-emerald-600 bg-emerald-50 border-emerald-100', icon: ShieldCheck };
    }
    if (ts === 'completed' && ms === 'completed' && mins === 'completed' && ais === 'analyzed') {
      return { label: '已一致', color: 'text-blue-600 bg-blue-50 border-blue-100', icon: CheckCircle2 };
    }
    const processingStates = ['uploading', 'pending', 'running', 'result-store', 'ai-pending', 'ai-running'];
    if (processingStates.includes(ts || '')) {
      return { label: '流转中', color: 'text-slate-400 bg-slate-50 border-slate-100', icon: Clock };
    }
    if (ts === 'failed' || ts === 'canceled') {
      return { label: '已终止', color: 'text-slate-400 bg-slate-50 border-slate-100', icon: XCircle };
    }
    return { label: '需审计', color: 'text-amber-600 bg-amber-50 border-amber-100', icon: AlertTriangle };
  };

  const patchTaskInState = async (id: string) => {
    try {
      const res = await fetch(`/__proxy/db/tasks/${encodeURIComponent(id)}`);
      if (!res.ok) return;
      const latest = await res.json();
      setTasks((prev) => {
        const idx = prev.findIndex((t) => t.id === id);
        if (idx === -1) return [latest, ...prev];
        const next = prev.slice();
        next[idx] = { ...prev[idx], ...latest };
        return next;
      });
    } catch { /* ignore */ }
  };

  const deleteTask = async (id: string) => {
    try {
      const res = await fetch('/__proxy/db/tasks', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [id] }),
      });
      if (!res.ok) throw new Error(`删除失败: HTTP ${res.status}`);
      toast.success('任务已删除');
      fetchTasks();
    } catch (err) {
      toast.error('删除失败', { description: String(err) });
    }
  };

  const callAction = async (t: ParseTask, action: 'retry' | 'reparse' | 're-ai' | 'cancel') => {
    try {
      const res = await fetch(`/__proxy/upload/tasks/${encodeURIComponent(t.id)}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const payload = await res.json().catch(() => ({} as any));
      if (!res.ok) throw new Error(payload?.error || `HTTP ${res.status}`);
      const verb = { retry: '已重试', reparse: '已重新解析', 're-ai': '已触发 Re-AI', cancel: '已取消' }[action];
      toast.success(`${verb}`, { description: payload?.newTaskId ? `新任务：${payload.newTaskId}` : undefined });
      fetchTasks();
    } catch (err) {
      toast.error(`${action} 失败`, { description: String(err) });
    }
  };

  const batchRetry = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    try {
      const res = await fetch('/__proxy/upload/tasks/batch/retry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
      const payload = await res.json().catch(() => ({} as any));
      if (!res.ok) throw new Error(payload?.error || `HTTP ${res.status}`);
      const okCount = (payload?.results || []).filter((r: any) => r.ok).length;
      toast.success(`批量重试完成：成功 ${okCount}/${ids.length}`);
      setSelectedIds(new Set());
      fetchTasks();
    } catch (err) {
      toast.error('批量重试失败', { description: String(err) });
    }
  };

  // ── SSE 接入（PRD v0.4 §10.2.2）───────────────────────────
  useEffect(() => {
    if (sseRef.current) return;
    try {
      const es = new EventSource('/__proxy/upload/tasks/stream');
      sseRef.current = es;
      es.addEventListener('task-update', (evt: MessageEvent) => {
        try {
          const data = JSON.parse(evt.data);
          if (data?.taskId) patchTaskInState(data.taskId);
        } catch { /* ignore */ }
      });
      es.onerror = () => {
        // 浏览器会自动重连；失败时不弹 toast，避免噪音
      };
    } catch (e) {
      console.warn('[TaskManagementPage] SSE init failed', e);
    }
    return () => {
      sseRef.current?.close();
      sseRef.current = null;
    };
  }, []);

  useEffect(() => {
    fetchTasks();
  }, []);

  const filteredTasks = useMemo(() => {
    if (filter === 'all') return tasks;
    return tasks.filter((t) => bucketOf(t.state, t.stage) === filter);
  }, [tasks, filter]);

  const counts = useMemo(() => {
    const c: Record<BucketKey, number> = {
      all: tasks.length,
      queued: 0, processing: 0, reviewing: 0, completed: 0, failed: 0, canceled: 0, unknown: 0,
    };
    for (const t of tasks) c[bucketOf(t.state, t.stage)] += 1;
    return c;
  }, [tasks]);

  const hasFailedSelected = useMemo(
    () => Array.from(selectedIds).some((id) => tasks.find((t) => t.id === id)?.state === 'failed'),
    [selectedIds, tasks],
  );

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="p-6 h-full flex flex-col space-y-5 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">任务管理</h1>
          <p className="text-sm text-gray-500 mt-1">监控文档解析与 AI 元数据提取的全生命周期（实时）。</p>
        </div>
        <div className="flex items-center gap-2">
          {/* P1 Patch: 直接在任务管理页发起上传，不再跳转 /workspace */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ''; void upload(files); }}
            accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.jpg,.jpeg,.png,.md"
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ''; void upload(files); }}
            accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.jpg,.jpeg,.png,.md"
          />
          <div className="flex bg-blue-600 rounded-lg overflow-hidden text-white text-sm shadow-sm">
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="flex items-center gap-2 px-4 py-2 hover:bg-blue-500 transition-colors disabled:opacity-60"
            >
              <Upload className="w-4 h-4" /> 上传文件
            </button>
            <div className="w-px bg-blue-500 my-2" />
            <button
              onClick={() => {
                const el = folderInputRef.current as unknown as { webkitdirectory?: boolean; directory?: boolean; setAttribute?: (k: string, v: string) => void } | null;
                if (el) { el.webkitdirectory = true; el.directory = true; el.setAttribute?.('webkitdirectory', ''); el.setAttribute?.('directory', ''); }
                folderInputRef.current?.click();
              }}
              disabled={uploading}
              className="flex items-center gap-2 px-3 py-2 hover:bg-blue-500 transition-colors disabled:opacity-60"
            >
              <FolderPlus className="w-4 h-4" /> 文件夹
            </button>
          </div>
          <button
            onClick={fetchTasks}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新列表
          </button>
          <button
            onClick={batchRetry}
            disabled={!hasFailedSelected}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-amber-200 text-amber-700 rounded-lg text-sm font-medium hover:bg-amber-50 disabled:opacity-40 transition-colors shadow-sm"
            title="批量重试：对所选 failed 任务执行 retry"
          >
            <RotateCw className="w-4 h-4" /> 批量重试
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 border-b border-gray-200 pb-px overflow-x-auto no-scrollbar">
        {(['all', 'queued', 'processing', 'reviewing', 'completed', 'failed', 'canceled'] as BucketKey[]).map((key) => {
          const active = filter === key;
          return (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-4 py-2.5 text-sm font-medium transition-all relative ${
                active ? 'text-blue-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <div className="flex items-center gap-2">
                {BUCKET_LABELS[key]}
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${active ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}`}>
                  {counts[key]}
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
                <th className="px-3 py-4 w-10"></th>
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
                  <td colSpan={6} className="px-6 py-16 text-center text-gray-400">
                    <div className="flex flex-col items-center gap-3">
                      {loading ? <Loader2 className="w-8 h-8 animate-spin text-blue-500" /> : <FileText className="w-10 h-10 opacity-20" />}
                      <p>{loading ? '正在加载数据...' : '暂无符合条件的任务'}</p>
                    </div>
                  </td>
                </tr>
              ) : (
                filteredTasks.map((t) => {
                  const bucket = bucketOf(t.state, t.stage);
                  const canRetry = t.state === 'failed';
                  const canReparse = t.state === 'failed' || t.state === 'completed' || t.state === 'review-pending' || t.state === 'canceled';
                  const canReAi = t.state === 'failed' || t.state === 'completed' || t.state === 'review-pending';
                  const canCancel = t.state === 'pending' || t.state === 'ai-pending' || t.state === 'review-pending';
                  return (
                    <tr key={t.id} className="hover:bg-gray-50/80 transition-colors group">
                      <td className="px-3 py-4">
                        <input
                          type="checkbox"
                          checked={selectedIds.has(t.id)}
                          onChange={() => toggleSelect(t.id)}
                          className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        />
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex flex-col gap-1 min-w-0 max-w-[280px]">
                          {/* P1 Patch: 主显示上传文件名，task id 为次级信息 */}
                          <button
                            onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}`)}
                            className="font-semibold text-gray-900 hover:text-blue-600 text-left truncate text-sm"
                            title={(t as any).fileName || (t as any).metadata?.fileName || (t as any).optionsSnapshot?.material?.fileName || t.id}
                          >
                            {(t as any).fileName
                              || (t as any).metadata?.fileName
                              || (t as any).optionsSnapshot?.material?.fileName
                              || (() => {
                                const mat = materials.find(m => String(m.id) === String(t.materialId));
                                return mat?.metadata?.fileName || mat?.title || null;
                              })()
                              || '未命名文件'}
                          </button>
                          <div className="flex items-center gap-1.5 text-[11px] text-gray-400 font-mono truncate">
                            <span className="truncate" title={t.id}>Task: {t.id.length > 16 ? t.id.slice(0, 16) + '…' : t.id}</span>
                            {t.retryOf ? <span className="text-amber-600">(重试自 {t.retryOf.slice(0, 12)}…)</span> : null}
                          </div>
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
                            <span className={`inline-flex items-center px-2 py-0.5 text-xs font-bold rounded-full ${stateBadgeClass(t.state, t.stage)}`}>
                              {t.stage === 'mineru-queued' ? 'MinerU 排队中' :
                               t.stage === 'mineru-processing' ? (() => {
                                 const obs = t.metadata?.mineruObservedProgress as any;
                                 if (!obs) return 'MinerU 正在解析';
                                 const level = obs.activityLevel || t.metadata?.mineruProgressHealth || '';
                                 // 日志观测通道滞后或不可用
                                 if (level.startsWith('log-observation-') || obs.observationStale) {
                                   const hint = obs.phase ? ` · 最后可见 ${obs.phase} ${obs.current ?? '?'}/${obs.total ?? '?'}` : '';
                                   return `MinerU 正在解析 · 日志观测滞后/不可用${hint}`;
                                 }
                                 // api-alive-only 不得显示为正在推进
                                 if (level === 'api-alive-only') return 'MinerU API 可达 · 未见业务进展';
                                 if (level === 'no-business-signal') return 'MinerU 正在解析 · 暂无信号';

                                 // Structural rendering
                                 if (obs.backendProfile) {
                                   const bp = obs.backendProfile.toLowerCase().includes('hybrid') ? 'Hybrid' : 'Pipeline';
                                   const parts = [bp];
                                   if (obs.stage && obs.stage.rawPhase) {
                                      let st = obs.stage.rawPhase;
                                      if (obs.stage.current != null && obs.stage.total != null) st += ` ${obs.stage.current}/${obs.stage.total}`;
                                      parts.push(st);
                                   } else if (obs.phase) {
                                      let st = obs.phase;
                                      if (obs.current != null && obs.total != null) st += ` ${obs.current}/${obs.total}`;
                                      parts.push(st);
                                   }
                                   
                                   const unitType = obs.stage?.unitType || '';
                                   if (unitType === 'model-units') parts.push('模型单元');
                                   else if (unitType === 'ocr-recognition-blocks') parts.push('OCR识别块');
                                   else if (unitType === 'table-regions') parts.push('表格识别');
                                   
                                   const win = obs.window || obs.latestWindow;
                                   if (win && win.total) {
                                      parts.push(`窗口 ${win.index || win.windowCurrent}/${win.total || win.windowTotal}`);
                                      if (win.pageStart && win.pageEnd) {
                                        parts.push(`页 ${win.pageStart}-${win.pageEnd}/${win.pageTotal || '?'}`);
                                      }
                                   } else if (obs.document && obs.document.totalPages) {
                                      parts.push(`文档 ${obs.document.totalPages}页`);
                                   }
                                   if (obs.observer === 'host-mineru-log-observer') {
                                      parts.push('(Sidecar)');
                                   }
                                   return parts.join(' · ');
                                 }

                                 const parts: string[] = ['正在解析'];
                                 if (obs.phase && obs.current != null && obs.total != null) {
                                   parts.push(`${obs.phase} ${obs.current}/${obs.total}`);
                                 }
                                 const win = obs.latestWindow;
                                 if (win) {
                                   parts.push(`窗口 ${win.windowCurrent}/${win.windowTotal} · 页 ${win.pageStart}-${win.pageEnd}/${win.pageTotal}`);
                                 }
                                 if (!obs.phase && level === 'active-business-log') {
                                   parts.push('检测到 MinerU 业务日志');
                                 }
                                 if (obs.observer === 'host-mineru-log-observer') {
                                   parts.push('(Sidecar)');
                                 }
                                 return parts.join(' · ');
                               })() :
                               zhLabelForState(t.state)}
                            </span>
                            {(() => {
                              const diag = diagnoseStatus(t);
                              const DiagIcon = diag.icon;
                              return (
                                <div className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-bold ${diag.color}`} title="四方状态一致性诊断">
                                  <DiagIcon size={10} />
                                  {diag.label}
                                </div>
                              );
                            })()}
                            {bucket === 'processing' && typeof t.progress === 'number' && (
                              <span className="text-[11px] font-mono font-medium text-blue-600">{t.progress || 0}%</span>
                            )}
                          </div>
                          {bucket === 'processing' && typeof t.progress === 'number' && (
                            <div className="w-32 h-1 bg-gray-100 rounded-full overflow-hidden">
                              <div className="h-full bg-blue-500 transition-all duration-700 ease-in-out" style={{ width: `${t.progress || 0}%` }} />
                            </div>
                          )}
                          {(t.errorMessage || t.message) && (
                            <p className="text-[11px] text-gray-400 line-clamp-1 max-w-[260px]" title={t.errorMessage || t.message}>
                              {t.errorMessage || t.message}
                            </p>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-gray-500 font-mono text-[11px]">
                        {t.createdAt ? new Date(t.createdAt).toLocaleString('zh-CN', { hour12: false }) : '—'}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex justify-end gap-1.5">
                          <button onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}`)} className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-all" title="查看详情">
                            <Eye size={16} />
                          </button>
                          <button
                            onClick={() => callAction(t, 'retry')}
                            disabled={!canRetry}
                            className="p-1.5 text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg transition-all disabled:opacity-30"
                            title="Retry（克隆新任务）"
                          >
                            <RotateCw size={16} />
                          </button>
                          <button
                            onClick={() => callAction(t, 'reparse')}
                            disabled={!canReparse}
                            className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-all disabled:opacity-30"
                            title="Reparse（重跑解析）"
                          >
                            <RefreshCw size={16} />
                          </button>
                          <button
                            onClick={() => callAction(t, 're-ai')}
                            disabled={!canReAi}
                            className="p-1.5 text-gray-400 hover:text-violet-600 hover:bg-violet-50 rounded-lg transition-all disabled:opacity-30"
                            title="Re-AI（重跑 AI）"
                          >
                            <Sparkles size={16} />
                          </button>
                          <button
                            onClick={() => callAction(t, 'cancel')}
                            disabled={!canCancel}
                            className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-all disabled:opacity-30"
                            title="Cancel"
                          >
                            <XCircle size={16} />
                          </button>
                          <button
                            onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}#review`)}
                            disabled={t.state !== 'review-pending' && t.state !== 'completed'}
                            className="p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-all disabled:opacity-30"
                            title="审核"
                          >
                            <ShieldCheck size={16} />
                          </button>
                          <button
                            className="p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-all disabled:opacity-30"
                            disabled={t.state !== 'completed'}
                            title="下载解析结果 (ZIP)"
                          >
                            <Download size={16} />
                          </button>
                          <button
                            onClick={() => { if (window.confirm('确定要删除此任务记录吗？')) deleteTask(t.id); }}
                            className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-all opacity-0 group-hover:opacity-100"
                            title="删除任务"
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
