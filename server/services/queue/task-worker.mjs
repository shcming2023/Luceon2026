/**
 * task-worker.mjs - ParseTask 任务执行器骨架
 * 
 * 约束要求：
 * 1. 模拟执行需明确标记 "worker skeleton"
 * 2. 内存锁防重复处理
 * 3. 常量化轮询与延迟配置
 * 4. 解析完成后自动创建 AI Metadata Job（含去重保护）
 */

import { getAllTasks, updateTask } from '../tasks/task-client.mjs';
import { logTaskEvent } from '../logging/task-events.mjs';
import { processWithLocalMinerU } from '../mineru/local-adapter.mjs';
import { createAiMetadataJob } from '../ai/metadata-job-client.mjs';

// 约束 3: 集中配置常量
const POLL_INTERVAL_MS = 10000; // 10秒检查一次
const SIMULATED_DELAY_MS = 5000; // 每个阶段模拟耗时 5秒

// 约束 2: 内存队列锁，防止同一个实例中的多个 tick 重复处理
const processingMap = new Set();

export class ParseTaskWorker {
  constructor(minioContext = null) {
    this.timer = null;
    this.isRunning = false;
    this.minioContext = minioContext;
  }

  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    console.log('[task-worker] ParseTask Worker started (skeleton mode)');
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
    const pendingTasks = tasks.filter(t => t.state === 'pending');

    for (const task of pendingTasks) {
      if (processingMap.has(task.id)) continue;
      
      // 异步处理，不阻塞 tick 扫描下一个
      this.processTask(task);
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
        
        const markdownObjectName = mineruResult.objectName;

        await this.transition(task, {
          stage: 'complete',
          state: 'ai-pending',
          progress: 100,
          message: 'MinerU 解析完成，产物已落库，等待 AI 元数据识别',
          metadata: {
            ...(task.metadata || {}),
            markdownObjectName,
            mineruTaskId: mineruResult.mineruTaskId,
            parsedAt: new Date().toISOString()
          },
          completedAt: new Date().toISOString()
        }, 'worker-completed');

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
            ...(task.metadata || {}),
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
            ...(task.metadata || {}),
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
    }
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
