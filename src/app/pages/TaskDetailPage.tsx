import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, RefreshCw, FileText, Clock, AlertTriangle, CheckCircle2, Loader2, XCircle, ChevronDown, ChevronRight, Brain } from 'lucide-react';
import { toast } from 'sonner';

/**
 * ParseTask 详情数据结构
 */
interface ParseTask {
  id: string;
  materialId?: string;
  engine?: string;
  stage?: string;
  state?: string;
  progress?: number;
  message?: string;
  errorMessage?: string;
  createdAt?: string;
  updatedAt?: string;
  completedAt?: string;
  optionsSnapshot?: Record<string, unknown>;
}

/**
 * TaskEvent 事件日志数据结构
 */
interface TaskEvent {
  id: string;
  taskId: string;
  taskType?: string;
  level?: string;
  event?: string;
  message?: string;
  payload?: Record<string, unknown>;
  createdAt?: string;
}

/**
 * AiMetadataJob 数据结构
 */
interface AiMetadataJob {
  id: string;
  materialId?: string;
  parseTaskId: string;
  state: string;
  progress?: number;
  providerId?: string;
  model?: string;
  inputMarkdownObjectName?: string;
  confidence?: number | null;
  needsReview?: boolean;
  result?: Record<string, unknown>;
  errorMessage?: string;
  createdAt?: string;
  updatedAt?: string;
}

/**
 * 根据状态返回对应的样式类名和图标
 * @param state - 任务状态字符串
 * @returns 包含 badgeClass 和 Icon 组件的对象
 */
function getStateStyle(state: string | undefined) {
  switch (state) {
    case 'running':
    case 'result-store':
      return { badgeClass: 'bg-blue-100 text-blue-700 border border-blue-200', Icon: Loader2, animate: true };
    case 'ai-pending':
    case 'success':
      return { badgeClass: 'bg-green-100 text-green-700', Icon: CheckCircle2, animate: false };
    case 'failed':
      return { badgeClass: 'bg-red-100 text-red-700', Icon: XCircle, animate: false };
    default:
      return { badgeClass: 'bg-slate-100 text-slate-700', Icon: Clock, animate: false };
  }
}

/**
 * 根据 AI Job 状态返回样式
 * @param state - AI Job 状态字符串
 * @returns 包含 badgeClass 的对象
 */
function getAiJobStateStyle(state: string | undefined) {
  switch (state) {
    case 'running':
      return { badgeClass: 'bg-purple-100 text-purple-700 border border-purple-200', icon: '⏳' };
    case 'succeeded':
      return { badgeClass: 'bg-green-100 text-green-700', icon: '✅' };
    case 'failed':
      return { badgeClass: 'bg-red-100 text-red-700', icon: '❌' };
    case 'pending':
    default:
      return { badgeClass: 'bg-amber-100 text-amber-700', icon: '🕐' };
  }
}

/**
 * 根据事件 level 返回时间线节点样式
 * @param level - 事件级别 (info / error / warn)
 * @returns 包含 dotClass 和 textClass 的对象
 */
function getEventStyle(level: string | undefined) {
  if (level === 'error') return { dotClass: 'bg-red-500', textClass: 'text-red-700' };
  if (level === 'warn') return { dotClass: 'bg-yellow-500', textClass: 'text-yellow-700' };
  return { dotClass: 'bg-blue-500', textClass: 'text-slate-700' };
}

/**
 * TaskDetailPage - 任务详情页
 *
 * 展示单个 ParseTask 的完整状态信息、关联 AI Metadata Job 和事件时间线。
 * 路由：/tasks/:id
 */
export function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [task, setTask] = useState<ParseTask | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [aiJobs, setAiJobs] = useState<AiMetadataJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [optionsExpanded, setOptionsExpanded] = useState(false);

  /**
   * 从后端加载任务详情、事件日志和关联 AI Jobs
   */
  const fetchData = async () => {
    if (!id) return;
    setLoading(true);
    setNotFound(false);
    try {
      const [taskRes, eventsRes, aiJobsRes] = await Promise.all([
        fetch(`/cms/__proxy/db/tasks/${encodeURIComponent(id)}`),
        fetch(`/cms/__proxy/db/task-events?taskId=${encodeURIComponent(id)}`),
        fetch(`/cms/__proxy/db/ai-metadata-jobs?parseTaskId=${encodeURIComponent(id)}`),
      ]);

      if (taskRes.status === 404) {
        setNotFound(true);
        setTask(null);
        setEvents([]);
        setAiJobs([]);
        return;
      }
      if (!taskRes.ok) throw new Error(`获取任务失败: HTTP ${taskRes.status}`);

      const taskData = await taskRes.json();
      setTask(taskData);

      if (eventsRes.ok) {
        const eventsData = await eventsRes.json();
        setEvents(Array.isArray(eventsData) ? eventsData : []);
      }

      if (aiJobsRes.ok) {
        const aiJobsData = await aiJobsRes.json();
        setAiJobs(Array.isArray(aiJobsData) ? aiJobsData : []);
      }
    } catch (err) {
      toast.error('加载任务详情失败', { description: String(err) });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [id]);

  // ─── 加载中 ──────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
          <p className="text-sm text-slate-500">加载任务详情...</p>
        </div>
      </div>
    );
  }

  // ─── 未找到 ──────────────────────────────────────────────────
  if (notFound || !task) {
    return (
      <div className="p-6 flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3 text-center">
          <AlertTriangle className="w-10 h-10 text-amber-400" />
          <h2 className="text-lg font-semibold text-slate-800">任务不存在</h2>
          <p className="text-sm text-slate-500">ID: {id}</p>
          <button
            onClick={() => navigate('/tasks')}
            className="mt-2 px-4 py-2 text-sm font-medium text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded-lg transition-colors"
          >
            ← 返回任务列表
          </button>
        </div>
      </div>
    );
  }

  // ─── 正常渲染 ────────────────────────────────────────────────
  const stateStyle = getStateStyle(task.state);

  return (
    <div className="p-6 h-full overflow-y-auto space-y-6">
      {/* ── 顶部导航栏 ────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/tasks')}
            className="p-2 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
            title="返回任务列表"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
              <FileText className="w-5 h-5 text-slate-400" />
              任务详情
            </h1>
            <p className="text-xs text-slate-400 mt-0.5 font-mono">{task.id}</p>
          </div>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 rounded-lg text-sm font-medium hover:bg-slate-50 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          刷新
        </button>
      </div>

      {/* ── 状态概览卡片 ──────────────────────────────────────── */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
          {/* 状态 */}
          <div>
            <p className="text-xs text-slate-400 mb-1.5 uppercase font-semibold tracking-wider">状态</p>
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-full ${stateStyle.badgeClass}`}>
              <stateStyle.Icon className={`w-3.5 h-3.5 ${stateStyle.animate ? 'animate-spin' : ''}`} />
              {task.state || 'pending'}
            </span>
          </div>
          {/* 阶段 */}
          <div>
            <p className="text-xs text-slate-400 mb-1.5 uppercase font-semibold tracking-wider">阶段</p>
            <p className="text-sm font-medium text-slate-800">{task.stage || '—'}</p>
          </div>
          {/* 引擎 */}
          <div>
            <p className="text-xs text-slate-400 mb-1.5 uppercase font-semibold tracking-wider">引擎</p>
            <span className="inline-flex items-center px-2 py-1 rounded bg-slate-100 text-slate-600 text-xs font-medium">
              {task.engine || 'pipeline'}
            </span>
          </div>
          {/* 进度 */}
          <div>
            <p className="text-xs text-slate-400 mb-1.5 uppercase font-semibold tracking-wider">进度</p>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${task.state === 'failed' ? 'bg-red-400' : 'bg-blue-500'}`}
                  style={{ width: `${task.progress || 0}%` }}
                />
              </div>
              <span className="text-xs font-medium text-slate-600 w-8 text-right">{task.progress || 0}%</span>
            </div>
          </div>
        </div>

        {/* 消息 */}
        {task.message && (
          <div className="mt-4 pt-4 border-t border-slate-100">
            <p className="text-xs text-slate-400 mb-1 uppercase font-semibold tracking-wider">消息</p>
            <p className="text-sm text-slate-700 break-words leading-relaxed">{task.message}</p>
          </div>
        )}

        {/* 错误信息 */}
        {task.errorMessage && (
          <div className="mt-3 p-3 bg-red-50 border border-red-100 rounded-md">
            <p className="text-xs font-semibold text-red-600 mb-1">错误详情</p>
            <p className="text-sm text-red-700 break-words">{task.errorMessage}</p>
          </div>
        )}
      </div>

      {/* ── 基础信息 ──────────────────────────────────────────── */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <h2 className="text-sm font-semibold text-slate-800 mb-3">基础信息</h2>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div className="flex justify-between">
            <dt className="text-slate-400">Material ID</dt>
            <dd className="text-slate-800 font-mono text-xs">{task.materialId || '—'}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-400">创建时间</dt>
            <dd className="text-slate-800">{task.createdAt ? new Date(task.createdAt).toLocaleString() : '—'}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-400">更新时间</dt>
            <dd className="text-slate-800">{task.updatedAt ? new Date(task.updatedAt).toLocaleString() : '—'}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-400">完成时间</dt>
            <dd className="text-slate-800">{task.completedAt ? new Date(task.completedAt).toLocaleString() : '—'}</dd>
          </div>
        </dl>
      </div>

      {/* ── AI Metadata Job 信息 ──────────────────────────────── */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <h2 className="text-sm font-semibold text-slate-800 mb-3 flex items-center gap-2">
          <Brain className="w-4 h-4 text-purple-500" />
          AI 元数据识别
        </h2>
        {aiJobs.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-4">
            {task.state === 'ai-pending' ? '等待创建 AI 任务...' : '暂无关联的 AI 任务'}
          </p>
        ) : (
          <div className="space-y-3">
            {aiJobs.map((job) => {
              const jobStyle = getAiJobStateStyle(job.state);
              return (
                <div
                  key={job.id}
                  className="border border-slate-100 rounded-lg p-4 bg-slate-50/50"
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-mono text-slate-500">{job.id}</span>
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-full ${jobStyle.badgeClass}`}>
                      {jobStyle.icon} {job.state}
                    </span>
                  </div>
                  <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-xs">
                    <div className="flex justify-between">
                      <dt className="text-slate-400">进度</dt>
                      <dd className="text-slate-700">{job.progress ?? 0}%</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">Provider</dt>
                      <dd className="text-slate-700">{job.providerId || '—'}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">Model</dt>
                      <dd className="text-slate-700">{job.model || '—'}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">置信度</dt>
                      <dd className="text-slate-700">
                        {job.confidence != null ? `${job.confidence}%` : '—'}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">需要审核</dt>
                      <dd className="text-slate-700">{job.needsReview ? '是' : '否'}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">Markdown 产物</dt>
                      <dd className="text-slate-700 font-mono truncate max-w-[200px]" title={job.inputMarkdownObjectName || ''}>
                        {job.inputMarkdownObjectName || '—'}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">创建时间</dt>
                      <dd className="text-slate-700">
                        {job.createdAt ? new Date(job.createdAt).toLocaleString() : '—'}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">更新时间</dt>
                      <dd className="text-slate-700">
                        {job.updatedAt ? new Date(job.updatedAt).toLocaleString() : '—'}
                      </dd>
                    </div>
                  </dl>

                  {/* AI 结果预览 */}
                  {job.result && (
                    <div className="mt-4 pt-3 border-t border-slate-100">
                      <p className="text-[10px] uppercase font-bold text-slate-400 mb-2 tracking-wider">识别结果预览 (JSON)</p>
                      <pre className="text-[10px] bg-slate-900 text-slate-300 p-3 rounded overflow-x-auto leading-relaxed max-h-48">
                        {JSON.stringify(job.result, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* 错误显示 */}
                  {job.errorMessage && (
                    <div className="mt-3 p-2 bg-red-50 border border-red-100 rounded text-[10px] text-red-600">
                      <strong>执行错误:</strong> {job.errorMessage}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── optionsSnapshot 折叠展示 ─────────────────────────── */}
      {task.optionsSnapshot && Object.keys(task.optionsSnapshot).length > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
          <button
            onClick={() => setOptionsExpanded(!optionsExpanded)}
            className="w-full flex items-center justify-between p-5 text-sm font-semibold text-slate-800 hover:bg-slate-50 transition-colors rounded-lg"
          >
            <span>配置快照 (optionsSnapshot)</span>
            {optionsExpanded
              ? <ChevronDown className="w-4 h-4 text-slate-400" />
              : <ChevronRight className="w-4 h-4 text-slate-400" />
            }
          </button>
          {optionsExpanded && (
            <div className="px-5 pb-5">
              <pre className="text-xs text-slate-600 bg-slate-50 border border-slate-100 rounded-md p-4 overflow-x-auto max-h-64 overflow-y-auto leading-relaxed">
                {JSON.stringify(task.optionsSnapshot, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* ── 事件时间线 ────────────────────────────────────────── */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <h2 className="text-sm font-semibold text-slate-800 mb-4">事件时间线</h2>
        {events.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-6">暂无事件记录</p>
        ) : (
          <div className="relative pl-6 space-y-0">
            {/* 垂直连接线 */}
            <div className="absolute left-[9px] top-2 bottom-2 w-px bg-slate-200" />

            {events.map((evt, idx) => {
              const evtStyle = getEventStyle(evt.level);
              return (
                <div key={evt.id || idx} className="relative pb-5 last:pb-0">
                  {/* 时间线圆点 */}
                  <div className={`absolute -left-6 top-1.5 w-[10px] h-[10px] rounded-full ring-2 ring-white ${evtStyle.dotClass}`} />

                  <div className="flex flex-col gap-0.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-slate-800">{evt.event || '—'}</span>
                      {evt.level === 'error' && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-red-100 text-red-600 rounded font-medium">ERROR</span>
                      )}
                      {evt.level === 'warn' && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded font-medium">WARN</span>
                      )}
                    </div>
                    <p className={`text-xs ${evtStyle.textClass} break-words`}>{evt.message || ''}</p>
                    <p className="text-[10px] text-slate-400 mt-0.5">
                      {evt.createdAt ? new Date(evt.createdAt).toLocaleString() : '—'}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
