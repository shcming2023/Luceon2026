import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Pencil, Clock } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import { PDFPreviewPanel } from '../components/PDFPreviewPanel';
import { PreviewTabPanel } from '../components/PreviewTabPanel';
import { ProcessPipelineCard } from '../components/ProcessPipelineCard';

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
  const numId = Number(id);
  const { state, dispatch } = useAppStore();
  const navigate = useNavigate();

  const detail = state.assetDetails[numId];
  const material = state.materials.find((m) => m.id === numId);

  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(detail?.title ?? '');

  const [mineruMarkdown, setMineruMarkdown] = useState<string>('');
  const mineruRunning = material?.mineruStatus === 'processing';
  const mineruProgress = Number(material?.metadata?.processingProgress || 0);
  const mineruProgressMsg = material?.metadata?.processingMsg || (mineruRunning ? '处理中...' : '');
  const mineruRetryCount = 0;

  const [aiAnalyzing, setAiAnalyzing] = useState(false);
  const [relatedTasks, setRelatedTasks] = useState<Array<{id: string; state: string; engine?: string}>>([]);

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

  const [metaForm, setMetaForm] = useState({
    language:   material?.metadata?.language || '',
    grade:      material?.metadata?.grade || '',
    subject:    material?.metadata?.subject || '',
    country:    material?.metadata?.country || '',
    type:       material?.metadata?.type || '',
    summary:    material?.metadata?.summary || '',
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
    setTitleDraft(detail?.title ?? '');
  }, [detail?.title]);

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
  useEffect(() => {
    if (!numId) return;
    const fetchRelatedTasks = async () => {
      try {
        const res = await fetch('/__proxy/db/tasks');
        if (!res.ok) return;
        const all: Array<{id: string; materialId?: string | number; state: string; engine?: string}> = await res.json();
        setRelatedTasks(all.filter(t => String(t.materialId) === String(numId)));
      } catch {}
    };
    fetchRelatedTasks();
  }, [numId, material?.mineruStatus, material?.aiStatus]);

  useEffect(() => {
    if (!isDirty) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty]);

  const handleBackToList = () => {
    if (isDirty && !window.confirm('当前元数据尚未保存，确定离开此页面吗？')) {
      return;
    }
    navigate('/workspace');
  };

  const handleSaveTitle = () => {
    const nextTitle = titleDraft.trim();
    if (!material) return;
    if (!nextTitle) {
      setTitleDraft(detail?.title ?? '');
      setEditingTitle(false);
      toast.error('标题不能为空');
      return;
    }
    if (nextTitle === detail?.title) {
      setEditingTitle(false);
      return;
    }
    dispatch({
      type: 'UPDATE_MATERIAL',
      payload: {
        id: numId,
        updates: { title: nextTitle },
      },
    });
    setEditingTitle(false);
    toast.success('标题已更新');
  };

  const handleDownloadParsedZip = async () => {
    if (!material?.id) return;
    try {
      toast.info('正在打包解析产物...');
      const r = await fetch('/__proxy/upload/parsed-zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialId: numId }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
        throw new Error(err.error || `HTTP ${r.status}`);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `parsed-${material.title || numId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('解析产物 ZIP 已下载');
    } catch (err) {
      toast.error(`下载失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  if (!detail) {
    return (
      <div className="p-6">
        <button
          onClick={handleBackToList}
          className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 mb-4"
        >
          <ArrowLeft size={16} /> 返回工作台
        </button>
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          资产 #{id} 不存在或已被删除
        </div>
      </div>
    );
  }

  const handleMineruParse = async () => {
    if (!material) { toast.error('找不到资料信息'); return; }

    // BUGFIX-001: 检查是否已有活跃的解析任务，避免重复提交
    const existingParseTasks = state.processTasks.filter((t) => t.materialId === numId);
    const hasActiveTask = existingParseTasks.some(
      (t) => t.status === 'processing' || t.status === 'completed'
    );
    if (hasActiveTask) {
      toast.warning('该文件已有解析任务在进行或已完成，无需重复提交');
      return;
    }

    let objectName = String(material.metadata?.objectName || '').trim();
    const fileUrl = String(material.metadata?.fileUrl || '').trim();
    if (!objectName && !fileUrl) {
      toast.error('文件尚未上传或缺少访问地址');
      return;
    }

    try {
      // 如果没有 objectName 但有 fileUrl，先上传到 MinIO
      if (!objectName && fileUrl) {
        const blob = await fetch(fileUrl).then((r) => {
          if (!r.ok) throw new Error(`下载文件失败: HTTP ${r.status}`);
          return r.blob();
        });
        const name = material.metadata?.fileName || `${material.title}.${material.type.toLowerCase()}`;
        const file = new File([blob], name, { type: blob.type || 'application/octet-stream' });

        const formData = new FormData();
        formData.append('file', file);
        formData.append('materialId', String(numId));
        const uploadRes = await fetch('/__proxy/upload/upload', { method: 'POST', body: formData });
        if (!uploadRes.ok) throw new Error(`上传失败: HTTP ${uploadRes.status}`);
        const uploadResult = await uploadRes.json();
        objectName = String(uploadResult?.objectName || '').trim();
        if (!objectName) throw new Error('上传成功但未获得 objectName');

        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: numId,
            updates: {
              metadata: {
                ...material.metadata,
                objectName,
                fileUrl: uploadResult.url,
                fileName: uploadResult.fileName,
                provider: uploadResult.provider,
                mimeType: uploadResult.mimeType,
              },
            },
          },
        });
      }

      // 使用 PRD 主链路：下载文件后通过 POST /tasks 创建 ParseTask
      // ParseTaskWorker 会自动拾取并通过 local-mineru adapter 调用 FastAPI
      const presignRes = await fetch(`/__proxy/upload/presign?objectName=${encodeURIComponent(objectName)}`, { cache: 'no-store' });
      const presignData = await presignRes.json();
      if (!presignData?.url) throw new Error('无法获取文件预签名URL');

      const fileBlob = await fetch(presignData.url).then(r => {
        if (!r.ok) throw new Error(`下载文件失败: HTTP ${r.status}`);
        return r.blob();
      });
      const fileName = material.metadata?.fileName || material.title || 'document.pdf';
      const file = new File([fileBlob], fileName, { type: material.metadata?.mimeType || 'application/pdf' });

      const formData = new FormData();
      formData.append('file', file);
      formData.append('materialId', String(numId));

      const res = await fetch('/__proxy/upload/tasks', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error((errData as { error?: string }).error || `HTTP ${res.status}`);
      }

      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: numId,
          updates: {
            status: 'processing',
            mineruStatus: 'pending',
            aiStatus: 'pending',
            metadata: {
              ...material.metadata,
              processingStage: 'mineru',
              processingMsg: '解析任务已提交（PRD 主链路）',
              processingProgress: '0',
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        },
      });

      toast.info('解析任务已提交，Worker 将自动处理');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误';
      dispatch({ type: 'UPDATE_MATERIAL_MINERU_STATUS', payload: { id: numId, mineruStatus: 'failed' } });
      toast.error(`MinerU 解析失败: ${msg}`);
    }
  };
