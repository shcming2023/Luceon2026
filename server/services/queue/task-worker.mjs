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
import { processWithLocalMinerU } from '../mineru/local-adapter.mjs';
import { createAiMetadataJob } from '../ai/metadata-job-client.mjs';

// 约束 3: 集中配置常量
const POLL_INTERVAL_MS = 10000; // 10秒检查一次
const SIMULATED_DELAY_MS = 5000; // 每个阶段模拟耗时 5秒

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
    const tasks = await getAllTasks();

    // 每轮 tick 顺便检查一次 stale-running 任务（不阻塞 pending 调度）
    await this.recoverStaleRunningTasks(tasks);

    const pendingTasks = tasks.filter(t => t.state === 'pending');
    for (const task of pendingTasks) {
      if (processingMap.has(task.id)) continue;
      // 异步处理，不阻塞 tick 扫描下一个
      this.processTask(task);
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
      const tasks = await getAllTasks();
      const now = Date.now();
      let recovered = 0;
      for (const task of tasks) {
        if (task.state !== 'running' && task.state !== 'result-store') continue;
        const updatedAt = task.updatedAt ? new Date(task.updatedAt).getTime() : 0;
        const timeoutMs = Number(task.optionsSnapshot?.localTimeout || 3600) * 1000;
        // 若任务还在健康窗口内（不更新时间未超时），自愈时不处理；
        // 但“本进程启动恢复”的语义是：无论时间多久，running/result-store 在启动时先归位为 pending，
        // 由轮询重新拾取，避免重启后任务永久卡死。
        const isExplicitlyStale = updatedAt > 0 && (now - updatedAt) > (timeoutMs + STALE_GRACE_MS);
        await updateTask(task.id, {
          state: 'pending',
          stage: 'upload',
          progress: 0,
          message: isExplicitlyStale
            ? `检测到卡住的解析任务，已自动重置为 pending。updatedAt=${task.updatedAt}`
            : '服务重启恢复：在执行中的任务已重置为 pending等待重新拾取',
          updatedAt: new Date().toISOString(),
        });
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
    } catch (err) {
      console.error(`[task-worker] runRecoveryScan error: ${err.message}`);
    }
  }

  /**
   * 日常轮询时的超时自愈：对 running/result-store 持续超时的任务自动重置为 pending
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
      await updateTask(task.id, {
        state: 'pending',
        stage: 'upload',
        progress: 0,
        message: `检测到卡住的解析任务（超过 ${Math.round((timeoutMs + STALE_GRACE_MS) / 1000)}s不更新），已重置为 pending`,
        updatedAt: new Date().toISOString(),
      });
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

  async processTask(task) {
    processingMap.add(task.id);
    const modeLabel = task.engine === 'local-mineru' ? 'local-mineru' : 'worker skeleton';
    console.log(`[task-worker] Picked up task: ${task.id} (${modeLabel})`);

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
          const mineruResult = await processWithLocalMinerU({
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
            }
          });
          markdownObjectName = mineruResult.objectName;
          mineruTaskId = mineruResult.mineruTaskId;
          parsedPrefix = mineruResult.parsedPrefix || `parsed/${task.materialId}/`;
          parsedArtifacts = Array.isArray(mineruResult.parsedArtifacts) ? mineruResult.parsedArtifacts : [];
          parsedFilesCount = Number(mineruResult.parsedFilesCount || parsedArtifacts.length || 1);
          zipObjectName = mineruResult.zipObjectName || null;
          artifactIncomplete = mineruResult.artifactIncomplete === true;

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

        await this.transition(task, {
          stage: 'complete',
          state: 'ai-pending',
          progress: 100,
          message: isMarkdown ? 'Markdown 文件无需解析，正在准备 AI 任务' : 'MinerU 解析完成，产物已落库，等待 AI 元数据识别',
          metadata: {
            ...(task.metadata || {}),
            markdownObjectName,
            mineruTaskId,
            parsedPrefix,
            parsedFilesCount,
            parsedArtifacts,
            zipObjectName: zipObjectName || undefined,
            artifactIncomplete,
            parsedAt: new Date().toISOString()
          },
          completedAt: new Date().toISOString()
        }, 'worker-completed');

        // 补齐 Material 状态：确保 AI 阶段开始前，Material 表达“解析阶段完成” (Requirement 3)
        await updateMaterial(task.materialId, {
          mineruStatus: 'completed',
          metadata: {
            ...(materialInfo.metadata || {}),
            markdownObjectName,
            parsedPrefix,
            parsedFilesCount,
            parsedArtifacts,
            zipObjectName: zipObjectName || undefined,
            processingStage: 'ai',
            processingMsg: '解析完成，等待 AI 元数据识别',
            processingUpdatedAt: new Date().toISOString()
          }
        });

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
          completedAt: new Date().toISOString()
        }, 'worker-completed');

        // ── 解析成功后自动创建 AI Metadata Job ──────────────────
        await this.tryCreateAiJob(task);
      }

    } catch (error) {
      console.error(`[task-worker] Task ${task.id} failed: ${error.message}`);
      await this.transition(task, {
        state: 'failed',
        errorMessage: error.message,
        message: `[${modeLabel}] 执行失败: ${error.message}`
      }, 'worker-failed', 'error');
    } finally {
      processingMap.delete(task.id);
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
        // 创建失败
        await logTaskEvent({
          taskId: task.id,
          taskType: 'parse',
          level: 'warn',
          event: 'ai-job-create-failed',
          message: `AI Metadata Job 创建失败: ${result.reason}`,
          payload: { reason: result.reason },
        });
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
    }
  }

  async transition(task, update, eventName, level = 'info') {
    const success = await updateTask(task.id, update);
    if (success) {
      await logTaskEvent({
        taskId: task.id,
        event: eventName,
        level,
        message: update.message || `Status changed to ${update.state}`,
        payload: update
      });
      // SSE 事件广播（若已注入事件总线）
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
    }
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
