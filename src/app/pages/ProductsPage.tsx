import { useState, useMemo } from 'react';
import { Star, TrendingUp, Trash2, GitBranch } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import { sortProducts } from '../../utils/sort';

type SortKey = '最新发布' | '使用最多' | '评分最高';
const SORT_OPTIONS: SortKey[] = ['最新发布', '使用最多', '评分最高'];

const PRODUCT_COLORS: Record<string, string> = {
  blue:   'border-blue-200 bg-blue-50',
  green:  'border-green-200 bg-green-50',
  purple: 'border-purple-200 bg-purple-50',
  orange: 'border-orange-200 bg-orange-50',
  yellow: 'border-yellow-200 bg-yellow-50',
  indigo: 'border-indigo-200 bg-indigo-50',
};

const ICON_COLORS: Record<string, string> = {
  blue:   'bg-blue-100 text-blue-600',
  green:  'bg-green-100 text-green-600',
  purple: 'bg-purple-100 text-purple-600',
  orange: 'bg-orange-100 text-orange-600',
  yellow: 'bg-yellow-100 text-yellow-700',
  indigo: 'bg-indigo-100 text-indigo-600',
};

export function ProductsPage() {
  const { state, dispatch } = useAppStore();
  const [sortKey, setSortKey] = useState<SortKey>('最新发布');
  const [search, setSearch] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const filtered = useMemo(() => {
    let list = state.products;
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.tags.some((t) => t.toLowerCase().includes(q)) ||
          p.description.toLowerCase().includes(q),
      );
    }
    return sortProducts(list, sortKey);
  }, [state.products, search, sortKey]);

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    dispatch({ type: 'DELETE_PRODUCT', payload: Array.from(selectedIds) });
    setSelectedIds(new Set());
    toast.success(`已删除 ${selectedIds.size} 件成品`);
  };

  return (
    <div className="p-6 space-y-4">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">成品库</h1>
          <p className="text-sm text-gray-500 mt-0.5">共 {state.products.length} 件成品</p>
        </div>
        {selectedIds.size > 0 && (
          <button
            onClick={handleBatchDelete}
            className="flex items-center gap-1.5 px-3 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50"
          >
            <Trash2 size={14} /> 删除选中 ({selectedIds.size})
          </button>
        )}
      </div>

      {/* 搜索 + 排序 */}
      <div className="flex items-center gap-3 flex-wrap">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索成品名称、标签..."
          className="flex-1 min-w-48 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
        />
        <div className="flex bg-gray-100 rounded-lg p-1 gap-0.5">
          {SORT_OPTIONS.map((o) => (
            <button
              key={o}
              onClick={() => setSortKey(o)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                sortKey === o ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {o}
            </button>
          ))}
        </div>
      </div>

      {/* 搜索结果计数 */}
      {search && (
        <p className="text-sm text-gray-500">找到 <span className="font-medium text-gray-800">{filtered.length}</span> 件成品</p>
      )}

      {/* 卡片网格 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {filtered.length === 0 && (
          <div className="col-span-3 text-center py-12 text-gray-400">暂无成品</div>
        )}
        {filtered.map((product) => {
          const colorClass = PRODUCT_COLORS[product.color] ?? 'border-gray-200 bg-gray-50';
          const iconClass = ICON_COLORS[product.color] ?? 'bg-gray-100 text-gray-600';
          const isSelected = selectedIds.has(product.id);
          return (
            <div
              key={product.id}
              className={`relative rounded-xl border-2 p-5 transition-shadow hover:shadow-md ${colorClass} ${isSelected ? 'ring-2 ring-blue-400' : ''}`}
            >
              {/* 选择框 */}
              <div className="absolute top-3 right-3">
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleSelect(product.id)}
                  className="rounded"
                />
              </div>

              {/* 类型图标 + 标题 */}
              <div className="flex items-start gap-3 mb-3">
                <div className={`flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center text-lg font-bold ${iconClass}`}>
                  {product.type.charAt(0)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-gray-900 line-clamp-2">{product.title}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{product.type}</p>
                </div>
              </div>

              <p className="text-sm text-gray-600 line-clamp-2 mb-3">{product.description}</p>

              {/* 统计 */}
              <div className="flex items-center gap-4 text-xs text-gray-500 mb-3">
                <span className="flex items-center gap-1">
                  <Star size={12} className="text-yellow-400 fill-yellow-400" />
                  {product.rating}
                </span>
                <span className="flex items-center gap-1">
                  <TrendingUp size={12} /> {product.useCount} 次使用
                </span>
                <span>{product.items}</span>
              </div>

              {/* 元数据 */}
              <div className="flex items-center gap-2 flex-wrap text-xs mb-3">
                {Object.values(product.metadata).filter(Boolean).map((v) => (
                  <span key={v} className="bg-white/60 px-2 py-0.5 rounded border border-white/80 text-gray-600">
                    {v}
                  </span>
                ))}
              </div>

              {/* 标签 */}
              <div className="flex gap-1 flex-wrap mb-3">
                {product.tags.slice(0, 4).map((tag) => (
                  <span key={tag} className="text-xs bg-white/70 border border-current/20 px-1.5 py-0.5 rounded text-gray-700">
                    {tag}
                  </span>
                ))}
              </div>

              {/* 血缘 */}
              {product.lineage.length > 0 && (
                <div className="mt-2 pt-2 border-t border-current/10">
                  <p className="text-xs text-gray-400 flex items-center gap-1 mb-1">
                    <GitBranch size={11} /> 来源血缘
                  </p>
                  <p className="text-xs text-gray-500 truncate">{product.lineage.join(' → ')}</p>
                </div>
              )}

              {/* 状态 + 日期 */}
              <div className="flex items-center justify-between mt-3">
                <StatusBadge status={product.status} />
                <span className="text-xs text-gray-400">{product.createdAt}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
