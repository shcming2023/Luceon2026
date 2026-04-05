import { useState, useMemo } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import type { TaskFilter, AssetStatus, Task } from '../../store/types';

const PRIORITY_COLOR: Record<string, string> = {
  high:   'bg-red-100 text-red-700 border-red-200',
  medium: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  low:    'bg-gray-100 text-gray-600 border-gray-200',
};

const PRIORITY_LABEL: Record<string, string> = {
  high: '高', medium: '中', low: '低',
};

const LOG_LEVEL_COLOR: Record<string, string> = {
  info:    'text-gray-400',
  success: 'text-green-500',
  warning: 'text-yellow-500',
  error:   'text-red-500',
};

const FILTER_OPTIONS: { key: TaskFilter; label: string }[] = [
  { key: 'all',       label: '全部' },
  { key: 'rawcode',   label: 'MinerU解析' },
  { key: 'cleancode', label: 'AI清洗' },
  { key: 'product',   label: '成品生成' },
];

const TYPE_FILTER_MAP: Record<string, string[]> = {
  rawcode:   ['MinerU解析', 'OCR识别'],
  cleancode: ['AI清洗', '格式转换'],
  product:   ['成品生成'],
};

function TaskRow({ task }: { task: Task }) {
  const { dispatch } = useAppStore();
  const [expanded, setExpanded] = useState(false);

  const handleStatusChange = (status: AssetStatus) => {
    dispatch({ type: 'UPDATE_TASK_STATUS', payload: { id: task.id, status } });
    toast.success('任务状态已更新');
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
      {/* 头部 */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-medium border px-2 py-0.5 rounded ${PRIORITY_COLOR[task.priority] ?? 'bg-gray-100'}`}>
              优先级：{PRIORITY_LABEL[task.priority]}
            </span>
            <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">{task.type}</span>
            <StatusBadge status={task.status} />
          </div>
          <p className="text-sm font-semibold text-gray-900 mt-1.5 truncate">{task.name}</p>
          <p className="text-xs text-gray-400 font-mono mt-0.5">{task.id}</p>
        </div>
        <div className="text-right text-xs text-gray-400 flex-shrink-0">
          <p>创建：{task.createdAt}</p>
          <p>更新：{task.updatedAt}</p>
          <p>负责人：{task.assignee}</p>
        </div>
      </div>

      {/* 进度 */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>进度</span>
          <span>{task.progress}%</span>
        </div>
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${
              task.status === 'failed' ? 'bg-red-400' :
              task.status === 'completed' ? 'bg-green-400' : 'bg-blue-500'
            }`}
            style={{ width: `${task.progress}%` }}
          />
        </div>
      </div>

      {/* 输入/输出 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <p className="text-gray-400 mb-0.5">输入</p>
          <p className="text-gray-700 truncate">{task.input}</p>
        </div>
        <div>
          <p className="text-gray-400 mb-0.5">输出</p>
          <p className="text-gray-700 truncate">{task.output}</p>
        </div>
      </div>

      {/* 错误/审核备注 */}
      {task.error && (
        <div className="p-2.5 bg-red-50 rounded-lg border border-red-100 text-xs text-red-700">
          <span className="font-medium">错误：</span>{task.error}
        </div>
      )}
      {task.reviewNote && (
        <div className="p-2.5 bg-yellow-50 rounded-lg border border-yellow-100 text-xs text-yellow-700">
          <span className="font-medium">审核备注：</span>{task.reviewNote}
        </div>
      )}

      {/* 操作 + 日志 */}
      <div className="flex items-center gap-2 flex-wrap">
        {task.status === 'reviewing' && (
          <>
            <button
              onClick={() => handleStatusChange('completed')}
              className="text-xs px-2.5 py-1.5 bg-green-50 text-green-700 border border-green-200 rounded-lg hover:bg-green-100"
            >
              通过
            </button>
            <button
              onClick={() => handleStatusChange('failed')}
              className="text-xs px-2.5 py-1.5 bg-red-50 text-red-700 border border-red-200 rounded-lg hover:bg-red-100"
            >
              驳回
            </button>
          </>
        )}
        {task.status === 'failed' && (
          <button
            onClick={() => handleStatusChange('pending')}
            className="text-xs px-2.5 py-1.5 bg-blue-50 text-blue-700 border border-blue-200 rounded-lg hover:bg-blue-100"
          >
            重试
          </button>
        )}
        {task.logs.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="ml-auto flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600"
          >
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            {expanded ? '收起' : '日志'}
          </button>
        )}
      </div>

      {/* 日志 */}
      {expanded && task.logs.length > 0 && (
        <div className="bg-gray-900 rounded-lg p-3 font-mono text-xs space-y-1 max-h-40 overflow-y-auto">
          {task.logs.map((log, i) => (
            <div key={i} className={`flex gap-2 ${LOG_LEVEL_COLOR[log.level] ?? 'text-gray-300'}`}>
              <span className="text-gray-600 flex-shrink-0">[{log.time}]</span>
              <span className="text-gray-300">{log.msg}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function TaskCenterPage() {
  const { state } = useAppStore();
  const [filter, setFilter] = useState<TaskFilter>('all');
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    let list = state.tasks;
    if (filter !== 'all') {
      const types = TYPE_FILTER_MAP[filter] ?? [];
      list = list.filter((t) => types.includes(t.type));
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.id.toLowerCase().includes(q) ||
          t.assignee.toLowerCase().includes(q),
      );
    }
    return list;
  }, [state.tasks, filter, search]);

  return (
    <div className="p-6 space-y-4">
      {/* 头部 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">任务中心</h1>
        <p className="text-sm text-gray-500 mt-0.5">共 {state.tasks.length} 个任务</p>
      </div>

      {/* 筛选 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex bg-gray-100 rounded-lg p-1 gap-0.5">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              onClick={() => setFilter(opt.key)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                filter === opt.key ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索任务名称、ID、负责人..."
          className="flex-1 min-w-48 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
        />
      </div>

      {/* 任务列表 */}
      <div className="space-y-3">
        {filtered.length === 0 && (
          <div className="text-center py-12 text-gray-400">暂无任务</div>
        )}
        {filtered.map((task) => (
          <TaskRow key={task.id} task={task} />
        ))}
      </div>
    </div>
  );
}
