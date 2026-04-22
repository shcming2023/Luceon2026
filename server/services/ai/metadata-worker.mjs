/**
 * metadata-worker.mjs - AI 元数据识别任务执行器
 * 
 * 职责：
 * 1. 扫描 pending 状态的 AI Metadata Jobs
 * 2. 从 MinIO 获取解析产物 Markdown 内容
 * 3. 选取合适的 Provider (Ollama/OpenAI) 进行元数据提取
 * 4. 解析结果并推进状态到 review-pending 或 confirmed
 * 5. 处理 Fallback 与降级 logic
 */

import { getAllJobs, updateJob } from './metadata-job-client.mjs';
import { getTaskById } from '../tasks/task-client.mjs';
import { logTaskEvent } from '../logging/task-events.mjs';
import { getSettings } from '../settings/settings-client.mjs';

import { OllamaProvider } from './providers/ollama.mjs';
import { OpenAiCompatibleProvider } from './providers/openai-compatible.mjs';

const POLL_INTERVAL_MS = 10000;

// 内存队列锁
const processingMap = new Set();

export class AiMetadataWorker {
  /**
   * @param {object|null} contextOrOptions - 兼容旧调用：传 minioContext 对象；新调用：传 options 对象
   * @param {object} [contextOrOptions.minioContext] - MinIO 上下文
   * @param {Function} [contextOrOptions.onComplete] - AI Job 到达终态时的回调 (job, update) => Promise<void>
   */
  constructor(contextOrOptions = null) {
    const options = contextOrOptions?.getFileStream ? { minioContext: contextOrOptions } : (contextOrOptions || {});
    this.timer = null;
    this.isRunning = false;
    this.minioContext = options.minioContext || null;
    this.onComplete = options.onComplete || null;
    // 默认超时时间，用于 stale running job 判断
    this.defaultTimeoutMs = 120000; // 120 秒
  }

  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    console.log('[ai-worker] AI Metadata Worker started');
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

  /**
   * 扫描并处理待执行的 AI Jobs
   * - 严格串行：每轮最多处理 1 个 job
   * - pending job 按 createdAt 升序（先处理最早任务）
   * - 跳过已在处理中的 job
   */
  async scanAndProcess() {
    // 如果当前已有 job 正在执行，直接跳过本轮 tick
    if (processingMap.size > 0) {
      console.log(`[ai-worker] Skipping scan: ${processingMap.size} job(s) already in progress`);
      return;
    }

    const jobs = await getAllJobs();

    // 处理 stale running jobs（长时间卡住的 running 状态）
    await this.recoverStaleRunningJobs(jobs);

    // 按 createdAt 升序排序，选择最早的 pending job
    const pendingJobs = jobs
      .filter(j => j.state === 'pending')
      .sort((a, b) => {
        const timeA = a.createdAt ? new Date(a.createdAt).getTime() : 0;
        const timeB = b.createdAt ? new Date(b.createdAt).getTime() : 0;
        return timeA - timeB;
      });

    // 每轮最多拾取 1 个 pending job
    if (pendingJobs.length > 0) {
      const job = pendingJobs[0];
      if (!processingMap.has(job.id)) {
        await this.processJob(job);
      }
    }
  }

  /**
   * 恢复长时间卡住的 running job
   * 规则：state=running 且 updatedAt 超过 timeoutMs + 60s → 标记 failed 或 reset 为 pending
   */
  async recoverStaleRunningJobs(jobs) {
    const runningJobs = jobs.filter(j => j.state === 'running');
    const now = Date.now();
    const GRACE_PERIOD_MS = 60000; // 60 秒额外缓冲

    for (const job of runningJobs) {
      if (!job.updatedAt) continue;
      
      const updatedAt = new Date(job.updatedAt).getTime();
      const staleThreshold = updatedAt + this.defaultTimeoutMs + GRACE_PERIOD_MS;
      
      if (now > staleThreshold) {
        console.warn(`[ai-worker] Stale running job detected: ${job.id}, updatedAt=${job.updatedAt}`);
        
        await logTaskEvent({
          taskId: job.parseTaskId,
          taskType: 'parse',
          event: 'ai-stale-running-recovered',
          level: 'warn',
          message: `检测到卡住的 AI Job，已自动重置为 pending 状态`,
          payload: {
            aiJobId: job.id,
            previousState: 'running',
            originalUpdatedAt: job.updatedAt,
            staleThresholdMs: this.defaultTimeoutMs + GRACE_PERIOD_MS
          }
        });

        // 重置为 pending，等待下次扫描处理
        await updateJob(job.id, {
          state: 'pending',
          message: '因长时间卡住，已自动重置为 pending 状态',
          updatedAt: new Date().toISOString()
        });
      }
    }
  }

  /**
   * 核心处理逻辑
   */
  async processJob(job) {
    processingMap.add(job.id);
    const startTime = Date.now();
    console.log(`[ai-worker] Picking up job: ${job.id} (parseTask=${job.parseTaskId})`);

    try {
      // 1. 获取全局设置
      const settings = await getSettings();
      // 优先读取 aiConfig，兼容旧的 ai
      const rawAiConfig = settings.aiConfig || settings.ai || {};

      // 降级检查：未启用 AI
      if (rawAiConfig.aiEnabled === false) {
        return await this.degradeToSkeleton(job, 'AI 功能已从控制台关闭，降级为骨架模拟');
      }

      // 2. 确定 Provider 配置 (优先选择 providers 数组中启用且优先级最高的)
      let aiSettings = {};
      let providerId = 'ollama';

      if (Array.isArray(rawAiConfig.providers) && rawAiConfig.providers.length > 0) {
        const sortedEnabled = rawAiConfig.providers
          .filter(p => p.enabled !== false)
          .sort((a, b) => (a.priority ?? 999) - (b.priority ?? 999));
        
        if (sortedEnabled.length > 0) {
          const chosen = sortedEnabled[0];
          providerId = chosen.provider || chosen.id || 'ollama';
          // 规范化字段：将 chosen 的字段映射到 aiSettings 中
          aiSettings = {
            ...rawAiConfig, // 继承全局配置 (如 confidenceThreshold)
            ...chosen,
            baseUrl: chosen.apiEndpoint || chosen.baseUrl,
            timeoutMs: this.normalizeTimeout(chosen.timeout || rawAiConfig.timeout)
          };
        } else {
          aiSettings = { ...rawAiConfig, timeoutMs: this.normalizeTimeout(rawAiConfig.timeout) };
          providerId = aiSettings.aiProviderId || aiSettings.providerId || 'ollama';
        }
      } else {
        aiSettings = { ...rawAiConfig, timeoutMs: this.normalizeTimeout(rawAiConfig.timeout) };
        providerId = aiSettings.aiProviderId || aiSettings.providerId || 'ollama';
      }

      let provider = this.createProvider(providerId, aiSettings);

      // 3. 获取 ParseTask 信息与 Markdown 内容
      const parseTask = await getTaskById(job.parseTaskId);
      const markdownObjectName = parseTask?.metadata?.markdownObjectName || job.inputMarkdownObjectName;
      
      if (!markdownObjectName || !this.minioContext) {
        return await this.degradeToSkeleton(job, '未找到 Markdown 产物或存储上下文不可用，降级为骨架模拟');
      }

      let markdownContent = '';
      try {
        const stream = await this.minioContext.getFileStream(markdownObjectName);
        markdownContent = await this.streamToString(stream);
      } catch (err) {
        return await this.degradeToSkeleton(job, `拉取 Markdown 内容失败: ${err.message}，降级为骨架模拟`);
      }

      if (!markdownContent.trim()) {
        throw new Error('Markdown 内容为空，无法提取元数据');
      }

      // 4. 内容截断处理 (根据 PRD 10.5.5)
      const MAX_CHARS = 32000; // 约 8000 tokens
      let isTruncated = false;
      const originalLength = markdownContent.length;
      if (originalLength > MAX_CHARS) {
        markdownContent = markdownContent.slice(0, MAX_CHARS);
        isTruncated = true;
        await logTaskEvent({
          taskId: job.parseTaskId,
          event: 'ai-content-truncated',
          level: 'info',
          message: `Markdown 内容过长已截断 (${originalLength} -> ${MAX_CHARS} 字符)`,
          payload: { originalLength, truncatedLength: MAX_CHARS }
        });
      }

      // 5. 执行 AI 识别
      const requestPayload = { 
        provider: providerId, 
        model: provider.model, 
        baseUrl: provider.baseUrl,
        timeoutMs: provider.timeoutMs,
        inputLength: markdownContent.length 
      };

      await this.transition(job, {
        state: 'running',
        progress: 20,
        message: `正在使用 ${providerId} (${provider.model}) 进行识别...`
      }, 'ai-provider-request-started', 'info', requestPayload);

      let aiResponse;
      try {
        aiResponse = await this.executeWithFallback(provider, markdownContent, aiSettings);
        
        await logTaskEvent({
          taskId: job.parseTaskId,
          event: 'ai-provider-request-succeeded',
          level: 'info',
          message: `AI Provider (${providerId}) 响应成功`,
          payload: { 
            ...requestPayload,
            durationMs: aiResponse.usage?.total_duration_ms,
            usage: aiResponse.usage 
          }
        });
      } catch (err) {
        const durationMs = Date.now() - startTime;
        console.error(`[ai-worker] Job ${job.id} failed after attempts: ${err.message}`);
        
        // 增强错误日志：记录详细的错误信息
        const errorPayload = {
          ...requestPayload,
          durationMs,
          errorName: err.name,
          errorMessage: err.message,
          errorCauseCode: err.cause?.code,
          errorCauseMessage: err.cause?.message
        };

        await logTaskEvent({
          taskId: job.parseTaskId,
          event: 'ai-provider-request-failed',
          level: 'warn',
          message: `AI Provider 调用失败: ${err.message}`,
          payload: errorPayload
        });

        // 如果所有 provider 都失败，尝试降级到模拟
        return await this.degradeToSkeleton(job, `AI Provider 调用全部失败: ${err.message}，自动降级为模拟结果完成链路`);
      }

      // 6. 结果后处理与归一化 (TASK-24)
      const result = this.normalizeResult(aiResponse.result);
      // 记录原始响应预览
      result.rawPreview = aiResponse.rawResponse?.slice(0, 1000);

      const confidence = result.confidence || aiResponse.usage?.confidence || 0;
      
      // 判断是否需要人工审核
      const threshold = Number(aiSettings.confidenceThreshold || 80);
      const isLowConfidence = confidence < threshold;
      const missingKeyFields = !result.subject || !result.grade || !result.materialType;
      const requireAllReview = aiSettings.requireAllReview === true;
      
      const needsReview = isLowConfidence || missingKeyFields || requireAllReview || aiResponse.fallbackOccurred;

      // 7. 完成任务
      const finalState = needsReview ? 'review-pending' : 'confirmed';
      const duration = Date.now() - startTime;

      await this.transition(job, {
        state: finalState,
        progress: 100,
        message: `AI 识别完成 (${duration}ms)${aiResponse.fallbackOccurred ? ' [Fallback已发生]' : ''}`,
        result,
        confidence,
        needsReview,
        providerId: aiResponse.provider,
        model: aiResponse.model,
        updatedAt: new Date().toISOString(),
        completedAt: new Date().toISOString()
      }, 'ai-provider-success', 'info', { 
        duration, 
        confidence, 
        needsReview, 
        fallback: !!aiResponse.fallbackOccurred 
      });

    } catch (error) {
      console.error(`[ai-worker] Job ${job.id} unexpected error: ${error.message}`);
      await this.transition(job, {
        state: 'failed',
        errorMessage: error.message,
        message: `AI 识别异常: ${error.message}`
      }, 'ai-provider-failed', 'error', { error: error.message });
    } finally {
      processingMap.delete(job.id);
    }
  }

  /**
   * 降级执行：返回模拟结果
   */
  async degradeToSkeleton(job, reason) {
    console.warn(`[ai-worker] Degrading job ${job.id}: ${reason}`);
    
    await logTaskEvent({
      taskId: job.parseTaskId,
      event: 'ai-skeleton-fallback',
      level: 'warn',
      message: reason,
      payload: { aiJobId: job.id }
    });

    const simulatedResult = {
      title: "模拟试卷 (降级模式)",
      subject: "未知",
      grade: "未知",
      materialType: "其他",
      summary: `[ai skeleton fallback] 由于 "${reason}"，系统使用了降级模拟结果。`,
      confidence: 50,
      needsReview: true,
      warnings: ["系统已降级为模拟模式"]
    };

    await this.transition(job, {
      state: 'review-pending',
      progress: 100,
      message: `[降级屏蔽] ${reason}`,
      result: simulatedResult,
      confidence: 50,
      needsReview: true,
      providerId: 'skeleton',
      updatedAt: new Date().toISOString(),
      completedAt: new Date().toISOString()
    }, 'ai-worker-completed');
  }

  /**
   * 执行 AI 条目识别并支持 Fallback
   */
  async executeWithFallback(mainProvider, markdown, aiSettings) {
    const providersToTry = [mainProvider];
    
    // 如果配置了 fallback，可以加入列表（此处简化逻辑，如果主 provider 失败，尝试 openai-compatible 作为垫背）
    if (mainProvider.id === 'ollama' && aiSettings.openaiApiKey) {
      providersToTry.push(this.createProvider('openai-compatible', aiSettings));
    }

    let lastError;
    for (let i = 0; i < providersToTry.length; i++) {
        const provider = providersToTry[i];
        try {
            const systemPrompt = aiSettings.systemPrompt || this.getDefaultPrompt();
            const resp = await provider.extractMetadata(markdown, { 
                systemPrompt,
                num_predict: aiSettings.num_predict || 512
            });
            if (i > 0) resp.fallbackOccurred = true;
            return resp;
        } catch (err) {
            lastError = err;
            console.warn(`[ai-worker] Provider ${provider.id} failed: ${err.message}`);
            if (i < providersToTry.length - 1) {
                await logTaskEvent({
                   taskId: 'system', // 此处记录系统级别警告
                   event: 'ai-provider-fallback',
                   level: 'warn',
                   message: `Provider ${provider.id} 失败，正在尝试下一个: ${providersToTry[i+1].id}`
                });
            }
        }
    }
    throw lastError;
  }

  createProvider(id, aiSettings) {
    // Mac mini Docker 部署默认使用内网 Ollama 地址
    const MAC_MINI_OLLAMA = 'http://192.168.31.33:11434/v1/chat/completions';
    
    // 优先使用配置的地址，否则使用 Mac mini 默认地址
    let url = aiSettings.ollamaBaseUrl || aiSettings.baseUrl || aiSettings.apiEndpoint || MAC_MINI_OLLAMA;
    const timeoutMs = aiSettings.timeoutMs || 120000;
    this.defaultTimeoutMs = timeoutMs; // 保存用于 stale job 判断
    
    // 路由逻辑变更 (TASK-24)：
    // 如果配置包含了 /v1/chat/completions，则使用 OpenAiCompatibleProvider
    if (url.includes('/v1/chat/completions')) {
      const baseUrl = url.split('/chat/completions')[0];
      return new OpenAiCompatibleProvider({
        baseUrl,
        model: aiSettings.ollamaModel || aiSettings.model || 'qwen3.5:9b',
        apiKey: aiSettings.openaiApiKey || aiSettings.apiKey,
        timeoutMs,
        // 特殊标记：即便底层用 OpenAI 协议，业务标识依然保留为原始 id (如 ollama)
        providerIdOverride: id 
      });
    }

    // 否则如果是 ollama 且不带 v1，或者 id 就是 ollama，使用原生 OllamaProvider (/api/chat)
    if (id === 'ollama') {
      return new OllamaProvider({
        baseUrl: url,
        model: aiSettings.ollamaModel || aiSettings.model || 'qwen3.5:9b',
        timeoutMs
      });
    }

    if (id === 'openai-compatible') {
      return new OpenAiCompatibleProvider({
        baseUrl: url,
        model: aiSettings.openaiModel || aiSettings.model,
        apiKey: aiSettings.openaiApiKey || aiSettings.apiKey,
        timeoutMs
      });
    }

    // 兜底返回 Mac mini 内网 Ollama
    return new OpenAiCompatibleProvider({
      baseUrl: 'http://192.168.31.33:11434',
      model: aiSettings.ollamaModel || aiSettings.model || 'qwen3.5:9b',
      apiKey: aiSettings.openaiApiKey || aiSettings.apiKey,
      timeoutMs,
      providerIdOverride: id
    });
  }

  /**
   * 规范化超时时间：如果数值过小（<= 3600），视作秒，转为毫秒
   */
  normalizeTimeout(val) {
    const n = Number(val);
    if (!n || isNaN(n)) return 120000;
    if (n <= 3600) return n * 1000; // 秒转毫秒
    return n;
  }

  /**
   * 结果归一化：确保所有 PRD 要求的字段都存在，即便为空
   */
  normalizeResult(raw) {
    if (!raw || typeof raw !== 'object') return { title: '解析失败', subject: '', grade: '' };
    
    return {
      title: raw.title || '',
      subject: raw.subject || '',
      grade: raw.grade || '',
      semester: raw.semester || '',
      materialType: raw.materialType || '',
      language: raw.language || '中文',
      curriculum: raw.curriculum || '',
      tags: Array.isArray(raw.tags) ? raw.tags : [],
      summary: raw.summary || '',
      confidence: Number(raw.confidence || 0),
      fieldConfidence: raw.fieldConfidence || {},
      needsReview: raw.needsReview !== undefined ? !!raw.needsReview : true,
      ...raw // 保留 AI 可能返回的其他字段
    };
  }

  getDefaultPrompt() {
    return `你是一个专业的教育资源元数据提取助手。你的任务是从提供的 Markdown 文本中提取结构化信息。
请严格仅返回 JSON 格式，不要包含任何解释性文本或 Markdown 代码块标识。

JSON 结构需包含以下字段（符合 PRD 10.5.3）：
- title (string): 资源标题
- subject (string): 学科（如：数学, 语文, 英语, 物理, 化学等）
- grade (string): 年级标识（如：G1-G12, K1-K3, Y1-Y13）
- semester (string): 学期（上册, 下册, 全一册）
- materialType (string): 资料类型（教材, 试卷, 讲义, 练习册, 课件, 视频等）
- language (string): 语种（中文, 英文等）
- curriculum (string): 课程体系/版本（如：人教版, 北师大版, IB, A-Level）
- tags (array of strings): 3-8个关键词标签
- summary (string): 核心内容简述（不超过200字）
- confidence (number): 0-100 的整体识别置信度
- fieldConfidence (object): 各核心字段的置信度得分
- needsReview (boolean): 建议人工复核

无法判断的字段请填入空字符串或 "unknown"；不要编造信息。`;
  }

  async transition(job, update, eventName, level = 'info', payload = {}) {
    const success = await updateJob(job.id, update);
    if (success) {
      await logTaskEvent({
        taskId: job.parseTaskId,
        taskType: 'parse',
        event: eventName,
        level,
        message: update.message || `AI Job status changed to ${update.state}`,
        payload: {
          aiJobId: job.id,
          ...update,
          ...payload
        }
      });

      // AI Job 到达终态时触发外部回调，用于回填 materials 表等联动操作
      const terminalStates = ['confirmed', 'review-pending', 'failed'];
      if (this.onComplete && terminalStates.includes(update.state)) {
        try {
          await this.onComplete(job, update);
        } catch (err) {
          console.error(`[ai-worker] onComplete callback failed: ${err.message}`);
        }
      }
    }
  }

  async streamToString(stream) {
    const chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    return Buffer.concat(chunks).toString('utf-8');
  }
}