import { useState, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Upload, Grid, List, Filter, SortAsc } from 'lucide-react';
import { toast } from 'sonner';
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

export function SourceMaterialsPage() {
  const { state, dispatch } = useAppStore();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [tab, setTab] = useState<TabFilter>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortOption>('newest');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // 筛选 + 搜索 + 排序
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
    return sortMaterials(list, sort);
  }, [state.materials, tab, search, sort]);

  const { currentItems, currentPage, totalPages, goToPage, hasPrev, hasNext, prevPage, nextPage } =
    usePagination(filtered);

  const pageNumbers = getPageNumbers(currentPage, totalPages);

  // 上传文件处理
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    files.forEach((file) => {
      const newId = Date.now() + Math.random();
      dispatch({
        type: 'ADD_MATERIAL',
        payload: {
          id: newId,
          title: file.name.replace(/\.[^.]+$/, ''),
          type: file.name.split('.').pop()?.toUpperCase() ?? 'FILE',
          size: `${(file.size / 1024 / 1024).toFixed(1)} MB`,
          sizeBytes: file.size,
          uploadTime: '刚刚',
          uploadTimestamp: Date.now(),
          status: 'pending',
          mineruStatus: 'pending',
          aiStatus: 'pending',
          tags: [],
          metadata: {},
          uploader: '当前用户',
        },
      });
    });
    toast.success(`已上传 ${files.length} 个文件`);
    e.target.value = '';
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
  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    dispatch({ type: 'DELETE_MATERIAL', payload: Array.from(selectedIds) });
    setSelectedIds(new Set());
    toast.success(`已删除 ${selectedIds.size} 条资料`);
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
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileChange}
            accept=".pdf,.docx,.doc,.pptx,.ppt,.jpg,.jpeg,.png"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Upload size={16} />
            上传资料
          </button>
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
      </div>

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
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {currentItems.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center py-12 text-gray-400">暂无数据</td>
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
              className="bg-white rounded-xl border border-gray-200 p-4 cursor-pointer hover:shadow-md transition-shadow"
            >
              <div className="flex items-start justify-between mb-2">
                <span className="text-xs font-medium bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                  {m.type}
                </span>
                <StatusBadge status={m.status} />
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
