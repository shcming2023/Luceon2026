/**
 * metadata-worker.mjs - AI 元数据识别任务执行器骨架
 * 
 * 约束要求：
 * 1. 模拟执行需明确标记 "[ai worker skeleton]"
 * 2. 内存锁防重复处理
 * 3. 结果符合 PRD 10.5.3 基础 schema
 */

import { getAllJobs, updateJob } from './metadata-job-client.mjs';
import { logTaskEvent } from '../logging/task-events.mjs';

const POLL_INTERVAL_MS = 10000; // 10秒检查一次
const SIMULATED_DELAY_MS = 5000; // 每个阶段模拟耗时 5秒

// 内存队列锁
const processingMap = new Set();

export class AiMetadataWorker {
  constructor() {
    this.timer = null;
    this.isRunning = false;
  }

  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    console.log('[ai-worker] AI Metadata Worker started (skeleton mode)');
    this.tick();
  }

  stop() {
    if (this.timer) clearTimeout(this.timer);
    this.isRunning = false;
    console.log('[ai-worker] AI Metadata Worker stopped');
  }

  async tick() {
    try {
      await this.scanAndProcess();
    } catch (error) {
      console.error(`[ai-worker] Error in tick: ${error.message}`);
    } finally {
      if (this.isRunning) {
        this.timer = setTimeout(() => this.tick(), POLL_INTERVAL_MS);
      }
    }
  }

  async scanAndProcess() {
    const jobs = await getAllJobs();
    const pendingJobs = jobs.filter(j => j.state === 'pending');

    for (const job of pendingJobs) {
      if (processingMap.has(job.id)) continue;
      this.processJob(job);
    }
  }

  async processJob(job) {
    processingMap.add(job.id);
    console.log(`[ai-worker] Picked up AI job: ${job.id} (parseTask=${job.parseTaskId})`);

    try {
      // 1. 进入 running 状态
      await this.transition(job, {
        state: 'running',
        progress: 10,
        message: '[ai worker skeleton] 正在拉取 Markdown 内容并初始化 AI 模型...'
      }, 'ai-worker-picked');

      await this.sleep(SIMULATED_DELAY_MS);

      // 2. 模拟分析进度
      await this.transition(job, {
        progress: 50,
        message: '[ai worker skeleton] 正在解析题目结构与提取元数据...'
      }, 'ai-progress-update');

      await this.sleep(SIMULATED_DELAY_MS);

      // 3. 构建模拟结果 (PRD 10.5.3)
      const simulatedResult = {
        title: "模拟试卷：2026年八年级数学期中测试",
        subject: "数学",
        grade: "G8",
        semester: "上册",
        materialType: "试卷",
        language: "中文",
        country: "中国",
        curriculum: "人教版",
        publisher: "模拟出版社",
        examType: "期中考试",
        difficulty: "中等",
        knowledgePoints: ["一次函数", "几何证明", "全等三角形"],
        tags: ["初二", "数学", "期中", "模拟"],
        summary: "[ai worker skeleton] 本文档是一份八年级数学期中考试模拟卷，涵盖了函数基础与几何证明核心考点。",
        confidence: 95,
        fieldConfidence: {
          subject: 99,
          grade: 98,
          materialType: 92
        },
        needsReview: false,
        warnings: []
      };

      // 4. 完成任务进入 review-pending (或者根据 needsReview 进入相应状态)
      await this.transition(job, {
        state: 'review-pending',
        progress: 100,
        message: '[ai worker skeleton] AI 元数据识别完成，等待人工拉回确认',
        result: simulatedResult,
        confidence: simulatedResult.confidence,
        needsReview: simulatedResult.needsReview,
        updatedAt: new Date().toISOString(),
        completedAt: new Date().toISOString()
      }, 'ai-worker-completed');

    } catch (error) {
      console.error(`[ai-worker] Job ${job.id} failed: ${error.message}`);
      await this.transition(job, {
        state: 'failed',
        errorMessage: error.message,
        message: `[ai worker skeleton] 执行失败: ${error.message}`
      }, 'ai-worker-failed', 'error');
    } finally {
      processingMap.delete(job.id);
    }
  }

  async transition(job, update, eventName, level = 'info') {
    const success = await updateJob(job.id, update);
    if (success) {
      // 写入事件日志，注意日志的 taskId 应该是关联的 parseTaskId，以便在详情页展示
      await logTaskEvent({
        taskId: job.parseTaskId,
        taskType: 'parse', // 解析任务流中的事件
        event: eventName,
        level,
        message: update.message || `AI Job ${job.id} status changed to ${update.state}`,
        payload: {
          aiJobId: job.id,
          ...update
        }
      });
    }
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
