import { useState, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Upload, Grid, List, SortAsc, AlertTriangle, RefreshCw, Trash2, ChevronDown, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';

/** 删除确认弹窗（始终使用 toast，避免 window.confirm 在各类环境中被屏蔽） */
function confirmDelete(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    toast(message, {
      duration: 10000,
      action: {
        label: '确认删除',
        onClick: () => resolve(true),
      },
      cancel: {
        label: '取消',
        onClick: () => resolve(false),
      },
      onDismiss: () => resolve(false),
      onAutoClose: () => resolve(false),
    });
  });
}
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import type { TabFilter, SortOption, ViewMode } from '../../store/types';
import { sortMaterials } from '../../utils/sort';
import { usePagination, getPageNumbers } from '../../utils/pagination';

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

let uploadIdCounter = 0;

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
      [...new Set(values.map((value) => value?.trim()).filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b, 'zh-CN'));

    return {
      subjects: unique(state.materials.map((item) => item.metadata?.subject)),
      grades: unique(state.materials.map((item) => item.metadata?.grade)),
      languages: unique(state.materials.map((item) => item.metadata?.language)),
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
          m.tags.some((t) => t.toLowerCase().includes(q)) ||
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
    const statusCounts = filtered.reduce<Record<TabFilter, number>>((acc, item) => {
      acc[item.status] += 1;
      return acc;
    }, { all: filtered.length, pending: 0, processing: 0, reviewing: 0, failed: 0, completed: 0 });

    const totalSizeBytes = filtered.reduce((sum, item) => sum + (item.sizeBytes || 0), 0);
    const subjectCoverage = new Set(filtered.map((item) => item.metadata?.subject).filter(Boolean)).size;

    return {
      statusCounts,
      totalSizeBytes,
      subjectCoverage,
    };
  }, [filtered]);

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
    const MAX_SIZE = (mineruConfig.maxFileSize || 0) > 0
      ? mineruConfig.maxFileSize
      : 200 * 1024 * 1024; // 默认 200MB

    if (file.size > MAX_SIZE) {
      const maxSizeMB = Math.round(MAX_SIZE / (1024 * 1024));
      return {
        valid: false,
        error: `文件 "${file.name}" 超过上传限制 (最大 ${maxSizeMB}MB)`,
      };
    }
    return { valid: true };
  };

  // 上传文件处理
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;

    const validationResults = files.map(f => ({ file: f, validation: validateFile(f) }));
    const invalidFiles = validationResults.filter(({ validation }) => !validation.valid);

    if (invalidFiles.length > 0) {
      invalidFiles.forEach(({ validation }) => {
        toast.error(validation.error, { icon: <AlertTriangle size={16} /> });
      });
      e.target.value = '';
      return;
    }
    for (let fileIdx = 0; fileIdx < files.length; fileIdx++) {
      const file = files[fileIdx];
      uploadIdCounter = (uploadIdCounter + 1) % 1000;
      const newId = Date.now() * 1000 + uploadIdCounter + fileIdx;
      
      dispatch({
        type: 'ADD_MATERIAL',
        payload: {
          id: newId,
          title: file.name.replace(/\.[^.]+$/, ''),
          type: file.name.split('.').pop()?.toUpperCase() ?? 'FILE',
          size: `${(file.size / 1024 / 1024).toFixed(1)} MB`,
          sizeBytes: file.size,
          uploadTime: '上传中...',
          uploadTimestamp: Date.now(),
          status: 'processing',
          mineruStatus: 'pending',
          aiStatus: 'pending',
          tags: [],
          metadata: {},
          uploader: '当前用户',
        },
      });

      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('materialId', String(newId));

        // 通过 Vite proxy 路由到 upload-server，兼容所有部署环境
        const uploadUrl = `/__proxy/upload/upload`;
        const response = await fetch(uploadUrl, {
          method: 'POST',
          body: formData,
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`上传失败: HTTP ${response.status} - ${errorText}`);
        }

        const result = await response.json();
        
        // 更新资料记录，添加文件 URL 并标记为待处理
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: newId,
            updates: {
              status: 'pending',
              uploadTime: '刚刚',
              metadata: {
                fileUrl: result.url,
                objectName: result.objectName || '',  // MinIO 对象路径（持久引用）
                fileName: result.fileName,
                provider: result.provider,
                mimeType: result.mimeType,
                // 上传时自动计算的字段
                ...(result.pages != null ? { pages: String(result.pages) } : {}),
                ...(result.format ? { format: result.format } : {}),
              },
            },
          },
        });
        
        if (result.provider === 'minio' && result.objectName) {
          toast.success(`"${file.name}" 已上传至 MinIO，可在详情页启动解析`);
        } else {
          toast.success(`"${file.name}" 上传成功`);
        }
      } catch (error) {
        console.error('上传失败:', error);
        // 标记为失败状态
        dispatch({
          type: 'UPDATE_MATERIAL',
          payload: {
            id: newId,
            updates: {
              status: 'failed',
              uploadTime: '上传失败',
            },
          },
        });
        toast.error(`"${file.name}" 上传失败: ${error instanceof Error ? error.message : '未知错误'}`);
      }
    }
    
    e.target.value = '';
  };

  // 重置配置
  const handleResetConfig = () => {
    try {
      // 清除所有配置类 localStorage key（不包含业务数据）
      localStorage.removeItem('app_ai_config');
      localStorage.removeItem('app_mineru_config');
      localStorage.removeItem('app_minio_config');       // MinIO 存储配置
      localStorage.removeItem('app_ai_rule_settings');   // AI 规则执行设置
      toast.success('配置已重置，页面将刷新');
      setTimeout(() => window.location.reload(), 1000);
    } catch (error) {
      toast.error('重置配置失败');
    }
  };

  // 选择切换
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  // 批量删除
  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    const relatedTaskCount = state.processTasks.filter(
      (t) => t.materialId !== undefined && ids.includes(t.materialId),
    ).length;
    const hasUploaded = ids.some((id) => {
      const m = state.materials.find((mat) => mat.id === id);
      return m?.metadata?.objectName || m?.mineruStatus || m?.aiStatus;
    });
    let msg = `确定删除选中的 ${ids.length} 条资料吗？此操作不可撤销。`;
    if (hasUploaded) msg += '\n\n⚠️ 其中部分资料已上传至云存储，删除后原始文件和解析产物将一并清除。';
    if (relatedTaskCount > 0) msg += `\n关联的 ${relatedTaskCount} 条处理任务也将同步删除。`;
    const ok = await confirmDelete(msg);
    if (!ok) return;
    dispatch({ type: 'DELETE_MATERIAL', payload: ids });
    setSelectedIds(new Set());
    toast.success(`已删除 ${ids.length} 条资料`);
  };

  // 单条删除
  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    const material = state.materials.find((m) => m.id === id);
    const name = material?.title ?? '该资料';
    const relatedTaskCount = state.processTasks.filter((t) => t.materialId === id).length;
    let msg = `确定删除「${name}」吗？此操作不可撤销。`;
    if (material?.metadata?.objectName || material?.mineruStatus || material?.aiStatus) {
      msg += '\n\n⚠️ 该资料已上传至云存储，删除后原始文件和解析产物将一并清除。';
    }
    if (relatedTaskCount > 0) msg += `\n关联的 ${relatedTaskCount} 条处理任务也将同步删除。`;
    const ok = await confirmDelete(msg);
    if (!ok) return;
    dispatch({ type: 'DELETE_MATERIAL', payload: [id] });
    setSelectedIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
    toast.success('已删除');
  };

  // 清空所有数据（localStorage + 内存，db-server 通过 DELETE_MATERIAL 联动删除）
  const handleClearAll = async () => {
    if (state.materials.length === 0) { toast('暂无数据'); return; }
    const allIds = state.materials.map((m) => m.id);
    const totalTaskCount = state.processTasks.filter(
      (t) => t.materialId !== undefined && allIds.includes(t.materialId),
    ).length;
    const uploadedCount = state.materials.filter(
      (m) => m.metadata?.objectName || m.mineruStatus || m.aiStatus,
    ).length;
    let msg = `确定清空全部 ${state.materials.length} 条资料吗？此操作不可撤销。`;
    if (uploadedCount > 0) {
      msg += `\n\n⚠️ 其中 ${uploadedCount} 条资料已上传至云存储，删除后原始文件和解析产物将一并清除。`;
    }
    if (totalTaskCount > 0) msg += `\n关联的 ${totalTaskCount} 条处理任务也将同步删除。`;
    const ok = await confirmDelete(msg);
    if (!ok) return;
    dispatch({ type: 'DELETE_MATERIAL', payload: allIds });
    setSelectedIds(new Set());
    toast.success(`已清空全部 ${allIds.length} 条资料`);
  };

  return (
    <div className="p-6 space-y-4">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">原始资料库</h1>
          <p className="text-sm text-gray-500 mt-0.5">共 {state.materials.length} 条资料</p>
        </div>
        <div className="flex items-center gap-2">
          {selectedIds.size > 0 && (
            <button
              onClick={handleBatchDelete}
              className="px-3 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50"
            >
              删除选中 ({selectedIds.size})
            </button>
          )}
          {currentItems.length > 0 && (
            <button
              onClick={handleSelectCurrentPage}
              className="px-3 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              {isCurrentPageFullySelected ? '取消当前页' : '全选当前页'}
            </button>
          )}
          <button
            onClick={handleClearAll}
            className="flex items-center gap-2 px-3 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50"
          >
            <Trash2 size={15} />
            清空全部
          </button>
          <button
            data-testid="reset-config-btn"
            onClick={handleResetConfig}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50"
            title="重置配置解决上传问题"
          >
            <RefreshCw size={16} />
            重置配置
          </button>
          <input
            ref={fileInputRef}
            data-testid="file-input"
            type="file"
            multiple
            className="hidden"
            onChange={handleFileChange}
            accept=".pdf,.docx,.doc,.pptx,.ppt,.jpg,.jpeg,.png"
          />
          <button
            data-testid="upload-button"
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Upload size={16} />
            上传资料
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">当前结果</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900">{filtered.length}</p>
          <p className="mt-1 text-xs text-gray-400">待处理 {summary.statusCounts.pending} · 完成 {summary.statusCounts.completed}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">处理中 / 审核中</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900">{summary.statusCounts.processing + summary.statusCounts.reviewing}</p>
          <p className="mt-1 text-xs text-gray-400">失败 {summary.statusCounts.failed}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">总存储占用</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900">{formatBytes(summary.totalSizeBytes)}</p>
          <p className="mt-1 text-xs text-gray-400">按当前筛选结果汇总</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500">学科覆盖</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900">{summary.subjectCoverage}</p>
          <p className="mt-1 text-xs text-gray-400">已识别学科数</p>
        </div>
      </div>

      {/* 筛选栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Tab 过滤 */}
        <div className="flex bg-gray-100 rounded-lg p-1 gap-0.5">
          {TAB_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              onClick={() => setTab(opt.key)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                tab === opt.key
                  ? 'bg-white text-blue-700 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* 搜索 */}
        <div className="flex-1 min-w-48 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索资料名称、标签、上传者..."
            className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
        </div>

        {/* 排序 */}
        <div className="flex items-center gap-1.5">
          <SortAsc size={14} className="text-gray-400" />
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortOption)}
            className="text-sm border border-gray-200 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
        </div>

        {/* 视图切换 */}
        <div className="flex border border-gray-200 rounded-lg overflow-hidden">
          <button
            onClick={() => setViewMode('list')}
            className={`p-2 ${viewMode === 'list' ? 'bg-blue-50 text-blue-600' : 'text-gray-400 hover:text-gray-600'}`}
          >
            <List size={16} />
          </button>
          <button
            onClick={() => setViewMode('grid')}
            className={`p-2 ${viewMode === 'grid' ? 'bg-blue-50 text-blue-600' : 'text-gray-400 hover:text-gray-600'}`}
          >
            <Grid size={16} />
          </button>
        </div>

        <button
          onClick={() => setAdvancedExpanded((prev) => !prev)}
          className="flex items-center gap-1.5 px-3 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50"
        >
          {advancedExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          高级筛选
        </button>
      </div>

      {advancedExpanded && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
            <select
              value={subjectFilter}
              onChange={(e) => setSubjectFilter(e.target.value)}
              className="text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              <option value="all">全部学科</option>
              {advancedOptions.subjects.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <select
              value={gradeFilter}
              onChange={(e) => setGradeFilter(e.target.value)}
              className="text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              <option value="all">全部年级</option>
              {advancedOptions.grades.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <select
              value={languageFilter}
              onChange={(e) => setLanguageFilter(e.target.value)}
              className="text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              <option value="all">全部语言</option>
              {advancedOptions.languages.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <select
              value={mineruStatusFilter}
              onChange={(e) => setMineruStatusFilter(e.target.value as (typeof MINERU_STATUS_OPTIONS)[number]['key'])}
              className="text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              {MINERU_STATUS_OPTIONS.map((item) => (
                <option key={item.key} value={item.key}>{item.label}</option>
              ))}
            </select>
            <select
              value={aiStatusFilter}
              onChange={(e) => setAiStatusFilter(e.target.value as (typeof AI_STATUS_OPTIONS)[number]['key'])}
              className="text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              {AI_STATUS_OPTIONS.map((item) => (
                <option key={item.key} value={item.key}>{item.label}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* 结果数量 */}
      {search && (
        <p className="text-sm text-gray-500">
          找到 <span className="font-medium text-gray-800">{filtered.length}</span> 条结果
        </p>
      )}

      {/* 列表/网格 */}
      {viewMode === 'list' ? (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="w-10 px-4 py-3 text-left">
                  <input
                    type="checkbox"
                    checked={selectedIds.size === currentItems.length && currentItems.length > 0}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedIds(new Set(currentItems.map((m) => m.id)));
                      } else {
                        setSelectedIds(new Set());
                      }
                    }}
                    className="rounded"
                  />
                </th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">资料名称</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">类型</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">大小</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">上传者</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">上传时间</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">状态</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-700">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {currentItems.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-center py-12 text-gray-400">暂无数据</td>
                </tr>
              )}
              {currentItems.map((m) => (
                <tr
                  key={m.id}
                  className="hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/asset/${m.id}`)}
                >
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(m.id)}
                      onChange={() => toggleSelect(m.id)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div>
                      <p className="font-medium text-gray-800 truncate max-w-xs">{m.title}</p>
                      {m.tags.length > 0 && (
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {m.tags.slice(0, 3).map((tag) => (
                            <span key={tag} className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">
                              {tag}
                            </span>
                          ))}
                          {m.tags.length > 3 && (
                            <span className="text-xs text-gray-400">+{m.tags.length - 3}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{m.type}</td>
                  <td className="px-4 py-3 text-gray-600">{m.size}</td>
                  <td className="px-4 py-3 text-gray-600">{m.uploader}</td>
                  <td className="px-4 py-3 text-gray-500">{m.uploadTime}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={m.status} />
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={(e) => handleDelete(e, m.id)}
                      className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                      title="删除"
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
          {currentItems.map((m) => (
            <div
              key={m.id}
              onClick={() => navigate(`/asset/${m.id}`)}
              className="relative bg-white rounded-xl border border-gray-200 p-4 cursor-pointer hover:shadow-md transition-shadow"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(m.id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleSelect(m.id)}
                    className="rounded"
                  />
                  <span className="text-xs font-medium bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                    {m.type}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={m.status} />
                  <button
                    onClick={(e) => handleDelete(e, m.id)}
                    className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                    title="删除"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <p className="text-sm font-semibold text-gray-800 line-clamp-2 mb-2">{m.title}</p>
              <p className="text-xs text-gray-400">{m.size} · {m.uploadTime}</p>
              <p className="text-xs text-gray-400 mt-0.5">{m.uploader}</p>
              {m.tags.length > 0 && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {m.tags.slice(0, 2).map((tag) => (
                    <span key={tag} className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {currentItems.length === 0 && (
            <div className="col-span-4 text-center py-12 text-gray-400">暂无数据</div>
          )}
        </div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-1 pt-2">
          <button
            onClick={prevPage}
            disabled={!hasPrev}
            className="px-3 py-1.5 text-sm border rounded-lg disabled:opacity-40 hover:bg-gray-50"
          >
            上一页
          </button>
          {pageNumbers.map((p, i) =>
            p === '...' ? (
              <span key={`ellipsis-${i}`} className="px-2 text-gray-400">…</span>
            ) : (
              <button
                key={p}
                onClick={() => goToPage(p as number)}
                className={`w-8 h-8 text-sm rounded-lg ${
                  p === currentPage
                    ? 'bg-blue-600 text-white'
                    : 'border hover:bg-gray-50 text-gray-700'
                }`}
              >
                {p}
              </button>
            ),
          )}
          <button
            onClick={nextPage}
            disabled={!hasNext}
            className="px-3 py-1.5 text-sm border rounded-lg disabled:opacity-40 hover:bg-gray-50"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}
