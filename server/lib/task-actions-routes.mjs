/**
 * task-actions-routes.mjs — 任务动作 API 与 SSE 流
 *
 * 落实 PRD v0.4 §8.2 的"必须补齐"API：
 *   POST /tasks/:id/retry       → 将 failed 任务整体重跑：克隆新 ParseTask（retryOf 指向原任务）
 *   POST /tasks/:id/reparse     → 仅重跑解析阶段：保留原文件，当前任务置回 pending
 *   POST /tasks/:id/re-ai       → 仅重跑 AI 阶段：当前任务置回 ai-pending，原 AI Job 置失效
 *   POST /tasks/:id/cancel      → 将 pending/ai-pending/review-pending 任务置为 canceled
 *   POST /tasks/:id/review      → 人工审核：接受修正后的元数据，写回 Material 并置 completed
 *   POST /tasks/batch/retry     → 批量重试 failed 任务
 *   GET  /tasks/stream          → SSE：实时推送任务状态变更与事件日志
 *
 * 所有写动作都会：
 *   1) 通过 db-server REST 更新状态；
 *   2) 调用 logTaskEvent 写入 taskEvents；
 *   3) 通过事件总线 emit('task-update', ...) 广播，使订阅者（SSE）即时感知。
 *
 * v0.4.1 防护增强：
 *   - reparse/retry/re-ai 执行前必须通过资源前置校验（Material 存在 + MinIO 原文件存在）
 *   - 校验失败返回 409 Conflict，不得修改任务状态
 *   - 杜绝"已完成任务因 Reparse 降级为失败"的数据污染问题
 */

import { taskEventBus } from './task-events-bus.mjs';
import { logTaskEvent } from '../services/logging/task-events.mjs';

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';

// ─── db-server REST 小封装 ───────────────────────────────────
async function dbGet(path) {
  const resp = await fetch(`${DB_BASE_URL}${path}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`GET ${path} failed: HTTP ${resp.status}`);
  return await resp.json();
}
async function dbPost(path, body) {
  const resp = await fetch(`${DB_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`POST ${path} failed: HTTP ${resp.status}`);
  return await resp.json().catch(() => ({}));
}
async function dbPatch(path, body) {
  const resp = await fetch(`${DB_BASE_URL}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`PATCH ${path} failed: HTTP ${resp.status}`);
  return await resp.json().catch(() => ({}));
}

/**
 * 资源前置校验：检查任务关联的 Material 和 MinIO 文件是否存在
 * @param {Object} task - ParseTask 对象
 * @param {Object} deps - 依赖注入对象 { getMinioClient, getMinioBucket, getStorageBackend, getParsedBucket }
 * @param {Object} options - 校验选项 { needOriginal: boolean, needMarkdown: boolean }
 * @returns {{ ok: boolean, reason?: string, materialMissing?: boolean, originalMissing?: boolean, markdownMissing?: boolean }}
 */
async function validateResources(task, deps, options = {}) {
  const { needOriginal = true, needMarkdown = false } = options;
  const result = { ok: true };

  // 1) Material 存在性校验
  if (!task.materialId) {
    result.ok = false;
    result.reason = '任务缺少 materialId，无法定位关联资料';
    result.materialMissing = true;
    return result;
  }
  const material = await dbGet(`/materials/${encodeURIComponent(task.materialId)}`);
  if (!material) {
    result.ok = false;
    result.reason = `关联的 Material (${task.materialId}) 已被删除，无法重跑。请重新上传文件创建新任务`;
    result.materialMissing = true;
    return result;
  }

  // 2) 原始文件（MinIO objectName）存在性校验
  if (needOriginal && deps.getStorageBackend() === 'minio') {
    const objectName = material.metadata?.objectName;
    if (!objectName) {
      result.ok = false;
      result.reason = `Material (${task.materialId}) 缺少 objectName，原始文件路径已丢失`;
      result.originalMissing = true;
      return result;
    }
    try {
      const minio = deps.getMinioClient();
      const bucket = deps.getMinioBucket();
      await minio.statObject(bucket, objectName);
    } catch (e) {
      result.ok = false;
      result.reason = `原始文件 (${material.metadata.objectName}) 在 MinIO 中不存在，无法重新解析。请重新上传文件`;
      result.originalMissing = true;
      return result;
    }
  }

  // 3) Markdown 产物存在性校验（Re-AI 需要）
  if (needMarkdown && deps.getStorageBackend() === 'minio') {
    const mdObjectName = material.metadata?.markdownObjectName || task.metadata?.markdownObjectName;
    if (!mdObjectName) {
      result.ok = false;
      result.reason = '缺少 Markdown 产物路径，无法重跑 AI 识别。请先执行 Reparse';
      result.markdownMissing = true;
      return result;
    }
    try {
      const minio = deps.getMinioClient();
      const parsedBucket = deps.getParsedBucket();
      await minio.statObject(parsedBucket, mdObjectName);
    } catch (e) {
      result.ok = false;
      result.reason = `Markdown 产物 (${mdObjectName}) 在 MinIO 中不存在，无法重跑 AI 识别。请先执行 Reparse`;
      result.markdownMissing = true;
      return result;
    }
  }

  return result;
}

async function emitAndLog({ taskId, taskType = 'parse', level = 'info', event, message, update = {}, payload = {} }) {
  await logTaskEvent({ taskId, taskType, level, event, message, payload });
  try {
    taskEventBus.emit('task-update', {
      taskId,
      event,
      level,
      update,
      at: new Date().toISOString(),
    });
  } catch (e) {
    console.warn(`[task-actions] eventBus emit failed: ${e.message}`);
  }
}

function newTaskId() {
  return `task-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
}

const NEW_RUN_METADATA_CLEAR_KEYS = [
  'mineruTaskId',
  'mineruStatus',
  'mineruSubmittedAt',
  'mineruQueuedAhead',
  'mineruStartedAt',
  'mineruLastStatusAt',
  'mineruObservedProgress',
  'localTimeoutOccurred',
  'localTimeoutAt',
  '_synthetic_warn',
  '_synthetic_warn_msg',
  'submitRetries',
  'lastSubmitError',
  'recoveredFromMisjudgedFailed',
  'previousState',
  'previousErrorMessage',
  'recoveredAt',
  'markdownObjectName',
  'parsedPrefix',
  'parsedFilesCount',
  'artifactManifestObjectName',
  'zipObjectName',
  'artifactIncomplete',
  'parsedAt',
  'aiJobId',
  'emptyMarkdownRetryAttempted',
  'emptyMarkdownRetryMineruTaskId',
  'emptyMarkdownRetryError',
  'emptyMarkdownRetryProfile',
];

export function buildMetadataForNewParseRun(metadata = {}, extra = {}) {
  const next = { ...(metadata || {}) };
  for (const key of NEW_RUN_METADATA_CLEAR_KEYS) {
    delete next[key];
  }
  return {
    ...next,
    ...extra,
  };
}

// ─── 核心动作实现 ────────────────────────────────────────────

/**
 * retry: failed → 克隆出新 ParseTask，指向原任务
 * 成功后：原任务仍保留为 failed（可审计），新任务进入 pending
 * @param {Object} task - ParseTask 对象
 * @param {Object} deps - 依赖注入对象（用于资源校验）
 * @returns {Object} 克隆的新任务
 */
async function retryTask(task, deps) {
  if (task.state !== 'failed') {
    throw new Error(`Only failed tasks can be retried (current: ${task.state})`);
  }
  // 前置校验：Retry 需要原始文件存在
  const validation = await validateResources(task, deps, { needOriginal: true, needMarkdown: false });
  if (!validation.ok) {
    const err = new Error(validation.reason);
    err.statusCode = 409;
    err.resourceCheck = validation;
    throw err;
  }
  const newId = newTaskId();
  const clone = {
    ...task,
    id: newId,
    state: 'pending',
    stage: 'upload',
    progress: 0,
    message: `Retry of ${task.id}`,
    errorMessage: null,
    retryOf: task.id,
    aiJobId: null,
    metadata: buildMetadataForNewParseRun(task.metadata, {
      retryOf: task.id,
      aiJobId: null,
      submitRetries: 0,
      manualRetryRequestedAt: new Date().toISOString(),
    }),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    completedAt: null,
  };
  await dbPost('/tasks', clone);
  await emitAndLog({
    taskId: newId,
    event: 'retry-requested',
    message: `由任务 ${task.id} 克隆而来，重新进入 pending`,
    update: { state: 'pending' },
    payload: { retryOf: task.id },
  });
  return clone;
}

/**
 * reparse: 将当前任务从 failed/completed/review-pending 置回 pending
 * 适用于希望"同一任务 ID 重新解析"的场景，保留任务血缘
 * 前置校验：Material 必须存在且 MinIO 原始对象必须存在，否则返回 409 不修改状态
 * @param {Object} task - ParseTask 对象
 * @param {Object} deps - 依赖注入对象（用于资源校验）
 * @returns {Object} 更新后的任务
 */
async function reparseTask(task, deps) {
  const allowed = new Set(['failed', 'completed', 'review-pending', 'canceled']);
  if (!allowed.has(task.state)) {
    throw new Error(`Task state ${task.state} cannot be reparsed`);
  }
  // 前置校验：Reparse 需要原始文件存在
  const validation = await validateResources(task, deps, { needOriginal: true, needMarkdown: false });
  if (!validation.ok) {
    const err = new Error(validation.reason);
    err.statusCode = 409;
    err.resourceCheck = validation;
    throw err;
  }
  const update = {
    state: 'pending',
    stage: 'upload',
    progress: 0,
    message: '用户发起 Reparse，任务已置回 pending',
    errorMessage: null,
    metadata: buildMetadataForNewParseRun(task.metadata, {
      reparseOf: task.id,
      submitRetries: 0,
      manualReparseRequestedAt: new Date().toISOString(),
    }),
    updatedAt: new Date().toISOString(),
    completedAt: null,
  };
  await dbPatch(`/tasks/${encodeURIComponent(task.id)}`, update);
  await emitAndLog({
    taskId: task.id,
    event: 'reparse-requested',
    message: update.message,
    update,
  });
  return { ...task, ...update };
}

/**
 * re-ai: 仅重跑 AI 阶段
 *   - 将当前 aiJobId 对应的 Job 置为 failed（若存在且非终态）
 *   - ParseTask 状态置回 ai-pending
 *   - 下一轮 tick 由 task-worker/ai-worker 自动重新创建 AI Job
 *
 * 前置：任务已产出 markdown（metadata.markdownObjectName 应存在）
 * 前置校验：Material 必须存在且 Markdown 产物必须存在，否则返回 409 不修改状态
 * @param {Object} task - ParseTask 对象
 * @param {Object} deps - 依赖注入对象（用于资源校验）
 * @returns {Object} 更新后的任务
 */
async function reAiTask(task, deps) {
  const allowed = new Set(['completed', 'review-pending', 'failed']);
  if (!allowed.has(task.state)) {
    throw new Error(`Task state ${task.state} cannot trigger Re-AI`);
  }
  // 前置校验：Re-AI 需要 Material 存在 + Markdown 产物存在
  const validation = await validateResources(task, deps, { needOriginal: false, needMarkdown: true });
  if (!validation.ok) {
    const err = new Error(validation.reason);
    err.statusCode = 409;
    err.resourceCheck = validation;
    throw err;
  }
  // 让旧 AI Job 失效（不阻塞主流程）
  if (task.aiJobId) {
    try {
      await dbPatch(`/ai-metadata-jobs/${encodeURIComponent(task.aiJobId)}`, {
        state: 'failed',
        message: '被 Re-AI 动作标记为失效',
        updatedAt: new Date().toISOString(),
      });
    } catch (e) {
      console.warn(`[task-actions] mark old AI job failed error: ${e.message}`);
    }
  }
  const update = {
    state: 'ai-pending',
    stage: 'ai',
    progress: 80,
    message: '用户发起 Re-AI，等待 AI Worker 拾取',
    errorMessage: null,
    aiJobId: null,
    metadata: { ...(task.metadata || {}), aiJobId: null },
    updatedAt: new Date().toISOString(),
  };
  await dbPatch(`/tasks/${encodeURIComponent(task.id)}`, update);
  await emitAndLog({
    taskId: task.id,
    event: 're-ai-requested',
    message: update.message,
    update,
  });
  return { ...task, ...update };
}

/**
 * cancel: 将活跃状态任务置为 canceled
 */
async function cancelTask(task) {
  // Check if task can be canceled
  const isCancellable = ['pending', 'running', 'ai-pending', 'ai-running', 'review-pending', 'result-store'].includes(task.state) ||
                        ['mineru-queued', 'mineru-processing', 'submit-failed-retryable', 'result-fetching'].includes(task.stage);
                        
  if (!isCancellable) {
    if (!(task.state === 'failed' && task.stage === 'submit-failed-retryable')) {
      throw new Error(`Task state ${task.state} (stage: ${task.stage}) cannot be canceled directly`);
    }
  }

  const mineruTaskId = task.metadata?.mineruTaskId;
  let externalMineruStateAtCancel = undefined;
  let externalMineruStateUnknown = undefined;

  if (mineruTaskId) {
    const localEndpointRaw = task.optionsSnapshot?.localEndpoint;
    if (localEndpointRaw) {
      let localEndpoint = localEndpointRaw;
      if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
        localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
      }
      localEndpoint = localEndpoint.replace(/\/+$/, '');
      try {
        const tRes = await fetch(`${localEndpoint}/tasks/${mineruTaskId}`, { signal: AbortSignal.timeout(3000) });
        if (tRes.ok) {
          const mineruData = await tRes.json();
          externalMineruStateAtCancel = String(mineruData.status || mineruData.state || '').toLowerCase();
        } else {
          externalMineruStateUnknown = true;
        }
      } catch (e) {
        externalMineruStateUnknown = true;
      }
    } else {
      externalMineruStateUnknown = true;
    }
  }

  const update = {
    state: 'canceled',
    stage: 'canceled',
    message: '用户已取消 / 测试终止',
    updatedAt: new Date().toISOString(),
    completedAt: new Date().toISOString(),
    metadata: {
      ...(task.metadata || {}),
      canceledAt: new Date().toISOString(),
      canceledBy: 'user'
    }
  };

  if (mineruTaskId) {
    update.metadata.externalMineruTaskId = mineruTaskId;
    if (externalMineruStateAtCancel !== undefined) {
      update.metadata.externalMineruStateAtCancel = externalMineruStateAtCancel;
    }
    if (externalMineruStateUnknown !== undefined) {
      update.metadata.externalMineruStateUnknown = externalMineruStateUnknown;
    }
    update.metadata.mineruStatus = 'canceled';
  }

  await dbPatch(`/tasks/${encodeURIComponent(task.id)}`, update);

  if (task.materialId) {
    try {
      const material = await dbGet(`/materials/${encodeURIComponent(task.materialId)}`);
      if (material) {
        const parsedFilesCount = material.metadata?.parsedFilesCount || task.metadata?.parsedFilesCount || 0;
        if (parsedFilesCount > 0) {
          await dbPatch(`/materials/${encodeURIComponent(task.materialId)}`, {
            metadata: {
              ...(material.metadata || {}),
              canceledTaskAt: new Date().toISOString(),
              canceledTaskId: task.id
            }
          });
        } else {
          await dbPatch(`/materials/${encodeURIComponent(task.materialId)}`, {
            status: 'canceled',
            mineruStatus: 'canceled',
            metadata: {
              ...(material.metadata || {}),
              mineruStatus: 'canceled',
              processingMsg: '用户已取消 / 测试终止',
              processingStage: 'canceled'
            }
          });
        }
      }
    } catch (e) {
      console.warn(`[task-actions] cancel backfill material failed: ${e.message}`);
    }
  }

  await emitAndLog({
    taskId: task.id,
    level: 'warn',
    event: 'task-canceled',
    message: update.message,
    update,
  });
  return { ...task, ...update };
}

/**
 * review: 人工审核通过，写回 Material.metadata 并将任务置为 completed
 * body: { metadata: Object, notes?: string }
 */
async function reviewTask(task, body) {
  const allowed = new Set(['review-pending', 'completed']);
  if (!allowed.has(task.state)) {
    throw new Error(`Task state ${task.state} cannot be reviewed`);
  }
  const metadata = body?.metadata || {};
  if (task.materialId) {
    try {
      await dbPatch(`/materials/${encodeURIComponent(task.materialId)}`, {
        aiStatus: 'analyzed',
        updateTime: Date.now(),
        metadata: {
          ...metadata,
          aiJobId: task.aiJobId,
          reviewedAt: new Date().toISOString(),
          reviewer: body?.reviewer || 'operator',
        },
      });
    } catch (e) {
      console.warn(`[task-actions] review backfill material failed: ${e.message}`);
    }
  }
  const update = {
    state: 'completed',
    stage: 'done',
    progress: 100,
    message: body?.notes || '审核通过',
    metadata: { ...(task.metadata || {}), ...metadata, reviewedAt: new Date().toISOString() },
    updatedAt: new Date().toISOString(),
    completedAt: new Date().toISOString(),
  };
  await dbPatch(`/tasks/${encodeURIComponent(task.id)}`, update);
  await emitAndLog({
    taskId: task.id,
    event: 'review-confirmed',
    message: update.message,
    update,
    payload: { reviewer: body?.reviewer || 'operator' },
  });
  return { ...task, ...update };
}

// ─── 路由注册 ────────────────────────────────────────────────

/**
 * 注册任务动作路由
 * @param {Object} app - Express 应用实例
 * @param {Object} deps - 依赖注入对象
 * @param {Function} deps.getMinioClient - 获取 MinIO 客户端实例
 * @param {Function} deps.getMinioBucket - 获取原始资料桶名
 * @param {Function} deps.getStorageBackend - 获取存储后端类型 ('minio' | 'tmpfiles')
 * @param {Function} deps.getParsedBucket - 获取解析产物桶名
 */
export function registerTaskActionRoutes(app, deps = {}) {
  if (!app) throw new Error('registerTaskActionRoutes requires an express app');

  // 构建 deps 默认值（兼容无 MinIO 环境）
  const safeDeps = {
    getMinioClient: deps.getMinioClient || (() => null),
    getMinioBucket: deps.getMinioBucket || (() => 'eduassets'),
    getStorageBackend: deps.getStorageBackend || (() => 'tmpfiles'),
    getParsedBucket: deps.getParsedBucket || (() => 'eduassets-parsed'),
  };

  async function loadTask(req, res) {
    const task = await dbGet(`/tasks/${encodeURIComponent(req.params.id)}`);
    if (!task) {
      res.status(404).json({ error: `task not found: ${req.params.id}` });
      return null;
    }
    return task;
  }

  /**
   * 统一错误响应：区分资源校验失败（409）和业务逻辑错误（400）
   */
  function handleActionError(res, err) {
    const status = err.statusCode || 400;
    const body = { error: err.message };
    if (err.resourceCheck) {
      body.resourceCheck = err.resourceCheck;
    }
    res.status(status).json(body);
  }
  
  // batch retry (MUST be before :id routes)
  app.post('/tasks/batch/retry', async (req, res) => {
    try {
      const ids = Array.isArray(req.body?.ids) ? req.body.ids : [];
      if (ids.length === 0) {
        res.status(400).json({ error: '缺少 ids 数组' });
        return;
      }
      const results = [];
      for (const id of ids) {
        try {
          const task = await dbGet(`/tasks/${encodeURIComponent(id)}`);
          if (!task) {
            results.push({ id, ok: false, error: 'not found' });
            continue;
          }
          const newTask = await retryTask(task, safeDeps);
          results.push({ id, ok: true, newTaskId: newTask.id });
        } catch (e) {
          results.push({ id, ok: false, error: e.message, resourceCheck: e.resourceCheck || undefined });
        }
      }
      res.json({ ok: true, results });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // retry
  app.post('/tasks/:id/retry', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const newTask = await retryTask(task, safeDeps);
      res.json({ ok: true, taskId: newTask.id, retryOf: task.id });
    } catch (err) {
      handleActionError(res, err);
    }
  });

  // reparse
  app.post('/tasks/:id/reparse', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const updated = await reparseTask(task, safeDeps);
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      handleActionError(res, err);
    }
  });

  // re-ai
  app.post('/tasks/:id/re-ai', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const updated = await reAiTask(task, safeDeps);
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      handleActionError(res, err);
    }
  });

  // cancel
  app.post('/tasks/:id/cancel', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const { dryRun } = req.body || {};

      if (dryRun) {
        const isCancellable = ['pending', 'running', 'ai-pending', 'ai-running', 'review-pending', 'result-store'].includes(task.state) ||
                              ['mineru-queued', 'mineru-processing', 'submit-failed-retryable', 'result-fetching'].includes(task.stage) ||
                              (task.state === 'failed' && task.stage === 'submit-failed-retryable');
        
        let materialHasParsedFiles = false;
        if (task.materialId) {
          try {
            const material = await dbGet(`/materials/${encodeURIComponent(task.materialId)}`);
            if (material) {
              const parsedFilesCount = material.metadata?.parsedFilesCount || task.metadata?.parsedFilesCount || 0;
              if (parsedFilesCount > 0) materialHasParsedFiles = true;
            }
          } catch (e) { /* ignore */ }
        }

        return res.json({
          ok: true,
          dryRun: true,
          taskId: task.id,
          summary: {
            cancellable: isCancellable,
            state: task.state,
            stage: task.stage,
            hasMineruTaskId: !!task.metadata?.mineruTaskId,
            willUpdateMaterial: !!task.materialId,
            materialHasParsedFiles,
            externalMineruStateUnknown: true
          }
        });
      }

      const updated = await cancelTask(task);
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      handleActionError(res, err);
    }
  });

  // cancel-all-live
  app.post('/tasks/cancel-all-live', async (req, res) => {
    try {
      const { dryRun } = req.body || {};
      const allTasks = await dbGet('/tasks') || [];
      const liveTasks = allTasks.filter(t => {
        if (['review-pending', 'completed', 'canceled', 'done'].includes(t.state)) return false;
        if ((t.state === 'ai-pending' || t.state === 'review-pending') && (t.metadata?.parsedFilesCount > 0 || t.parsedFilesCount > 0)) return false;

        return ['pending', 'running', 'ai-running'].includes(t.state) ||
               ['mineru-queued', 'mineru-processing', 'result-fetching', 'submit-failed-retryable'].includes(t.stage) ||
               (t.state === 'failed' && t.stage === 'submit-failed-retryable');
      });

      let mineruApiUnreachableCount = 0;
      let mineruTaskIdCount = 0;
      let pendingCount = 0;
      let runningCount = 0;

      for (const t of liveTasks) {
        if (t.state === 'pending' || t.state === 'ai-pending' || t.state === 'review-pending') pendingCount++;
        else runningCount++;
        if (t.metadata?.mineruTaskId) mineruTaskIdCount++;
        if (t.stage === 'mineru-unreachable' || t.metadata?.mineruApiUnreachableCount > 0) mineruApiUnreachableCount++;
      }

      if (dryRun) {
        res.json({
          ok: true,
          summary: {
            totalToCancel: liveTasks.length,
            pendingCount,
            runningCount,
            mineruTaskIdCount,
            mineruApiUnreachableCount
          },
          affectedTaskIds: liveTasks.map(t => t.id)
        });
        return;
      }

      const results = [];
      for (const t of liveTasks) {
        try {
          await cancelTask(t);
          results.push({ id: t.id, ok: true });
        } catch (e) {
          results.push({ id: t.id, ok: false, error: e.message });
        }
      }

      res.json({
        ok: true,
        summary: { totalCanceled: results.filter(r => r.ok).length, totalFailed: results.filter(r => !r.ok).length },
        results
      });
    } catch (err) {
      handleActionError(res, err);
    }
  });

  // reset-test-env
  app.post('/ops/reset-test-env', async (req, res) => {
    try {
      const { dryRun, force } = req.body || {};
      
      function toCollectionArray(value) {
        if (Array.isArray(value)) return value;
        if (value && typeof value === 'object') return Object.values(value);
        return [];
      }

      const [materialsRaw, tasksRaw, taskEventsRaw, aiJobsRaw, assetDetailsRaw] = await Promise.all([
        dbGet('/materials').catch(() => []),
        dbGet('/tasks').catch(() => []),
        dbGet('/task-events').catch(() => []),
        dbGet('/ai-metadata-jobs').catch(() => []),
        dbGet('/asset-details').catch(() => [])
      ]);

      const materials = toCollectionArray(materialsRaw);
      const tasks = toCollectionArray(tasksRaw);
      const taskEvents = toCollectionArray(taskEventsRaw);
      const aiJobs = toCollectionArray(aiJobsRaw);
      const assetDetails = toCollectionArray(assetDetailsRaw);

      const runningTasks = tasks.filter(t => ['running', 'ai-running', 'result-store'].includes(t.state) || ['mineru-processing', 'mineru-queued'].includes(t.stage));
      const completedMaterials = materials.filter(m => m.status === 'completed');

      if (!force && runningTasks.length > 0 && !dryRun) {
        return res.status(400).json({ error: '存在运行中的任务，请先勾选 force 以强制清理。' });
      }

      let minioOriginalsCountEstimate = 0;
      let minioParsedCountEstimate = 0;
      const minioOriginalObjects = [];
      const minioParsedObjects = [];

      materials.forEach(m => {
        if (m.metadata?.objectName) {
          minioOriginalsCountEstimate++;
          minioOriginalObjects.push(m.metadata.objectName);
        }
        if (m.metadata?.markdownObjectName || m.metadata?.zipObjectName) {
          minioParsedCountEstimate++;
          if (m.metadata?.markdownObjectName) minioParsedObjects.push(m.metadata.markdownObjectName);
          if (m.metadata?.zipObjectName) minioParsedObjects.push(m.metadata.zipObjectName);
        }
      });
      tasks.forEach(t => {
        if (t.metadata?.markdownObjectName || t.metadata?.zipObjectName) {
          minioParsedCountEstimate++;
          if (t.metadata?.markdownObjectName) minioParsedObjects.push(t.metadata.markdownObjectName);
          if (t.metadata?.zipObjectName) minioParsedObjects.push(t.metadata.zipObjectName);
        }
      });

      if (dryRun) {
        return res.json({
          ok: true,
          dryRun: true,
          summary: {
            deletedMaterials: materials.length,
            deletedTasks: tasks.length,
            deletedTaskEvents: taskEvents.length,
            deletedAiJobs: aiJobs.length,
            deletedAssetDetails: assetDetails.length,
            deletedMinioOriginals: minioOriginalsCountEstimate,
            deletedMinioParsed: minioParsedCountEstimate,
          }
        });
      }

      // EXECUTE
      if (force && runningTasks.length > 0) {
        for (const t of runningTasks) {
          try { await cancelTask(t); } catch (e) { /* ignore */ }
        }
      }
      
      const deleteCollection = async (collName, items) => {
        const ids = items.map(i => i.id).filter(Boolean);
        if (ids.length === 0) return { ok: true, deleted: 0 };
        try {
          const result = await fetch(`${DB_BASE_URL}/${collName}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids })
          });
          if (!result.ok) {
            return { ok: false, deleted: 0, error: `HTTP ${result.status}` };
          }
          return { ok: true, deleted: ids.length };
        } catch (e) {
          return { ok: false, deleted: 0, error: e.message };
        }
      };

      const [resMat, resTask, resEvent, resAi, resAsset] = await Promise.all([
        deleteCollection('materials', materials),
        deleteCollection('tasks', tasks),
        deleteCollection('task-events', taskEvents),
        deleteCollection('ai-metadata-jobs', aiJobs),
        deleteCollection('asset-details', assetDetails)
      ]);

      const details = {
        materials: resMat,
        tasks: resTask,
        taskEvents: resEvent,
        aiJobs: resAi,
        assetDetails: resAsset
      };

      const hasError = Object.values(details).some(r => !r.ok);

      // Delete MinIO objects
      let minioError = null;
      try {
        const minioClient = deps.getMinioClient ? deps.getMinioClient() : null;
        if (minioClient) {
          const rawBucket = deps.getMinioBucket ? deps.getMinioBucket() : 'eduassets';
          const parsedBucket = deps.getParsedBucket ? deps.getParsedBucket() : 'eduassets-parsed';
          
          if (minioOriginalObjects.length > 0) {
            await minioClient.removeObjects(rawBucket, minioOriginalObjects).catch(() => null);
          }
          if (minioParsedObjects.length > 0) {
            await minioClient.removeObjects(parsedBucket, minioParsedObjects).catch(() => null);
          }
        }
      } catch (e) {
        minioError = e.message;
        console.warn(`[task-actions] minio reset failed: ${e.message}`);
      }

      return res.json({
        ok: !hasError,
        dryRun: false,
        summary: {
          deletedMaterials: materials.length,
          deletedTasks: tasks.length,
          deletedTaskEvents: taskEvents.length,
          deletedAiJobs: aiJobs.length,
          deletedAssetDetails: assetDetails.length,
          deletedMinioOriginals: minioOriginalsCountEstimate,
          deletedMinioParsed: minioParsedCountEstimate,
        },
        details
      });
    } catch (err) {
      handleActionError(res, err);
    }
  });

  // review
  app.post('/tasks/:id/review', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const updated = await reviewTask(task, req.body || {});
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      handleActionError(res, err);
    }
  });


  // SSE：/tasks/stream
  app.get('/tasks/stream', (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no'); // 禁用 Nginx 缓冲
    res.flushHeaders?.();

    const taskIdFilter = typeof req.query?.taskId === 'string' ? req.query.taskId : null;

    // 连接建立即发一条 hello，便于前端确认通道可用
    res.write(`event: hello\n`);
    res.write(`data: ${JSON.stringify({ at: new Date().toISOString(), filter: taskIdFilter || null })}\n\n`);

    const onUpdate = (payload) => {
      if (taskIdFilter && payload.taskId !== taskIdFilter) return;
      try {
        res.write(`event: task-update\n`);
        res.write(`data: ${JSON.stringify(payload)}\n\n`);
      } catch (e) {
        // 忽略写失败，close 会清理
      }
    };
    taskEventBus.on('task-update', onUpdate);

    // 保活心跳（每 25s），避免 Nginx/中间件断流
    const heartbeat = setInterval(() => {
      try {
        res.write(`: ping ${Date.now()}\n\n`);
      } catch (e) {
        /* noop */
      }
    }, 25_000);

    req.on('close', () => {
      clearInterval(heartbeat);
      taskEventBus.removeListener('task-update', onUpdate);
    });
  });

  console.log('[upload-server] task-actions & SSE routes registered');
}
