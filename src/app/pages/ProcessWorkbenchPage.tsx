import { useState } from 'react';
import { ChevronDown, ChevronUp, RefreshCw, CheckCircle, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../store/appContext';
import { StatusBadge } from '../components/StatusBadge';
import type { AssetStatus, ProcessTask } from '../../store/types';

const TYPE_COLOR: Record<string, string> = {
  'rawcode生成':  'bg-orange-50 text-orange-700 border-orange-200',
  'cleancode生成': 'bg-blue-50 text-blue-700 border-blue-200',
  '成品生成':     'bg-purple-50 text-purple-700 border-purple-200',
};

const LOG_LEVEL_COLOR: Record<string, string> = {
  info:    'text-gray-500',
  success: 'text-green-600',
  warning: 'text-yellow-600',
  error:   'text-red-600',
};

function TaskCard({ task }: { task: ProcessTask }) {
  const { dispatch } = useAppStore();
  const [expanded, setExpanded] = useState(false);

  const handleStatusChange = (status: AssetStatus) => {
    dispatch({ type: 'UPDATE_PROCESS_TASK_STATUS', payload: { id: task.id, status } });
    toast.success(`任务状态已更新为：${status}`);
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      {/* 头部 */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-medium border px-2 py-0.5 rounded ${TYPE_COLOR[task.type] ?? 'bg-gray-100 text-gray-600 border-gray-200'}`}>
              {task.type}
            </span>
            <StatusBadge status={task.status} />
          </div>
          <p className="text-sm font-semibold text-gray-900 mt-1.5 truncate">{task.name}</p>
          <p className="text-xs text-gray-400 mt-0.5">{task.stage}</p>
        </div>
        <div className="text-right text-xs text-gray-400 flex-shrink-0">
          <p>开始：{task.startTime}</p>
          <p>预计：{task.estimatedTime}</p>
        </div>
      </div>

      {/* 进度条 */}
      <div className="mt-3">
        <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
          <span>进度</span>
          <span>{task.progress}%</span>
        </div>
        <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              task.status === 'failed' ? 'bg-red-400' :
              task.status === 'completed' ? 'bg-green-400' : 'bg-blue-500'
            }`}
            style={{ width: `${task.progress}%` }}
          />
        </div>
      </div>

      {/* 输入/输出 */}
      <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
        <div>
          <p className="text-gray-400 mb-0.5">输入</p>
          <p className="text-gray-700 font-medium truncate">{task.input}</p>
        </div>
        <div>
          <p className="text-gray-400 mb-0.5">输出</p>
          <p className="text-gray-700 font-medium truncate">{task.output}</p>
        </div>
      </div>

      {/* 错误 / 审核备注 */}
      {task.error && (
        <div className="mt-3 p-3 bg-red-50 rounded-lg border border-red-100 text-xs text-red-700">
          <span className="font-medium">错误：</span>{task.error}
        </div>
      )}
      {task.reviewNote && (
        <div className="mt-3 p-3 bg-yellow-50 rounded-lg border border-yellow-100 text-xs text-yellow-700">
          <span className="font-medium">审核备注：</span>{task.reviewNote}
        </div>
      )}

      {/* diff 统计 */}
      {task.diffStats && (
        <div className="mt-3 flex gap-3 text-xs">
          <span className="text-green-600">+{task.diffStats.added} 新增</span>
          <span className="text-red-500">-{task.diffStats.removed} 删除</span>
          <span className="text-blue-500">~{task.diffStats.changed} 修改</span>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="mt-4 flex items-center gap-2 flex-wrap">
        {task.status === 'reviewing' && (
          <>
            <button
              onClick={() => handleStatusChange('completed')}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-green-50 text-green-700 border border-green-200 rounded-lg hover:bg-green-100"
            >
              <CheckCircle size={13} /> 通过审核
            </button>
            <button
              onClick={() => handleStatusChange('failed')}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-red-50 text-red-700 border border-red-200 rounded-lg hover:bg-red-100"
            >
              <XCircle size={13} /> 驳回
            </button>
          </>
        )}
        {task.status === 'failed' && (
          <button
            onClick={() => handleStatusChange('pending')}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-50 text-blue-700 border border-blue-200 rounded-lg hover:bg-blue-100"
          >
            <RefreshCw size={13} /> 重试
          </button>
        )}
        {task.status === 'pending' && (
          <button
            onClick={() => handleStatusChange('processing')}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-orange-50 text-orange-700 border border-orange-200 rounded-lg hover:bg-orange-100"
          >
            启动任务
          </button>
        )}

        {/* 日志展开 */}
        {task.logs.length > 0 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="ml-auto flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600"
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {expanded ? '收起日志' : '查看日志'}
          </button>
        )}
      </div>

      {/* 日志 */}
      {expanded && task.logs.length > 0 && (
        <div className="mt-3 bg-gray-900 rounded-lg p-3 font-mono text-xs space-y-1 max-h-48 overflow-y-auto">
          {task.logs.map((log, i) => (
            <div key={i} className={`flex gap-2 ${LOG_LEVEL_COLOR[log.level] ?? 'text-gray-300'}`}>
              <span className="text-gray-500 flex-shrink-0">[{log.time}]</span>
              <span className="uppercase font-bold flex-shrink-0">[{log.level}]</span>
              <span className="text-gray-300">{log.msg}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const STATUS_TABS = ['全部', '处理中', '待审核', '失败', '已完成', '待处理'] as const;
type StatusTab = typeof STATUS_TABS[number];

const TAB_STATUS_MAP: Record<StatusTab, string | null> = {
  '全部': null,
  '处理中': 'processing',
  '待审核': 'reviewing',
  '失败': 'failed',
  '已完成': 'completed',
  '待处理': 'pending',
};

export function ProcessWorkbenchPage() {
  const { state } = useAppStore();
  const [tab, setTab] = useState<StatusTab>('全部');

  const filtered = state.processTasks.filter((t) => {
    const statusFilter = TAB_STATUS_MAP[tab];
    return statusFilter === null || t.status === statusFilter;
  });

  return (
    <div className="p-6 space-y-4">
      {/* 头部 */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">处理工作台</h1>
        <p className="text-sm text-gray-500 mt-0.5">共 {state.processTasks.length} 个任务</p>
      </div>

      {/* Tab 筛选 */}
      <div className="flex bg-gray-100 rounded-lg p-1 gap-0.5 w-fit">
        {STATUS_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              tab === t ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            {t}
            <span className="ml-1.5 text-xs opacity-60">
              ({state.processTasks.filter((x) => {
                const s = TAB_STATUS_MAP[t];
                return s === null || x.status === s;
              }).length})
            </span>
          </button>
        ))}
      </div>

      {/* 任务列表 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {filtered.length === 0 && (
          <div className="col-span-2 text-center py-12 text-gray-400">暂无任务</div>
        )}
        {filtered.map((task) => (
          <TaskCard key={task.id} task={task} />
        ))}
      </div>
    </div>
  );
}
