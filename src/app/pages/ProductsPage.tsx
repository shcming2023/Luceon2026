import { useState, useMemo } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Star, TrendingUp, Trash2, GitBranch } from 'lucide-react';
import { toast } from 'sonner';
import type { Material, Product } from '../../store/types';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import { sortProducts } from '../../utils/sort';
import { fetchMinerUMarkdown } from '../../utils/mineruApi';

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
  const navigate = useNavigate();
  const location = useLocation();
  const { state, dispatch } = useAppStore();
  const [sortKey, setSortKey] = useState<SortKey>('最新发布');
  const [search, setSearch] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [activeProductId, setActiveProductId] = useState<number | null>(null);
  const [mdLoading, setMdLoading] = useState(false);
  const [mdPreview, setMdPreview] = useState<string | null>(null);
  const [mdError, setMdError] = useState('');

  const activeProduct = useMemo(() => {
    if (activeProductId === null) return null;
    return state.products.find((p) => p.id === activeProductId) ?? null;
  }, [activeProductId, state.products]);

  const sourceMaterial = useMemo<Material | null>(() => {
    if (!activeProduct?.source) return null;
    const m = String(activeProduct.source).match(/^material:(\d+)$/);
    if (!m) return null;
    const id = Number(m[1]);
    if (!Number.isFinite(id)) return null;
    return state.materials.find((x) => x.id === id) ?? null;
  }, [activeProduct?.source, state.materials]);

  const openProduct = (product: Product) => {
    setActiveProductId(product.id);
    setMdPreview(null);
    setMdError('');
  };

  const closeProduct = () => {
    setActiveProductId(null);
    setMdPreview(null);
    setMdError('');
  };

  const handleToggleMarkdown = async () => {
    if (mdLoading) return;

    if (mdPreview !== null) {
      setMdPreview(null);
      setMdError('');
      return;
    }

    if (!sourceMaterial) {
      setMdError('找不到来源资料，无法加载内容');
      return;
    }

    const { markdownObjectName, markdownUrl } = sourceMaterial.metadata || {};

    setMdLoading(true);
    setMdError('');
    try {
      let text = '';
      if (markdownObjectName) {
        const bucket = String(state.minioConfig.parsedBucket || state.minioConfig.bucket || '');
        const url = `/__proxy/upload/proxy-file?objectName=${encodeURIComponent(markdownObjectName)}${bucket ? `&bucket=${encodeURIComponent(bucket)}` : ''}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`读取失败: HTTP ${res.status}`);
        text = await res.text();
      } else {
        text = await fetchMinerUMarkdown(markdownUrl, sourceMaterial.mineruZipUrl);
      }

      if (!text.trim()) {
        setMdError('暂无可用的 Markdown 内容（可能尚未完成 MinerU 解析）');
        return;
      }

      setMdPreview(text.length > 20000 ? `${text.slice(0, 20000)}\n\n...(内容已截断)` : text);
    } catch (error) {
      setMdError(error instanceof Error ? error.message : String(error));
    } finally {
      setMdLoading(false);
    }
  };

  const handleDownloadMarkdown = () => {
    if (!mdPreview) return;
    const blob = new Blob([mdPreview], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(activeProduct?.title || 'product').replace(/[\\/:*?"<>|]+/g, '_')}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const materialIdFilter = useMemo(() => {
    const sp = new URLSearchParams(location.search);
    const raw = sp.get('materialId');
    if (!raw) return null;
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    return n;
  }, [location.search]);

  const filterMaterial = useMemo<Material | null>(() => {
    if (materialIdFilter === null) return null;
    return state.materials.find((m) => m.id === materialIdFilter) ?? null;
  }, [materialIdFilter, state.materials]);

  const baseList = useMemo(() => {
    if (materialIdFilter === null) return state.products;
    const source = `material:${materialIdFilter}`;
    return state.products.filter(
      (p) => p.source === source || (p.lineage || []).some((id) => Number(id) === materialIdFilter),
    );
  }, [materialIdFilter, state.products]);

  const filtered = useMemo(() => {
    let list = baseList;
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
  }, [baseList, search, sortKey]);

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
          <p className="text-sm text-gray-500 mt-0.5">
            共 {baseList.length} 件成品{materialIdFilter !== null ? `（全部 ${state.products.length}）` : ''}
          </p>
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

      {materialIdFilter !== null && (
        <div className="flex items-center gap-2 flex-wrap text-sm text-gray-600 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">
          <span className="text-gray-700">
            当前筛选：来源资料 {filterMaterial ? `“${filterMaterial.title}”` : `ID=${materialIdFilter}`}
          </span>
          {filterMaterial && (
            <button
              onClick={() => navigate(`/asset/${filterMaterial.id}`)}
              className="text-xs px-2.5 py-1.5 rounded bg-white text-blue-700 border border-blue-200 hover:bg-blue-50"
            >
              打开资料
            </button>
          )}
          <button
            onClick={() => navigate('/products')}
            className="text-xs px-2.5 py-1.5 rounded bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"
          >
            清除筛选
          </button>
        </div>
      )}

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
              role="button"
              tabIndex={0}
              onClick={() => openProduct(product)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') openProduct(product);
              }}
              className={`relative rounded-xl border-2 p-5 transition-shadow hover:shadow-md cursor-pointer ${colorClass} ${isSelected ? 'ring-2 ring-blue-400' : ''}`}
            >
              {/* 选择框 */}
              <div className="absolute top-3 right-3" onClick={(e) => e.stopPropagation()}>
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

      {activeProduct && (
        <div
          className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
          onClick={closeProduct}
        >
          <div
            className="w-full max-w-3xl bg-white rounded-xl border border-gray-200 shadow-xl p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs text-gray-500">{activeProduct.type}</p>
                <h2 className="text-lg font-semibold text-gray-900 truncate">{activeProduct.title}</h2>
              </div>
              <button
                onClick={closeProduct}
                className="text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50"
              >
                关闭
              </button>
            </div>

            <p className="text-sm text-gray-600 mt-2">{activeProduct.description}</p>

            <div className="flex items-center gap-2 flex-wrap text-xs mt-3">
              <StatusBadge status={activeProduct.status} />
              <span className="text-gray-400">创建：{activeProduct.createdAt}</span>
              {sourceMaterial && (
                <button
                  onClick={() => navigate(`/asset/${sourceMaterial.id}`)}
                  className="text-xs px-2.5 py-1.5 rounded bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100"
                >
                  打开来源资料
                </button>
              )}
              <button
                onClick={handleToggleMarkdown}
                className="text-xs px-2.5 py-1.5 rounded bg-orange-50 text-orange-700 border border-orange-200 hover:bg-orange-100 disabled:opacity-60 disabled:cursor-not-allowed"
                disabled={mdLoading}
              >
                {mdLoading ? '读取中...' : mdPreview !== null ? '收起内容' : '预览内容'}
              </button>
              <button
                onClick={handleDownloadMarkdown}
                className="text-xs px-2.5 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed"
                disabled={!mdPreview}
              >
                下载 .md
              </button>
            </div>

            {!!activeProduct.source && (
              <p className="text-xs text-gray-400 mt-3 break-all">
                <span className="text-gray-500">来源：</span>
                {activeProduct.source}
              </p>
            )}

            {mdError && <p className="text-sm text-red-600 mt-3">{mdError}</p>}

            {mdPreview !== null && (
              <pre className="mt-3 bg-gray-50 rounded border border-gray-200 p-3 text-[12px] text-gray-700 overflow-auto max-h-[60vh] whitespace-pre-wrap leading-relaxed">
                {mdPreview}
              </pre>
            )}

            {mdPreview === null && !mdError && (
              <p className="text-sm text-gray-400 mt-3">
                {sourceMaterial?.metadata?.markdownObjectName || sourceMaterial?.metadata?.markdownUrl || sourceMaterial?.mineruZipUrl
                  ? '点击“预览内容”加载 Markdown'
                  : '当前成品未关联可预览内容（请先完成 MinerU 解析）'}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
