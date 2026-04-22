import { useEffect, useState, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search,
  Upload,
  Grid,
  List,
  SortAsc,
  AlertTriangle,
  AlertCircle,
  RefreshCw,
  Trash2,
  ChevronDown,
  ChevronRight,
  FolderPlus,
  FileText,
  Cpu,
  CheckCircle,
  Loader,
  Play,
  Clock,
} from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import type { TabFilter, SortOption, ViewMode } from '../../store/types';
import { sortMaterials } from '../../utils/sort';
import { usePagination, getPageNumbers } from '../../utils/pagination';
import { checkLocalMinerUHealth } from '../../utils/mineruLocalApi';
import { generateNumericIdFromUuid } from '../../utils/id';

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
  m.type || inferTypeFromMimeOrName(m.metadata?.mimeType || m.mimeType, m.fileName || m.title) || 'UNKNOWN';

/** 删除确认弹窗 */
function confirmDelete(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    toast(message, {
      duration: 10000,
      action: { label: '确认删除', onClick: () => resolve(true) },
      cancel: { label: '取消', onClick: () => resolve(false) },
      onDismiss: () => resolve(false),
      onAutoClose: () => resolve(false),
    });
  });
}

const TAB_OPTIONS: { key: TabFilter; label: string }[] = [
  { key: 'all',        label: '全部' },
  { key: 'pending',    label: '待处理' },
  { key: 'processing', label: '处理中' },
  { key: 'reviewing',  label: '审核中' },
  { key: 'failed',     label: '失败' },
  { key: 'completed',  label: '已完成' },
];

const SORT_OPTIONS: { key: SortOption; label: string }[] = [
  { key: 'newest', label: '最新上传' },
  { key: 'oldest', label: '最早上传' },
  { key: 'name',   label: '名称' },
  { key: 'size',   label: '文件大小' },
];

const MINERU_STATUS_OPTIONS = [
  { key: 'all', label: '全部 MinerU 状态' },
  { key: 'pending', label: '待解析' },
  { key: 'processing', label: '解析中' },
  { key: 'completed', label: '解析完成' },
  { key: 'failed', label: '解析失败' },
] as const;

const AI_STATUS_OPTIONS = [
  { key: 'all', label: '全部 AI 状态' },
  { key: 'pending', label: '待分析' },
  { key: 'analyzing', label: '分析中' },
  { key: 'analyzed', label: '已分析' },
  { key: 'failed', label: '分析失败' },
] as const;

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function SourceMaterialsPage() {
  const { state, dispatch } = useAppStore();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const uploadHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [tab, setTab] = useState<TabFilter>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortOption>('newest');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [advancedExpanded, setAdvancedExpanded] = useState(false);
  const [subjectFilter, setSubjectFilter] = useState('all');
  const [gradeFilter, setGradeFilter] = useState('all');
  const [languageFilter, setLanguageFilter] = useState('all');
  const [mineruStatusFilter, setMineruStatusFilter] = useState<(typeof MINERU_STATUS_OPTIONS)[number]['key']>('all');
  const [aiStatusFilter, setAiStatusFilter] = useState<(typeof AI_STATUS_OPTIONS)[number]['key']>('all');

  const advancedOptions = useMemo(() => {
    const unique = (values: (string | undefined)[]) =>
      [...new Set(values.map((v) => v?.trim()).filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    return {
      subjects: unique(state.materials.map((i) => i.metadata?.subject)),
      grades: unique(state.materials.map((i) => i.metadata?.grade)),
      languages: unique(state.materials.map((i) => i.metadata?.language)),
    };
  }, [state.materials]);

  const filtered = useMemo(() => {
    let list = state.materials;
    if (tab !== 'all') list = list.filter((m) => m.status === tab);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (m) =>
          m.title.toLowerCase().includes(q) ||
          getMaterialTags(m).some((t: string) => t.toLowerCase().includes(q)) ||
          m.uploader.toLowerCase().includes(q),
      );
    }
    if (subjectFilter !== 'all') list = list.filter((m) => m.metadata?.subject === subjectFilter);
    if (gradeFilter !== 'all') list = list.filter((m) => m.metadata?.grade === gradeFilter);
    if (languageFilter !== 'all') list = list.filter((m) => m.metadata?.language === languageFilter);
    if (mineruStatusFilter !== 'all') list = list.filter((m) => m.mineruStatus === mineruStatusFilter);
    if (aiStatusFilter !== 'all') list = list.filter((m) => m.aiStatus === aiStatusFilter);
    return sortMaterials(list, sort);
  }, [state.materials, tab, search, sort, subjectFilter, gradeFilter, languageFilter, mineruStatusFilter, aiStatusFilter]);

  const { currentItems, currentPage, totalPages, goToPage, hasPrev, hasNext, prevPage, nextPage } =
    usePagination(filtered);
  const pageNumbers = getPageNumbers(currentPage, totalPages);

  const summary = useMemo(() => {
    const statusCounts = filtered.reduce<Record<TabFilter, number>>(
      (acc, item) => { acc[item.status] += 1; return acc; },
      { all: filtered.length, pending: 0, processing: 0, reviewing: 0, failed: 0, completed: 0 },
    );
    const totalSizeBytes = filtered.reduce((sum, item) => sum + (item.sizeBytes || 0), 0);
    const subjectCoverage = new Set(filtered.map((item) => item.metadata?.subject).filter(Boolean)).size;
    return { statusCounts, totalSizeBytes, subjectCoverage };
  }, [filtered]);

  const [uploadServerOk, setUploadServerOk] = useState<boolean | null>(null);
  const [mineruOk, setMineruOk] = useState<boolean | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number; failed: number } | null>(null);

  useEffect(() => {
    let mounted = true;
    const check = () => {
      fetch('/__proxy/upload/health', { signal: AbortSignal.timeout(5000) })
        .then((r) => r.ok ? r.json().catch(() => ({})) : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then((data) => {
          if (!mounted) return;
          setUploadServerOk(Boolean((data as { ok?: boolean } | null)?.ok));
        })
        .catch(() => {
          if (!mounted) return;
          setUploadServerOk(false);
        });
    };
    check();
    const timer = setInterval(check, 30_000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);

  useEffect(() => {
    let mounted = true;
    const check = () => {
      checkLocalMinerUHealth(String(state.mineruConfig.localEndpoint || ''))
        .then((res) => {
          if (!mounted) return;
          setMineruOk(Boolean(res.ok));
        })
        .catch(() => {
          if (!mounted) return;
          setMineruOk(false);
        });
    };
    check();
    const timer = setInterval(check, 30_000);
    return () => { mounted = false; clearInterval(timer); };
  }, [state.mineruConfig.localEndpoint]);

  // 轮询 materials 列表，每3秒刷新一次
  useEffect(() => {
    let mounted = true;
    const refreshMaterials = async () => {
      try {
        const res = await fetch('/__proxy/db/materials', { signal: AbortSignal.timeout(5000) });
        if (res.ok && mounted) {
          const data = await res.json();
          if (Array.isArray(data)) {
            dispatch({ type: 'SET_MATERIALS', payload: data });
          }
        }
      } catch (err) {
        console.error('Failed to refresh materials:', err);
      }
    };
    refreshMaterials();
    const timer = setInterval(refreshMaterials, 3000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);

  const isCurrentPageFullySelected = currentItems.length > 0 && currentItems.every((item) => selectedIds.has(item.id));

  const handleSelectCurrentPage = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (isCurrentPageFullySelected) {
        currentItems.forEach((item) => next.delete(item.id));
      } else {
        currentItems.forEach((item) => next.add(item.id));
      }
      return next;
    });
  };

  const validateFile = (file: File): { valid: boolean; error?: string } => {
    const { mineruConfig } = state;
    const MAX_SIZE = (mineruConfig.maxFileSize || 0) > 0 ? mineruConfig.maxFileSize : 200 * 1024 * 1024;
    if (file.size > MAX_SIZE) {
      return { valid: false, error: `文件 "${file.name}" 超过上传限制 (最大 ${Math.round(MAX_SIZE / (1024 * 1024))}MB)` };
    }
    return { valid: true };
  };

  const showStatusColumn = tab !== 'completed';

  const getStageSummary = (m: typeof state.materials[number]) => {
    if (m.status === 'completed') return { label: '完成', detail: '' };
    if (m.status === 'failed') {
      const detail = m.aiStatus === 'failed' ? 'AI 分析失败' : m.mineruStatus === 'failed' ? 'MinerU 解析失败' : '处理失败';
      return { label: '失败', detail };
    }
    const stage = String(m.metadata?.processingStage || '').trim();
    const msg = String(m.metadata?.processingMsg || '').trim();
    if (stage === 'upload') return { label: '上传中', detail: msg };
    if (stage === 'mineru') return { label: 'MinerU 解析中', detail: msg };
    if (stage === 'ai') return { label: 'AI 分析中', detail: msg };
    if (m.aiStatus === 'analyzing') return { label: 'AI 分析中', detail: msg };
    if (m.mineruStatus === 'processing') return { label: 'MinerU 解析中', detail: msg };
    if (m.mineruStatus === 'pending') return { label: '待解析', detail: msg };
    if (m.aiStatus === 'pending') return { label: '待分析', detail: msg };
    return { label: '处理中', detail: msg };
  };

  const [batchUploading, setBatchUploading] = useState(false);

  const handleBatchFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    const invalidFiles = files.filter((f) => !validateFile(f).valid);
    if (invalidFiles.length > 0) {
      toast.error(`发现 ${invalidFiles.length} 个不符合规范的文件被过滤`, { icon: <AlertTriangle size={16} /> });
    }
    const validFiles = files.filter((f) => validateFile(f).valid);
    e.target.value = '';
    if (validFiles.length === 0) return;

    setBatchUploading(true);
    if (uploadHideTimerRef.current) {
      clearTimeout(uploadHideTimerRef.current);
      uploadHideTimerRef.current = null;
    }
    setUploadProgress({ done: 0, total: validFiles.length, failed: 0 });

    const items = validFiles.map((file) => {
      const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      const materialId = generateNumericIdFromUuid();
      const jobId = `job-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      return { file, filePath, materialId, jobId };
    });

    for (const it of items) {
      dispatch({
        type: 'ADD_MATERIAL',
        payload: {
          id: it.materialId,
          title: it.file.name.replace(/\.[^.]+$/, ''),
          type: it.file.name.split('.').pop()?.toUpperCase() ?? 'FILE',
          size: `${(it.file.size / 1024 / 1024).toFixed(1)} MB`,
          sizeBytes: it.file.size,
          uploadTime: '上传中...',
          uploadTimestamp: Date.now(),
          status: 'processing',
          mineruStatus: 'pending',
          aiStatus: 'pending',
          tags: [],
          metadata: {
            relativePath: it.filePath,
            processingStage: 'upload',
            processingMsg: '待上传',
            processingProgress: '0',
            processingUpdatedAt: new Date().toISOString(),
          },
          uploader: '当前用户',
        },
      });
    }

    const uploadWithProgress = (file: File, materialId: number, onProgress: (pct: number) => void) => {
      return new Promise<any>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/__proxy/upload/tasks');
        xhr.responseType = 'json';
        xhr.upload.onprogress = (evt) => {
          if (!evt.lengthComputable) return;
          const pct = Math.max(0, Math.min(100, Math.round((evt.loaded / Math.max(1, evt.total)) * 100)));
          onProgress(pct);
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
          else reject(new Error((xhr.response && xhr.response.error) || xhr.responseText || `HTTP ${xhr.status}`));
        };
        xhr.onerror = () => reject(new Error('网络错误'));
        const formData = new FormData();
        formData.append('file', file);
        formData.append('materialId', String(materialId));
        xhr.send(formData);
      });
    };

    const uploadOne = async (it: { file: File; filePath: string; materialId: number; jobId: string }) => {
      let lastEmitAt = 0;
      const emit = async (pct: number) => {
        const now = Date.now();
        if (now - lastEmitAt < 400 && pct !== 100) return;
        lastEmitAt = now;
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: it.materialId,
            updates: {
              metadata: {
                relativePath: it.filePath,
                processingStage: 'upload',
                processingMsg: `上传中 ${pct}%`,
                processingProgress: String(pct),
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });
      };

      try {
        await emit(0);
        const uploadResult = await uploadWithProgress(it.file, it.materialId, (pct) => { void emit(pct); });
        const objectName = String(uploadResult?.objectName || '').trim();
        if (!objectName) throw new Error('上传成功但未获得 objectName（未写入 MinIO）');

        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: it.materialId,
            updates: {
              uploadTime: '刚刚',
              metadata: {
                relativePath: it.filePath,
                fileUrl: uploadResult.url,
                objectName,
                fileName: uploadResult.fileName,
                provider: uploadResult.provider,
                mimeType: uploadResult.mimeType,
                ...(uploadResult.pages != null ? { pages: String(uploadResult.pages) } : {}),
                ...(uploadResult.format ? { format: uploadResult.format } : {}),
                processingStage: 'mineru',
                processingMsg: '等待后端队列处理',
                processingProgress: '0',
                processingUpdatedAt: new Date().toISOString(),
              },
            },
          },
        });

        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          console.warn(`[frontend] 上传中途出错: ${msg}`);
          dispatch({
            type: 'UPDATE_MATERIAL',
            payload: {
              id: it.materialId,
              updates: {
                status: 'failed',
                mineruStatus: 'failed',
                aiStatus: 'failed',
                uploadTime: '上传失败',
                metadata: {
                  processingStage: '',
                  processingMsg: `上传失败：${msg}`,
                  processingUpdatedAt: new Date().toISOString(),
                },
              },
            },
          });
          setUploadProgress((prev) => (prev ? { ...prev, failed: prev.failed + 1 } : prev));
        } finally {
        setUploadProgress((prev) => {
          if (!prev) return prev;
          const next = { ...prev, done: prev.done + 1 };
          if (next.done >= next.total) {
            uploadHideTimerRef.current = setTimeout(() => {
              setUploadProgress(null);
              uploadHideTimerRef.current = null;
            }, 2000);
          }
          return next;
        });
      }
    };

    const concurrency = 3;
    let idx = 0;
    const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
      while (idx < items.length) {
        const current = items[idx];
        idx += 1;
        await uploadOne(current);
      }
    });
    await Promise.all(runners);

    setBatchUploading(false);
  };

  const handleResetConfig = () => {
    try {
      localStorage.removeItem('app_ai_config');
      localStorage.removeItem('app_mineru_config');
      localStorage.removeItem('app_minio_config');
      localStorage.removeItem('app_ai_rule_settings');
      toast.success('配置已重置，页面将刷新');
      setTimeout(() => window.location.reload(), 1000);
    } catch {
      toast.error('重置配置失败');
    }
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    const relatedTaskCount = state.processTasks.filter((t) => t.materialId !== undefined && ids.includes(t.materialId)).length;
    const hasUploaded = ids.some((id) => {
      const m = state.materials.find((mat) => mat.id === id);
      return m?.metadata?.objectName || m?.mineruStatus || m?.aiStatus;
    });
    let msg = `确定删除选中的 ${ids.length} 条资料吗？此操作不可撤销。`;
    if (hasUploaded) msg += '\n\n其中部分资料已上传至云存储，删除后原始文件和解析产物将一并清除。';
    if (relatedTaskCount > 0) msg += `\n关联的 ${relatedTaskCount} 条处理任务也将同步删除。`;
    const ok = await confirmDelete(msg);
    if (!ok) return;
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

      dispatch({ type: 'DELETE_MATERIAL', payload: ids });
      setSelectedIds(new Set());
      if (errors.length > 0) {
        toast.warning(`部分删除失败：${errors.join('; ')}`, { duration: 6000 });
      } else {
        toast.success(`已删除 ${ids.length} 条资料`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `删除失败：${String(err)}`);
    }
  };

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    const material = state.materials.find((m) => m.id === id);
    const name = material?.title ?? '该资料';
    const relatedTaskCount = state.processTasks.filter((t) => t.materialId === id).length;
    let msg = `确定删除「${name}」吗？此操作不可撤销。`;
    if (material?.metadata?.objectName || material?.mineruStatus || material?.aiStatus) {
      msg += '\n\n该资料已上传至云存储，删除后原始文件和解析产物将一并清除。';
    }
    if (relatedTaskCount > 0) msg += `\n关联的 ${relatedTaskCount} 条处理任务也将同步删除。`;
    const ok = await confirmDelete(msg);
    if (!ok) return;
    try {
      // 使用级联删除接口
      const resp = await fetch(`/__proxy/upload/materials/${encodeURIComponent(String(id))}`, {
        method: 'DELETE',
        signal: AbortSignal.timeout(30_000),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => null);
        throw new Error(errData?.error || `HTTP ${resp.status}`);
      }

      dispatch({ type: 'DELETE_MATERIAL', payload: [id] });
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
      toast.success('已删除');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `删除失败：${String(err)}`);
    }
  };

  const handleClearAll = async () => {
    if (state.materials.length === 0) { toast('暂无数据'); return; }
    const allIds = state.materials.map((m) => m.id);
    const totalTaskCount = state.processTasks.filter((t) => t.materialId !== undefined && allIds.includes(t.materialId)).length;
    const uploadedCount = state.materials.filter((m) => m.metadata?.objectName || m.mineruStatus || m.aiStatus).length;
    let msg = `确定清空全部 ${state.materials.length} 条资料吗？此操作不可撤销。`;
    if (uploadedCount > 0) msg += `\n\n其中 ${uploadedCount} 条资料已上传至云存储，删除后原始文件和解析产物将一并清除。`;
    if (totalTaskCount > 0) msg += `\n关联的 ${totalTaskCount} 条处理任务也将同步删除。`;
    const ok = await confirmDelete(msg);
    if (!ok) return;
    try {
      // 使用级联删除接口
      const errors: string[] = [];
      for (const id of allIds) {
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

      dispatch({ type: 'DELETE_MATERIAL', payload: allIds });
      setSelectedIds(new Set());
      if (errors.length > 0) {
        toast.warning(`部分删除失败：${errors.join('; ')}`, { duration: 6000 });
      } else {
        toast.success(`已清空全部 ${allIds.length} 条资料`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `清空失败：${String(err)}`);
    }
  };

  const handleParse = async (material: typeof state.materials[number]) => {
    try {
      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: material.id,
          updates: {
            mineruStatus: 'processing',
            metadata: {
              ...material.metadata,
              processingStage: 'mineru',
              processingMsg: '正在准备解析任务...',
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        },
      });

      const objectName = String(material.metadata?.objectName || '').trim();
      if (!objectName) {
        throw new Error('文件尚未上传或缺少 objectName');
      }

      // 下载文件后通过 POST /tasks 创建 ParseTask (PRD 主链路)
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
      formData.append('materialId', String(material.id));

      const res = await fetch('/__proxy/upload/tasks', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: material.id,
          updates: {
            mineruStatus: 'pending',
            metadata: {
              ...material.metadata,
              processingStage: 'mineru',
              processingMsg: '解析任务已提交（PRD 主链路）',
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        },
      });

      toast.success('解析任务已提交，Worker 将自动处理');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`解析失败：${msg}`);
      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: material.id,
          updates: {
            mineruStatus: 'failed',
            metadata: {
              ...material.metadata,
              processingMsg: `解析失败：${msg}`,
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        },
      });
    }
  };

  const handleAnalyze = async (material: typeof state.materials[number]) => {
    try {
      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: material.id,
          updates: {
            aiStatus: 'analyzing',
            metadata: {
              ...material.metadata,
              processingStage: 'ai',
              processingMsg: '正在AI分析中...',
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        },
      });

      // TODO: 调用 AI 分析端点，确认参数格式
      const res = await fetch('/__proxy/upload/ai/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialId: material.id }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      toast.success('AI分析任务已提交');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`AI分析失败：${msg}`);
      dispatch({
        type: 'UPDATE_MATERIAL',
        payload: {
          id: material.id,
          updates: {
            aiStatus: 'failed',
            metadata: {
              ...material.metadata,
              processingMsg: `AI分析失败：${msg}`,
              processingUpdatedAt: new Date().toISOString(),
            },
          },
        },
      });
    }
  };

  /* ── 获取文件类型颜色 ─────────────────────────────────── */
  const typeColor = (type: string) => {
    if (type === 'PDF') return { bg: 'bg-red-100', text: 'text-red-600', badge: 'bg-red-600' };
    if (type === 'DOCX' || type === 'DOC') return { bg: 'bg-blue-100', text: 'text-blue-600', badge: 'bg-blue-600' };
    return { bg: 'bg-orange-100', text: 'text-orange-600', badge: 'bg-orange-600' };
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50/30">
      <div className="max-w-[1400px] mx-auto px-8 py-8">
        {/* ── 页面头部 ─────────────────────────────────── */}
        <div className="flex items-start justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-slate-900 mb-1">资料库</h1>
            <p className="text-slate-500 text-sm">
              管理上传的教育资料 · 共 {state.materials.length} 条
            </p>
            {uploadProgress && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
                  <span>
                    上传进度：已完成 {uploadProgress.done} / {uploadProgress.total}
                    {uploadProgress.failed > 0 ? `（失败 ${uploadProgress.failed}）` : ''}
                  </span>
                  <span>{Math.round((uploadProgress.done / Math.max(1, uploadProgress.total)) * 100)}%</span>
                </div>
                <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-600 transition-all"
                    style={{ width: `${Math.round((uploadProgress.done / Math.max(1, uploadProgress.total)) * 100)}%` }}
                  />
                </div>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {selectedIds.size > 0 && (
              <button
                onClick={handleBatchDelete}
                className="px-4 py-2 text-sm text-red-600 border border-red-200 rounded-xl hover:bg-red-50 font-medium transition-colors"
              >
                删除选中 ({selectedIds.size})
              </button>
            )}
            {currentItems.length > 0 && (
              <button
                onClick={handleSelectCurrentPage}
                className="px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-xl hover:bg-slate-50 font-medium transition-colors"
              >
                {isCurrentPageFullySelected ? '取消当前页' : '全选当前页'}
              </button>
            )}
            <button
              onClick={handleClearAll}
              className="flex items-center gap-1.5 px-4 py-2 text-sm text-red-600 border border-red-200 rounded-xl hover:bg-red-50 font-medium transition-colors"
            >
              <Trash2 size={14} />
              清空
            </button>
            <button
              data-testid="reset-config-btn"
              onClick={handleResetConfig}
              className="flex items-center gap-1.5 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-xl hover:bg-slate-50 font-medium transition-colors"
              title="重置配置解决上传问题"
            >
              <RefreshCw size={14} />
              重置
            </button>
            <button
              onClick={() => navigate('/workspace')}
              className="flex items-center gap-1.5 px-4 py-2 text-sm text-white bg-blue-600 rounded-xl hover:bg-blue-700 font-semibold transition-colors"
              type="button"
            >
              <Upload size={15} />
              前往工作台上传
            </button>
          </div>
        </div>

        {/* ── 统计卡片 ─────────────────────────────────── */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-6">
          <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-2">当前结果</p>
            <p className="text-3xl font-bold text-slate-900">{filtered.length}</p>
            <p className="text-xs text-slate-400 mt-1">待处理 {summary.statusCounts.pending} · 完成 {summary.statusCounts.completed}</p>
          </div>
          <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-2">处理中 / 审核中</p>
            <p className="text-3xl font-bold text-slate-900">{summary.statusCounts.processing + summary.statusCounts.reviewing}</p>
            <p className="text-xs text-slate-400 mt-1">失败 {summary.statusCounts.failed}</p>
          </div>
          <div className="bg-gradient-to-br from-blue-600 to-blue-700 rounded-2xl p-5 text-white shadow-lg">
            <p className="text-[11px] font-semibold uppercase tracking-wide mb-2 text-blue-100">总存储占用</p>
            <p className="text-3xl font-bold">{formatBytes(summary.totalSizeBytes)}</p>
            <p className="text-xs text-blue-200 mt-1">按当前筛选结果汇总</p>
          </div>
          <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-2">学科覆盖</p>
            <p className="text-3xl font-bold text-slate-900">{summary.subjectCoverage}</p>
            <p className="text-xs text-slate-400 mt-1">已识别学科数</p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            {/* ── 筛选工具栏 ─────────────────────────────────── */}
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm mb-6">
              <div className="p-5">
                {/* 搜索栏 */}
                <div className="mb-4">
                  <div className="relative">
                    <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="搜索资料名称、标签、上传者..."
                      className="w-full pl-11 pr-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent text-sm bg-slate-50"
                    />
                  </div>
                </div>

                {/* 筛选行 */}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    {/* Tab 过滤 */}
                    <div className="flex bg-slate-100 rounded-lg p-0.5 gap-0.5">
                      {TAB_OPTIONS.map((opt) => (
                        <button
                          key={opt.key}
                          onClick={() => setTab(opt.key)}
                          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                            tab === opt.key
                              ? 'bg-white text-blue-700 shadow-sm'
                              : 'text-slate-500 hover:text-slate-900'
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>

                    {/* 排序 */}
                    <div className="flex items-center gap-1">
                      <SortAsc size={14} className="text-slate-400" />
                      <select
                        value={sort}
                        onChange={(e) => setSort(e.target.value as SortOption)}
                        className="text-sm border border-slate-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white"
                      >
                        {SORT_OPTIONS.map((o) => (
                          <option key={o.key} value={o.key}>{o.label}</option>
                        ))}
                      </select>
                    </div>

                    <button
                      onClick={() => setAdvancedExpanded((prev) => !prev)}
                      className="flex items-center gap-1 px-3 py-1.5 text-sm text-slate-500 hover:text-slate-900 hover:bg-slate-50 rounded-lg transition-colors"
                    >
                      {advancedExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      高级筛选
                    </button>
                  </div>

                  {/* 视图切换 */}
                  <div className="flex items-center gap-1 p-0.5 bg-slate-100 rounded-lg">
                    <button
                      onClick={() => setViewMode('list')}
                      className={`p-2 rounded ${viewMode === 'list' ? 'bg-white shadow-sm' : 'hover:bg-slate-50'}`}
                    >
                      <List className={`w-4 h-4 ${viewMode === 'list' ? 'text-blue-600' : 'text-slate-400'}`} />
                    </button>
                    <button
                      onClick={() => setViewMode('grid')}
                      className={`p-2 rounded ${viewMode === 'grid' ? 'bg-white shadow-sm' : 'hover:bg-slate-50'}`}
                    >
                      <Grid className={`w-4 h-4 ${viewMode === 'grid' ? 'text-blue-600' : 'text-slate-400'}`} />
                    </button>
                  </div>
                </div>
              </div>

              {/* 高级筛选面板 */}
              {advancedExpanded && (
                <div className="px-5 pb-5 pt-0">
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-5 p-4 bg-slate-50 rounded-xl">
                    <select value={subjectFilter} onChange={(e) => setSubjectFilter(e.target.value)} className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300">
                      <option value="all">全部学科</option>
                      {advancedOptions.subjects.map((item) => <option key={item} value={item}>{item}</option>)}
                    </select>
                    <select value={gradeFilter} onChange={(e) => setGradeFilter(e.target.value)} className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300">
                      <option value="all">全部年级</option>
                      {advancedOptions.grades.map((item) => <option key={item} value={item}>{item}</option>)}
                    </select>
                    <select value={languageFilter} onChange={(e) => setLanguageFilter(e.target.value)} className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300">
                      <option value="all">全部语言</option>
                      {advancedOptions.languages.map((item) => <option key={item} value={item}>{item}</option>)}
                    </select>
                    <select value={mineruStatusFilter} onChange={(e) => setMineruStatusFilter(e.target.value as (typeof MINERU_STATUS_OPTIONS)[number]['key'])} className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300">
                      {MINERU_STATUS_OPTIONS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                    </select>
                    <select value={aiStatusFilter} onChange={(e) => setAiStatusFilter(e.target.value as (typeof AI_STATUS_OPTIONS)[number]['key'])} className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300">
                      {AI_STATUS_OPTIONS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                    </select>
                  </div>
                </div>
              )}
            </div>

            {/* 搜索结果提示 */}
            {search && (
              <p className="text-sm text-slate-500 mb-4">
                找到 <span className="font-semibold text-slate-800">{filtered.length}</span> 条结果
              </p>
            )}

            {/* ── 列表视图 ─────────────────────────────────── */}
            {viewMode === 'list' ? (
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="w-10 px-4 py-3 text-left">
                        <input
                          type="checkbox"
                          checked={selectedIds.size === currentItems.length && currentItems.length > 0}
                          onChange={(e) => {
                            if (e.target.checked) setSelectedIds(new Set(currentItems.map((m) => m.id)));
                            else setSelectedIds(new Set());
                          }}
                          className="rounded"
                        />
                      </th>
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">资料名称</th>
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">类型</th>
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">大小</th>
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">上传者</th>
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">上传时间</th>
                      {showStatusColumn && (
                        <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">阶段</th>
                      )}
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">处理</th>
                      <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {currentItems.length === 0 && (
                      <tr>
                        <td colSpan={showStatusColumn ? 9 : 8} className="text-center py-16 text-slate-400">暂无数据</td>
                      </tr>
                    )}
                    {currentItems.map((m) => {
                      const mType = getMaterialType(m);
                      const mTags = getMaterialTags(m);
                      const tc = typeColor(mType);
                      return (
                        <tr key={m.id} className="hover:bg-slate-50 cursor-pointer transition-colors" onClick={() => navigate(`/asset/${m.id}`)}>
                          <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" checked={selectedIds.has(m.id)} onChange={() => toggleSelect(m.id)} className="rounded" />
                          </td>
                          <td className="px-4 py-3.5">
                            <div className="flex items-center gap-3">
                              <div className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${tc.bg}`}>
                                <FileText className={`w-4 h-4 ${tc.text}`} />
                              </div>
                              <div className="min-w-0">
                                <p className="font-medium text-slate-800 truncate max-w-xs">{m.title}</p>
                                {mTags.length > 0 && (
                                  <div className="flex gap-1 mt-1 flex-wrap">
                                    {mTags.slice(0, 3).map((tag: string) => (
                                      <span key={tag} className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full">{tag}</span>
                                    ))}
                                    {mTags.length > 3 && <span className="text-[10px] text-slate-400">+{mTags.length - 3}</span>}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-3.5">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold text-white ${tc.badge}`}>{mType}</span>
                          </td>
                          <td className="px-4 py-3.5 text-slate-500">{m.size}</td>
                          <td className="px-4 py-3.5 text-slate-500">{m.uploader}</td>
                          <td className="px-4 py-3.5 text-slate-400">{m.uploadTime}</td>
                          {showStatusColumn && (
                            <td className="px-4 py-3.5">
                              <div className="flex flex-col gap-1">
                                <div className="flex items-center gap-2">
                                  <StatusBadge status={m.status} />
                                  <span className="text-xs text-slate-600">{getStageSummary(m).label}</span>
                                </div>
                                {getStageSummary(m).detail && (
                                  <div className="text-[10px] text-slate-400 truncate max-w-xs" title={getStageSummary(m).detail}>
                                    {getStageSummary(m).detail}
                                  </div>
                                )}
                              </div>
                            </td>
                          )}
                          <td className="px-4 py-3.5">
                            <div className="flex items-center gap-1.5">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (m.mineruStatus === 'completed') return;
                                  if (m.mineruStatus === 'processing') return;
                                  handleParse(m);
                                }}
                                disabled={m.mineruStatus === 'completed' || m.mineruStatus === 'processing'}
                                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                                  m.mineruStatus === 'completed'
                                    ? 'bg-green-50 text-green-600 cursor-not-allowed'
                                    : m.mineruStatus === 'processing'
                                      ? 'bg-blue-50 text-blue-600 cursor-not-allowed'
                                      : 'bg-blue-600 text-white hover:bg-blue-700'
                                }`}
                                title={
                                  m.mineruStatus === 'completed'
                                    ? '已解析'
                                    : m.mineruStatus === 'processing'
                                      ? '解析中'
                                      : '开始解析'
                                }
                              >
                                {m.mineruStatus === 'completed' ? (
                                  <>
                                    <CheckCircle size={12} /> 已解析
                                  </>
                                ) : m.mineruStatus === 'processing' ? (
                                  <>
                                    <Loader size={12} className="animate-spin" /> 解析中
                                  </>
                                ) : (
                                  <>
                                    <Cpu size={12} /> 解析
                                  </>
                                )}
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (m.aiStatus === 'analyzed') return;
                                  if (m.aiStatus === 'analyzing') return;
                                  if (m.mineruStatus !== 'completed') {
                                    toast.error('请先完成解析');
                                    return;
                                  }
                                  handleAnalyze(m);
                                }}
                                disabled={m.aiStatus === 'analyzed' || m.aiStatus === 'analyzing' || m.mineruStatus !== 'completed'}
                                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                                  m.aiStatus === 'analyzed'
                                    ? 'bg-green-50 text-green-600 cursor-not-allowed'
                                    : m.aiStatus === 'analyzing'
                                      ? 'bg-purple-50 text-purple-600 cursor-not-allowed'
                                      : m.mineruStatus !== 'completed'
                                        ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                                        : 'bg-purple-600 text-white hover:bg-purple-700'
                                }`}
                                title={
                                  m.aiStatus === 'analyzed'
                                    ? '已分析'
                                    : m.aiStatus === 'analyzing'
                                      ? '分析中'
                                      : m.mineruStatus !== 'completed'
                                        ? '需先解析'
                                        : '开始AI分析'
                                }
                              >
                                {m.aiStatus === 'analyzed' ? (
                                  <>
                                    <CheckCircle size={12} /> 已分析
                                  </>
                                ) : m.aiStatus === 'analyzing' ? (
                                  <>
                                    <Loader size={12} className="animate-spin" /> 分析中
                                  </>
                                ) : (
                                  <>
                                    <Cpu size={12} /> AI分析
                                  </>
                                )}
                              </button>
                            </div>
                          </td>
                          <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                            <button
                              onClick={(e) => handleDelete(e, m.id)}
                              className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                              title="删除"
                            >
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              /* ── 网格视图 ─────────────────────────────────── */
              <div className="grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-3">
                {currentItems.map((m) => {
                  const tc = typeColor(m.type);
                  return (
                    <div
                      key={m.id}
                      onClick={() => navigate(`/asset/${m.id}`)}
                      className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-shadow cursor-pointer group"
                    >
                      {/* 缩略图区域 */}
                      <div className="relative aspect-video bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center">
                        <FileText className={`w-14 h-14 ${tc.text} opacity-30`} />
                        <div className="absolute top-3 left-3">
                          <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold text-white ${tc.badge}`}>{m.type}</span>
                        </div>
                        <div className="absolute top-3 right-3">
                          {m.status === 'pending' && (
                            <span className="px-2.5 py-1 bg-yellow-100 text-yellow-700 text-[10px] font-semibold rounded-full flex items-center gap-1">
                              <Clock className="w-3 h-3" /> 待处理
                            </span>
                          )}
                          {m.status === 'processing' && (
                            <span className="px-2.5 py-1 bg-purple-100 text-purple-700 text-[10px] font-semibold rounded-full flex items-center gap-1">
                              <Cpu className="w-3 h-3 animate-spin" /> 处理中
                            </span>
                          )}
                          {m.status === 'completed' && (
                            <span className="px-2.5 py-1 bg-green-100 text-green-700 text-[10px] font-semibold rounded-full flex items-center gap-1">
                              <CheckCircle className="w-3 h-3" /> 已完成
                            </span>
                          )}
                        </div>
                        <div className="absolute top-3 left-14" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(m.id)}
                            onChange={() => toggleSelect(m.id)}
                            className="rounded"
                          />
                        </div>
                      </div>

                      {/* 内容 */}
                      <div className="p-4">
                        <h3 className="text-sm font-semibold text-slate-900 line-clamp-1 group-hover:text-blue-600 transition-colors mb-1">
                          {m.title}
                        </h3>
                        <div className="flex items-center justify-between text-xs text-slate-500 mb-3">
                          <span>{m.size}</span>
                          <span>{m.uploadTime}</span>
                        </div>
                        {m.tags.length > 0 && (
                          <div className="flex gap-1 flex-wrap">
                            {m.tags.slice(0, 2).map((tag) => (
                              <span key={tag} className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full">{tag}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
                {currentItems.length === 0 && (
                  <div className="col-span-3 text-center py-16 text-slate-400">暂无数据</div>
                )}
              </div>
            )}

            {/* ── 分页 ─────────────────────────────────── */}
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-1 pt-6">
                <button onClick={prevPage} disabled={!hasPrev} className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50 transition-colors">
                  上一页
                </button>
                {pageNumbers.map((p, i) =>
                  p === '...' ? (
                    <span key={`ellipsis-${i}`} className="px-2 text-slate-400">…</span>
                  ) : (
                    <button
                      key={p}
                      onClick={() => goToPage(p as number)}
                      className={`w-8 h-8 text-sm rounded-lg transition-colors ${
                        p === currentPage ? 'bg-blue-600 text-white' : 'border border-slate-200 hover:bg-slate-50 text-slate-600'
                      }`}
                    >
                      {p}
                    </button>
                  ),
                )}
                <button onClick={nextPage} disabled={!hasNext} className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50 transition-colors">
                  下一页
                </button>
              </div>
            )}
          </div>

          <div className="space-y-6">
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5">
              <h2 className="text-base font-semibold text-slate-900 mb-4">系统状态</h2>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-600">upload-server</span>
                  <span className={`flex items-center gap-1.5 text-xs font-semibold ${uploadServerOk ? 'text-green-600' : uploadServerOk === false ? 'text-red-600' : 'text-slate-400'}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${uploadServerOk ? 'bg-green-500 animate-pulse' : uploadServerOk === false ? 'bg-red-500' : 'bg-slate-300'}`} />
                    {uploadServerOk ? '正常' : uploadServerOk === false ? '异常' : '检测中'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-600">本地 MinerU</span>
                  <span className={`flex items-center gap-1.5 text-xs font-semibold ${mineruOk ? 'text-green-600' : mineruOk === false ? 'text-red-600' : 'text-slate-400'}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${mineruOk ? 'bg-green-500 animate-pulse' : mineruOk === false ? 'bg-red-500' : 'bg-slate-300'}`} />
                    {mineruOk ? '正常' : mineruOk === false ? '异常' : '检测中'}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
