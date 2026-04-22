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

// ─── 核心动作实现 ────────────────────────────────────────────

/**
 * retry: failed → 克隆出新 ParseTask，指向原任务
 * 成功后：原任务仍保留为 failed（可审计），新任务进入 pending
 */
async function retryTask(task) {
  if (task.state !== 'failed') {
    throw new Error(`Only failed tasks can be retried (current: ${task.state})`);
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
    metadata: { ...(task.metadata || {}), retryOf: task.id, aiJobId: null },
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
 */
async function reparseTask(task) {
  const allowed = new Set(['failed', 'completed', 'review-pending', 'canceled']);
  if (!allowed.has(task.state)) {
    throw new Error(`Task state ${task.state} cannot be reparsed`);
  }
  const update = {
    state: 'pending',
    stage: 'upload',
    progress: 0,
    message: '用户发起 Reparse，任务已置回 pending',
    errorMessage: null,
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
 */
async function reAiTask(task) {
  const allowed = new Set(['completed', 'review-pending', 'failed']);
  if (!allowed.has(task.state)) {
    throw new Error(`Task state ${task.state} cannot trigger Re-AI`);
  }
  if (!task.metadata?.markdownObjectName) {
    throw new Error('Task has no markdown product; Reparse first before Re-AI');
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
 * cancel: 将 pending/ai-pending/review-pending 置为 canceled
 * 对 running/ai-running 状态的任务，暂不做强制打断（v0.4 由 Worker 超时自愈接管）
 */
async function cancelTask(task) {
  const allowed = new Set(['pending', 'ai-pending', 'review-pending']);
  if (!allowed.has(task.state)) {
    throw new Error(`Task state ${task.state} cannot be canceled directly`);
  }
  const update = {
    state: 'canceled',
    message: '任务已被用户取消',
    updatedAt: new Date().toISOString(),
    completedAt: new Date().toISOString(),
  };
  await dbPatch(`/tasks/${encodeURIComponent(task.id)}`, update);
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

export function registerTaskActionRoutes(app) {
  if (!app) throw new Error('registerTaskActionRoutes requires an express app');

  async function loadTask(req, res) {
    const task = await dbGet(`/tasks/${encodeURIComponent(req.params.id)}`);
    if (!task) {
      res.status(404).json({ error: `task not found: ${req.params.id}` });
      return null;
    }
    return task;
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
          const newTask = await retryTask(task);
          results.push({ id, ok: true, newTaskId: newTask.id });
        } catch (e) {
          results.push({ id, ok: false, error: e.message });
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
      const newTask = await retryTask(task);
      res.json({ ok: true, taskId: newTask.id, retryOf: task.id });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // reparse
  app.post('/tasks/:id/reparse', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const updated = await reparseTask(task);
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // re-ai
  app.post('/tasks/:id/re-ai', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const updated = await reAiTask(task);
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // cancel
  app.post('/tasks/:id/cancel', async (req, res) => {
    try {
      const task = await loadTask(req, res);
      if (!task) return;
      const updated = await cancelTask(task);
      res.json({ ok: true, taskId: updated.id, state: updated.state });
    } catch (err) {
      res.status(400).json({ error: err.message });
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
      res.status(400).json({ error: err.message });
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
