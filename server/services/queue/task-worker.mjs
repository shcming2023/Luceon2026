/**
 * task-worker.mjs - ParseTask 任务执行器骨架
 * 
 * 约束要求：
 * 1. 模拟执行需明确标记 "worker skeleton"
 * 2. 内存锁防重复处理
 * 3. 常量化轮询与延迟配置
 * 4. 解析完成后自动创建 AI Metadata Job（含去重保护）
 */

import { getAllTasks, updateTask, updateMaterial } from '../tasks/task-client.mjs';
import { logTaskEvent } from '../logging/task-events.mjs';
import { processWithLocalMinerU, resumeWithLocalMinerU, MineruStillProcessingError } from '../mineru/local-adapter.mjs';
import { createAiMetadataJob } from '../ai/metadata-job-client.mjs';
import { parseLatestMineruProgress } from '../../lib/ops-mineru-log-parser.mjs';

// 约束 3: 集中配置常量
const POLL_INTERVAL_MS = 10000; // 10秒检查一次
const SIMULATED_DELAY_MS = 5000; // 每个阶段模拟耗时 5秒
const MAX_CONCURRENT_TASKS = 1;

// stale-running 自愈缓冲期（PRD v0.4 §9.3）
const STALE_GRACE_MS = 60_000;
// 启动后等待多久再做首次恢复扫描，确保 db-server 已就绪
const RECOVERY_DELAY_MS = 2_000;

// 约束 2: 内存队列锁，防止同一个实例中的多个 tick 重复处理
const processingMap = new Set();

export class ParseTaskWorker {
  /**
   * @param {object|null} contextOrOptions - 兼容旧调用（传 minioContext 对象）与新调用（传 options）
   * @param {object} [contextOrOptions.minioContext]
   * @param {object} [contextOrOptions.eventBus] - 事件总线（用于 SSE 广播，可选）
   */
  constructor(contextOrOptions = null) {
    let options = {};
    if (contextOrOptions && (contextOrOptions.minioContext || contextOrOptions.eventBus)) {
      options = contextOrOptions;
    } else if (contextOrOptions?.getFileStream) {
      options = { minioContext: contextOrOptions };
    } else {
      options = contextOrOptions || {};
    }
    this.timer = null;
    this.isRunning = false;
    this.minioContext = options.minioContext
      || (typeof options.getFileStream === 'function' ? options : null);
    this.eventBus = options.eventBus || null;
    this.taskClient = options.taskClient || { getAllTasks, updateTask, updateMaterial };
    this.mineruProcessor = options.mineruProcessor || processWithLocalMinerU;
    this.mineruResumer = options.mineruResumer || resumeWithLocalMinerU;
    this.pendingTaskPatches = new Map();
    this.pendingMaterialPatches = new Map();
  }

  /** 将 ReadableStream 转换为 Buffer */
  streamToBuffer(stream) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      stream.on('data', chunk => chunks.push(chunk));
      stream.on('end', () => resolve(Buffer.concat(chunks)));
      stream.on('error', reject);
    });
  }

  /**
   * P0 OOM 修复：将完整 parsedArtifacts 清单写入 MinIO manifest 文件，
   * DB 只保存摘要字段，避免单个大 PDF 5000+ 条 artifacts 膨胀内存。
   *
   * @param {string} materialId - 关联的素材 ID
   * @param {Array} parsedArtifacts - 完整的解析产物清单
   * @returns {Promise<string>} manifest 对象名 (parsed/{materialId}/artifact-manifest.json)
   */
  async writeArtifactManifest(materialId, parsedArtifacts) {
    const manifestObjectName = `parsed/${materialId}/artifact-manifest.json`;
    const manifest = {
      materialId,
      generatedAt: new Date().toISOString(),
      totalFiles: parsedArtifacts.length,
      artifacts: parsedArtifacts,
    };
    const buf = Buffer.from(JSON.stringify(manifest, null, 2), 'utf-8');
    await this.minioContext.saveObject(manifestObjectName, buf, 'application/json');
    console.log(`[task-worker] Wrote artifact manifest: ${manifestObjectName} (${parsedArtifacts.length} items, ${(buf.length / 1024).toFixed(1)} KB)`);
    return manifestObjectName;
  }

  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    console.log('[task-worker] ParseTask Worker started');
    // 启动后做一次延迟恢复扫描，再进入常规轮询
    setTimeout(() => {
      this.runRecoveryScan().catch((err) => {
        console.error(`[task-worker] recovery scan failed: ${err.message}`);
      });
    }, RECOVERY_DELAY_MS);
    this.tick();
  }

  stop() {
    if (this.timer) clearTimeout(this.timer);
    this.isRunning = false;
    console.log('[task-worker] ParseTask Worker stopped');
  }

  async tick() {
    try {
      await this.scanAndProcess();
    } catch (error) {
      console.error(`[task-worker] Error in tick: ${error.message}`);
    } finally {
      if (this.isRunning) {
        this.timer = setTimeout(() => this.tick(), POLL_INTERVAL_MS);
      }
    }
  }

  async scanAndProcess() {
    await this.flushPendingPatches();
    const tasks = await this.taskClient.getAllTasks();

    // P0: 每轮 tick 检查 MinerU API 是否已确认失败（running 任务终态同步）
    await this.syncMineruApiFailedState(tasks);

    // 每轮 tick 顺便检查一次 stale-running 任务（不阻塞 pending 调度）
    await this.recoverStaleRunningTasks(tasks);

    const pendingTasks = tasks.filter(t => t.state === 'pending');
    
    // P0 Patch: ParseTask FIFO 调度收口
    pendingTasks.sort((a, b) => {
      // 1. 已有 mineruTaskId 的恢复接管任务优先
      const aHasMineru = !!a.metadata?.mineruTaskId;
      const bHasMineru = !!b.metadata?.mineruTaskId;
      if (aHasMineru && !bHasMineru) return -1;
      if (!aHasMineru && bHasMineru) return 1;

      // 2. 普通 pending 严格按 createdAt ASC
      const aTime = new Date(a.createdAt || 0).getTime();
      const bTime = new Date(b.createdAt || 0).getTime();
      if (aTime !== bTime) {
        return aTime - bTime;
      }
      
      // 3. 时间相同时按 id ASC
      return String(a.id).localeCompare(String(b.id));
    });

    const available = Math.max(0, MAX_CONCURRENT_TASKS - processingMap.size);
    let started = 0;
    for (const task of pendingTasks) {
      if (started >= available) break;
      if (processingMap.has(task.id)) continue;
      // 异步处理，不阻塞 tick 扫描下一个
      this.processTask(task);
      started += 1;
    }
  }


  /**
   * 启动后的一次性恢复扫描（PRD v0.4 P0 §10.1.4）
   *
   * 处理两类 “僵尸任务”：
   *   - 之前尚在 running/result-store 中的任务：由于本进程或 upload-server 重启而用户态信息丢失，
   *     直接需要重新拾取，因此归位为 pending 并清理 processingMap。
   *   - 远超超时阈值仍为 running 的任务：写入事件后同样归位 pending。
   *
   * 对 ai-pending 的任务本进程不接手：AiMetadataWorker 负责其自愈。
   */
  async runRecoveryScan() {
    try {
      const tasks = await this.taskClient.getAllTasks();
      const now = Date.now();
      let recovered = 0;
      for (const task of tasks) {
        if (task.state !== 'running' && task.state !== 'result-store') continue;
        
        // P0 Patch 2: check if MinerU is still processing before resetting
        const mineruTaskId = task.metadata?.mineruTaskId;
        const localEndpointRaw = task.optionsSnapshot?.localEndpoint;

        if (mineruTaskId && localEndpointRaw && task.engine === 'local-mineru') {
          let localEndpoint = localEndpointRaw;
          if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
            localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
          }
          localEndpoint = localEndpoint.replace(/\/+$/, '');

          let mineruStatus = null;
          let mineruResponseData = null;
          let fetchError = null;

          try {
            const tRes = await fetch(`${localEndpoint}/tasks/${mineruTaskId}`, { signal: AbortSignal.timeout(3000) });
            if (tRes.ok) {
              const tData = await tRes.json();
              mineruResponseData = tData;
              mineruStatus = String(tData.status || tData.state || tData.task_status || tData.data?.status || tData.data?.state).toLowerCase();
            } else if (tRes.status === 404) {
              mineruStatus = 'not_found';
            } else {
              fetchError = `HTTP ${tRes.status}`;
            }
          } catch (e) {
            fetchError = e.message;
          }

          if (mineruStatus) {
            const isDone = ['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(mineruStatus);
            const isFailed = ['failed', 'error', 'failure', 'canceled', 'cancelled'].includes(mineruStatus);
            const isQueued = ['pending', 'queued'].includes(mineruStatus);
            const isProcessing = ['processing', 'running'].includes(mineruStatus);

            if (isProcessing) {
               await this.updateTaskWithRetry(task.id, {
                 state: 'running',
                 stage: 'mineru-processing',
                 message: '重启恢复：检测到 MinerU 仍在处理，正在接管',
                 metadata: { ...task.metadata, mineruStatus: 'processing' }
               }, { enqueueOnFailure: true });
               this.resumeMineruTask(task, mineruTaskId).catch(err => console.error(`[task-worker] Error resuming task ${task.id}:`, err));
            } else if (isQueued) {
               await this.updateTaskWithRetry(task.id, {
                 state: 'running',
                 stage: 'mineru-queued',
                 message: '重启恢复：检测到 MinerU 仍在排队，正在接管',
                 metadata: { ...task.metadata, mineruStatus: 'queued' }
               }, { enqueueOnFailure: true });
               this.resumeMineruTask(task, mineruTaskId).catch(err => console.error(`[task-worker] Error resuming task ${task.id}:`, err));
            } else if (isDone) {
               await this.updateTaskWithRetry(task.id, {
                 state: 'result-store',
                 stage: 'store',
                 message: '重启恢复：检测到 MinerU 已完成，准备拉取结果',
                 metadata: { ...task.metadata, mineruStatus: 'completed' }
               }, { enqueueOnFailure: true });
               this.resumeMineruTask(task, mineruTaskId).catch(err => console.error(`[task-worker] Error resuming task ${task.id}:`, err));
            } else if (isFailed) {
               const mineruError = mineruResponseData?.error || mineruResponseData?.message || '无详细错误';
               const errorSummary = String(mineruError).slice(0, 500);
               await this.updateTaskWithRetry(task.id, {
                 state: 'failed',
                 stage: 'mineru-failed',
                 progress: 100,
                 message: 'MinerU 已确认失败',
                 errorMessage: `MinerU API failed: ${errorSummary}`,
                 metadata: {
                   ...task.metadata,
                   mineruTaskId: mineruTaskId,
                   mineruStatus: 'failed',
                   mineruFailedAt: mineruResponseData?.completed_at || new Date().toISOString(),
                   mineruFailureSource: 'mineru-api',
                   mineruFailureReason: errorSummary
                 }
               }, { enqueueOnFailure: true });
               // Material 同步失败
               if (task.materialId) {
                 await this.updateMaterialWithRetry(task.materialId, {
                   status: 'failed',
                   mineruStatus: 'failed',
                   aiStatus: 'pending',
                   metadata: {
                     processingStage: 'mineru-failed',
                     processingMsg: `MinerU 已确认失败：${errorSummary}`,
                     mineruFailureSource: 'mineru-api',
                     mineruFailureReason: errorSummary
                   }
                 }, { enqueueOnFailure: true });
               }
               // 事件日志
               await logTaskEvent({
                 taskId: task.id,
                 taskType: 'parse',
                 level: 'error',
                 event: 'mineru-failed-confirmed',
                 message: 'MinerU API 已确认失败',
                 payload: {
                   mineruTaskId,
                   mineruStatus: 'failed',
                   error: errorSummary
                 }
               });
            } else if (mineruStatus === 'not_found') {
               await this.transition(task, {
                 state: 'failed',
                 message: '重启恢复：MinerU 任务已丢失，需人工干预',
                 metadata: { ...task.metadata, mineruStatus: 'not_found' }
               }, 'worker-failed', 'error');
            } else {
               await this.transition(task, {
                 state: 'failed',
                 message: `重启恢复：MinerU 状态异常 (${mineruStatus})，需人工干预`,
                 metadata: { ...task.metadata, mineruStatus: mineruStatus }
               }, 'worker-failed', 'error');
            }
            continue; 
          } else if (fetchError) {
             await this.transition(task, {
               state: 'failed',
               message: `重启恢复：查询 MinerU 状态失败 (${fetchError})，转为失败态避免重复提交`,
               metadata: { ...task.metadata, mineruStatus: 'unknown' }
             }, 'worker-failed', 'error');
             continue;
          }
        }

        const updatedAt = task.updatedAt ? new Date(task.updatedAt).getTime() : 0;
        const timeoutMs = Number(task.optionsSnapshot?.localTimeout || 3600) * 1000;
        // 若任务还在健康窗口内（不更新时间未超时），自愈时不处理；
        // 但“本进程启动恢复”的语义是：无论时间多久，running/result-store 在启动时先归位为 pending，
        // 由轮询重新拾取，避免重启后任务永久卡死。
        const isExplicitlyStale = updatedAt > 0 && (now - updatedAt) > (timeoutMs + STALE_GRACE_MS);
        await this.updateTaskWithRetry(task.id, {
          state: 'pending',
          stage: 'upload',
          progress: 0,
          message: isExplicitlyStale
            ? `检测到卡住的解析任务，已自动重置为 pending。updatedAt=${task.updatedAt}`
            : '服务重启恢复：在执行中的任务已重置为 pending等待重新拾取',
          updatedAt: new Date().toISOString(),
        }, { enqueueOnFailure: true });
        await logTaskEvent({
          taskId: task.id,
          taskType: 'parse',
          level: 'warn',
          event: isExplicitlyStale ? 'stale-running-recovered' : 'restart-recovered',
          message: isExplicitlyStale
            ? '检测到卡住的解析任务，已自动重置为 pending'
            : '检测到重启前的运行中任务，已重置为 pending 重新拾取',
          payload: { previousState: task.state, previousUpdatedAt: task.updatedAt },
        });
        processingMap.delete(task.id);
        recovered += 1;
      }
      if (recovered > 0) {
        console.log(`[task-worker] recovery scan: reset ${recovered} running/result-store tasks to pending`);
      }

      // P0: 纠偏错误 failed 的任务（有 mineruTaskId 但 Luceon 误判 failed）
      await this.recoverMisjudgedFailedTasks(tasks);

      // P1 Patch 7.1: 补偿清理已恢复/已完成任务上残留的旧 errorMessage
      await this.cleanupStaleErrorMessages(tasks);

      // P1 Patch: 补偿清理 Material.metadata 中残留的 mineruStatus = 'processing'
      await this.cleanupStaleMineruStatus();
    } catch (err) {
      console.error(`[task-worker] runRecoveryScan error: ${err.message}`);
    }
  }

  /**
   * P0: 每轮 tick 检查 running 状态的 MinerU 任务是否已被 MinerU API 确认失败。
   * 当 MinerU API 返回 failed/error/canceled 时，将 Luceon ParseTask 和 Material 同步到失败终态。
   *
   * 仅处理：
   * - engine=local-mineru
   * - state=running
   * - stage=mineru-processing/mineru-queued/result-fetching
   * - metadata.mineruTaskId 存在
   *
   * 不允许：
   * - 重新提交 MinerU 任务
   * - 自动重试
   * - 重启 MinerU
   *
   * 事件只记录一次（通过检查 task.stage !== 'mineru-failed' 避免重复）。
   *
   * @param {Array} tasks - 当前所有任务列表
   * @returns {Promise<void>}
   */
  async syncMineruApiFailedState(tasks) {
    const eligibleStages = ['mineru-processing', 'mineru-queued', 'result-fetching'];
    const candidates = tasks.filter(t =>
      t.engine === 'local-mineru' &&
      t.state === 'running' &&
      eligibleStages.includes(t.stage) &&
      t.metadata?.mineruTaskId
    );

    if (candidates.length === 0) return;

    for (const task of candidates) {
      const mineruTaskId = task.metadata.mineruTaskId;
      const localEndpointRaw = task.optionsSnapshot?.localEndpoint;
      if (!localEndpointRaw) continue;

      let localEndpoint = localEndpointRaw;
      if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
        localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
      }
      localEndpoint = localEndpoint.replace(/\/+$/, '');

      let mineruStatus = null;
      let mineruData = null;

      try {
        const tRes = await fetch(`${localEndpoint}/tasks/${mineruTaskId}`, { signal: AbortSignal.timeout(5000) });
        if (tRes.ok) {
          mineruData = await tRes.json();
          mineruStatus = String(mineruData.status || mineruData.state || '').toLowerCase();
        } else if (tRes.status === 404) {
          // 404: 不与 confirmed failed 混淆，保留已有策略（交给 recoverStaleRunning 或 recovery scan）
          continue;
        } else {
          // 非 200/404：网络异常，跳过
          continue;
        }
      } catch (e) {
        // 网络不可达：跳过，不做判定
        continue;
      }

      if (!mineruStatus) continue;

      const isFailed = ['failed', 'error', 'failure', 'canceled', 'cancelled'].includes(mineruStatus);
      if (!isFailed) continue;

      // MinerU 已确认失败：同步 Luceon 终态
      const mineruError = mineruData?.error || mineruData?.message || '无详细错误';
      const errorSummary = String(mineruError).slice(0, 500);

      console.log(`[task-worker] syncMineruApiFailedState: Task ${task.id} MinerU ${mineruTaskId} confirmed ${mineruStatus}: ${errorSummary}`);

      // 1. 更新 ParseTask
      await this.updateTaskWithRetry(task.id, {
        state: 'failed',
        stage: 'mineru-failed',
        progress: 100,
        message: 'MinerU 已确认失败',
        errorMessage: `MinerU API failed: ${errorSummary}`,
        metadata: {
          ...(task.metadata || {}),
          mineruTaskId,
          mineruStatus: 'failed',
          mineruFailedAt: mineruData?.completed_at || new Date().toISOString(),
          mineruFailureSource: 'mineru-api',
          mineruFailureReason: errorSummary
        }
      }, { enqueueOnFailure: true });

      // 2. 更新 Material
      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          status: 'failed',
          mineruStatus: 'failed',
          aiStatus: 'pending',
          metadata: {
            processingStage: 'mineru-failed',
            processingMsg: `MinerU 已确认失败：${errorSummary}`,
            mineruFailureSource: 'mineru-api',
            mineruFailureReason: errorSummary
          }
        }, { enqueueOnFailure: true });
      }

      // 3. 写入事件日志（仅一次，因为下次扫描时 stage 已是 mineru-failed，state 已是 failed）
      await logTaskEvent({
        taskId: task.id,
        taskType: 'parse',
        level: 'error',
        event: 'mineru-failed-confirmed',
        message: 'MinerU API 已确认失败',
        payload: {
          mineruTaskId,
          mineruStatus: 'failed',
          error: errorSummary
        }
      });

      // 释放 processingMap（若被占用）
      processingMap.delete(task.id);
    }
  }

  /**
   * 日常轮询时的超时自愈：对 running/result-store 持续超时的任务自动重置为 pending。
   *
   * P0 硬约束：metadata.mineruTaskId 存在时，**禁止重置为 pending**。
   * 改为查询 MinerU API 裁决实际状态，并按裁决结果路由：
   *   - queued/processing → 保持 running，后台接管（不重提交）
   *   - completed → result-fetching，拉取结果入库
   *   - failed → failed/mineru-failed
   *   - 404 → failed/manual-audit
   *   - 网络不可达 → 保持 running，不重提交
   *
   * @param {Array} tasks - 当前所有任务列表
   * @returns {Promise<void>}
   */
  async recoverStaleRunningTasks(tasks) {
    const now = Date.now();
    for (const task of tasks) {
      if (task.state !== 'running' && task.state !== 'result-store') continue;
      if (processingMap.has(task.id)) continue; // 本进程正在处理的不干预
      const updatedAt = task.updatedAt ? new Date(task.updatedAt).getTime() : 0;
      if (!updatedAt) continue;
      const timeoutMs = Number(task.optionsSnapshot?.localTimeout || 3600) * 1000;
      if ((now - updatedAt) <= (timeoutMs + STALE_GRACE_MS)) continue;

      // P0：已提交 MinerU 的任务禁止重置为 pending，须查询 MinerU API 裁决
      const mineruTaskId = task.metadata?.mineruTaskId;
      if (mineruTaskId && task.engine === 'local-mineru') {
        await this._adjudicateStaleWithMineruApi(task, mineruTaskId, 'stale-running-adjudication');
        continue;
      }

      await this.updateTaskWithRetry(task.id, {
        state: 'pending',
        stage: 'upload',
        progress: 0,
        message: `检测到卡住的解析任务（超过 ${Math.round((timeoutMs + STALE_GRACE_MS) / 1000)}s不更新），已重置为 pending`,
        updatedAt: new Date().toISOString(),
      }, { enqueueOnFailure: true });
      await logTaskEvent({
        taskId: task.id,
        taskType: 'parse',
        level: 'warn',
        event: 'stale-running-recovered',
        message: '日常扫描发现运行超时，已重置为 pending',
        payload: { previousState: task.state, previousUpdatedAt: task.updatedAt, timeoutMs },
      });
    }
  }

  /**
   * P0 内部方法：对已有 mineruTaskId 的 stale/pending 任务，查询 MinerU API 裁决实际状态。
   * 按 MinerU 返回值将 Luceon 任务路由到正确的状态，禁止重新 POST /tasks。
   *
   * @param {Object} task - 当前任务对象
   * @param {string} mineruTaskId - MinerU 内部任务 ID
   * @param {string} eventSource - 事件来源标识，用于日志追踪
   * @returns {Promise<void>}
   */
  async _adjudicateStaleWithMineruApi(task, mineruTaskId, eventSource) {
    const localEndpointRaw = task.optionsSnapshot?.localEndpoint;
    if (!localEndpointRaw) {
      // 无法查询 MinerU，保持 running 不重提交
      console.warn(`[task-worker] ${eventSource}: Task ${task.id} has mineruTaskId=${mineruTaskId} but no localEndpoint, keeping current state`);
      return;
    }

    let localEndpoint = localEndpointRaw;
    if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
      localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
    }
    localEndpoint = localEndpoint.replace(/\/+$/, '');

    let mineruStatus = null;
    let mineruData = null;

    try {
      const tRes = await fetch(`${localEndpoint}/tasks/${mineruTaskId}`, { signal: AbortSignal.timeout(5000) });
      if (tRes.ok) {
        mineruData = await tRes.json();
        mineruStatus = String(mineruData.status || mineruData.state || '').toLowerCase();
      } else if (tRes.status === 404) {
        mineruStatus = 'not_found';
      }
    } catch (e) {
      // 网络不可达 → 保持当前状态，不重提交
      console.warn(`[task-worker] ${eventSource}: Task ${task.id} MinerU API unreachable: ${e.message}, keeping current state`);
      await this.updateTaskWithRetry(task.id, {
        updatedAt: new Date().toISOString(),
        message: `${eventSource}: MinerU API 不可达 (${e.message})，保持当前状态不重提交`,
      }, { enqueueOnFailure: true });
      return;
    }

    if (!mineruStatus) return;

    const isProcessing = ['processing', 'running'].includes(mineruStatus);
    const isQueued = ['queued', 'pending'].includes(mineruStatus);
    const isDone = ['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(mineruStatus);
    const isFailed = ['failed', 'error', 'failure', 'canceled', 'cancelled'].includes(mineruStatus);

    if (isProcessing || isQueued) {
      // MinerU 仍在工作 → 保持 running，后台接管
      const stage = isQueued ? 'mineru-queued' : 'mineru-processing';
      console.log(`[task-worker] ${eventSource}: Task ${task.id} MinerU ${mineruTaskId} still ${mineruStatus}, keeping running`);
      await this.updateTaskWithRetry(task.id, {
        state: 'running',
        stage,
        message: `${eventSource}: MinerU 仍在 ${mineruStatus}，保持 running 不重提交`,
        updatedAt: new Date().toISOString(),
        metadata: { ...(task.metadata || {}), mineruStatus },
      }, { enqueueOnFailure: true });
      // 同步 Material
      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          status: 'processing',
          mineruStatus: isQueued ? 'queued' : 'processing',
          metadata: {
            processingStage: stage,
            processingMsg: `${eventSource}: MinerU 仍在 ${mineruStatus}`,
            processingUpdatedAt: new Date().toISOString(),
          }
        }, { enqueueOnFailure: true });
      }
      // 后台接管（不重新 POST）
      this.resumeMineruTask(task, mineruTaskId).catch(err =>
        console.error(`[task-worker] Error resuming stale task ${task.id}:`, err)
      );
      await logTaskEvent({
        taskId: task.id, taskType: 'parse', level: 'warn',
        event: 'stale-adjudicated-resume',
        message: `${eventSource}: MinerU 仍在 ${mineruStatus}，已接管不重提交`,
        payload: { mineruTaskId, mineruStatus, source: eventSource },
      });

    } else if (isDone) {
      // MinerU 已完成 → result-fetching，拉取结果入库
      console.log(`[task-worker] ${eventSource}: Task ${task.id} MinerU ${mineruTaskId} completed, entering result-fetching`);
      await this.updateTaskWithRetry(task.id, {
        state: 'running',
        stage: 'result-fetching',
        message: `${eventSource}: MinerU 已完成，正在拉取结果入库`,
        updatedAt: new Date().toISOString(),
        metadata: { ...(task.metadata || {}), mineruStatus: 'completed' },
      }, { enqueueOnFailure: true });
      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          status: 'processing',
          mineruStatus: 'completed',
          metadata: {
            processingStage: 'result-fetching',
            processingMsg: `${eventSource}: MinerU 已完成，正在拉取结果入库`,
            processingUpdatedAt: new Date().toISOString(),
          }
        }, { enqueueOnFailure: true });
      }
      this.resumeMineruTask(task, mineruTaskId).catch(err =>
        console.error(`[task-worker] Error resuming completed stale task ${task.id}:`, err)
      );
      await logTaskEvent({
        taskId: task.id, taskType: 'parse', level: 'warn',
        event: 'stale-adjudicated-completed',
        message: `${eventSource}: MinerU 已完成，开始拉取结果入库`,
        payload: { mineruTaskId, source: eventSource },
      });

    } else if (isFailed) {
      // MinerU 已失败 → failed/mineru-failed
      const mineruError = mineruData?.error || mineruData?.message || '无详细错误';
      const errorSummary = String(mineruError).slice(0, 500);
      console.log(`[task-worker] ${eventSource}: Task ${task.id} MinerU ${mineruTaskId} failed: ${errorSummary}`);
      await this.updateTaskWithRetry(task.id, {
        state: 'failed',
        stage: 'mineru-failed',
        progress: 100,
        message: 'MinerU 已确认失败',
        errorMessage: `MinerU API failed: ${errorSummary}`,
        metadata: {
          ...(task.metadata || {}),
          mineruTaskId,
          mineruStatus: 'failed',
          mineruFailedAt: mineruData?.completed_at || new Date().toISOString(),
          mineruFailureSource: 'mineru-api',
          mineruFailureReason: errorSummary,
        }
      }, { enqueueOnFailure: true });
      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          status: 'failed',
          mineruStatus: 'failed',
          aiStatus: 'pending',
          metadata: {
            processingStage: 'mineru-failed',
            processingMsg: `MinerU 已确认失败：${errorSummary}`,
            mineruFailureSource: 'mineru-api',
            mineruFailureReason: errorSummary,
          }
        }, { enqueueOnFailure: true });
      }
      await logTaskEvent({
        taskId: task.id, taskType: 'parse', level: 'error',
        event: 'mineru-failed-confirmed',
        message: `${eventSource}: MinerU API 已确认失败`,
        payload: { mineruTaskId, mineruStatus: 'failed', error: errorSummary, source: eventSource },
      });
      processingMap.delete(task.id);

    } else if (mineruStatus === 'not_found') {
      // MinerU 404 → failed/manual-audit，不重提交
      await this.updateTaskWithRetry(task.id, {
        state: 'failed',
        message: `${eventSource}: MinerU 任务记录已丢失 (404)，需人工审计，不重提交`,
        metadata: {
          ...(task.metadata || {}),
          mineruStatus: 'not_found',
          failureEvidenceSource: `MinerU API 404 (${eventSource})`,
          failureConfirmedAt: new Date().toISOString(),
        }
      }, { enqueueOnFailure: true });
      await logTaskEvent({
        taskId: task.id, taskType: 'parse', level: 'error',
        event: 'stale-adjudicated-404',
        message: `${eventSource}: MinerU 任务 404，不重提交`,
        payload: { mineruTaskId, source: eventSource },
      });
      processingMap.delete(task.id);
    }
  }

  /**
   * P0 纠偏：扫描 failed 状态的任务，若含 mineruTaskId 则查询 MinerU API 裁决。
   * - MinerU queued/processing：纠正回 running，由后台轮询接管，不重新提交。
   * - MinerU completed 且 result 可取：纠正并拉取结果入库，进入后续 AI 流程。
   * - MinerU failed/error/canceled：保持 failed，补充 MinerU 明确失败证据。
   * - MinerU 404 / 不可达：保持 failed，记录不可确认原因。
   *
   * @param {Array} tasks - 当前所有任务列表
   * @returns {Promise<void>}
   */
  async recoverMisjudgedFailedTasks(tasks) {
    const failedWithMineruId = tasks.filter(t =>
      t.state === 'failed' && t.metadata?.mineruTaskId && t.engine === 'local-mineru'
    );
    if (failedWithMineruId.length === 0) return;

    for (const task of failedWithMineruId) {
      if (processingMap.has(task.id)) continue;
      const mineruTaskId = task.metadata.mineruTaskId;
      const localEndpointRaw = task.optionsSnapshot?.localEndpoint;
      if (!localEndpointRaw) continue;

      let localEndpoint = localEndpointRaw;
      if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
        localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
      }
      localEndpoint = localEndpoint.replace(/\/+$/, '');

      let mineruStatus = null;
      let mineruData = null;

      try {
        const tRes = await fetch(`${localEndpoint}/tasks/${mineruTaskId}`, { signal: AbortSignal.timeout(5000) });
        if (tRes.ok) {
          mineruData = await tRes.json();
          mineruStatus = String(mineruData.status || mineruData.state || '').toLowerCase();
        } else if (tRes.status === 404) {
          mineruStatus = 'not_found';
        }
      } catch (e) {
        console.warn(`[task-worker] recoverMisjudgedFailed: 查询 MinerU ${mineruTaskId} 失败: ${e.message}`);
        continue; // 网络不可达则跳过，不做任何判定
      }

      if (!mineruStatus) continue;

      const isProcessing = ['queued', 'pending', 'processing', 'running'].includes(mineruStatus);
      const isCompleted = ['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(mineruStatus);
      const isFailed = ['failed', 'error', 'failure', 'canceled', 'cancelled'].includes(mineruStatus);

      if (isProcessing) {
        // 纠正回 running：MinerU 仍在工作，由后台接管
        console.log(`[task-worker] recoverMisjudgedFailed: Task ${task.id} 纠偏：MinerU ${mineruTaskId} 仍在 ${mineruStatus}`);
        await this.updateTaskWithRetry(task.id, {
          state: 'running',
          stage: mineruStatus === 'queued' || mineruStatus === 'pending' ? 'mineru-queued' : 'mineru-processing',
          errorMessage: '',
          message: `纠偏恢复：Luceon 误判 failed，但 MinerU 仍在 ${mineruStatus}，已纠正为 running`,
          metadata: {
            ...(task.metadata || {}),
            mineruStatus,
            recoveredFromMisjudgedFailed: true,
            previousState: 'failed',
            previousErrorMessage: task.errorMessage || task.message || '',
            recoveredAt: new Date().toISOString()
          }
        }, { enqueueOnFailure: true });

        // 同步 Material：不再显示 failed
        if (task.materialId) {
          await this.updateMaterialWithRetry(task.materialId, {
            status: 'processing',
            mineruStatus: mineruStatus === 'queued' || mineruStatus === 'pending' ? 'queued' : 'processing',
            aiStatus: 'pending',
            metadata: {
              processingStage: mineruStatus === 'queued' || mineruStatus === 'pending' ? 'mineru-queued' : 'mineru-processing',
              processingMsg: `纠偏恢复：MinerU 仍在 ${mineruStatus}`,
              processingUpdatedAt: new Date().toISOString()
            }
          }, { enqueueOnFailure: true });
        }

        await logTaskEvent({
          taskId: task.id, taskType: 'parse', level: 'warn',
          event: 'misjudged-failed-corrected',
          message: `Luceon 误判 failed 已纠正：MinerU ${mineruTaskId} 实际状态为 ${mineruStatus}`,
          payload: { mineruTaskId, mineruStatus }
        });
        // 启动后台接管
        this.resumeMineruTask(task, mineruTaskId).catch(err =>
          console.error(`[task-worker] Error resuming misjudged task ${task.id}:`, err)
        );

      } else if (isCompleted) {
        // MinerU 已完成但 Luceon 标了 failed：拉取结果入库
        console.log(`[task-worker] recoverMisjudgedFailed: Task ${task.id} 纠偏：MinerU ${mineruTaskId} 已完成，尝试拉取结果`);
        await this.updateTaskWithRetry(task.id, {
          state: 'running',
          stage: 'result-fetching',
          errorMessage: '',
          message: `纠偏恢复：MinerU 已完成，正在拉取结果入库`,
          metadata: {
            ...(task.metadata || {}),
            mineruStatus: 'completed',
            recoveredFromMisjudgedFailed: true,
            previousState: 'failed',
            previousErrorMessage: task.errorMessage || task.message || '',
            recoveredAt: new Date().toISOString()
          }
        }, { enqueueOnFailure: true });

        // 同步 Material：标记 MinerU 已完成，等待结果入库
        if (task.materialId) {
          await this.updateMaterialWithRetry(task.materialId, {
            status: 'processing',
            mineruStatus: 'completed',
            aiStatus: 'pending',
            metadata: {
              processingStage: 'result-fetching',
              processingMsg: '纠偏恢复：MinerU 已完成，正在拉取结果入库',
              processingUpdatedAt: new Date().toISOString()
            }
          }, { enqueueOnFailure: true });
        }

        await logTaskEvent({
          taskId: task.id, taskType: 'parse', level: 'warn',
          event: 'misjudged-failed-corrected',
          message: `Luceon 误判 failed 已纠正：MinerU ${mineruTaskId} 实际已完成，开始拉取结果`,
          payload: { mineruTaskId, mineruStatus: 'completed' }
        });
        this.resumeMineruTask(task, mineruTaskId).catch(err =>
          console.error(`[task-worker] Error resuming completed misjudged task ${task.id}:`, err)
        );

      } else if (isFailed) {
        // MinerU 也确认失败：保持 failed 但补充证据与标准字段
        const mineruError = mineruData?.error || mineruData?.message || '无详细错误';
        const errorSummary = String(mineruError).slice(0, 500);
        if (!task.message?.includes('MinerU 已确认失败') && !task.stage?.includes('mineru-failed')) {
          await this.updateTaskWithRetry(task.id, {
            state: 'failed',
            stage: 'mineru-failed',
            progress: 100,
            message: 'MinerU 已确认失败',
            errorMessage: `MinerU API failed: ${errorSummary}`,
            metadata: {
              ...(task.metadata || {}),
              mineruTaskId,
              mineruStatus: 'failed',
              mineruFailedAt: mineruData?.completed_at || new Date().toISOString(),
              mineruFailureSource: 'mineru-api',
              mineruFailureReason: errorSummary
            }
          }, { enqueueOnFailure: true });
          // Material 同步失败
          if (task.materialId) {
            await this.updateMaterialWithRetry(task.materialId, {
              status: 'failed',
              mineruStatus: 'failed',
              aiStatus: 'pending',
              metadata: {
                processingStage: 'mineru-failed',
                processingMsg: `MinerU 已确认失败：${errorSummary}`,
                mineruFailureSource: 'mineru-api',
                mineruFailureReason: errorSummary
              }
            }, { enqueueOnFailure: true });
          }
          await logTaskEvent({
            taskId: task.id, taskType: 'parse', level: 'error',
            event: 'mineru-failed-confirmed',
            message: 'MinerU API 已确认失败',
            payload: { mineruTaskId, mineruStatus: 'failed', error: errorSummary }
          });
        }

      } else if (mineruStatus === 'not_found') {
        // MinerU 404：任务记录已丢失，保持 failed，不与 confirmed failed 混淆
        if (!task.message?.includes('MinerU 任务记录已丢失')) {
          await this.updateTaskWithRetry(task.id, {
            state: 'failed',
            message: `[failed 已确认] MinerU 任务记录已丢失 (404)，需人工审计`,
            metadata: {
              ...(task.metadata || {}),
              mineruStatus: 'not_found',
              failureEvidenceSource: 'MinerU API 404',
              failureConfirmedAt: new Date().toISOString()
            }
          }, { enqueueOnFailure: true });
        }
      }
    }
  }

  /**
   * P1 Patch 7.1: 补偿清理已恢复/已完成任务上残留的旧 errorMessage。
   *
   * 触发条件（必须同时满足）：
   * - state 为 completed / review-pending / ai-pending / running 之一
   * - metadata.recoveredFromMisjudgedFailed === true
   * - errorMessage 非空（即存在残留旧错误）
   *
   * 执行动作：
   * - 将旧 errorMessage 转存到 metadata.previousErrorMessage（若已存在则不覆盖）
   * - 清空 errorMessage
   * - 不改变 state / stage / progress / parsedFilesCount 等任何业务字段
   *
   * 安全保护：
   * - state=failed 的任务绝不清理——真实失败必须保留证据
   * - 不清理不含 recoveredFromMisjudgedFailed 标记的任务——避免误清 AI 阶段错误
   *
   * @param {Array} tasks - 当前所有任务列表
   * @returns {Promise<void>}
   */
  async cleanupStaleErrorMessages(tasks) {
    const recoveredStates = ['completed', 'review-pending', 'ai-pending', 'running'];
    const candidates = tasks.filter(t =>
      recoveredStates.includes(t.state) &&
      t.metadata?.recoveredFromMisjudgedFailed === true &&
      t.errorMessage && t.errorMessage.trim() !== ''
    );

    if (candidates.length === 0) return;

    let cleaned = 0;
    for (const task of candidates) {
      const oldErrorMessage = task.errorMessage;
      const existingPrevious = task.metadata?.previousErrorMessage;

      // 如果 previousErrorMessage 已存在且非空，不覆盖为更弱的信息
      const previousErrorMessage = (existingPrevious && existingPrevious.trim() !== '')
        ? existingPrevious
        : oldErrorMessage;

      await this.updateTaskWithRetry(task.id, {
        errorMessage: '',
        metadata: {
          ...(task.metadata || {}),
          previousErrorMessage,
          errorMessageCleanedAt: new Date().toISOString()
        }
      }, { enqueueOnFailure: true });

      console.log(`[task-worker] cleanupStaleErrorMessages: Task ${task.id} (state=${task.state}) 旧 errorMessage 已清理`);
      await logTaskEvent({
        taskId: task.id,
        taskType: 'parse',
        level: 'info',
        event: 'stale-error-cleaned',
        message: `已恢复任务残留 errorMessage 已清理: "${oldErrorMessage.substring(0, 80)}"`,
        payload: { previousErrorMessage, cleanedState: task.state }
      });
      cleaned++;
    }

    if (cleaned > 0) {
      console.log(`[task-worker] cleanupStaleErrorMessages: 共清理 ${cleaned} 个已恢复任务的旧 errorMessage`);
    }
  }

  /**
   * P1 Patch: 补偿清理 Material.metadata 中残留的 mineruStatus = 'processing'
   * 仅处理顶层 mineruStatus=completed 但 metadata.mineruStatus=processing 的 Material。
   * 幂等、非破坏性清理，不修改其他 metadata 字段。
   */
  async cleanupStaleMineruStatus() {
    const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';
    try {
      const resp = await fetch(`${DB_BASE_URL}/materials`);
      if (!resp.ok) return;
      const materials = await resp.json();
      
      const candidates = materials.filter(m => 
        m.mineruStatus === 'completed' && 
        m.metadata?.mineruStatus === 'processing'
      );
      
      for (const m of candidates) {
        let processingStage = 'mineru-completed';
        let processingMsg = 'MinerU 解析完成，等待 AI 元数据识别';
        
        if (m.aiStatus === 'analyzed') {
           if (m.status === 'reviewing') {
              processingStage = 'review';
              processingMsg = 'AI 识别完成，待人工复核';
           } else {
              processingStage = 'completed';
              processingMsg = 'AI 识别完成';
           }
        }
        
        await this.updateMaterialWithRetry(m.id, {
          metadata: {
            ...(m.metadata || {}),
            mineruStatus: 'completed',
            processingStage,
            processingMsg
          }
        }, { enqueueOnFailure: true });
        
        console.log(`[task-worker] cleanupStaleMineruStatus: Material ${m.id} residual metadata.mineruStatus fixed`);
      }
    } catch (e) {
      console.error(`[task-worker] cleanupStaleMineruStatus failed: ${e.message}`);
    }
  }

  async processTask(task) {
    processingMap.add(task.id);
    const modeLabel = task.engine === 'local-mineru' ? 'local-mineru' : 'worker skeleton';
    console.log(`[task-worker] Picked up task: ${task.id} (${modeLabel})`);

    // P0 硬约束：已有 mineruTaskId 的任务禁止重新 POST /tasks
    // 必须改走 resumeWithLocalMinerU 路径
    const existingMineruTaskId = task.metadata?.mineruTaskId;
    if (existingMineruTaskId && task.engine === 'local-mineru') {
      console.log(`[task-worker] Task ${task.id} already has mineruTaskId=${existingMineruTaskId}, routing to adjudication+resume (not re-POST)`);
      // 必须先释放 processingMap，否则 fire-and-forget 的 resumeMineruTask
      // 会因为 processingMap.has(task.id) === true 而立即退出
      processingMap.delete(task.id);
      try {
        await this._adjudicateStaleWithMineruApi(task, existingMineruTaskId, 'pending-has-mineruTaskId');
      } catch (err) {
        console.error(`[task-worker] Task ${task.id} adjudication failed: ${err.message}`);
        // 裁决失败也不重新提交，保持当前状态
      }
      return;
    }

    try {
      if (task.engine === 'local-mineru') {
        const materialInfo = task.optionsSnapshot?.material || {}; // Need real file info
        const objectName = materialInfo.metadata?.objectName;
        if (!objectName) throw new Error('任务缺少真实的文件对象信息 (objectName)');
        if (!this.minioContext) throw new Error('Worker 缺少存储上下文 (MinIO 客户端未注入)');
        
        await this.transition(task, {
          stage: 'process',
          state: 'running',
          progress: 5,
          message: '正在拉取文件并连接本地 MinerU...'
        }, 'worker-picked');
        
        const fileStream = await this.minioContext.getFileStream(objectName);
        
        let markdownObjectName = null;
        let mineruTaskId = null;
        let parsedPrefix = `parsed/${task.materialId}/`;
        let parsedFilesCount = 0;
        let parsedArtifacts = [];
        let zipObjectName = null;
        let artifactIncomplete = false;

        const isMarkdown = (materialInfo.fileName || '').toLowerCase().endsWith('.md') || materialInfo.mimeType === 'text/markdown';

        if (isMarkdown) {
          console.log(`[task-worker] Task ${task.id} is Markdown, skipping MinerU parsing`);
          // 必须将 Markdown 内容保存到 parsed/{materialId}/full.md（PRD 强制要求 markdownObjectName 以 parsed/ 开头）
          // 原始文件在 originals/{materialId}/{filename}，Worker 读取后写入规范路径
          const targetObjectName = `parsed/${task.materialId}/full.md`;
          let markdownContent = '';
          try {
            const buffer = await this.streamToBuffer(fileStream);
            markdownContent = buffer.toString('utf-8');
            await this.minioContext.saveMarkdown(targetObjectName, markdownContent);
            console.log(`[task-worker] Saved Markdown to ${targetObjectName} (${markdownContent.length} chars)`);
          } catch (saveErr) {
            console.error(`[task-worker] Failed to save Markdown to MinIO: ${saveErr.message}`);
            throw new Error(`Markdown 文件保存失败: ${saveErr.message}`);
          }
          markdownObjectName = targetObjectName;
          mineruTaskId = 'skip-markdown';
          parsedPrefix = `parsed/${task.materialId}/`;
          parsedFilesCount = 1;
          parsedArtifacts = [{ objectName: targetObjectName, relativePath: 'full.md', size: Buffer.byteLength(markdownContent, 'utf-8'), mimeType: 'text/markdown' }];
          zipObjectName = null;
          artifactIncomplete = false;
        } else {
          const mineruResult = await this.mineruProcessor({
            task,
            material: materialInfo,
            fileStream,
            fileName: materialInfo.fileName || 'document.pdf',
            mimeType: materialInfo.mimeType || 'application/pdf',
            timeoutMs: Number(task.optionsSnapshot?.localTimeout || 3600) * 1000,
            minioContext: this.minioContext,
            updateProgress: async (updateInfo) => {
              const eventName = updateInfo.stage === 'store' ? 'stage-changed' : 'progress-update';
              await this.transition(task, updateInfo, eventName);
              
              // P0 Task 3: 同步 Material 状态
              if (task.materialId && (updateInfo.stage || updateInfo.message || updateInfo.metadata)) {
                await this.updateMaterialWithRetry(task.materialId, {
                  metadata: {
                    ...(materialInfo.metadata || {}),
                    ...(updateInfo.metadata || {}), // 透传 MinerU taskId, startedAt 等
                    processingStage: updateInfo.stage || task.stage,
                    processingMsg: updateInfo.message || task.message,
                    processingUpdatedAt: new Date().toISOString()
                  }
                }, { enqueueOnFailure: true });
              }
            }
          });
          markdownObjectName = mineruResult.objectName;
          mineruTaskId = mineruResult.mineruTaskId;
          parsedPrefix = mineruResult.parsedPrefix || `parsed/${task.materialId}/`;
          parsedArtifacts = Array.isArray(mineruResult.parsedArtifacts) ? mineruResult.parsedArtifacts : [];
          parsedFilesCount = Number(mineruResult.parsedFilesCount || parsedArtifacts.length || 1);
          zipObjectName = mineruResult.zipObjectName || null;
          artifactIncomplete = mineruResult.artifactIncomplete === true;

          // ── P0 completed-empty 检测 ──
          if (mineruResult.markdownEmpty === true) {
            const emptyHandled = await this.handleCompletedEmpty(task, materialInfo, mineruResult);
            if (emptyHandled) return; // 已处理（重试或终态），不继续正常流程
          }

          await logTaskEvent({
            taskId: task.id,
            taskType: 'parse',
            level: 'info',
            event: 'artifacts-saved',
            message: `解析产物已保存到 ${parsedPrefix} (count=${parsedFilesCount})`,
            payload: {
              parsedPrefix,
              parsedFilesCount,
              hasMineruZip: Boolean(zipObjectName),
            },
          });

          if (artifactIncomplete) {
            await logTaskEvent({
              taskId: task.id,
              taskType: 'parse',
              level: 'warn',
              event: 'artifact-incomplete',
              message: 'MinerU 仅返回 Markdown，完整解析产物未入库',
              payload: {
                parsedPrefix,
                parsedFilesCount,
                markdownObjectName,
                mineruTaskId,
              },
            });
          }
        }

        // P0 OOM 修复：parsedArtifacts 写入 MinIO manifest，DB 只保存摘要
        let artifactManifestObjectName = null;
        if (parsedArtifacts.length > 0 && this.minioContext) {
          try {
            artifactManifestObjectName = await this.writeArtifactManifest(task.materialId, parsedArtifacts);
          } catch (e) {
            console.warn(`[task-worker] Failed to write artifact manifest: ${e.message}`);
          }
        }

        await this.transition(task, {
          stage: 'complete',
          state: 'ai-pending',
          progress: 100,
          message: isMarkdown ? 'Markdown 文件无需解析，正在准备 AI 任务' : 'MinerU 解析完成，产物已落库，等待 AI 元数据识别',
          metadata: {
            ...(task.metadata || {}),
            mineruStatus: 'completed',
            markdownObjectName,
            mineruTaskId,
            parsedPrefix,
            parsedFilesCount,
            // P0 OOM: 不再写入完整 parsedArtifacts 到 DB
            artifactManifestObjectName,
            zipObjectName: zipObjectName || undefined,
            artifactIncomplete,
            parsedAt: new Date().toISOString()
          },
          completedAt: new Date().toISOString()
        }, 'worker-completed');

        // 补齐 Material 状态：确保 AI 阶段开始前，Material 表达“解析阶段完成” (Requirement 3)
        await this.updateMaterialWithRetry(task.materialId, {
          mineruStatus: 'completed',
          metadata: {
            ...(materialInfo.metadata || {}),
            mineruStatus: 'completed',
            markdownObjectName,
            parsedPrefix,
            parsedFilesCount,
            // P0 OOM: 不再写入完整 parsedArtifacts 到 DB
            artifactManifestObjectName,
            zipObjectName: zipObjectName || undefined,
            processingStage: 'mineru-completed',
            processingMsg: 'MinerU 解析完成，等待 AI 元数据识别',
            processingUpdatedAt: new Date().toISOString()
          }
        }, { enqueueOnFailure: true });

        // ── 解析成功后自动创建 AI Metadata Job ──────────────────
        await this.tryCreateAiJob(task, markdownObjectName);

      } else {
        // 1. 进入 running 状态 (模拟过程)
        await this.transition(task, {
          stage: 'process',
          state: 'running',
          progress: 10,
          message: '[worker skeleton] 正在解析文档结构...'
        }, 'worker-picked');

        await this.sleep(SIMULATED_DELAY_MS);

        // 2. 模拟中途进度
        await this.transition(task, {
          progress: 50,
          message: '[worker skeleton] 正在提取文本与表格内容...'
        }, 'progress-update');

        await this.sleep(SIMULATED_DELAY_MS);

        // 3. 进入 result-store 状态
        await this.transition(task, {
          stage: 'store',
          state: 'result-store',
          progress: 80,
          message: '[worker skeleton] 正在保存解析产物到存储后端...'
        }, 'stage-changed');

        await this.sleep(SIMULATED_DELAY_MS);

        // 4. 完成模拟，进入已就绪待 AI 处理状态
        await this.transition(task, {
          stage: 'complete',
          state: 'ai-pending',
          progress: 100,
          message: '[worker skeleton] 解析完成（模拟），等待 AI 元数据识别',
          metadata: {
            ...(task.metadata || {}),
            mineruStatus: 'completed'
          },
          completedAt: new Date().toISOString()
        }, 'worker-completed');

        // ── 解析成功后自动创建 AI Metadata Job ──────────────────
        await this.tryCreateAiJob(task);
      }

    } catch (error) {
      // P0: 区分 MineruStillProcessingError 与真正的业务失败
      if (error instanceof MineruStillProcessingError || error?.name === 'MineruStillProcessingError') {
        // MinerU 仍在 processing/queued：保持 running，不进入 failed
        console.log(`[task-worker] Task ${task.id}: MinerU ${error.mineruTaskId} 仍在 ${error.mineruStatus}，保持 running 等待后续轮询接管`);
        await this.transition(task, {
          state: 'running',
          stage: error.mineruStatus === 'queued' ? 'mineru-queued' : 'mineru-processing',
          message: `本地等待超时但 MinerU 仍在 ${error.mineruStatus}，后台将继续观测`,
          metadata: {
            ...(task.metadata || {}),
            mineruTaskId: error.mineruTaskId,
            mineruStatus: error.mineruStatus,
            mineruLastStatusAt: new Date().toISOString(),
            localTimeoutOccurred: true,
            localTimeoutAt: new Date().toISOString()
          }
        }, 'mineru-timeout-but-still-processing', 'warn');
        // 不标记 material 失败
        return;
      }

      console.error(`[task-worker] Task ${task.id} failed: ${error.message}`);
      // P0: 确保 failed 终态不残留 mineru-processing/processing
      // 从 taskClient 读取最新 task（因为 updateProgress 可能已通过 transition 更新了 mineruTaskId）
      let hasMineruTaskId = Boolean(task.metadata?.mineruTaskId);
      if (!hasMineruTaskId) {
        try {
          const allTasks = await this.taskClient.getAllTasks();
          const latestTask = allTasks.find(t => t.id === task.id);
          if (latestTask?.metadata?.mineruTaskId) hasMineruTaskId = true;
        } catch (_e) { /* ignore, use in-memory fallback */ }
      }
      const failedStage = hasMineruTaskId ? 'mineru-failed' : 'execution-failed';
      await this.transition(task, {
        state: 'failed',
        stage: failedStage,
        errorMessage: error.message,
        message: `[${modeLabel}] 执行失败: ${error.message}`
      }, 'worker-failed', 'error');

      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          status: 'failed',
          mineruStatus: hasMineruTaskId ? 'failed' : 'pending',
          aiStatus: 'pending',
          metadata: {
            processingStage: failedStage,
            processingMsg: `解析失败: ${error.message}`,
            processingUpdatedAt: new Date().toISOString(),
          }
        }, { enqueueOnFailure: true });
      }
    } finally {
      processingMap.delete(task.id);
    }
  }

  /**
   * 接管已在 MinerU 端存在的任务，执行后台轮询与结果拉取。
   *
   * @param {Object} task 要接管的 Luceon 任务记录
   * @param {string} mineruTaskId 对应的 MinerU 内部任务 ID
   * @returns {Promise<void>} 异步接管流程，不阻塞当前线程
   */
  async resumeMineruTask(task, mineruTaskId) {
    if (processingMap.has(task.id)) return;
    processingMap.add(task.id);
    console.log(`[task-worker] Resuming task: ${task.id} (mineruTaskId: ${mineruTaskId})`);

    try {
      const materialInfo = task.optionsSnapshot?.material || {};
      
      const mineruResult = await this.mineruResumer({
        task,
        material: materialInfo,
        mineruTaskId,
        timeoutMs: Number(task.optionsSnapshot?.localTimeout || 3600) * 1000,
        minioContext: this.minioContext,
        updateProgress: async (updateInfo) => {
          const eventName = updateInfo.stage === 'store' ? 'stage-changed' : 'progress-update';
          await this.transition(task, updateInfo, eventName);
          
          if (task.materialId && (updateInfo.stage || updateInfo.message || updateInfo.metadata)) {
            await this.updateMaterialWithRetry(task.materialId, {
              metadata: {
                ...(materialInfo.metadata || {}),
                ...(updateInfo.metadata || {}),
                processingStage: updateInfo.stage || task.stage,
                processingMsg: updateInfo.message || task.message,
                processingUpdatedAt: new Date().toISOString()
              }
            }, { enqueueOnFailure: true });
          }
        }
      });

      const markdownObjectName = mineruResult.objectName;
      const parsedPrefix = mineruResult.parsedPrefix || `parsed/${task.materialId}/`;
      const parsedArtifacts = Array.isArray(mineruResult.parsedArtifacts) ? mineruResult.parsedArtifacts : [];
      const parsedFilesCount = Number(mineruResult.parsedFilesCount || parsedArtifacts.length || 1);
      const zipObjectName = mineruResult.zipObjectName || null;
      const artifactIncomplete = mineruResult.artifactIncomplete === true;

      // ── P0 completed-empty 检测（resume 路径） ──
      if (mineruResult.markdownEmpty === true) {
        const materialInfo = task.optionsSnapshot?.material || {};
        const emptyHandled = await this.handleCompletedEmpty(task, materialInfo, mineruResult);
        if (emptyHandled) return; // 已处理
      }

      await logTaskEvent({
        taskId: task.id,
        taskType: 'parse',
        level: 'info',
        event: 'artifacts-saved',
        message: `解析产物已保存到 ${parsedPrefix} (count=${parsedFilesCount})`,
        payload: {
          parsedPrefix,
          parsedFilesCount,
          hasMineruZip: Boolean(zipObjectName),
        },
      });

      if (artifactIncomplete) {
        await logTaskEvent({
          taskId: task.id,
          taskType: 'parse',
          level: 'warn',
          event: 'artifact-incomplete',
          message: 'MinerU 仅返回 Markdown，完整解析产物未入库',
          payload: {
            parsedPrefix,
            parsedFilesCount,
            markdownObjectName,
            mineruTaskId,
          },
        });
      }

      // P0 OOM 修复：parsedArtifacts 写入 MinIO manifest
      let artifactManifestObjectName = null;
      if (parsedArtifacts.length > 0 && this.minioContext) {
        try {
          artifactManifestObjectName = await this.writeArtifactManifest(task.materialId, parsedArtifacts);
        } catch (e) {
          console.warn(`[task-worker] Failed to write artifact manifest (resume): ${e.message}`);
        }
      }

      await this.transition(task, {
        stage: 'complete',
        state: 'ai-pending',
        progress: 100,
        errorMessage: '',
        message: 'MinerU 解析完成，产物已落库，等待 AI 元数据识别',
        metadata: {
          ...(task.metadata || {}),
          markdownObjectName,
          mineruTaskId,
          parsedPrefix,
          parsedFilesCount,
          // P0 OOM: 不再写入完整 parsedArtifacts 到 DB
          artifactManifestObjectName,
          zipObjectName: zipObjectName || undefined,
          artifactIncomplete,
          parsedAt: new Date().toISOString()
        },
        completedAt: new Date().toISOString()
      }, 'worker-completed');

      await this.updateMaterialWithRetry(task.materialId, {
        status: 'processing',
        mineruStatus: 'completed',
        aiStatus: 'pending',
        metadata: {
          ...(materialInfo.metadata || {}),
          mineruStatus: 'completed',
          markdownObjectName,
          parsedPrefix,
          parsedFilesCount,
          // P0 OOM: 不再写入完整 parsedArtifacts 到 DB
          artifactManifestObjectName,
          zipObjectName: zipObjectName || undefined,
          processingStage: 'ai',
          processingMsg: '解析完成，等待 AI 元数据识别',
          processingUpdatedAt: new Date().toISOString()
        }
      }, { enqueueOnFailure: true });

      await this.tryCreateAiJob(task, markdownObjectName);

    } catch (error) {
      console.error(`[task-worker] Task ${task.id} failed during resume: ${error.message}`);
      await this.transition(task, {
        state: 'failed',
        errorMessage: error.message,
        message: `[resume] 执行失败: ${error.message}`
      }, 'worker-failed', 'error');

      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          status: 'failed',
          mineruStatus: 'failed',
          aiStatus: 'failed',
          metadata: {
            processingStage: '',
            processingMsg: `解析失败: ${error.message}`,
            processingUpdatedAt: new Date().toISOString(),
          }
        }, { enqueueOnFailure: true });
      }
    } finally {
      processingMap.delete(task.id);
    }
  }

  /**
   * P0: 处理 MinerU completed 但 Markdown 为空的 completed-empty 语义。
   *
   * 1. 写入 artifact-empty-detected 事件
   * 2. 设置 artifact-empty 状态和 artifactQuality metadata
   * 3. 如果符合 OCR 降级重试条件，执行一次重试
   * 4. 如果不符合或重试后仍为空，设置最终失败态
   *
   * @param {Object} task - 当前 ParseTask
   * @param {Object} materialInfo - 关联的 Material 信息
   * @param {Object} mineruResult - local-adapter 返回的结果（含 markdownEmpty=true）
   * @returns {Promise<boolean>} true 表示已完成处理（调用方应 return），false 表示未处理
   */
  async handleCompletedEmpty(task, materialInfo, mineruResult) {
    const mineruTaskId = mineruResult.mineruTaskId || task.metadata?.mineruTaskId;
    const parsedPrefix = mineruResult.parsedPrefix || `parsed/${task.materialId}/`;
    const parsedArtifacts = mineruResult.parsedArtifacts || [];

    // 构建 artifactQuality 结构化信息
    const artifactQuality = {
      status: 'completed-empty-markdown',
      reason: 'MinerU API completed but markdown/content_list is empty',
      markdownBytes: 0,
      contentListItems: 0,
      hasMiddleJson: parsedArtifacts.some(a => String(a.relativePath || '').includes('middle')),
      hasModelJson: parsedArtifacts.some(a => String(a.relativePath || '').includes('model')),
      hasOriginPdf: parsedArtifacts.some(a => String(a.relativePath || '').endsWith('.pdf')),
    };

    // 写入 artifact-empty-detected 事件
    await logTaskEvent({
      taskId: task.id,
      taskType: 'parse',
      level: 'warn',
      event: 'artifact-empty-detected',
      message: 'MinerU API completed 但 Markdown 为空',
      payload: { mineruTaskId, artifactQuality, parsedPrefix, parsedFilesCount: parsedArtifacts.length },
    });

    // ── 检查 OCR 降级重试条件 ──
    const canRetry = (
      task.metadata?.emptyMarkdownRetryAttempted !== true &&
      task.engine === 'local-mineru' &&
      task.state !== 'canceled' &&
      task.optionsSnapshot?.localEndpoint
    );

    if (canRetry) {
      // 尝试 OCR 降级重试
      const retrySuccess = await this.retryWithOcrDegradation(task, materialInfo, mineruResult);
      if (retrySuccess) return true; // 重试成功，已进入正常后续流程
      // 重试失败（仍为空或异常），进入最终失败态
    }

    // ── 设置 artifact-empty 最终失败态 ──
    const failMsg = task.metadata?.emptyMarkdownRetryAttempted
      ? 'OCR 降级重试后仍未产出可用 Markdown'
      : 'MinerU 已完成但未产出可用 Markdown';

    await this.updateTaskWithRetry(task.id, {
      state: 'failed',
      stage: 'artifact-empty',
      progress: 100,
      errorMessage: failMsg,
      message: failMsg,
      metadata: {
        ...(task.metadata || {}),
        mineruTaskId,
        mineruStatus: 'artifact-empty',
        artifactQuality,
        parsedPrefix,
        parsedFilesCount: parsedArtifacts.length,
        // P0 OOM: parsedArtifacts 不写入 DB（handleCompletedEmpty 路径）
      }
    }, { enqueueOnFailure: true });

    // Material 同步
    if (task.materialId) {
      await this.updateMaterialWithRetry(task.materialId, {
        status: 'failed',
        mineruStatus: 'artifact-empty',
        aiStatus: 'pending',
        metadata: {
          ...(materialInfo.metadata || {}),
          processingStage: 'artifact-empty',
          processingMsg: failMsg,
          artifactQuality,
          processingUpdatedAt: new Date().toISOString(),
        }
      }, { enqueueOnFailure: true });
    }

    processingMap.delete(task.id);
    return true;
  }

  /**
   * P0: 对 completed-empty 任务执行一次 OCR 降级重试。
   *
   * 重试参数：backend=pipeline, parseMethod=ocr, enableOcr=true, enableTable=false, enableFormula=false
   * 重试创建新的 MinerU task，但仍归属于同一个 ParseTask，不创建新 Material / ParseTask。
   * 仅执行一次（通过 emptyMarkdownRetryAttempted 守卫）。
   *
   * @param {Object} task - 当前 ParseTask
   * @param {Object} materialInfo - Material 信息
   * @param {Object} mineruResult - 上次空 Markdown 结果
   * @returns {Promise<boolean>} true 表示重试成功（产出非空 Markdown），false 表示仍为空或异常
   */
  async retryWithOcrDegradation(task, materialInfo, mineruResult) {
    const retryProfile = {
      backend: 'pipeline',
      parseMethod: 'ocr',
      enableOcr: true,
      enableTable: false,
      enableFormula: false,
    };

    // 标记已尝试重试（BEFORE 重试，防止异常后无限循环）
    await this.updateTaskWithRetry(task.id, {
      state: 'running',
      stage: 'artifact-empty-ocr-retry',
      message: '检测到 Markdown 为空，正在尝试 OCR 降级重试...',
      metadata: {
        ...(task.metadata || {}),
        emptyMarkdownRetryAttempted: true,
        emptyMarkdownRetryProfile: retryProfile,
        emptyMarkdownRetryStartedAt: new Date().toISOString(),
      }
    }, { enqueueOnFailure: true });

    // 写入重试开始事件
    await logTaskEvent({
      taskId: task.id,
      taskType: 'parse',
      level: 'info',
      event: 'artifact-empty-ocr-retry-started',
      message: 'OCR 降级重试已启动',
      payload: { retryProfile, previousMineruTaskId: mineruResult.mineruTaskId },
    });

    try {
      // 获取原始文件流
      const objectName = materialInfo.objectName
        || materialInfo.metadata?.objectName
        || task.optionsSnapshot?.material?.objectName
        || task.optionsSnapshot?.material?.metadata?.objectName;
      if (!objectName || !this.minioContext?.getFileStream) {
        console.error(`[task-worker] OCR retry: 无法获取原始文件流 for task ${task.id}`);
        await logTaskEvent({
          taskId: task.id, taskType: 'parse', level: 'error',
          event: 'artifact-empty-ocr-retry-failed',
          message: 'OCR 降级重试失败：无法获取原始文件流',
          payload: { reason: 'missing-file-stream' },
        });
        return false;
      }

      const fileStream = await this.minioContext.getFileStream(objectName);

      // 构建 OCR 重试的 task options
      const retryTask = {
        ...task,
        optionsSnapshot: {
          ...(task.optionsSnapshot || {}),
          backend: retryProfile.backend,
          parseMethod: retryProfile.parseMethod,
          enableOcr: retryProfile.enableOcr,
          enableTable: retryProfile.enableTable,
          enableFormula: retryProfile.enableFormula,
        }
      };

      const retryResult = await this.mineruProcessor({
        task: retryTask,
        material: materialInfo,
        fileStream,
        fileName: materialInfo.fileName || 'document.pdf',
        mimeType: materialInfo.mimeType || 'application/pdf',
        timeoutMs: Number(task.optionsSnapshot?.localTimeout || 3600) * 1000,
        minioContext: this.minioContext,
        updateProgress: async (updateInfo) => {
          const eventName = updateInfo.stage === 'store' ? 'stage-changed' : 'progress-update';
          await this.transition(task, {
            ...updateInfo,
            message: `[OCR \u964d\u7ea7\u91cd\u8bd5] ${updateInfo.message || ''}`,
          }, eventName);
        }
      });

      // 检查重试结果
      if (retryResult.markdownEmpty === true) {
        // 重试后仍为空
        await logTaskEvent({
          taskId: task.id, taskType: 'parse', level: 'warn',
          event: 'artifact-empty-ocr-retry-failed',
          message: 'OCR 降级重试后 Markdown 仍为空',
          payload: { retryMineruTaskId: retryResult.mineruTaskId, retryProfile },
        });
        // 更新 metadata 保留重试的 mineruTaskId
        task.metadata = {
          ...(task.metadata || {}),
          emptyMarkdownRetryAttempted: true,
          emptyMarkdownRetryMineruTaskId: retryResult.mineruTaskId,
          emptyMarkdownRetryCompletedAt: new Date().toISOString(),
          emptyMarkdownRetryResult: 'still-empty',
        };
        return false;
      }

      // 重试成功！Markdown 非空
      const markdownObjectName = retryResult.objectName;
      const parsedPrefix = retryResult.parsedPrefix || `parsed/${task.materialId}/`;
      const parsedArtifacts = Array.isArray(retryResult.parsedArtifacts) ? retryResult.parsedArtifacts : [];
      const parsedFilesCount = Number(retryResult.parsedFilesCount || parsedArtifacts.length || 1);
      const zipObjectName = retryResult.zipObjectName || null;

      await logTaskEvent({
        taskId: task.id, taskType: 'parse', level: 'info',
        event: 'artifact-empty-ocr-retry-completed',
        message: 'OCR 降级重试成功，已产出非空 Markdown',
        payload: { retryMineruTaskId: retryResult.mineruTaskId, retryProfile, markdownObjectName },
      });

      // P0 OOM 修复：parsedArtifacts 写入 MinIO manifest
      let artifactManifestObjectName = null;
      if (parsedArtifacts.length > 0 && this.minioContext) {
        try {
          artifactManifestObjectName = await this.writeArtifactManifest(task.materialId, parsedArtifacts);
        } catch (e) {
          console.warn(`[task-worker] Failed to write artifact manifest (OCR retry): ${e.message}`);
        }
      }

      // 写入正常完成态
      await this.transition(task, {
        stage: 'complete',
        state: 'ai-pending',
        progress: 100,
        message: 'OCR 降级重试成功，MinerU 解析完成，等待 AI 元数据识别',
        metadata: {
          ...(task.metadata || {}),
          mineruStatus: 'completed',
          markdownObjectName,
          mineruTaskId: retryResult.mineruTaskId,
          parsedPrefix,
          parsedFilesCount,
          // P0 OOM: 不再写入完整 parsedArtifacts 到 DB
          artifactManifestObjectName,
          zipObjectName: zipObjectName || undefined,
          emptyMarkdownRetryMineruTaskId: retryResult.mineruTaskId,
          emptyMarkdownRetryCompletedAt: new Date().toISOString(),
          emptyMarkdownRetryResult: 'success',
          parsedAt: new Date().toISOString()
        },
        completedAt: new Date().toISOString()
      }, 'worker-completed');

      // Material 同步
      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          mineruStatus: 'completed',
          metadata: {
            ...(materialInfo.metadata || {}),
            markdownObjectName,
            parsedPrefix,
            parsedFilesCount,
            // P0 OOM: 不再写入完整 parsedArtifacts 到 DB
            artifactManifestObjectName,
            zipObjectName: zipObjectName || undefined,
            processingStage: 'mineru-completed',
            processingMsg: 'OCR 降级重试成功，等待 AI 元数据识别',
            processingUpdatedAt: new Date().toISOString()
          }
        }, { enqueueOnFailure: true });
      }

      // 触发 AI Job
      await this.tryCreateAiJob(task, markdownObjectName);
      return true;

    } catch (retryErr) {
      console.error(`[task-worker] OCR retry failed for task ${task.id}: ${retryErr.message}`);
      await logTaskEvent({
        taskId: task.id, taskType: 'parse', level: 'error',
        event: 'artifact-empty-ocr-retry-failed',
        message: `OCR 降级重试异常: ${retryErr.message}`,
        payload: { error: retryErr.message, retryProfile },
      });
      // 更新 metadata
      task.metadata = {
        ...(task.metadata || {}),
        emptyMarkdownRetryAttempted: true,
        emptyMarkdownRetryCompletedAt: new Date().toISOString(),
        emptyMarkdownRetryResult: 'error',
        emptyMarkdownRetryError: retryErr.message,
      };
      return false;
    }
  }

  /**
   * 尝试为完成的 ParseTask 创建 AI Metadata Job。
   * 创建失败不伪装为解析失败，仅记录 warning 事件。
   * @param {object} task - 当前 ParseTask
   * @param {string} [markdownObjectName] - Markdown 产物的 MinIO objectName（可选）
   */
  async tryCreateAiJob(task, markdownObjectName) {
    try {
      const result = await createAiMetadataJob({
        parseTaskId: task.id,
        materialId: task.materialId || null,
        inputMarkdownObjectName: markdownObjectName || null,
      });

      if (result.created) {
        console.log(`[task-worker] AI Job created: ${result.jobId} for task ${task.id}`);
        // 写入 ai-job-created 事件
        await logTaskEvent({
          taskId: task.id,
          taskType: 'parse',
          level: 'info',
          event: 'ai-job-created',
          message: `AI Metadata Job 已创建: ${result.jobId}`,
          payload: { aiJobId: result.jobId },
        });

        // 将 aiJobId 同步更新到 ParseTask 记录中
        await updateTask(task.id, {
          aiJobId: result.jobId,
          metadata: {
            aiJobId: result.jobId
          }
        });
      } else if (result.reason === 'duplicate') {
        console.log(`[task-worker] AI Job already exists for task ${task.id}: ${result.jobId}`);
        // 去重跳过
        await logTaskEvent({
          taskId: task.id,
          taskType: 'parse',
          level: 'info',
          event: 'ai-job-skipped',
          message: `AI Metadata Job 已存在，跳过创建 (existingJobId=${result.jobId})`,
          payload: { existingJobId: result.jobId },
        });

        // 即使是重复，也确保记录中有这个 ID
        await updateTask(task.id, {
          aiJobId: result.jobId,
          metadata: {
            aiJobId: result.jobId
          }
        });
      } else {
        console.warn(`[task-worker] AI Job creation failed for task ${task.id}: ${result.reason}`);
        // 创建失败——仅标记 AI 阶段问题，不回滚 MinerU 成果
        await logTaskEvent({
          taskId: task.id,
          taskType: 'parse',
          level: 'warn',
          event: 'ai-job-create-failed',
          message: `AI Metadata Job 创建失败: ${result.reason}`,
          payload: { reason: result.reason },
        });
        if (task.materialId) {
          await this.updateMaterialWithRetry(task.materialId, {
            aiStatus: 'create-failed',
            metadata: {
              aiCreateFailedReason: result.reason,
              processingUpdatedAt: new Date().toISOString()
            }
          }, { enqueueOnFailure: true });
        }
      }
    } catch (error) {
      // 兜底：创建过程本身异常，只记日志不影响 ParseTask 状态
      console.warn(`[task-worker] tryCreateAiJob unexpected error: ${error.message}`);
      await logTaskEvent({
        taskId: task.id,
        taskType: 'parse',
        level: 'warn',
        event: 'ai-job-create-failed',
        message: `AI Metadata Job 创建异常: ${error.message}`,
        payload: { error: error.message },
      });
      if (task.materialId) {
        await this.updateMaterialWithRetry(task.materialId, {
          aiStatus: 'create-failed',
          metadata: {
            aiCreateFailedReason: error.message,
            processingUpdatedAt: new Date().toISOString()
          }
        }, { enqueueOnFailure: true });
      }
    }
  }

  /**
   * 通用状态转换：更新任务并写事件日志。
   * progress-update 事件使用语义去重 key 降噪，只有 state/stage/message 语义变化时才写事件。
   * 其他事件类型（stage-changed, worker-picked, ...）不受限制。
   *
   * @param {object} task - 当前任务对象
   * @param {object} update - 要写入的更新内容
   * @param {string} eventName - 事件名称
   * @param {string} [level='info'] - 事件级别
   * @returns {Promise<void>}
   */
  async transition(task, update, eventName, level = 'info') {
    const success = await this.updateTaskWithRetry(task.id, update, { enqueueOnFailure: true });
    if (!success) return;

    // SSE 事件广播（若已注入事件总线）——始终广播，保证 UI 实时刷新
    if (this.eventBus?.emit) {
      try {
        this.eventBus.emit('task-update', {
          taskId: task.id,
          event: eventName,
          level,
          update,
          at: new Date().toISOString(),
        });
      } catch (e) {
        console.warn(`[task-worker] eventBus emit failed: ${e.message}`);
      }
    }

    // progress-update 事件降噪：构造语义 key，同 key 不重复写事件日志
    if (eventName === 'progress-update') {
      const obs = update.metadata?.mineruObservedProgress || {};
      const logStatus = obs.logSource?.logSourceSelectedReason || (obs.logSource?.logSourceExists ? 'exists' : 'missing');
      const activityLevel = obs.activityLevel || '';
      const phase = obs.phase || '';
      const windowIdx = obs.window?.index || '';
      const pageStart = obs.window?.pageStart || '';
      const unitType = obs.stage?.unitType || '';

      const semanticKey = [
        `state=${update.state || task.state || ''}`,
        `stage=${update.stage || task.stage || ''}`,
        `message=${update.message || ''}`,
        `logStatus=${logStatus}`,
        `activity=${activityLevel}`,
        `phase=${phase}`,
        `window=${windowIdx}`,
        `page=${pageStart}`,
        `unitType=${unitType}`
      ].join('|');
      const prevKey = task.metadata?.progressEventKey || '';
      if (semanticKey === prevKey) {
        // key 未变，不写事件日志（但 SSE 和 task update 已完成）
        return;
      }
      // 更新 progressEventKey 到 DB
      await this.updateTaskWithRetry(task.id, {
        metadata: { ...(task.metadata || {}), progressEventKey: semanticKey }
      }, { enqueueOnFailure: true });
      // 同步更新内存对象，避免长轮询中旧 task 对象导致去重失效
      if (!task.metadata) task.metadata = {};
      task.metadata.progressEventKey = semanticKey;
    }

    // 写事件日志
    await logTaskEvent({
      taskId: task.id,
      event: eventName,
      level,
      message: update.message || `Status changed to ${update.state}`,
      payload: update
    });
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async updateTaskWithRetry(taskId, update, opts = {}) {
    const maxAttempts = Number.isFinite(opts.maxAttempts) ? opts.maxAttempts : 5;
    for (let i = 0; i < maxAttempts; i++) {
      const ok = await this.taskClient.updateTask(taskId, update);
      if (ok) return true;
      await this.sleep(Math.min(5000, 300 * Math.pow(2, i)));
    }
    if (opts.enqueueOnFailure) {
      this.pendingTaskPatches.set(String(taskId), update);
    }
    return false;
  }

  async updateMaterialWithRetry(materialId, update, opts = {}) {
    const maxAttempts = Number.isFinite(opts.maxAttempts) ? opts.maxAttempts : 5;
    for (let i = 0; i < maxAttempts; i++) {
      const ok = await this.taskClient.updateMaterial(materialId, update);
      if (ok) return true;
      await this.sleep(Math.min(5000, 300 * Math.pow(2, i)));
    }
    if (opts.enqueueOnFailure) {
      this.pendingMaterialPatches.set(String(materialId), update);
    }
    return false;
  }

  async flushPendingPatches() {
    const taskEntries = Array.from(this.pendingTaskPatches.entries());
    for (const [taskId, patch] of taskEntries) {
      const ok = await this.taskClient.updateTask(taskId, patch);
      if (ok) this.pendingTaskPatches.delete(taskId);
    }
    const materialEntries = Array.from(this.pendingMaterialPatches.entries());
    for (const [materialId, patch] of materialEntries) {
      const ok = await this.taskClient.updateMaterial(materialId, patch);
      if (ok) this.pendingMaterialPatches.delete(materialId);
    }
  }
}
