import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, Pencil, Clock, Eye } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import { PDFPreviewPanel } from '../components/PDFPreviewPanel';
import { PreviewTabPanel } from '../components/PreviewTabPanel';
import { ProcessPipelineCard } from '../components/ProcessPipelineCard';
import { deriveMaterialTaskView, ParseTask, deriveTaskBucket } from '../utils/taskView';
import { AlertTriangle, ExternalLink, RotateCw, RefreshCw, Sparkles, XCircle, ShieldCheck } from 'lucide-react';
import { appendMineruTaskOptions } from '../utils/mineruTaskOptions';

// ── 工具函数 ──────────────────────────────────────────────
const getMaterialTags = (m: any) =>
  Array.isArray(m.tags) ? m.tags : Array.isArray(m.metadata?.tags) ? m.metadata.tags : [];

function inferTypeFromMimeOrName(mime: string, name: string) {
  if (mime?.includes('pdf') || name?.toLowerCase().endsWith('.pdf')) return 'PDF';
  if (mime?.includes('word') || name?.toLowerCase().endsWith('.docx')) return 'DOCX';
  if (mime?.includes('markdown') || name?.toLowerCase().endsWith('.md')) return 'MD';
  return 'UNKNOWN';
}

const getMaterialType = (m: any) =>
  m?.type || inferTypeFromMimeOrName(m?.metadata?.mimeType || m?.mimeType, m?.fileName || m?.title) || 'UNKNOWN';

function getPresignedExpireAtMs(url: string): number | null {
  try {
    const u = new URL(url);
    const dateStr = u.searchParams.get('X-Amz-Date');
    const expStr = u.searchParams.get('X-Amz-Expires');
    if (!dateStr || !expStr) return null;
    const y = Number(dateStr.slice(0, 4));
    const mo = Number(dateStr.slice(4, 6));
    const d = Number(dateStr.slice(6, 8));
    const h = Number(dateStr.slice(9, 11));
    const mi = Number(dateStr.slice(11, 13));
    const s = Number(dateStr.slice(13, 15));
    const issuedAt = Date.UTC(y, mo - 1, d, h, mi, s);
    const expiresSec = Number(expStr);
    if (!Number.isFinite(issuedAt) || !Number.isFinite(expiresSec)) return null;
    return issuedAt + expiresSec * 1000;
  } catch {
    return null;
  }
}

export function AssetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const routeMaterialId = String(id || '');
  const numId = Number(routeMaterialId);
  const { state, dispatch } = useAppStore();
  const navigate = useNavigate();

  const assetDetailsById = state.assetDetails as unknown as Record<string, any>;
  const detail = assetDetailsById[routeMaterialId] ?? (Number.isFinite(numId) ? assetDetailsById[String(numId)] : undefined);
  const material = state.materials.find((m) => String(m.id) === routeMaterialId);
  const materialId = material?.id ?? routeMaterialId;

  // P0 防御：material 未找到时的处理
  // 不在组件内 early return，而是让 React 继续渲染
  // deriveMaterialTaskView 已改为接受 Material | undefined，会返回安全默认值
  // 如果 material 确实是 undefined，页面会显示"加载中..."而不是崩溃

  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(detail?.title ?? '');

  const [mineruMarkdown, setMineruMarkdown] = useState<string>('');
  const [aiAnalyzing, setAiAnalyzing] = useState(false);
  const [submittingMineru, setSubmittingMineru] = useState(false);
  const [relatedTasks, setRelatedTasks] = useState<ParseTask[]>([]);
  
  // P0 Patch: 资产详情页运行态事实源统一
  const taskView = deriveMaterialTaskView(material, relatedTasks);
  const currentTask = taskView.currentTask;
  
  const mineruRunning = currentTask ? deriveTaskBucket(currentTask.state) === 'processing' : false;
  const mineruProgress = currentTask?.progress || 0;
  const mineruProgressMsg = currentTask?.message || (mineruRunning ? '处理中...' : '');
  const mineruRetryCount = 0;

  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  const originalRefreshTimerRef = useRef<number | null>(null);

  const objectName = material?.metadata?.objectName;

  useEffect(() => {
    const obj = String(objectName || '').trim();
    if (!obj) return;
    const request = async () => {
      try {
        const r = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(obj)}`, { cache: 'no-store' });
        const d = await r.json();
        if (d?.url) {
          setOriginalUrl(d.url);
          const expireAt = getPresignedExpireAtMs(d.url);
          if (expireAt) {
            const delay = Math.max(30_000, expireAt - Date.now() - 60_000);
            if (originalRefreshTimerRef.current) window.clearTimeout(originalRefreshTimerRef.current);
            originalRefreshTimerRef.current = window.setTimeout(() => {
              void request();
            }, delay);
          }
        }
      } catch {}
    };
    void request();
    return () => {
      if (originalRefreshTimerRef.current) window.clearTimeout(originalRefreshTimerRef.current);
      originalRefreshTimerRef.current = null;
    };
  }, [objectName]);

  const [mdBootLoading, setMdBootLoading] = useState(false);
  const [mdBootError, setMdBootError] = useState('');

  useEffect(() => {
    const mdObj = material?.metadata?.markdownObjectName;
    const mdUrl = material?.metadata?.markdownUrl;
    if (!material?.id || (!mdObj && !mdUrl)) return;

    setMdBootLoading(true);
    setMdBootError('');

    (async () => {
      try {
        let url = mdUrl;
        if (!url && mdObj) {
          const r = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(mdObj)}`, { cache: 'no-store' });
          const d = await r.json();
          url = d?.url;
        }
        if (!url) throw new Error('无法获取 Markdown 访问地址');
        let res = await fetch(url, { cache: 'no-store' });
        if (res.status === 403 && mdObj) {
          const r = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(mdObj)}`, { cache: 'no-store' });
          const d = await r.json();
          const retryUrl = d?.url;
          if (retryUrl) res = await fetch(retryUrl, { cache: 'no-store' });
        }
        if (!res.ok) throw new Error(`读取失败: HTTP ${res.status}`);
        setMineruMarkdown(await res.text());
      } catch (e) {
        setMdBootError(e instanceof Error ? e.message : String(e));
      } finally {
        setMdBootLoading(false);
      }
    })();
  }, [material?.id, material?.metadata?.markdownObjectName, material?.metadata?.markdownUrl]);

  // 元数据可编辑表单（语言/年级/学科/国家/类型 + 摘要）
  const [metaForm, setMetaForm] = useState({
    language:    material?.metadata?.language || '',
    grade:       material?.metadata?.grade || '',
    subject:     material?.metadata?.subject || '',
    country:     material?.metadata?.country || '',
    type:        material?.metadata?.type || '',
    summary:     material?.metadata?.summary || '',
  });

  useEffect(() => {
    if (!material) return;
    setMetaForm({
      language: material.metadata?.language || '',
      grade: material.metadata?.grade || '',
      subject: material.metadata?.subject || '',
      country: material.metadata?.country || '',
      type: material.metadata?.type || '',
      summary: material.metadata?.summary || '',
    });
  }, [
    material?.id,
    material?.metadata?.language,
    material?.metadata?.grade,
    material?.metadata?.subject,
    material?.metadata?.country,
    material?.metadata?.type,
    material?.metadata?.summary,
  ]);

  useEffect(() => {
    setTitleDraft((material?.title || detail?.title) ?? '');
  }, [material?.title, detail?.title]);

  const updateMeta = (key: keyof typeof metaForm, val: string) =>
    setMetaForm((prev) => ({ ...prev, [key]: val }));

  const isDirty = !!material && (
    metaForm.language !== (material.metadata?.language || '')
    || metaForm.grade !== (material.metadata?.grade || '')
    || metaForm.subject !== (material.metadata?.subject || '')
    || metaForm.country !== (material.metadata?.country || '')
    || metaForm.type !== (material.metadata?.type || '')
    || metaForm.summary !== (material.metadata?.summary || '')
  );

  // ── W2-4: 获取关联任务列表 ────────────────────────────────
  const fetchRelatedTasks = async () => {
    if (!routeMaterialId) return;
    try {
      const res = await fetch('/__proxy/db/tasks');
      if (!res.ok) return;
      const all: ParseTask[] = await res.json();
      setRelatedTasks(all.filter(t => String(t.materialId) === String(materialId)));
    } catch {}
  };

  useEffect(() => {
    fetchRelatedTasks();
    const timer = setInterval(fetchRelatedTasks, 5000); // 轮询任务状态
    return () => clearInterval(timer);
  }, [routeMaterialId, material?.id]);

  useEffect(() => {
    const handleRefresh = () => fetchRelatedTasks();
    window.addEventListener('task-action-completed', handleRefresh);
    return () => window.removeEventListener('task-action-completed', handleRefresh);
  }, []);

  useEffect(() => {
    if (!isDirty) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty]);

  /**
   * P1 Patch: 智能返回逻辑。
   * 优先使用浏览器 history 返回上一页；
   * 如果无历史记录（直接访问该页），默认跳转 /library。
   * 不再返回 /workspace。
   */
  const handleBackToList = () => {
    if (isDirty && !window.confirm('当前元数据尚未保存，确定离开此页面吗？')) {
      return;
    }
    // 浏览器 history.length > 2 表示有可返回页（“2”是因为某些浏览器初始 length=1）
    if (window.history.length > 2) {
      navigate(-1);
    } else {
      navigate('/library');
    }
  };

  const handleSaveTitle = async () => {
    const nextTitle = titleDraft.trim();
    if (!material) return;
    if (!nextTitle) {
      setTitleDraft(material.title || detail?.title || '');
      setEditingTitle(false);
      toast.error('标题不能为空');
      return;
    }
    if (nextTitle === material.title) {
      setEditingTitle(false);
      return;
    }
    try {
      // 同时同步到 Materials 和 AssetDetails
      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: materialId as any,
          updates: { title: nextTitle },
        },
      });
      setEditingTitle(false);
      toast.success('标题已同步');
    } catch (e) {
      toast.error('保存标题失败');
    }
  };

  const handleDownloadParsedZip = async () => {
    if (!material?.id) return;
    try {
      toast.info('正在打包解析产物...');
      const r = await fetch('/__proxy/upload/parsed-zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialId }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
        throw new Error(err.error || `HTTP ${r.status}`);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `parsed-${material.title || materialId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('解析产物 ZIP 已下载');
    } catch (err) {
      toast.error(`下载失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  if (!material && !detail) {
    return (
      <div className="p-6">
        <button
          onClick={handleBackToList}
          className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 mb-4"
        >
          <ArrowLeft size={16} /> 返回上一页
        </button>
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          资产 #{id} 不存在或已被删除
        </div>
      </div>
    );
  }

  const handleMineruParse = async () => {
    if (!material) { toast.error('找不到资料信息'); return; }
    
    const view = deriveMaterialTaskView(material, relatedTasks);
    if (view.currentTask && deriveTaskBucket(view.currentTask.state) === 'processing') {
      toast.warning('已有进行中的任务，请勿重复提交', {
        action: { label: '查看任务', onClick: () => navigate(`/tasks/${view.currentTask?.id}`) }
      });
      return;
    }

    let objectName = String(material.metadata?.objectName || '').trim();
    const fileUrl = String(material.metadata?.fileUrl || '').trim();
    if (!objectName && !fileUrl) {
      toast.error('文件尚未上传或缺少访问地址');
      return;
    }

    setSubmittingMineru(true);

    try {
      if (!objectName && fileUrl) {
        // 先下载并上传到 MinIO (BUGFIX-001 逻辑)
        const blob = await fetch(fileUrl).then((r) => {
          if (!r.ok) throw new Error(`下载文件失败: HTTP ${r.status}`);
          return r.blob();
        });
        const name = material.metadata?.fileName || `${material.title}.${material.type.toLowerCase()}`;
        const formData = new FormData();
        formData.append('file', blob, name);
        formData.append('materialId', String(materialId));
        
        appendMineruTaskOptions(formData, state.mineruConfig);
        const uploadResp = await fetch('/__proxy/upload/tasks', { method: 'POST', body: formData });
        const uploadResult = await uploadResp.json();
        if (!uploadResp.ok) throw new Error(uploadResult.error || `HTTP ${uploadResp.status}`);
        
        toast.success('解析任务已启动');
        fetchRelatedTasks();
      } else {
        const formData = new FormData();
        formData.append('materialId', String(materialId));
        formData.append('objectName', objectName);
        
        appendMineruTaskOptions(formData, state.mineruConfig);
        const resp = await fetch('/__proxy/upload/tasks', { method: 'POST', body: formData });
        const result = await resp.json();
        if (!resp.ok) {
          if (resp.status === 409) {
            toast.warning('当前素材已有进行中的任务', {
              action: { label: '查看任务', onClick: () => navigate(`/tasks/${result.existingTaskId}`) }
            });
          } else {
            throw new Error(result.error || `HTTP ${resp.status}`);
          }
        } else {
          toast.success('解析任务已启动');
          fetchRelatedTasks();
        }
      }
    } catch (err) {
      toast.error(`解析启动失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmittingMineru(false);
    }
  };

  const handleTaskAction = async (taskId: string, action: 'retry' | 'reparse' | 're-ai') => {
    try {
      const res = await fetch(`/__proxy/upload/tasks/${encodeURIComponent(taskId)}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '操作失败');
      toast.success('操作已执行', { description: `新任务 ID: ${data.newTaskId}` });
      fetchRelatedTasks();
    } catch (e) {
      toast.error('操作失败', { description: String(e) });
    }
  };

  const handleAiAnalyze = async () => {
    const view = deriveMaterialTaskView(material!, relatedTasks);
    if (view.currentTask && deriveTaskBucket(view.currentTask.state) === 'processing') {
      toast.warning('已有任务正在处理中');
      return;
    }
    
    if (view.currentTask) {
      await handleTaskAction(view.currentTask?.id, 're-ai');
    } else if (material?.metadata?.markdownObjectName || material?.metadata?.markdownUrl) {
      toast.info('未找到关联任务，请重新解析以建立追踪关系');
    } else {
      toast.error('请先完成 MinerU 解析');
    }
  };

  const handleSaveMeta = () => {
    if (!material) return;
    dispatch({
      type: 'UPDATE_MATERIAL',
      payload: {
        id: materialId as any,
        updates: {
          metadata: {
            ...material.metadata,
            ...metaForm,
          },
        },
      },
    });
    toast.success('元数据已保存');
  };

  const handleRefreshOriginalUrl = async (opts: { silent?: boolean } = {}) => {
    const silent = opts.silent ?? true;
    const objectName = material?.metadata?.objectName;
    if (!objectName) return;
    try {
      const r = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(objectName)}`);
      const d = await r.json();
      if (d?.url) {
        setOriginalUrl(d.url);
        if (!silent) toast.success('访问链接已刷新');
      }
    } catch {
      if (!silent) toast.error('刷新失败，请检查 MinIO 连接');
    }
  };

  const previewMdContent = mineruMarkdown;

  return (
    <div className="h-full p-6 flex flex-col gap-5 overflow-hidden">
      <div className="flex-shrink-0">
        <button
          onClick={handleBackToList}
          className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 mb-3"
        >
          <ArrowLeft size={15} /> 返回上一页
        </button>
        <div className="flex items-start justify-between gap-4">
          <div>
            {editingTitle ? (
              <input
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={handleSaveTitle}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveTitle();
                  if (e.key === 'Escape') {
                    setTitleDraft(material?.title || detail.title);
                    setEditingTitle(false);
                  }
                }}
                autoFocus
                className="w-full max-w-xl text-xl font-bold text-gray-900 border border-blue-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-300"
              />
            ) : (
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold text-gray-900">{material?.title || detail?.title || '未命名资产'}</h1>
                <button
                  onClick={() => setEditingTitle(true)}
                  className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg"
                  title="编辑标题"
                >
                  <Pencil size={14} />
                </button>
              </div>
            )}
            <p className="text-xs text-gray-400 mt-1">
              资产 ID：{detail?.assetId || `MAT-${materialId}`}
              {material?.metadata?.fileName && ` · 文件名：${material.metadata.fileName}`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {(() => {
              const view = deriveMaterialTaskView(material!, relatedTasks);
              return (
                <div className="flex items-center gap-2">
                  <span className={`px-3 py-1 rounded-full text-xs font-bold border ${
                    view.bucket === 'completed' ? 'bg-green-50 text-green-700 border-green-200' :
                    view.bucket === 'failed' ? 'bg-red-50 text-red-700 border-red-200' :
                    view.bucket === 'processing' ? 'bg-blue-50 text-blue-700 border-blue-200 animate-pulse' :
                    'bg-gray-50 text-gray-600 border-gray-200'
                  }`}>
                    {view.displayStatus}
                  </span>
                  {view.hasStateDrift && (
                    <div className="flex items-center gap-1 px-2 py-1 bg-amber-50 text-amber-600 border border-amber-200 rounded text-[10px] font-bold" title={view.driftReason}>
                      <AlertTriangle size={12} /> 状态漂移
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-1 gap-5 lg:grid-cols-5 overflow-hidden">
        <div className="lg:col-span-2 space-y-5 min-h-0 overflow-y-auto pr-1">
          {objectName && getMaterialType(material).toUpperCase() === 'PDF' && (
            <PDFPreviewPanel objectName={objectName} />
          )}
          <ProcessPipelineCard
            material={material}
            originalUrl={originalUrl}
            onRefreshOriginalUrl={handleRefreshOriginalUrl}
            mineruEngineLabel={'本地 FastAPI'}
            mineruRunning={submittingMineru || mineruRunning}
            mineruProgress={mineruProgress}
            mineruProgressMsg={mineruProgressMsg}
            mineruRetryCount={mineruRetryCount}
            onMineruParse={handleMineruParse}
            onDownloadParsedZip={handleDownloadParsedZip}
            aiAnalyzing={aiAnalyzing}
            onAiAnalyze={handleAiAnalyze}
            aiDisabledReason={(!material?.metadata?.markdownObjectName && !material?.metadata?.markdownUrl && !material?.mineruZipUrl && !mineruMarkdown) ? '请先完成 MinerU 解析' : ''}
          />

          {/* [P0] 当前任务卡片 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-gray-800 flex items-center gap-2">
                <Clock size={16} className="text-blue-600" /> 当前任务
              </h2>
              {(() => {
                const view = deriveMaterialTaskView(material!, relatedTasks);
                if (view.currentTask) {
                  return (
                    <Link to={`/tasks/${view.currentTask?.id}`} className="text-xs text-blue-600 hover:underline flex items-center gap-1 font-medium">
                      查看详情 <ExternalLink size={12} />
                    </Link>
                  );
                }
                return null;
              })()}
            </div>
            
            {(() => {
              const view = deriveMaterialTaskView(material!, relatedTasks);
              const task = view.currentTask;
              if (!task) {
                return (
                  <div className="text-center py-6 border-2 border-dashed border-gray-100 rounded-lg bg-gray-50 flex flex-col items-center gap-2">
                    <AlertTriangle size={20} className="text-amber-500" />
                    <p className="text-sm font-bold text-gray-700">暂无关联任务 / 需审计</p>
                    <p className="text-xs text-gray-500 max-w-[200px]">找不到与此素材关联的解析任务。如果这是一条残留数据，请前往审计中心处理，或重新发起解析。</p>
                    <div className="flex gap-2 mt-2">
                      <button onClick={() => navigate('/audit')} className="px-3 py-1.5 bg-white border border-gray-200 rounded text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors">前往一致性审计</button>
                    </div>
                  </div>
                );
              }
              
              const isFailed = task.state === 'failed';
              const isProcessing = deriveTaskBucket(task.state) === 'processing';
              
              return (
                <div className="space-y-4">
                  <div className="flex items-start justify-between gap-4 p-3 bg-gray-50 rounded-lg border border-gray-100">
                    <div>
                      <p className="text-[10px] text-gray-400 font-mono uppercase tracking-wider mb-1">Task ID</p>
                      <p className="text-sm font-bold text-gray-700 font-mono">{task.id}</p>
                      <div className="flex items-center gap-2 mt-2">
                         <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                           isFailed ? 'bg-red-100 text-red-700' :
                           task.state === 'completed' ? 'bg-green-100 text-green-700' :
                           'bg-blue-100 text-blue-700'
                         }`}>
                           {task.state}
                         </span>
                         <span className="text-[10px] text-gray-400">{task.stage || 'prepare'}</span>
                      </div>
                    </div>
                    {isProcessing && (
                      <div className="text-right">
                        <span className="text-lg font-bold text-blue-600 font-mono">{task.progress || 0}%</span>
                      </div>
                    )}
                  </div>

                  {isProcessing && (
                    <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
                      <div className="bg-blue-600 h-full transition-all duration-500" style={{ width: `${task.progress || 0}%` }} />
                    </div>
                  )}

                  {(task.errorMessage || task.message) && (
                    <div className={`text-xs p-2.5 rounded border ${isFailed ? 'bg-red-50 text-red-600 border-red-100' : 'bg-blue-50 text-blue-600 border-blue-100'}`}>
                       <p className="font-semibold mb-1">{isFailed ? '错误信息' : '状态说明'}</p>
                       <p className="line-clamp-3">{task.errorMessage || task.message}</p>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-2 pt-1">
                    <button 
                       onClick={() => handleTaskAction(task.id, 'retry')}
                       disabled={task.state !== 'failed'}
                       className="flex items-center justify-center gap-1.5 py-2 text-xs font-semibold bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-30"
                    >
                      <RotateCw size={14} /> Retry
                    </button>
                    <button 
                       onClick={() => handleTaskAction(task.id, 'reparse')}
                       disabled={!['completed', 'failed', 'review-pending', 'canceled'].includes(task.state || '')}
                       className="flex items-center justify-center gap-1.5 py-2 text-xs font-semibold bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-30"
                    >
                      <RefreshCw size={14} /> Reparse
                    </button>
                    <button 
                       onClick={() => handleTaskAction(task.id, 're-ai')}
                       disabled={!['completed', 'failed', 'review-pending'].includes(task.state || '')}
                       className="flex items-center justify-center gap-1.5 py-2 text-xs font-semibold bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-30"
                    >
                      <Sparkles size={14} /> Re-AI
                    </button>
                    <button 
                       onClick={() => navigate(`/tasks/${task.id}`)}
                       className="flex items-center justify-center gap-1.5 py-2 text-xs font-semibold bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                    >
                      <Eye size={14} /> 详情
                    </button>
                  </div>
                </div>
              );
            })()}
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-gray-800 flex items-center gap-2">
                <RefreshCw size={16} className="text-gray-400" /> 最近任务
              </h2>
              <span className="text-[10px] bg-gray-100 px-1.5 py-0.5 rounded text-gray-500 font-mono">
                {relatedTasks.length}
              </span>
            </div>
            {relatedTasks.length === 0 ? (
              <p className="text-xs text-gray-400 text-center py-4">暂无历史任务</p>
            ) : (
              <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1 custom-scrollbar">
                {relatedTasks.slice(0, 5).map((t) => (
                  <div
                    key={t.id}
                    onClick={() => navigate(`/tasks/${t.id}`)}
                    className="flex items-center justify-between p-2.5 rounded-lg border border-gray-50 hover:border-blue-200 hover:bg-blue-50/30 cursor-pointer transition-all group"
                  >
                    <div className="min-w-0">
                      <p className="text-[11px] font-mono font-bold text-gray-700 group-hover:text-blue-700 truncate">{t.id}</p>
                      <p className="text-[9px] text-gray-400 mt-0.5 uppercase">{t.engine || 'local-mineru'}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-bold uppercase ${
                        t.state === 'completed' ? 'bg-green-100 text-green-700' :
                        t.state === 'failed' ? 'bg-red-100 text-red-700' :
                        deriveTaskBucket(t.state) === 'processing' ? 'bg-blue-100 text-blue-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {t.state}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {detail?.relatedAssets && detail.relatedAssets.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="font-semibold text-gray-800 mb-3">相关资产</h2>
              <div className="space-y-2">
                {detail.relatedAssets.map((ra: any) => (
                  <div
                    key={ra.id}
                    onClick={() => navigate(`/asset/${ra.id}`)}
                    className="flex items-center justify-between p-2 rounded-lg hover:bg-gray-50 cursor-pointer"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-700 truncate">{ra.title}</p>
                      <p className="text-xs text-gray-400">{ra.type}</p>
                    </div>
                    <StatusBadge status={ra.status} className="ml-2 flex-shrink-0" />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="lg:col-span-3 min-h-0 overflow-hidden">
          <PreviewTabPanel
            materialId={materialId}
            material={material}
            markdownContent={previewMdContent}
            mdLoading={mdBootLoading}
            mdError={mdBootError}
            metaForm={metaForm}
            updateMeta={updateMeta}
            isDirty={isDirty}
            onSaveMeta={handleSaveMeta}
          />
        </div>
      </div>
    </div>
  );
}
