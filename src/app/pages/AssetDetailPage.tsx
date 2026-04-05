import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Tag, Shield, Clock, GitBranch, FileText, ChevronDown, ChevronUp, Play } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import type { PermissionLevel, ProcessTask } from '../../store/types';

const PERMISSION_OPTIONS: { value: PermissionLevel; label: string; color: string }[] = [
  { value: 'internal',   label: '内部',   color: 'bg-gray-100 text-gray-600' },
  { value: 'review',     label: '审核中', color: 'bg-yellow-100 text-yellow-700' },
  { value: 'production', label: '生产',   color: 'bg-blue-100 text-blue-700' },
  { value: 'public',     label: '公开',   color: 'bg-green-100 text-green-700' },
];

const STAGE_COLOR_MAP: Record<string, string> = {
  blue:   'bg-blue-500',
  orange: 'bg-orange-500',
  green:  'bg-green-500',
  purple: 'bg-purple-500',
};

export function AssetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const numId = Number(id);
  const { state, dispatch } = useAppStore();
  const navigate = useNavigate();

  const detail = state.assetDetails[numId];
  const material = state.materials.find((m) => m.id === numId);

  const [expandHistory, setExpandHistory] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [editingTags, setEditingTags] = useState(false);
  const [localTags, setLocalTags] = useState<string[]>(detail?.tags ?? []);

  if (!detail) {
    return (
      <div className="p-6">
        <button
          onClick={() => navigate('/source-materials')}
          className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 mb-4"
        >
          <ArrowLeft size={16} /> 返回资料库
        </button>
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-400">
          资产 #{id} 不存在或已被删除
        </div>
      </div>
    );
  }

  const handlePermissionChange = (p: PermissionLevel) => {
    dispatch({ type: 'UPDATE_ASSET_PERMISSION', payload: { id: numId, permission: p } });
    toast.success('权限已更新');
  };

  const handleStartProcessing = () => {
    if (!detail) return;

    // 创建一个新的处理任务
    const newTask: ProcessTask = {
      id: Date.now(),
      name: detail.title,
      type: 'rawcode生成',
      status: 'processing',
      stage: '启动中',
      progress: 0,
      input: detail.assetId,
      output: '-',
      assignee: '系统',
      startTime: new Date().toLocaleString('zh-CN'),
      estimatedTime: '预计 30 分钟',
      logs: [
        {
          time: new Date().toLocaleTimeString('zh-CN'),
          level: 'info',
          msg: '任务已创建，开始处理',
        },
      ],
      materialId: numId, // 关联到当前资料
    };

    // 添加任务并更新资料状态
    dispatch({ type: 'ADD_PROCESS_TASK', payload: newTask });

    // 更新资料状态为 processing
    dispatch({
      type: 'UPDATE_MATERIAL_AI_STATUS',
      payload: { id: numId, aiStatus: 'analyzing', status: 'processing' },
    });

    // 更新 assetDetails 中的状态
    const updatedDetail = {
      ...detail,
      status: 'processing',
      history: [
        ...detail.history,
        {
          id: detail.history.length + 1,
          action: '开始处理',
          time: '刚刚',
          operator: '系统',
          type: 'system',
          status: 'processing',
        },
      ],
    };

    // 需要手动更新 assetDetails，因为 UPDATE_MATERIAL_AI_STATUS 没有完整更新 lineage
    dispatch({
      type: 'UPDATE_ASSET_PERMISSION',
      payload: { id: numId, permission: detail.permission },
    });

    toast.success('处理任务已创建，正在处理中');
  };

  const handleSaveTags = () => {
    dispatch({ type: 'UPDATE_ASSET_TAGS', payload: { id: numId, tags: localTags } });
    setEditingTags(false);
    toast.success('标签已保存');
  };

  const addTag = () => {
    const t = tagInput.trim();
    if (t && !localTags.includes(t)) {
      setLocalTags((prev) => [...prev, t]);
    }
    setTagInput('');
  };

  const removeTag = (tag: string) => setLocalTags((prev) => prev.filter((t) => t !== tag));

  const displayedHistory = expandHistory ? detail.history : detail.history.slice(0, 3);

  return (
    <div className="p-6 space-y-5">
      {/* 返回 + 标题 */}
      <div>
        <button
          onClick={() => navigate('/source-materials')}
          className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 mb-3"
        >
          <ArrowLeft size={15} /> 返回资料库
        </button>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{detail.title}</h1>
            <p className="text-xs text-gray-400 mt-1">资产 ID：{detail.assetId}</p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={detail.status} />
            {/* 当资料状态为 pending 时，显示「开始处理」按钮 */}
            {detail.status === 'pending' && (
              <button
                onClick={handleStartProcessing}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-orange-50 text-orange-700 border border-orange-200 rounded-lg hover:bg-orange-100"
              >
                <Play size={12} />
                开始处理
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* 左主列 */}
        <div className="lg:col-span-2 space-y-5">
          {/* 处理溯源 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <GitBranch size={16} className="text-blue-500" /> 处理溯源
            </h2>
            <div className="flex items-stretch gap-0">
              {detail.lineage.map((node, i) => (
                <div key={i} className="flex-1 flex flex-col items-center">
                  <div className={`w-3 h-3 rounded-full ${STAGE_COLOR_MAP[node.color] ?? 'bg-gray-400'} flex-shrink-0`} />
                  {i < detail.lineage.length - 1 && (
                    <div className="w-full h-0.5 bg-gray-200 mt-1.5 mx-1" />
                  )}
                  <div className="mt-2 text-center">
                    <p className="text-xs font-semibold text-gray-700">{node.stage}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{node.label}</p>
                    {node.file && <p className="text-xs text-gray-400 truncate max-w-24">{node.file}</p>}
                    <StatusBadge status={node.status} className="mt-1" />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 操作历史 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <Clock size={16} className="text-orange-500" /> 操作历史
            </h2>
            <div className="space-y-3">
              {displayedHistory.map((h) => (
                <div key={h.id} className="flex items-start gap-3 pb-3 border-b border-gray-50 last:border-0">
                  <div className="flex-shrink-0 w-6 h-6 rounded-full bg-gray-100 flex items-center justify-center text-xs">
                    {h.type === 'ai' ? '🤖' : h.type === 'system' ? '⚙️' : '👤'}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-gray-800">{h.action}</p>
                      <StatusBadge status={h.status} />
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {h.operator} · {h.time}
                    </p>
                    {h.note && <p className="text-xs text-gray-500 mt-1 italic">{h.note}</p>}
                  </div>
                </div>
              ))}
            </div>
            {detail.history.length > 3 && (
              <button
                onClick={() => setExpandHistory(!expandHistory)}
                className="mt-2 flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700"
              >
                {expandHistory ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {expandHistory ? '收起' : `展开全部 (${detail.history.length})`}
              </button>
            )}
          </div>

          {/* 版本管理 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <FileText size={16} className="text-purple-500" /> 版本管理
            </h2>
            <div className="space-y-2">
              {detail.versions.map((v) => (
                <div key={v.version} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                  <div>
                    <span className="text-sm font-semibold text-gray-800">{v.version}</span>
                    {v.status === 'current' && (
                      <span className="ml-2 text-xs text-green-600 bg-green-50 px-1.5 py-0.5 rounded">当前</span>
                    )}
                    <p className="text-xs text-gray-400 mt-0.5">{v.note}</p>
                  </div>
                  <div className="text-right text-xs text-gray-400">
                    <p>{v.time}</p>
                    <p>{v.operator}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 右侧列 */}
        <div className="space-y-5">
          {/* 元数据 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-4">元数据</h2>
            <dl className="space-y-2">
              {Object.entries(detail.metadata).filter(([, v]) => v !== '').map(([k, v]) => (
                <div key={k} className="flex justify-between text-sm">
                  <dt className="text-gray-500 capitalize">{k}</dt>
                  <dd className="text-gray-800 font-medium text-right max-w-32 truncate">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </div>

          {/* 权限 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">
              <Shield size={15} className="text-blue-500" /> 权限级别
            </h2>
            <div className="grid grid-cols-2 gap-2">
              {PERMISSION_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handlePermissionChange(opt.value)}
                  className={`px-3 py-2 text-xs font-medium rounded-lg border transition-colors ${
                    detail.permission === opt.value
                      ? `${opt.color} border-current`
                      : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* 标签 */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-gray-800 flex items-center gap-2">
                <Tag size={15} className="text-green-500" /> 标签
              </h2>
              {!editingTags ? (
                <button onClick={() => { setEditingTags(true); setLocalTags(detail.tags); }} className="text-xs text-blue-600">
                  编辑
                </button>
              ) : (
                <div className="flex gap-2">
                  <button onClick={() => setEditingTags(false)} className="text-xs text-gray-400">取消</button>
                  <button onClick={handleSaveTags} className="text-xs text-blue-600 font-medium">保存</button>
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(editingTags ? localTags : detail.tags).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded-full"
                >
                  {tag}
                  {editingTags && (
                    <button onClick={() => removeTag(tag)} className="text-blue-400 hover:text-red-500">×</button>
                  )}
                </span>
              ))}
            </div>
            {editingTags && (
              <div className="flex gap-2 mt-3">
                <input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addTag()}
                  placeholder="输入新标签..."
                  className="flex-1 text-xs border border-gray-200 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-300"
                />
                <button onClick={addTag} className="text-xs px-2 py-1.5 bg-blue-600 text-white rounded">
                  添加
                </button>
              </div>
            )}
          </div>

          {/* 相关资产 */}
          {detail.relatedAssets.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <h2 className="font-semibold text-gray-800 mb-3">相关资产</h2>
              <div className="space-y-2">
                {detail.relatedAssets.map((ra) => (
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
      </div>
    </div>
  );
}
