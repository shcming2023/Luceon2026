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
  metadata?: Record<string, any>;
}

/**
 * 任务展示桶类型
 */
export type TaskBucket = 'queued' | 'processing' | 'reviewing' | 'completed' | 'failed' | 'canceled' | 'unknown';

/**
 * 根据任务状态派生展示桶 (PRD v0.4 §6.3)
 */
export function deriveTaskBucket(state: string | undefined, stage?: string): TaskBucket {
  if (!state) return 'unknown';

  if (state === 'running' && stage === 'mineru-queued') return 'queued';
  
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

export function deriveMaterialTaskView(
  material: Material | undefined,
  tasks: ParseTask[],
  options?: { tasksLoaded?: boolean }
): MaterialTaskView {
  // P0 防御：material 未定义时返回安全默认值
  if (!material) {
    return {
      materialId: 'unknown',
      title: '加载中...',
      currentTask: null,
      latestTask: null,
      taskState: undefined,
      bucket: 'unknown',
      displayStatus: '加载中...',
      hasStateDrift: false,
    };
  }

  const tasksLoaded = options?.tasksLoaded ?? true; // 默认认为已加载完成，除非显式传入 false
  const currentTask = deriveCurrentTask(material.id, tasks);
  const bucket = deriveTaskBucket(currentTask?.state, currentTask?.stage);
  
  // 基础信息
  const view: MaterialTaskView = {
    materialId: String(material.id),
    title: material.title, // 优先使用 Material.title
    fileName: material.metadata?.fileName,
    currentTask,
    latestTask: currentTask,
    taskState: currentTask?.state,
    bucket,
    displayStatus: currentTask?.stage === 'mineru-queued' ? 'MinerU 排队中' :
                   currentTask?.stage === 'mineru-processing' ? 'MinerU 正在解析' :
                   (STATE_LABELS[currentTask?.state || ''] || (currentTask ? '未知' : '待处理')),
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
    // P0/P1 修复：3. 任务已完成但 Material 元数据仍残留 processing
    if (ts === 'completed' && material.metadata?.mineruStatus === 'processing') {
      view.hasStateDrift = true;
      view.driftReason = 'Material 元数据残留 processing';
    }
  } else if (tasksLoaded) {
    // 只有在任务列表确实加载完成，且依然找不到任务时，才报告漂移
    // 无任务但素材处于 processing
    if (material.status === 'processing') {
      view.hasStateDrift = true;
      view.driftReason = '暂无关联任务 / 需审计';
      // 强制把显示状态改为需审计，避免显示“正在解析”
      view.displayStatus = '暂无关联任务 / 需审计';
    } else if (material.metadata?.mineruStatus === 'processing') {
      view.hasStateDrift = true;
      view.driftReason = '暂无关联任务 / 需审计';
      view.displayStatus = '暂无关联任务 / 需审计';
    } else {
      view.displayStatus = '暂无关联任务 / 需审计';
    }
  }

  return view;
}
