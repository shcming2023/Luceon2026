/**
 * batch-queue.mjs — 简化的并发控制器
 *
 * 原复杂队列已移除，现仅保留：
 * - 简单的信号量并发控制（确保同时提交给 MinerU 的任务不超过限制）
 * - 当前正在解析的任务追踪
 *
 * MinerU 自带内部队列（max_concurrent_requests=1），
 * 我们只需确保不超出其承受上限即可。
 */

// ─── Docker 环境检测 ────────────────────────────────────────────
import fs from 'fs';
const IS_DOCKER = fs.existsSync('/.dockerenv') || (process.env.DB_BASE_URL || '').includes('db-server');

/**
 * Docker 环境下自动将 localhost/127.0.0.1 替换为 host.docker.internal
 */
function dockerRewriteEndpoint(endpoint) {
  if (!IS_DOCKER || !endpoint) return endpoint;
  return endpoint
    .replace(/\/\/localhost([:\/])/g, '//host.docker.internal$1')
    .replace(/\/\/127\.0\.0\.1([:\/])/g, '//host.docker.internal$1');
}

// ─── 并发控制配置 ───────────────────────────────────────────────
const MAX_CONCURRENT_MINERU = Number(process.env.MAX_CONCURRENT_MINERU || 1); // MinerU 默认单并发
const MAX_CONCURRENT_AI = Number(process.env.MAX_CONCURRENT_AI || 2);

// ─── 状态追踪 ───────────────────────────────────────────────────
const concurrencyState = {
  // MinerU 解析任务
  mineruTasks: new Map(), // taskId -> { materialId, status, startTime }
  // AI 分析任务
  aiTasks: new Map(),     // taskId -> { materialId, status, startTime }
};

// ─── 获取 db-server 地址 ────────────────────────────────────────
let dbBaseUrl = 'http://localhost:8789';

export function initBatchQueue(deps) {
  if (deps?.dbBaseUrl) {
    dbBaseUrl = deps.dbBaseUrl;
  }
}

export function shutdown() {
  // 无需持久化，简化架构
  console.log('[batch-queue] shutdown (noop)');
}

// ─── 并发控制方法 ───────────────────────────────────────────────

export function canStartMineruTask() {
  const activeCount = Array.from(concurrencyState.mineruTasks.values())
    .filter(t => t.status === 'parsing').length;
  return activeCount < MAX_CONCURRENT_MINERU;
}

export function canStartAITask() {
  const activeCount = Array.from(concurrencyState.aiTasks.values())
    .filter(t => t.status === 'analyzing').length;
  return activeCount < MAX_CONCURRENT_AI;
}

export function registerMineruTask(taskId, materialId) {
  concurrencyState.mineruTasks.set(taskId, {
    materialId,
    status: 'parsing',
    startTime: Date.now(),
  });
  console.log(`[batch-queue] MinerU task registered: ${taskId} (material ${materialId})`);
}

export function registerAITask(taskId, materialId) {
  concurrencyState.aiTasks.set(taskId, {
    materialId,
    status: 'analyzing',
    startTime: Date.now(),
  });
  console.log(`[batch-queue] AI task registered: ${taskId} (material ${materialId})`);
}

export function updateMineruTaskStatus(taskId, status) {
  const task = concurrencyState.mineruTasks.get(taskId);
  if (task) {
    task.status = status;
    task.updatedAt = Date.now();
    console.log(`[batch-queue] MinerU task ${taskId} status: ${status}`);
  }
}

export function updateAITaskStatus(taskId, status) {
  const task = concurrencyState.aiTasks.get(taskId);
  if (task) {
    task.status = status;
    task.updatedAt = Date.now();
    console.log(`[batch-queue] AI task ${taskId} status: ${status}`);
  }
}

export function unregisterMineruTask(taskId) {
  concurrencyState.mineruTasks.delete(taskId);
  console.log(`[batch-queue] MinerU task unregistered: ${taskId}`);
}

export function unregisterAITask(taskId) {
  concurrencyState.aiTasks.delete(taskId);
  console.log(`[batch-queue] AI task unregistered: ${taskId}`);
}

export function getActiveMineruTasksCount() {
  return Array.from(concurrencyState.mineruTasks.values())
    .filter(t => t.status === 'parsing').length;
}

export function getActiveAITasksCount() {
  return Array.from(concurrencyState.aiTasks.values())
    .filter(t => t.status === 'analyzing').length;
}

export function getTaskStatus() {
  return {
    mineru: {
      active: getActiveMineruTasksCount(),
      max: MAX_CONCURRENT_MINERU,
      tasks: Array.from(concurrencyState.mineruTasks.entries()).map(([id, t]) => ({
        id,
        ...t,
      })),
    },
    ai: {
      active: getActiveAITasksCount(),
      max: MAX_CONCURRENT_AI,
      tasks: Array.from(concurrencyState.aiTasks.entries()).map(([id, t]) => ({
        id,
        ...t,
      })),
    },
    timestamp: Date.now(),
  };
}

// ─── 旧 API 兼容性（返回空/静态数据，防止前端报错）────────────────

export function getQueueStatus() {
  return {
    running: false,
    paused: false,
    autoMinerU: false,
    autoAI: false,
    total: 0,
    pending: 0,
    processing: 0,
    completed: 0,
    errors: 0,
    items: [],
    alerts: [],
    unreadAlerts: 0,
    memory: { usedRatio: 0, freeMB: 0, totalMB: 0, pressure: false },
    updatedAt: Date.now(),
    // 新增并发控制状态
    concurrency: getTaskStatus(),
  };
}

// 以下函数均为兼容性保留，返回空操作结果
export function addJobs() { return { ok: true, added: 0, rejected: [] }; }
export function startQueue() { return { ok: false, error: '队列已移除，请使用新的 /parse/mineru 端点' }; }
export function pauseQueue() { return { ok: false }; }
export function resumeQueue() { return { ok: false }; }
export function stopQueue() { return { ok: false }; }
export function cancelJob() { return { ok: false }; }
export function cancelCurrentJob() { return { ok: false }; }
export function retryFailed() { return { ok: false }; }
export function retryJob() { return { ok: false }; }
export async function removeJob() { return { ok: false }; }
export function patchJob() { return { ok: false }; }
export function reorderPending() { return { ok: false }; }
export function clearCompleted() { return { ok: false }; }
export async function clearAll() { return { ok: false }; }
export function readAlerts() { return { ok: false }; }
export async function recoverOrphanMaterials() { /* noop */ }
export async function restoreBatchQueue() { /* noop */ }
export function removeJobsByMaterialIds() { /* noop */ }

// ─── 导出配置工具 ───────────────────────────────────────────────

export { dockerRewriteEndpoint };
