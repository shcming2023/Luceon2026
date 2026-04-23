import { useEffect, useMemo, useRef, useState } from 'react';
import { Eye, FolderPlus, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import type { Material } from '../../store/types';
import { Link } from 'react-router-dom';
import { useFileUpload } from '../hooks/useFileUpload';
import { deriveMaterialTaskView, ParseTask } from '../utils/taskView';
import { AlertTriangle, ExternalLink } from 'lucide-react';
 
type FilterKey = 'all' | 'pending' | 'processing' | 'failed' | 'completed';
 
function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
 
export function WorkspacePage() {
  const { state, dispatch } = useAppStore();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const { upload, uploading, progress } = useFileUpload();
  const [tasks, setTasks] = useState<ParseTask[]>([]);

  const fetchTasks = async () => {
    try {
      const res = await fetch('/__proxy/db/tasks');
      if (res.ok) {
        const data = await res.json();
        setTasks(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.warn('[Workspace] fetchTasks failed:', e);
    }
  };
 
  useEffect(() => {
    const el = folderInputRef.current as unknown as { webkitdirectory?: boolean; directory?: boolean; setAttribute?: (k: string, v: string) => void } | null;
    if (!el) return;
    el.webkitdirectory = true;
    el.directory = true;
    el.setAttribute?.('webkitdirectory', '');
    el.setAttribute?.('directory', '');
    fetchTasks();
  }, []);

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
 
  const items = useMemo(() => {
    const list = [...state.materials];
    list.sort((a, b) => (b.uploadTimestamp || 0) - (a.uploadTimestamp || 0));
    return list;
  }, [state.materials]);

  const getFilterKey = (m: Material): FilterKey => {
    const view = deriveMaterialTaskView(m, tasks);
    if (view.bucket === 'failed' || view.bucket === 'canceled') return 'failed';
    if (view.bucket === 'completed') return 'completed';
    if (view.bucket === 'queued') return 'pending';
    return 'processing';
  };

  const filtered = useMemo(() => {
    if (filter === 'all') return items;
    return items.filter((m) => getFilterKey(m) === filter);
  }, [filter, items]);

  const counts = useMemo(() => {
    const all = items.length;
    const pending = items.filter((m) => getFilterKey(m) === 'pending').length;
    const processing = items.filter((m) => getFilterKey(m) === 'processing').length;
    const failed = items.filter((m) => getFilterKey(m) === 'failed').length;
    const completed = items.filter((m) => getFilterKey(m) === 'completed').length;
    return { all, pending, processing, failed, completed };
  }, [items]);

  const toggleSelectAll = (checked: boolean) => {
    if (!checked) return setSelectedIds(new Set());
    setSelectedIds(new Set(filtered.map((m) => m.id)));
  };

  const toggleOne = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const deleteMaterials = async (ids: number[]) => {
    if (ids.length === 0) return;
    try {
      // 使用级联删除接口，确保 MinIO 文件、关联任务和 AI Job 同步清除
      const errors: string[] = [];
      for (const id of ids) {
        try {
          const resp = await fetch(`/__proxy/upload/materials/${encodeURIComponent(String(id))}`, {
            method: 'DELETE',
            signal: AbortSignal.timeout(30_000),
          });
          if (!resp.ok) {
            const errData = await resp.json().catch(() => null);
            errors.push(`ID ${id}: ${errData?.error || `HTTP ${resp.status}`}`);
          }
        } catch (e) {
          errors.push(`ID ${id}: ${e instanceof Error ? e.message : String(e)}`);
        }
      }

      if (errors.length > 0 && errors.length === ids.length) {
        throw new Error(`全部删除失败：${errors.join('; ')}`);
      }

      dispatch({ type: 'DELETE_MATERIAL', payload: ids });
      if (errors.length > 0) {
        toast.warning(`部分删除失败：${errors.join('; ')}`, { duration: 6000 });
      } else {
        toast.success(ids.length === 1 ? '已删除' : '已删除所选资料');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `删除失败：${String(e)}`);
    }
  };

  const removeSelected = async () => {
    const ids = Array.from(selectedIds);
    await deleteMaterials(ids);
    setSelectedIds(new Set());
  };

  const stageLabel = (m: Material) => {
    if (m.status === 'failed' || m.mineruStatus === 'failed' || m.aiStatus === 'failed') return '失败';
    if (m.aiStatus === 'analyzing') return 'AI 分析';
    if (m.mineruStatus === 'processing') return 'MinerU 解析';
    if (m.mineruStatus === 'pending') return '待解析';
    if (m.aiStatus === 'pending') return '待分析';
    if (m.status === 'completed' && m.aiStatus === 'analyzed') return '已完成';
    return '处理中';
  };
 
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
            资料 {counts.all} · 待处理 {counts.pending} · 处理中 {counts.processing} · 失败 {counts.failed} · 完成 {counts.completed}
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
              onClick={() => { setFilter(t.key); setSelectedIds(new Set()); }}
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
                  checked={filtered.length > 0 && selectedIds.size === filtered.length}
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
            {filtered.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center py-14 text-gray-400">暂无任务</td>
              </tr>
            )}
            {filtered.map((material) => {
              const type = material?.metadata?.format || material?.type || '-';
              const createdAt = material.uploadTimestamp || 0;
              const stage = stageLabel(material);
              return (
                <tr key={material.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(material.id)}
                      onChange={() => toggleOne(material.id)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="min-w-0">
                      {material.id ? (
                        <Link
                          to={`/asset/${material.id}`}
                          className="text-blue-600 hover:underline font-medium truncate block"
                          title={material.metadata?.relativePath || material.title}
                        >
                          {material.metadata?.relativePath || material.title}
                        </Link>
                      ) : (
                        <div className="text-gray-900 font-medium truncate" title={material.metadata?.relativePath || material.title}>
                          {material.metadata?.relativePath || material.title}
                        </div>
                      )}
                      <div className="text-xs text-gray-400 mt-0.5">{formatBytes(material.sizeBytes)}</div>
                      {material.metadata?.processingMsg && (
                        <div className={`text-xs truncate ${stage === '失败' ? 'text-red-600' : 'text-gray-500'}`} title={material.metadata.processingMsg}>
                          {material.metadata.processingMsg}
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{type}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col gap-1">
                      {(() => {
                        const view = deriveMaterialTaskView(material, tasks);
                        return (
                          <>
                            <div className="flex items-center gap-1.5">
                              <span className={`text-xs px-2 py-1 rounded-full border w-fit font-medium ${
                                view.bucket === 'completed'
                                  ? 'bg-green-50 text-green-700 border-green-200'
                                  : view.bucket === 'failed' || view.bucket === 'canceled'
                                    ? 'bg-red-50 text-red-700 border-red-200'
                                    : view.bucket === 'processing' || view.bucket === 'reviewing'
                                      ? 'bg-blue-50 text-blue-700 border-blue-200'
                                      : 'bg-gray-50 text-gray-700 border-gray-200'
                              }`}>
                                {view.displayStatus}
                              </span>
                              {view.hasStateDrift && (
                                <span className="text-amber-600" title={`状态异常: ${view.driftReason}`}>
                                  <AlertTriangle size={14} />
                                </span>
                              )}
                            </div>
                            {view.currentTask && (
                              <Link 
                                to={`/tasks/${view.currentTask.id}`}
                                className="text-[10px] text-gray-400 hover:text-blue-500 flex items-center gap-0.5"
                              >
                                <ExternalLink size={10} /> {view.currentTask.id.slice(0, 12)}...
                              </Link>
                            )}
                          </>
                        );
                      })()}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
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
                      {material.id && (
                        <Link
                          to={`/asset/${material.id}`}
                          className="p-2 rounded border border-gray-200 bg-white hover:bg-blue-50 text-blue-600"
                          title="查看详情"
                        >
                          <Eye size={14} />
                        </Link>
                      )}
                      <button
                        onClick={async () => {
                          await deleteMaterials([material.id]);
                        }}
                        className="p-2 rounded border border-red-200 bg-white text-red-600 hover:bg-red-50"
                        title="删除"
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
