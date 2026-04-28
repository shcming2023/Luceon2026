/**
 * 已处理资料库（原"成品库"）
 * 数据源：state.materials（已完成 MinerU 解析 + AI 元数据识别的第2类资产）
 * 功能：多维筛选检索、原始文件预览/下载、Markdown 行内展开预览与下载、ZIP 下载、跳转详情
 */
import { useState, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search,
  FileText,
  Download,
  Eye,
  EyeOff,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  SortAsc,
  Grid,
  List,
  X,
  Archive,
  Trash2,
} from 'lucide-react';
import { useAppStore } from '../../store/appContext';
import { toast } from 'sonner';
import { sortMaterials } from '../../utils/sort';
import { usePagination, getPageNumbers } from '../../utils/pagination';
import { fetchMinerUMarkdown } from '../../utils/mineruApi';
import type { SortOption, ViewMode } from '../../store/types';

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

// ── 筛选选项常量 ──────────────────────────────────────────

const SORT_OPTIONS: { key: SortOption; label: string }[] = [
  { key: 'newest', label: '最新上传' },
  { key: 'oldest', label: '最早上传' },
  { key: 'name',   label: '名称' },
  { key: 'size',   label: '文件大小' },
];

const MINERU_STATUS_OPTIONS = [
  { key: 'all',        label: '全部 MinerU 状态' },
  { key: 'completed',  label: '解析完成' },
  { key: 'processing', label: '解析中' },
  { key: 'pending',    label: '待解析' },
  { key: 'failed',     label: '解析失败' },
] as const;

const AI_STATUS_OPTIONS = [
  { key: 'all',       label: '全部 AI 状态' },
  { key: 'analyzed',  label: '已分析' },
  { key: 'analyzing', label: '分析中' },
  { key: 'pending',   label: '待分析' },
  { key: 'failed',    label: '分析失败' },
] as const;

// ── 工具函数 ──────────────────────────────────────────────

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function typeColor(type: string) {
  if (type === 'PDF') return { bg: 'bg-red-100', text: 'text-red-600', badge: 'bg-red-600' };
  if (type === 'DOCX' || type === 'DOC') return { bg: 'bg-blue-100', text: 'text-blue-600', badge: 'bg-blue-600' };
  return { bg: 'bg-orange-100', text: 'text-orange-600', badge: 'bg-orange-600' };
}

// ── Markdown 加载状态类型 ─────────────────────────────────

interface MdState {
  loading: boolean;
  content: string | null;
  error: string;
}

// ── 主组件 ────────────────────────────────────────────────

export function ProductsPage() {
  const navigate = useNavigate();
  const { state, dispatch } = useAppStore();

  // 筛选器状态
  const [search, setSearch]               = useState('');
  const [sort, setSort]                   = useState<SortOption>('newest');
  const [viewMode, setViewMode]           = useState<ViewMode>('list');
  const [advancedExpanded, setAdvancedExpanded] = useState(false);
  const [subjectFilter, setSubjectFilter] = useState('all');
  const [gradeFilter, setGradeFilter]     = useState('all');
  const [languageFilter, setLanguageFilter] = useState('all');
  const [typeFilter, setTypeFilter]       = useState('all');
  const [mineruStatusFilter, setMineruStatusFilter] =
    useState<(typeof MINERU_STATUS_OPTIONS)[number]['key']>('all');
  const [aiStatusFilter, setAiStatusFilter] =
    useState<(typeof AI_STATUS_OPTIONS)[number]['key']>('all');

  // 每个资料的 Markdown 展开状态（Map 不触发 re-render，用 state 存）
  const [mdStates, setMdStates] = useState<Map<number, MdState>>(new Map());

  // PDF 预览弹窗
  const [pdfPreviewId, setPdfPreviewId] = useState<number | null>(null);

  // ── 高级筛选选项（动态从数据中提取）──────────────────────
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const advancedOptions = useMemo(() => {
    const unique = (values: (string | undefined)[]) =>
      [...new Set(values.map((v) => v?.trim()).filter(Boolean) as string[])].sort((a, b) =>
        a.localeCompare(b, 'zh-CN'),
      );
    return {
      subjects:  unique(state.materials.map((m) => m.metadata?.subject)),
      grades:    unique(state.materials.map((m) => m.metadata?.grade)),
      languages: unique(state.materials.map((m) => m.metadata?.language)),
      types:     unique(state.materials.map((m) => getMaterialType(m))),
    };
  }, [state.materials]);

  // ── 数据富化与收口 ──────────────────────────────────────────
  const enrichedMaterials = useMemo(() => {
    return state.materials.map((m: any) => {
      const assetDetails: any = state.assetDetails[m.id] || {};
      const latestTask = [...state.processTasks, ...state.tasks]
        .filter((t: any) => String(t.materialId) === String(m.id))
        .sort((a: any, b: any) => new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime())[0] as any;

      const rawCount = m.metadata?.parsedFilesCount ?? latestTask?.metadata?.parsedFilesCount ?? latestTask?.parsedFilesCount ?? assetDetails.metadata?.parsedFilesCount;
      const parsedFilesCount = Number(rawCount) || 0;
      const parsedCountDisplay = rawCount !== undefined && rawCount !== null ? String(rawCount) : '—';

      const hasProductEvidence = parsedFilesCount > 0;

      let derivedMineruStatus = 'processing';
      let derivedAiStatus = m.aiStatus || 'pending';
      let statusDisplay = '';
      let statusColor = 'text-slate-500';

      const isTaskState = (states: string[]) => latestTask && states.includes(latestTask.state);
      
      if (latestTask) {
        if (isTaskState(['completed', 'done'])) { derivedMineruStatus = 'completed'; statusDisplay = '解析完成'; statusColor = 'text-green-600'; }
        else if (isTaskState(['ai-pending', 'ai-running'])) { derivedMineruStatus = 'completed'; derivedAiStatus = isTaskState(['ai-running']) ? 'analyzing' : 'pending'; statusDisplay = 'AI 处理中'; statusColor = 'text-blue-600'; }
        else if (isTaskState(['review-pending'])) { derivedMineruStatus = 'completed'; derivedAiStatus = 'analyzed'; statusDisplay = '待审核'; statusColor = 'text-orange-600'; }
        else if (isTaskState(['failed', 'artifact-empty', 'mineru-failed', 'canceled'])) { derivedMineruStatus = 'failed'; statusDisplay = '解析失败'; statusColor = 'text-red-600'; }
        else if (isTaskState(['pending'])) { derivedMineruStatus = 'pending'; statusDisplay = '待解析'; statusColor = 'text-slate-500'; }
        else { derivedMineruStatus = 'processing'; statusDisplay = '处理中'; statusColor = 'text-blue-600'; }
      } else {
        if (m.mineruStatus === 'completed' || m.status === 'completed') { derivedMineruStatus = 'completed'; statusDisplay = '解析完成'; statusColor = 'text-green-600'; }
        else if (m.mineruStatus === 'failed' || m.status === 'failed') { derivedMineruStatus = 'failed'; statusDisplay = '解析失败'; statusColor = 'text-red-600'; }
        else if (m.mineruStatus === 'pending') { derivedMineruStatus = 'pending'; statusDisplay = '待解析'; statusColor = 'text-slate-500'; }
        else { derivedMineruStatus = m.mineruStatus === 'processing' ? 'processing' : (m.mineruStatus || '未知'); statusDisplay = '处理中'; statusColor = 'text-blue-600'; }
      }

      // 准入条件
      const isSuccessful = derivedMineruStatus === 'completed';
      const isNotFailed = derivedMineruStatus !== 'failed' 
        && !['failed', 'artifact-empty', 'mineru-failed', 'canceled'].includes(m.mineruStatus) 
        && !['failed', 'artifact-empty', 'mineru-failed', 'canceled'].includes(m.status);
      const isUsable = isSuccessful && isNotFailed && hasProductEvidence;

      const title = m.title || m.fileName || m.metadata?.fileName || assetDetails.name || assetDetails.title || assetDetails.fileName || String(m.id);
      const rawSize = m.sizeBytes || m.metadata?.sizeBytes || assetDetails.sizeBytes;
      const sizeDisplay = rawSize > 0 ? formatBytes(rawSize as number) : '—';
      const rawTime = m.uploadTimestamp || m.createTime || m.metadata?.uploadTimestamp || assetDetails.uploadTimestamp;
      const timeDisplay = rawTime && rawTime !== '刚刚' ? new Date(rawTime).toLocaleString() : '—';

      return {
        ...m,
        _enriched: {
          title, sizeDisplay, timeDisplay, parsedCountDisplay, statusDisplay, statusColor,
          derivedMineruStatus, derivedAiStatus, isUsable
        }
      };
    });
  }, [state.materials, state.processTasks, state.tasks, state.assetDetails]);

  // ── 筛选 + 排序 ──────────────────────────────────────────
  const filtered = useMemo(() => {
    let list = enrichedMaterials.filter((m: any) => m._enriched.isUsable);

    if (mineruStatusFilter !== 'all') list = list.filter((m: any) => m._enriched.derivedMineruStatus === mineruStatusFilter);
    if (aiStatusFilter !== 'all')     list = list.filter((m: any) => m._enriched.derivedAiStatus === aiStatusFilter);
    if (subjectFilter !== 'all')      list = list.filter((m: any) => m.metadata?.subject === subjectFilter);
    if (gradeFilter !== 'all')        list = list.filter((m: any) => m.metadata?.grade === gradeFilter);
    if (languageFilter !== 'all')     list = list.filter((m: any) => m.metadata?.language === languageFilter);
    if (typeFilter !== 'all')         list = list.filter((m: any) => getMaterialType(m) === typeFilter);
    
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (m: any) =>
          m._enriched.title.toLowerCase().includes(q) ||
          getMaterialTags(m).some((t: string) => t.toLowerCase().includes(q)) ||
          (m.metadata?.subject || '').toLowerCase().includes(q) ||
          (m.metadata?.grade || '').toLowerCase().includes(q)
      );
    }
    return sortMaterials(list, sort);
  }, [enrichedMaterials, mineruStatusFilter, aiStatusFilter, subjectFilter, gradeFilter, languageFilter, typeFilter, search, sort]);

  const { currentItems, currentPage, totalPages, goToPage, hasPrev, hasNext, prevPage, nextPage } =
    usePagination(filtered);
  const pageNumbers = getPageNumbers(currentPage, totalPages);
  const filteredIds = useMemo(() => filtered.map((m: any) => Number(m.id)), [filtered]);
  const allFilteredSelected =
    filteredIds.length > 0 && filteredIds.every((id) => selectedIds.has(id));

  const handleSelectAll = () => {
    if (allFilteredSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredIds));
    }
  };

  const toggleSelect = (id: number) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  };

  // ── 统计摘要 ─────────────────────────────────────────────
  const totalCount = state.materials.length;
  const processedCount = useMemo(
    () => state.materials.filter((m) => m.mineruStatus === 'completed' && m.aiStatus === 'analyzed').length,
    [state.materials],
  );

  // ── Markdown 拉取 ─────────────────────────────────────────
  const handleToggleMarkdown = useCallback(
    async (id: number) => {
      const cur = mdStates.get(id);

      // 已有内容则收起
      if (cur?.content !== undefined && cur.content !== null) {
        setMdStates((prev) => {
          const next = new Map(prev);
          next.set(id, { loading: false, content: null, error: '' });
          return next;
        });
        return;
      }

      // 正在加载则忽略
      if (cur?.loading) return;

      const material = state.materials.find((m) => m.id === id);
      if (!material) return;

      setMdStates((prev) => {
        const next = new Map(prev);
        next.set(id, { loading: true, content: null, error: '' });
        return next;
      });

      try {
        let text = '';
        const { markdownObjectName, markdownUrl } = material.metadata || {};
        if (markdownObjectName) {
          const bucket = String(state.minioConfig.parsedBucket || state.minioConfig.bucket || '');
          const url = `/__proxy/upload/proxy-file?objectName=${encodeURIComponent(markdownObjectName)}${bucket ? `&bucket=${encodeURIComponent(bucket)}` : ''}`;
          const res = await fetch(url);
          if (!res.ok) throw new Error(`读取失败: HTTP ${res.status}`);
          text = await res.text();
        } else {
          text = await fetchMinerUMarkdown(markdownUrl, material.mineruZipUrl);
        }
        if (!text.trim()) {
          setMdStates((prev) => {
            const next = new Map(prev);
            next.set(id, { loading: false, content: null, error: '暂无可用的 Markdown 内容' });
            return next;
          });
          return;
        }
        const content = text.length > 20000 ? `${text.slice(0, 20000)}\n\n...(内容已截断)` : text;
        setMdStates((prev) => {
          const next = new Map(prev);
          next.set(id, { loading: false, content, error: '' });
          return next;
        });
      } catch (err) {
        setMdStates((prev) => {
          const next = new Map(prev);
          next.set(id, { loading: false, content: null, error: err instanceof Error ? err.message : String(err) });
          return next;
        });
      }
    },
    [mdStates, state.materials, state.minioConfig],
  );

  const handleDownloadMarkdown = useCallback(
    (id: number, title: string) => {
      const content = mdStates.get(id)?.content;
      if (!content) return;
      const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${title.replace(/[\\/:*?"<>|]+/g, '_')}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    [mdStates],
  );

  // ── PDF 预览 URL 构造 ─────────────────────────────────────
  const getPdfPreviewUrl = useCallback(
    (id: number) => {
      const m = state.materials.find((mat) => mat.id === id);
      if (!m) return null;
      if (m.metadata?.objectName) {
        const bucket = String(state.minioConfig.bucket || '');
        return `/__proxy/upload/proxy-file?objectName=${encodeURIComponent(m.metadata.objectName)}${bucket ? `&bucket=${encodeURIComponent(bucket)}` : ''}`;
      }
      if (m.previewUrl) return m.previewUrl;
      return null;
    },
    [state.materials, state.minioConfig],
  );

  const pdfPreviewUrl = pdfPreviewId !== null ? getPdfPreviewUrl(pdfPreviewId) : null;
  const pdfPreviewMaterial = pdfPreviewId !== null ? state.materials.find((m) => m.id === pdfPreviewId) : null;

  // ── 重置筛选 ─────────────────────────────────────────────
  const handleResetFilters = () => {
    setSearch('');
    setSubjectFilter('all');
    setGradeFilter('all');
    setLanguageFilter('all');
    setTypeFilter('all');
    setMineruStatusFilter('completed');
    setAiStatusFilter('analyzed');
  };

      const isFiltered =
    search ||
    subjectFilter !== 'all' ||
    gradeFilter !== 'all' ||
    languageFilter !== 'all' ||
    typeFilter !== 'all' ||
    mineruStatusFilter !== 'completed' ||
    aiStatusFilter !== 'analyzed';

  // ── 级联删除 ─────────────────────────────────────────────
  const executeDelete = useCallback(async (materialIds: (string | number)[], promptTitle = '删除成果') => {
    if (materialIds.length === 0) return;
    try {
      const dryRes = await fetch('/__proxy/upload/delete/materials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialIds, mode: 'cascade', dryRun: true, force: false })
      });
      if (!dryRes.ok) throw new Error(`HTTP ${dryRes.status}`);
      const { summary } = await dryRes.json();

      const msg = `预演检查结果：
- 待删 Materials：${summary.materials}
- 待删 Tasks：${summary.tasks}
- 待删 AI Jobs：${summary.aiJobs}
- 待删 Events：${summary.taskEvents}
- 待删 原始对象：${summary.originalObjects}
- 待删 解析产物：${summary.parsedObjects}
- 运行中任务：${summary.runningTasks}

确定要执行级联删除吗？此操作不可撤销！`;
      if (!window.confirm(msg)) return;

      const force = summary.runningTasks > 0;
      const execRes = await fetch('/__proxy/upload/delete/materials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialIds, mode: 'cascade', dryRun: false, force })
      });
      
      if (!execRes.ok) throw new Error(`HTTP ${execRes.status}`);
      const numericIds = materialIds
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id));
      if (numericIds.length > 0) {
        dispatch({ type: 'DELETE_MATERIAL', payload: numericIds });
      }
      toast.success(`${promptTitle}成功，相关记录与产物已彻底清理。`);
      setSelectedIds(new Set());
    } catch (err) {
      toast.error(`${promptTitle}失败`, { description: String(err) });
    }
  }, [dispatch]);

  const handleDeleteProduct = useCallback((id: string | number) => {
    executeDelete([id], '删除成果');
  }, [executeDelete]);

  const handleBatchDelete = useCallback(() => {
    executeDelete(Array.from(selectedIds), '批量删除');
  }, [executeDelete, selectedIds]);

  const handleClearAllResiduals = useCallback(() => {
    const allMaterialIds = state.materials.map(m => m.id);
    if (allMaterialIds.length === 0) {
      toast.info('当前没有需要清理的记录。');
      return;
    }
    executeDelete(allMaterialIds, '清理残留素材');
  }, [executeDelete, state.materials]);

  // ────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50/30">
      <div className="max-w-[1400px] mx-auto px-8 py-8">

        {/* ── 页面头部 ────────────────────────────────── */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold text-slate-900 mb-1">成果库</h1>
            <p className="text-slate-500 text-sm">
              已完成 MinerU 解析 + AI 元数据识别的资产 · 共 {processedCount} 条
              {totalCount !== processedCount && (
                <span className="ml-1 text-slate-400">（全库 {totalCount} 条）</span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleClearAllResiduals}
              className="px-4 py-2 bg-red-50 text-red-600 hover:bg-red-100 hover:text-red-700 rounded-xl font-medium transition-colors border border-red-200 text-sm flex items-center gap-2"
            >
              <Trash2 size={16} /> 清理全部素材 (测试环境)
            </button>
          </div>
        </div>

        {/* ── 筛选工具栏 ─────────────────────────────── */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm mb-6">
          <div className="p-5">
            {/* 搜索行 */}
            <div className="flex items-center gap-3 mb-4 flex-wrap">
              <div className="relative flex-1 min-w-[200px]">
                <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜索资料名称、标签、学科、年级..."
                  className="w-full pl-11 pr-4 py-2.5 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent text-sm bg-slate-50"
                />
              </div>
              <div className="flex items-center gap-1.5">
                <SortAsc size={14} className="text-slate-400" />
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value as SortOption)}
                  className="text-sm border border-slate-200 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white"
                >
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </div>
              <button
                onClick={() => setAdvancedExpanded((p) => !p)}
                className="flex items-center gap-1 px-3 py-2 text-sm text-slate-500 hover:text-slate-900 hover:bg-slate-50 rounded-lg transition-colors"
              >
                {advancedExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                高级筛选
              </button>
              {isFiltered && (
                <button
                  onClick={handleResetFilters}
                  className="flex items-center gap-1 px-3 py-2 text-xs text-blue-600 hover:bg-blue-50 rounded-lg transition-colors font-medium"
                >
                  <X size={12} /> 重置筛选
                </button>
              )}
              {/* 视图切换 */}
              <div className="ml-auto flex items-center gap-1 p-0.5 bg-slate-100 rounded-lg">
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

            {/* 批量操作工具栏 */}
            {selectedIds.size > 0 && (
              <div className="flex items-center gap-3 mb-4 bg-blue-50/50 p-2.5 rounded-xl border border-blue-100/50">
                <span className="text-sm font-medium text-blue-800 ml-2">
                  已选择 {selectedIds.size} 项
                  {filtered.length > 0 && selectedIds.size === filtered.length && (
                    <span className="ml-1 text-blue-500">（当前筛选全部）</span>
                  )}
                </span>
                <button
                  onClick={handleBatchDelete}
                  className="px-3 py-1.5 bg-red-100 text-red-600 text-sm font-medium rounded-lg hover:bg-red-200 transition-colors flex items-center gap-1.5"
                >
                  <Trash2 size={14} /> 批量级联删除
                </button>
                <button
                  onClick={() => setSelectedIds(new Set())}
                  className="px-3 py-1.5 text-slate-500 hover:text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-100 transition-colors"
                >
                  取消
                </button>
              </div>
            )}

            {/* 快捷状态筛选行 */}
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={mineruStatusFilter}
                onChange={(e) => setMineruStatusFilter(e.target.value as typeof mineruStatusFilter)}
                className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white"
              >
                {MINERU_STATUS_OPTIONS.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
              <select
                value={aiStatusFilter}
                onChange={(e) => setAiStatusFilter(e.target.value as typeof aiStatusFilter)}
                className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white"
              >
                {AI_STATUS_OPTIONS.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
              {search && (
                <span className="text-sm text-slate-500">
                  找到 <span className="font-semibold text-slate-800">{filtered.length}</span> 条结果
                </span>
              )}
            </div>
          </div>

          {/* 高级筛选面板 */}
          {advancedExpanded && (
            <div className="px-5 pb-5 pt-0">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 p-4 bg-slate-50 rounded-xl">
                <select
                  value={subjectFilter}
                  onChange={(e) => setSubjectFilter(e.target.value)}
                  className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
                >
                  <option value="all">全部学科</option>
                  {advancedOptions.subjects.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
                <select
                  value={gradeFilter}
                  onChange={(e) => setGradeFilter(e.target.value)}
                  className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
                >
                  <option value="all">全部年级</option>
                  {advancedOptions.grades.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
                <select
                  value={languageFilter}
                  onChange={(e) => setLanguageFilter(e.target.value)}
                  className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
                >
                  <option value="all">全部语言</option>
                  {advancedOptions.languages.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
                <select
                  value={typeFilter}
                  onChange={(e) => setTypeFilter(e.target.value)}
                  className="text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
                >
                  <option value="all">全部文件类型</option>
                  {advancedOptions.types.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>

        {/* ── 列表视图 ──────────────────────────────── */}
        {viewMode === 'list' ? (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="px-4 py-3 w-10">
                    <input
                      type="checkbox"
                      checked={allFilteredSelected}
                      onChange={handleSelectAll}
                      title="选择当前筛选结果中的全部成果"
                      className="w-4 h-4 text-blue-600 rounded border-slate-300 focus:ring-blue-500"
                    />
                  </th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">资料名称</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">类型</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">解析状态</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">产物数</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">大小</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">学科 / 年级</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">上传时间</th>
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 text-xs uppercase tracking-wide">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {currentItems.length === 0 && (
                  <tr>
                    <td colSpan={6} className="text-center py-16 text-slate-400">
                      暂无符合条件的资料
                      {isFiltered && (
                        <button
                          onClick={handleResetFilters}
                          className="ml-2 text-blue-500 underline text-xs"
                        >
                          重置筛选
                        </button>
                      )}
                    </td>
                  </tr>
                )}
                {currentItems.map((m: any) => {
                  const en = m._enriched;
                  const title = en.title;
                  const sizeDisplay = en.sizeDisplay;
                  const timeDisplay = en.timeDisplay;
                  const parsedCountDisplay = en.parsedCountDisplay;
                  const statusDisplay = en.statusDisplay;
                  const statusColor = en.statusColor;

                  const mType = getMaterialType(m);
                  const mTags = getMaterialTags(m);
                  const tc = typeColor(mType);
                  const mdSt = mdStates.get(m.id);
                  const hasMd = !!(m.metadata?.markdownObjectName || m.metadata?.markdownUrl || m.mineruZipUrl);
                  const hasZip = !!m.mineruZipUrl;
                  const hasPdf = !!(m.metadata?.objectName || m.previewUrl);
                  return (
                    <>
                      <tr
                        key={m.id}
                        className="hover:bg-slate-50 cursor-pointer transition-colors"
                        onClick={() => navigate(`/asset/${m.id}`)}
                      >
                        <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(m.id)}
                            onChange={() => toggleSelect(m.id)}
                            className="w-4 h-4 text-blue-600 rounded border-slate-300 focus:ring-blue-500"
                          />
                        </td>
                        <td className="px-4 py-3.5">
                          <div className="flex items-center gap-3">
                            <div className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${tc.bg}`}>
                              <FileText className={`w-4 h-4 ${tc.text}`} />
                            </div>
                            <div className="min-w-0">
                              <p className="font-medium text-slate-800 truncate max-w-xs hover:text-blue-600 transition-colors">
                                {title}
                              </p>
                              {mTags.length > 0 && (
                                <div className="flex gap-1 mt-1 flex-wrap">
                                  {mTags.slice(0, 3).map((tag: string) => (
                                    <span key={tag} className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full">
                                      {tag}
                                    </span>
                                  ))}
                                  {mTags.length > 3 && (
                                    <span className="text-[10px] text-slate-400">+{mTags.length - 3}</span>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3.5">
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold text-white ${tc.badge}`}>{mType}</span>
                        </td>
                        <td className={`px-4 py-3.5 font-medium ${statusColor}`}>
                          {statusDisplay}
                        </td>
                        <td className="px-4 py-3.5 text-slate-600 font-mono">
                          {parsedCountDisplay}
                        </td>
                        <td className="px-4 py-3.5 text-slate-500">
                          {sizeDisplay}
                        </td>
                        <td className="px-4 py-3.5">
                          <div className="flex flex-col gap-0.5">
                            {m.metadata?.subject && (
                              <span className="text-xs text-slate-700 font-medium">{m.metadata.subject}</span>
                            )}
                            {m.metadata?.grade && (
                              <span className="text-xs text-slate-400">{m.metadata.grade}</span>
                            )}
                            {!m.metadata?.subject && !m.metadata?.grade && (
                              <span className="text-xs text-slate-300">—</span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3.5 text-slate-400 text-xs">
                          {timeDisplay}
                        </td>
                        <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                          <div className="flex items-center gap-1">
                            {/* 查看详情 */}
                            <button
                              onClick={() => navigate(`/asset/${m.id}`)}
                              className="p-1.5 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                              title="查看详情"
                            >
                              <ExternalLink size={14} />
                            </button>
                            {/* 预览原文件 */}
                            {hasPdf && (
                              <button
                                onClick={() => setPdfPreviewId(m.id)}
                                className="p-1.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                                title="预览原文件"
                              >
                                <Eye size={14} />
                              </button>
                            )}
                            {/* 预览/收起 Markdown */}
                            {hasMd && (
                              <button
                                onClick={() => handleToggleMarkdown(m.id)}
                                disabled={mdSt?.loading}
                                className={`p-1.5 rounded-lg transition-colors disabled:opacity-50 ${
                                  mdSt?.content
                                    ? 'text-orange-600 bg-orange-50 hover:bg-orange-100'
                                    : 'text-slate-400 hover:text-orange-600 hover:bg-orange-50'
                                }`}
                                title={mdSt?.content ? '收起 Markdown' : '预览 Markdown'}
                              >
                                {mdSt?.content ? <EyeOff size={14} /> : <Eye size={14} />}
                              </button>
                            )}
                            {/* 下载 Markdown */}
                            {mdSt?.content && (
                              <button
                                onClick={() => handleDownloadMarkdown(m.id, m.title)}
                                className="p-1.5 text-slate-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-colors"
                                title="下载 .md"
                              >
                                <Download size={14} />
                              </button>
                            )}
                            {/* 下载 ZIP */}
                            {hasZip && (
                              <a
                                href={m.mineruZipUrl}
                                download
                                className="p-1.5 text-slate-400 hover:text-purple-600 hover:bg-purple-50 rounded-lg transition-colors"
                                title="下载解析 ZIP"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <Archive size={14} />
                              </a>
                            )}
                            {/* 删除成果 */}
                            <button
                              onClick={(e) => { e.stopPropagation(); handleDeleteProduct(m.id); }}
                              className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                              title="级联删除"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                      {/* Markdown 行内展开 */}
                      {(mdSt?.loading || mdSt?.error || mdSt?.content) && (
                        <tr key={`md-${m.id}`}>
                          <td colSpan={6} className="px-4 pb-4 pt-0">
                            {mdSt?.loading && (
                              <div className="flex items-center gap-2 text-sm text-slate-400 bg-slate-50 rounded-xl px-4 py-3">
                                <div className="w-4 h-4 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
                                读取 Markdown 内容中...
                              </div>
                            )}
                            {mdSt?.error && (
                              <div className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-4 py-3">
                                {mdSt.error}
                              </div>
                            )}
                            {mdSt?.content && (
                              <div className="relative">
                                <div className="flex items-center justify-between mb-2">
                                  <span className="text-xs text-slate-500 font-medium">Markdown 预览</span>
                                  <div className="flex gap-1">
                                    <button
                                      onClick={() => handleDownloadMarkdown(m.id, m.title)}
                                      className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-green-50 text-green-700 border border-green-200 hover:bg-green-100 font-medium"
                                    >
                                      <Download size={11} /> 下载 .md
                                    </button>
                                    <button
                                      onClick={() => handleToggleMarkdown(m.id)}
                                      className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200 font-medium"
                                    >
                                      <EyeOff size={11} /> 收起
                                    </button>
                                  </div>
                                </div>
                                <pre className="bg-slate-50 rounded-xl border border-slate-200 p-4 text-[12px] text-slate-700 overflow-auto max-h-[40vh] whitespace-pre-wrap leading-relaxed">
                                  {mdSt.content}
                                </pre>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          /* ── 卡片视图 ──────────────────────────────── */
          <div className="grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-4">
            {currentItems.length === 0 && (
              <div className="col-span-4 text-center py-16 text-slate-400">
                暂无符合条件的资料
                {isFiltered && (
                  <button onClick={handleResetFilters} className="ml-2 text-blue-500 underline text-xs">
                    重置筛选
                  </button>
                )}
              </div>
            )}
            {currentItems.map((m: any) => {
              const en = m._enriched;
              const title = en.title;
              const sizeDisplay = en.sizeDisplay;
              const timeDisplay = en.timeDisplay;
              const parsedCountDisplay = en.parsedCountDisplay;
              const statusDisplay = en.statusDisplay;
              const statusColor = en.statusColor;

              const mType = getMaterialType(m);
              const mTags = getMaterialTags(m);
              const tc = typeColor(mType);
              const hasMd = !!(m.metadata?.markdownObjectName || m.metadata?.markdownUrl || m.mineruZipUrl);
              const hasZip = !!m.mineruZipUrl;
              const hasPdf = !!(m.metadata?.objectName || m.previewUrl);
              const mdSt = mdStates.get(m.id);
              return (
                <div
                  key={m.id}
                  className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-lg transition-shadow group flex flex-col"
                >
                  {/* 缩略图区 */}
                  <div
                    className="relative aspect-video bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center cursor-pointer"
                    onClick={() => navigate(`/asset/${m.id}`)}
                  >
                    <div className="absolute top-3 right-3 z-10" onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(m.id)}
                        onChange={() => toggleSelect(m.id)}
                        className="w-4 h-4 text-blue-600 rounded border-slate-300 focus:ring-blue-500 shadow-sm"
                      />
                    </div>
                    <FileText className={`w-14 h-14 ${tc.text} opacity-30`} />
                    <div className="absolute top-3 left-3">
                      <span className={`px-2.5 py-1 rounded-full text-[10px] font-bold text-white ${tc.badge}`}>
                        {mType}
                      </span>
                    </div>
                    {m.metadata?.subject && (
                      <div className="absolute bottom-3 left-3">
                        <span className="px-2 py-0.5 rounded-full text-[10px] bg-white/80 text-slate-600 font-medium">
                          {m.metadata.subject}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* 内容区 */}
                  <div className="p-4 flex-1 flex flex-col">
                    <h3
                      className="text-sm font-semibold text-slate-900 line-clamp-2 group-hover:text-blue-600 transition-colors mb-1 cursor-pointer"
                      onClick={() => navigate(`/asset/${m.id}`)}
                    >
                      {title}
                    </h3>
                    <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                      <span className={`font-medium ${statusColor}`}>{statusDisplay}</span>
                      <span className="font-mono">产物: {parsedCountDisplay}</span>
                    </div>
                    <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                      <span>{sizeDisplay}</span>
                      <span>{timeDisplay}</span>
                    </div>
                    {m.metadata?.grade && (
                      <span className="text-[10px] text-slate-400 mb-2">{m.metadata.grade}</span>
                    )}
                    {mTags.length > 0 && (
                      <div className="flex gap-1 flex-wrap mb-3">
                        {mTags.slice(0, 2).map((tag: string) => (
                          <span key={tag} className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full">
                            {tag}
                          </span>
                        ))}
                        {mTags.length > 2 && (
                          <span className="text-[10px] text-slate-400">+{mTags.length - 2}</span>
                        )}
                      </div>
                    )}
                    {/* 操作按钮 */}
                    <div className="flex items-center gap-1 mt-auto pt-2 border-t border-slate-100 flex-wrap">
                      <button
                        onClick={() => navigate(`/asset/${m.id}`)}
                        className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg text-blue-600 hover:bg-blue-50 font-medium transition-colors"
                      >
                        <ExternalLink size={11} /> 详情
                      </button>
                      {hasPdf && (
                        <button
                          onClick={() => setPdfPreviewId(m.id)}
                          className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg text-indigo-600 hover:bg-indigo-50 font-medium transition-colors"
                        >
                          <Eye size={11} /> 预览
                        </button>
                      )}
                      {hasMd && (
                        <button
                          onClick={() => handleToggleMarkdown(m.id)}
                          disabled={mdSt?.loading}
                          className={`flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg font-medium transition-colors disabled:opacity-50 ${
                            mdSt?.content
                              ? 'text-orange-600 bg-orange-50 hover:bg-orange-100'
                              : 'text-orange-600 hover:bg-orange-50'
                          }`}
                        >
                          <Eye size={11} /> {mdSt?.loading ? '加载中' : mdSt?.content ? 'Markdown ▲' : 'Markdown'}
                        </button>
                      )}
                      {hasZip && (
                        <a
                          href={m.mineruZipUrl}
                          download
                          className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg text-purple-600 hover:bg-purple-50 font-medium transition-colors"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Archive size={11} /> ZIP
                        </a>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteProduct(m.id); }}
                        className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg text-red-600 hover:bg-red-50 font-medium transition-colors ml-auto"
                        title="级联删除"
                      >
                        <Trash2 size={11} /> 删除
                      </button>
                    </div>
                  </div>

                  {/* Markdown 行内展开（卡片模式） */}
                  {(mdSt?.loading || mdSt?.error || mdSt?.content) && (
                    <div className="px-4 pb-4 border-t border-slate-100">
                      {mdSt?.loading && (
                        <div className="flex items-center gap-2 text-xs text-slate-400 py-3">
                          <div className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
                          读取中...
                        </div>
                      )}
                      {mdSt?.error && (
                        <p className="text-xs text-red-600 py-2">{mdSt.error}</p>
                      )}
                      {mdSt?.content && (
                        <div className="pt-3">
                          <div className="flex justify-between mb-1.5">
                            <span className="text-[10px] text-slate-400">Markdown 预览</span>
                            <div className="flex gap-1">
                              <button
                                onClick={() => handleDownloadMarkdown(m.id, m.title)}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-green-50 text-green-700 hover:bg-green-100"
                              >
                                下载
                              </button>
                              <button
                                onClick={() => handleToggleMarkdown(m.id)}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 hover:bg-slate-200"
                              >
                                收起
                              </button>
                            </div>
                          </div>
                          <pre className="bg-slate-50 rounded-lg border border-slate-200 p-3 text-[11px] text-slate-700 overflow-auto max-h-48 whitespace-pre-wrap leading-relaxed">
                            {mdSt.content}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* ── 分页 ────────────────────────────────────── */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-1 pt-6">
            <button
              onClick={prevPage}
              disabled={!hasPrev}
              className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50 transition-colors"
            >
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
                    p === currentPage
                      ? 'bg-blue-600 text-white'
                      : 'border border-slate-200 hover:bg-slate-50 text-slate-600'
                  }`}
                >
                  {p}
                </button>
              ),
            )}
            <button
              onClick={nextPage}
              disabled={!hasNext}
              className="px-3 py-1.5 text-sm border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50 transition-colors"
            >
              下一页
            </button>
          </div>
        )}
      </div>

      {/* ── PDF 预览弹窗 ─────────────────────────────── */}
      {pdfPreviewId !== null && pdfPreviewUrl && (
        <div
          className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setPdfPreviewId(null)}
        >
          <div
            className="w-full max-w-5xl h-[85vh] bg-white rounded-2xl border border-slate-200 shadow-2xl overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 弹窗头部 */}
            <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between flex-shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <FileText className="w-5 h-5 text-red-500 flex-shrink-0" />
                <span className="font-medium text-slate-800 truncate text-sm">
                  {pdfPreviewMaterial?.title ?? '文件预览'}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {pdfPreviewMaterial?.metadata?.objectName && (
                  <a
                    href={pdfPreviewUrl}
                    download={pdfPreviewMaterial?.metadata?.fileName || pdfPreviewMaterial?.title}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100 font-medium"
                  >
                    <Download size={13} /> 下载
                  </a>
                )}
                <button
                  onClick={() => setPdfPreviewId(null)}
                  className="p-2 text-slate-400 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors"
                >
                  <X size={18} />
                </button>
              </div>
            </div>
            {/* iframe */}
            <div className="flex-1 overflow-hidden">
              {getMaterialType(pdfPreviewMaterial) === 'PDF' || pdfPreviewUrl.includes('.pdf') || pdfPreviewMaterial?.metadata?.mimeType === 'application/pdf' ? (
                <iframe
                  src={pdfPreviewUrl}
                  className="w-full h-full border-0"
                  title="PDF 预览"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center bg-slate-50 overflow-auto p-4">
                  <img src={pdfPreviewUrl} alt={pdfPreviewMaterial?.title} className="max-w-full max-h-full object-contain rounded-lg" />
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
