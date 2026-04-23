import type { Material } from '../../store/types';

/**
 * ParseTask 接口定义（对齐后端 db-server.mjs）
 */
export interface ParseTask {
  id: string;
  materialId?: string | number;
  engine?: string;
  stage?: string;
  state?: string;
  progress?: number;
  message?: string;
  errorMessage?: string | null;
  createdAt?: string;
  updatedAt?: string;
  completedAt?: string | null;
  retryOf?: string | null;
}

/**
 * 任务展示桶类型
 */
export type TaskBucket = 'queued' | 'processing' | 'reviewing' | 'completed' | 'failed' | 'canceled' | 'unknown';

/**
 * 根据任务状态派生展示桶 (PRD v0.4 §6.3)
 */
export function deriveTaskBucket(state: string | undefined): TaskBucket {
  if (!state) return 'unknown';
  
  switch (state) {
    case 'uploading':
    case 'pending':
    case 'ai-pending':
      return 'queued';
    case 'running':
    case 'result-store':
    case 'ai-running':
      return 'processing';
    case 'review-pending':
      return 'reviewing';
    case 'completed':
      return 'completed';
    case 'failed':
      return 'failed';
    case 'canceled':
      return 'canceled';
    default:
      return 'unknown';
  }
}

/**
 * 活跃任务状态集合 (PRD v0.4 修订建议 §9.1)
 */
const ACTIVE_STATES = new Set([
  'pending',
  'running',
  'result-store',
  'ai-pending',
  'ai-running',
  'review-pending'
]);

/**
 * 从任务列表中找到属于特定素材的“当前任务”
 * 规则：
 * 1. 优先返回 active 任务
 * 2. 若无 active 任务，返回最近更新的任务 (updatedAt || createdAt)
 */
export function deriveCurrentTask(materialId: string | number, tasks: ParseTask[]): ParseTask | null {
  const myTasks = tasks.filter(t => String(t.materialId) === String(materialId));
  if (myTasks.length === 0) return null;

  // 1. 寻找 active 任务
  const activeTask = myTasks.find(t => ACTIVE_STATES.has(t.state || ''));
  if (activeTask) return activeTask;

  // 2. 寻找最近的任务
  return myTasks.sort((a, b) => {
    const timeA = new Date(a.updatedAt || a.createdAt || 0).getTime();
    const timeB = new Date(b.updatedAt || b.createdAt || 0).getTime();
    return timeB - timeA;
  })[0];
}

/**
 * 派生素材的任务视图视图 (P0 收口任务 1)
 */
export interface MaterialTaskView {
  materialId: string;
  title: string;
  fileName?: string;
  currentTask: ParseTask | null;
  latestTask: ParseTask | null;
  taskState?: string;
  bucket: TaskBucket;
  displayStatus: string;
  failureMessage?: string;
  taskUrl?: string;
  hasStateDrift: boolean;
  driftReason?: string;
}

const STATE_LABELS: Record<string, string> = {
  uploading: '上传中',
  pending: '等待中',
  running: '解析中',
  'result-store': '产物落库',
  'ai-pending': '等待 AI',
  'ai-running': 'AI 分析中',
  'review-pending': '待审核',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
};

export function deriveMaterialTaskView(material: Material, tasks: ParseTask[]): MaterialTaskView {
  const currentTask = deriveCurrentTask(material.id, tasks);
  const bucket = deriveTaskBucket(currentTask?.state);
  
  // 基础信息
  const view: MaterialTaskView = {
    materialId: String(material.id),
    title: material.title, // 优先使用 Material.title
    fileName: material.metadata?.fileName,
    currentTask,
    latestTask: currentTask,
    taskState: currentTask?.state,
    bucket,
    displayStatus: STATE_LABELS[currentTask?.state || ''] || (currentTask ? '未知' : '待处理'),
    failureMessage: currentTask?.errorMessage || currentTask?.message,
    taskUrl: currentTask ? `/tasks/${currentTask.id}` : undefined,
    hasStateDrift: false,
  };

  // 状态漂移/需审计判断
  if (currentTask) {
    const ts = currentTask.state;
    // 1. 任务失败但素材仍显示处理中
    if ((ts === 'failed' || ts === 'canceled') && material.status === 'processing') {
      view.hasStateDrift = true;
      view.driftReason = '任务已终止但素材状态未同步';
    }
    // 2. 任务已完成但素材缺少必要字段
    if (ts === 'completed' && !material.metadata?.markdownObjectName) {
      view.hasStateDrift = true;
      view.driftReason = '任务显示完成但缺少解析产物';
    }
  } else {
    // 无任务但素材处于 processing
    if (material.status === 'processing') {
      view.hasStateDrift = true;
      view.driftReason = '素材处于处理中但找不到关联任务';
    }
  }

  return view;
}
