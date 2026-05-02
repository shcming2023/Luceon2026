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
import { getTaskById, getMaterialById } from '../tasks/task-client.mjs';
import { logTaskEvent } from '../logging/task-events.mjs';
import { getSettings } from '../settings/settings-client.mjs';

import { OllamaProvider } from './providers/ollama.mjs';
import { OpenAiCompatibleProvider } from './providers/openai-compatible.mjs';
import { sampleMarkdown } from './metadata-sampler.mjs';
import { getDefaultV02Skeleton, validateAndNormalizeV02, generateV02Prompt, generateV02DraftPrompt, generateV02RepairPrompt } from './metadata-standard-v0.2.mjs';

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
    // 逻辑修正：
    // 1. 如果包含 onComplete 或 minioContext，判定为新式 options 对象
    // 2. 如果包含 getFileStream 但不包含 onComplete，判定为旧式 minioContext 对象
    let options = {};
    if (contextOrOptions && (contextOrOptions.onComplete || contextOrOptions.minioContext)) {
      options = contextOrOptions;
    } else if (contextOrOptions?.getFileStream) {
      options = { minioContext: contextOrOptions };
    } else {
      options = contextOrOptions || {};
    }
    
    this.timer = null;
    this.isRunning = false;
    this.minioContext = options.minioContext || (typeof options.getFileStream === 'function' ? options : null);
    this.onComplete = options.onComplete || null;
    this.eventBus = options.eventBus || null;

    // 注入诊断：确保存储上下文和回调已就绪
    const hasContext = typeof this.minioContext?.getFileStream === 'function';
    console.log(`[ai-worker] Initialized. Context: ${hasContext ? 'OK' : 'MISSING'}, Callback: ${this.onComplete ? 'YES' : 'NO'}`);
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
      // 减少冗余日志，仅在有任务时提示
      return;
    }

    try {
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

      if (pendingJobs.length > 0) {
        console.log(`[ai-worker] Found ${pendingJobs.length} pending jobs. Picking the earliest one.`);
        const job = pendingJobs[0];
        if (!processingMap.has(job.id)) {
          await this.processJob(job);
        }
      }
    } catch (err) {
      console.error(`[ai-worker] scanAndProcess error: ${err.message}`);
    }
  }

  /**
   * 恢复长时间卡住的 running job
   * 规则：state=running 且 updatedAt 超过 timeoutMs + 60s → 标记 failed 或 reset 为 pending
   * P0 OOM Patch: 同一 aiJobId 的 ai-stale-running-recovered 事件不重复写入
   */
  async recoverStaleRunningJobs(jobs) {
    const runningJobs = jobs.filter(j => j.state === 'running');
    const now = Date.now();
    const GRACE_PERIOD_MS = 60000; // 60 秒额外缓冲

    // P0 OOM: 初始化去重集合（进程级，不跨重启）
    if (!this._staleRecoveredJobIds) {
      this._staleRecoveredJobIds = new Set();
    }

    for (const job of runningJobs) {
      if (!job.updatedAt) continue;
      
      const updatedAt = new Date(job.updatedAt).getTime();
      const staleThreshold = updatedAt + this.defaultTimeoutMs + GRACE_PERIOD_MS;
      
      if (now > staleThreshold) {
        // P0 OOM: 同一 aiJobId 不重复写 stale 恢复事件
        if (this._staleRecoveredJobIds.has(job.id)) {
          // 仍然重置状态，但不写事件
          await updateJob(job.id, {
            state: 'pending',
            message: '因长时间卡住，已自动重置为 pending 状态',
            updatedAt: new Date().toISOString()
          });
          continue;
        }

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

        this._staleRecoveredJobIds.add(job.id);

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
   * Helper to persist raw output and generate error details
   */
  async _persistRawOutputSafe(job, phase, rawContent) {
    if (!this.minioContext || typeof this.minioContext.saveObject !== 'function' || !rawContent) return {};
    try {
      const crypto = await import('crypto');
      const hash = crypto.createHash('sha256').update(rawContent).digest('hex');
      const objectName = `ai-raw/${job.materialId}/${job.id}/${phase}.txt`;
      await this.minioContext.saveObject(objectName, Buffer.from(rawContent, 'utf-8'), 'text/plain');
      return { rawObjectName: objectName, rawContentHash: hash };
    } catch (err) {
      console.warn(`[ai-worker] Failed to persist raw output for ${phase}: ${err.message}`);
      return { rawPersistFailedReason: err.message };
    }
  }

  _buildErrorDetails(response, expectJson, persistMeta) {
    const rawContent = response.rawResponse || '';
    const rawTrimmed = rawContent.trim();
    return {
      rawContentLength: rawContent.length,
      rawContentHead: rawContent.slice(0, 500),
      rawContentTail: rawContent.slice(-500),
      rawLooksTruncated: rawContent.includes('{') && !rawTrimmed.endsWith('}') && !rawTrimmed.endsWith(']'),
      rawContainsThinkTag: response.rawContainsThinkTag || false,
      responseFormatRequested: expectJson !== false,
      expectJson: expectJson !== false,
      parseErrorMessage: response.parseError || 'Parse error',
      ...persistMeta
    };
  }

  async _runProviderPass(provider, job, aiSettings, prompt, options) {
    const response = await this.executeWithFallback(provider, options.markdownContent, {
      ...aiSettings,
      systemPrompt: prompt,
      temperature: options.temperature,
      expectJson: options.expectJson,
      num_predict: options.num_predict,
      returnRawOnParseFailure: true
    });

    const persistMeta = await this._persistRawOutputSafe(job, options.phaseName, response.rawResponse);
    const traceDetails = this._buildErrorDetails(response, options.expectJson, persistMeta);

    if (response.parseFailed) {
      const err = new Error(response.parseError);
      err.details = traceDetails;
      throw err;
    }

    response.persistMeta = persistMeta;
    response.traceDetails = traceDetails;
    return response;
  }

  _extractTrace(details) {
    if (!details) return undefined;
    return {
      objectName: details.rawObjectName,
      contentHash: details.rawContentHash,
      contentLength: details.rawContentLength,
      contentHead: details.rawContentHead,
      contentTail: details.rawContentTail,
      containsThinkTag: details.rawContainsThinkTag,
      looksTruncated: details.rawLooksTruncated,
      parseErrorMessage: details.parseErrorMessage !== 'Parse error' ? details.parseErrorMessage : undefined
    };
  }

  async _buildSourceMeta(job, parseTask = null) {
    let material = {};
    if (job.materialId) {
      try {
        material = await getMaterialById(job.materialId) || {};
      } catch (e) {
        console.warn(`[ai-worker] Failed to fetch material ${job.materialId}`);
      }
    }
    
    return {
      materialId: job.materialId,
      filename: material.fileName || material.title || parseTask?.metadata?.originalFilename || parseTask?.originalFilename || 'unknown',
      fileSize: material.metadata?.fileSize || material.fileSize || parseTask?.metadata?.mineruExecutionProfile?.fileSize || parseTask?.metadata?.fileSize || parseTask?.metadata?.originalFileSize || parseTask?.originalFileSize || 0,
      mimeType: material.mimeType || material.metadata?.mimeType || parseTask?.metadata?.mimeType || parseTask?.mimeType || '',
      rawObjectName: material.metadata?.objectName || parseTask?.metadata?.objectName || '',
      parsedPrefix: parseTask?.metadata?.parsedPrefix || material.metadata?.parsedPrefix || '',
      markdownObjectName: parseTask?.metadata?.markdownObjectName || material.metadata?.markdownObjectName || job.inputMarkdownObjectName || '',
      parsedFilesCount: parseTask?.metadata?.parsedFilesCount || material.metadata?.parsedFilesCount || 0,
      mineruExecutionProfile: parseTask?.metadata?.mineruExecutionProfile || material.metadata?.mineruExecutionProfile || {}
    };
  }

  /**
   * 核心处理逻辑
   */
  async processJob(job) {
    processingMap.add(job.id);
    const startTime = Date.now();
    console.log(`[ai-worker] Picking up job: ${job.id} (parseTask=${job.parseTaskId})`);

    try {
      // 1. 提前获取 ParseTask 信息以校验是否取消
      const parseTask = await getTaskById(job.parseTaskId);
      const sourceMeta = await this._buildSourceMeta(job, parseTask);
      
      if (parseTask?.state === 'canceled' || parseTask?.metadata?.canceledAt) {
         console.warn(`[ai-worker] Job ${job.id} skipped: ParseTask ${job.parseTaskId} is canceled.`);
         await updateJob(job.id, {
           state: 'skipped-canceled',
           message: '关联解析任务已取消，AI 识别跳过',
           updatedAt: new Date().toISOString(),
           completedAt: new Date().toISOString()
         });
         await logTaskEvent({
            taskId: job.parseTaskId,
            taskType: 'parse',
            event: 'ai-job-skipped-canceled',
            level: 'warn',
            message: '关联任务已取消，AI 元数据识别被跳过',
            payload: { aiJobId: job.id }
         });
         return;
      }

      // 2. 获取全局设置
      const settings = await getSettings();
      // 优先读取 aiConfig，兼容旧的 ai
      const rawAiConfig = settings.aiConfig || settings.ai || {};

      // 降级检查：未启用 AI
      if (rawAiConfig.aiEnabled === false) {
        return await this.degradeToSkeleton(job, 'AI 功能已从控制台关闭，降级为骨架模拟', sourceMeta);
      }

      // 3. 确定 Provider 配置 (优先选择 providers 数组中启用且优先级最高的)
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

      // 4. 获取 Markdown 内容
      const markdownObjectName = parseTask?.metadata?.markdownObjectName || job.inputMarkdownObjectName;
      
      if (!markdownObjectName || !this.minioContext) {
        return await this.degradeToSkeleton(job, '未找到 Markdown 产物或存储上下文不可用，降级为骨架模拟', sourceMeta);
      }

      let markdownContent = '';
      try {
        const stream = await this.minioContext.getFileStream(markdownObjectName);
        markdownContent = await this.streamToString(stream);
      } catch (err) {
        return await this.degradeToSkeleton(job, `拉取 Markdown 内容失败: ${err.message}，降级为骨架模拟`, sourceMeta);
      }

      if (!markdownContent.trim()) {
        throw new Error('Markdown 内容为空，无法提取元数据');
      }

      // 4. 内容截断处理 (根据 PRD 10.5.5) -> 升级为抽样策略 (V0.2)
      // sourceMeta 已经提前构建完成

      const originalLength = markdownContent.length;
      const { sampledContent, inputHash } = sampleMarkdown(markdownContent, sourceMeta, 80000);
      let isTruncated = originalLength > sampledContent.length;

      if (isTruncated) {
        await logTaskEvent({
          taskId: job.parseTaskId,
          event: 'ai-content-truncated',
          level: 'info',
          message: `Markdown 内容过长已按策略抽样截断 (${originalLength} -> ${sampledContent.length} 字符)`,
          payload: { originalLength, truncatedLength: sampledContent.length }
        });
      }
      
      markdownContent = sampledContent;

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

      // 同步将关联 ParseTask 写为 ai-running（PRD v0.4 §6.1 新增显式状态）
      if (job.parseTaskId) {
        try {
          await fetch(`${process.env.DB_BASE_URL || 'http://localhost:8789'}/tasks/${encodeURIComponent(job.parseTaskId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              state: 'ai-running',
              stage: 'ai',
              message: `AI 识别进行中 (${providerId}/${provider.model})`,
              updatedAt: new Date().toISOString(),
            }),
          });
        } catch (e) {
          console.warn(`[ai-worker] Failed to mark ParseTask ${job.parseTaskId} as ai-running: ${e.message}`);
        }
      }

      const twoPassEnabled = (aiSettings.ollamaTwoPassJsonRepair !== false) && (providerId === 'ollama' || (provider && provider.id === 'ollama'));
      
      let aiResponse;
      let twoPassAttempted = false;
      let repairSucceeded = false;
      let repairFailedReason = '';
      let repairProviderDetails = null;
      const rawTrace = {};

      try {
        if (twoPassEnabled) {
          aiResponse = await this._runProviderPass(provider, job, aiSettings, generateV02DraftPrompt(), {
            markdownContent,
            temperature: aiSettings.temperature ?? 0.1,
            expectJson: false,
            phaseName: 'first-pass'
          });
        } else {
          aiResponse = await this._runProviderPass(provider, job, aiSettings, systemPrompt, {
            markdownContent,
            temperature: aiSettings.temperature ?? 0.1,
            expectJson: true,
            phaseName: 'first-pass'
          });
        }
        
        rawTrace.firstPass = this._extractTrace(aiResponse.traceDetails);
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
        if (err.details) {
          rawTrace.firstPass = this._extractTrace(err.details);
        }
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
        const skeletonReason = `AI Provider 调用全部失败: ${err.message}，自动降级为模拟结果完成链路`;
        const degradedResult = await this.degradeToSkeleton(job, skeletonReason, sourceMeta);
        if (degradedResult && degradedResult.result) {
            degradedResult.result.aiClassificationRawTrace = rawTrace;
            if (rawTrace.firstPass?.objectName) {
                degradedResult.result.aiClassificationRawObjectName = rawTrace.firstPass.objectName;
                degradedResult.result.aiClassificationRawContentHash = rawTrace.firstPass.contentHash;
            }
        }
        return degradedResult;
      }

      // 6. 结果后处理与归一化 (TASK-24 + V0.2)
      let parsedResult = {};
      let jsonParseFailed = false;
      let schemaInvalid = false;

      function checkSchemaInvalid(obj) {
        if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false; // Handled by jsonParseFailed if not an object, but if extractJson somehow returns an array, it's invalid
        const hasPrimaryFacets = obj.primary_facets && typeof obj.primary_facets === 'object' && !Array.isArray(obj.primary_facets);
        const hasGovernance = obj.governance && typeof obj.governance === 'object' && !Array.isArray(obj.governance);
        const hasEvidence = Array.isArray(obj.evidence);
        return !(hasPrimaryFacets && hasGovernance && hasEvidence);
      }

      try {
        parsedResult = this.extractJson(aiResponse.result);
        if (parsedResult && typeof parsedResult === 'object' && !Array.isArray(parsedResult)) {
          if (checkSchemaInvalid(parsedResult)) {
            schemaInvalid = true;
          }
        } else {
          jsonParseFailed = true;
        }
      } catch (err) {
        jsonParseFailed = true;
      }

      // === Two-Pass Repair Logic ===
      let repairRetryAttempted = false;
      let repairRetrySucceeded = false;
      let repairRetryReason = '';
      let firstFailureDetails = null;

      if ((jsonParseFailed || schemaInvalid) && twoPassEnabled) {
        twoPassAttempted = true;
        const repairPrompt = generateV02RepairPrompt(aiResponse.result);
        try {
          console.log(`[ai-worker] First pass JSON extraction failed, attempting repair for job ${job.id}...`);
          const repairResponse = await this._runProviderPass(provider, job, aiSettings, repairPrompt, {
            markdownContent,
            temperature: 0.1,
            expectJson: true,
            num_predict: 3072,
            phaseName: 'repair-pass'
          });
          
          parsedResult = this.extractJson(repairResponse.result);
          
          if (parsedResult && typeof parsedResult === 'object' && !Array.isArray(parsedResult)) {
            if (checkSchemaInvalid(parsedResult)) {
              const err = new Error("Repair output is still schema-invalid v0.2");
              err.details = { rawLooksTruncated: false, expectJson: true };
              throw err;
            }
          } else {
            const err = new Error("Repair output is not a valid JSON object");
            err.details = { rawLooksTruncated: false, expectJson: true };
            throw err;
          }

          jsonParseFailed = false;
          schemaInvalid = false;
          repairSucceeded = true;
          aiResponse = repairResponse;
          rawTrace.repairPass = this._extractTrace(repairResponse.traceDetails);
          
          await logTaskEvent({
            taskId: job.parseTaskId,
            event: 'ai-provider-repair-succeeded',
            level: 'info',
            message: `AI Provider (${providerId}) JSON Repair 成功`,
            payload: { usage: aiResponse.usage }
          });
        } catch (repairErr) {
          jsonParseFailed = true;
          repairSucceeded = false;
          repairFailedReason = repairErr.message;
          if (repairErr.details) {
            repairProviderDetails = repairErr.details;
            rawTrace.repairPass = this._extractTrace(repairProviderDetails);
          }

          if (repairProviderDetails?.rawLooksTruncated === true) {
            repairRetryAttempted = true;
            repairRetryReason = 'raw-truncated';
            firstFailureDetails = repairProviderDetails;
            
            await logTaskEvent({
              taskId: job.parseTaskId,
              event: 'ai-provider-repair-retry-started',
              level: 'warn',
              message: `AI Provider (${providerId}) JSON Repair truncated, retrying...`,
              payload: { reason: 'raw-truncated' }
            });

            try {
              const retryResponse = await this._runProviderPass(provider, job, aiSettings, repairPrompt, {
                markdownContent,
                temperature: 0,
                expectJson: true,
                num_predict: 4096,
                phaseName: 'repair-retry-pass'
              });

              parsedResult = this.extractJson(retryResponse.result);
              
              if (parsedResult && typeof parsedResult === 'object' && !Array.isArray(parsedResult)) {
                if (checkSchemaInvalid(parsedResult)) {
                  const err = new Error("Repair output is still schema-invalid v0.2");
                  err.details = { rawLooksTruncated: false, expectJson: true };
                  throw err;
                }
              } else {
                const err = new Error("Repair output is not a valid JSON object");
                err.details = { rawLooksTruncated: false, expectJson: true };
                throw err;
              }

              jsonParseFailed = false;
              schemaInvalid = false;
              repairSucceeded = true;
              repairRetrySucceeded = true;
              aiResponse = retryResponse;
              rawTrace.repairRetryPass = this._extractTrace(retryResponse.traceDetails);
              repairProviderDetails = null; // clear on success

              await logTaskEvent({
                taskId: job.parseTaskId,
                event: 'ai-provider-repair-retry-succeeded',
                level: 'info',
                message: `AI Provider (${providerId}) JSON Repair Retry 成功`
              });
            } catch (retryErr) {
              repairRetrySucceeded = false;
              repairFailedReason = retryErr.message;
              if (retryErr.details) {
                repairProviderDetails = retryErr.details;
                rawTrace.repairRetryPass = this._extractTrace(repairProviderDetails);
              }
              await logTaskEvent({
                taskId: job.parseTaskId,
                event: 'ai-provider-repair-retry-failed',
                level: 'warn',
                message: `AI Provider (${providerId}) JSON Repair Retry 失败: ${retryErr.message}`,
                payload: repairProviderDetails ? { repairProviderDetails } : {}
              });
            }
          } else {
            console.warn(`[ai-worker] Two-pass JSON repair failed for job ${job.id}: ${repairErr.message}`);
            await logTaskEvent({
              taskId: job.parseTaskId,
              event: 'ai-provider-repair-failed',
              level: 'warn',
              message: `AI Provider (${providerId}) JSON Repair 失败: ${repairErr.message}`,
              payload: repairProviderDetails ? { repairProviderDetails } : {}
            });
          }
        }
      }

      let resultV02;
      let aiClassificationDegraded = false;
      let aiClassificationDegradedReason = '';
      let aiClassificationErrorSource = '';

      if (jsonParseFailed || schemaInvalid) {
        aiClassificationDegraded = true;
        if (twoPassAttempted) {
          if (schemaInvalid) {
            aiClassificationDegradedReason = 'AI 元数据识别未产出合规 v0.2 结构，当前为 skeleton 降级结果';
            aiClassificationErrorSource = 'ai-metadata-schema-invalid-repair-failed';
          } else {
            aiClassificationDegradedReason = 'AI Provider 二段式 JSON 修复失败，降级为 skeleton 结果';
            aiClassificationErrorSource = 'ollama-json-repair-failed';
          }
        } else {
          if (schemaInvalid) {
            aiClassificationDegradedReason = 'AI 元数据识别未产出合规 v0.2 结构，当前为 skeleton 降级结果';
            aiClassificationErrorSource = 'ai-metadata-schema-invalid';
          } else {
            aiClassificationDegradedReason = 'AI Provider JSON 解析失败，已降级为 skeleton 结果';
            aiClassificationErrorSource = 'ollama-json-parse-failed';
          }
        }
        resultV02 = getDefaultV02Skeleton(sourceMeta, 'low', aiClassificationDegradedReason);
      } else {
        resultV02 = validateAndNormalizeV02(parsedResult, sourceMeta);
        
        // fields_missing 直接 skeleton 的兼容兜底
        if (resultV02.governance.risk_flags.includes('skeleton_fallback') && resultV02.governance.human_review_reason === 'fields_missing') {
          aiClassificationDegraded = true;
          aiClassificationDegradedReason = 'AI 元数据存在核心字段缺失，降级为 skeleton 兜底';
          aiClassificationErrorSource = 'ai-metadata-schema-invalid';
        } else if (resultV02.governance.risk_flags.includes('skeleton_fallback')) {
          aiClassificationDegraded = true;
          aiClassificationDegradedReason = resultV02.governance.human_review_reason || 'AI 解析结果触发了降级规则';
          aiClassificationErrorSource = 'ai-metadata-fallback';
        }
      }

      const result = this.normalizeResult(resultV02); // 保持兼容性
      // 记录原始响应预览
      let rawString = '';
      if (typeof aiResponse.result === 'object' && aiResponse.result !== null) {
        rawString = JSON.stringify(aiResponse.result);
      } else {
        rawString = String(aiResponse.result || '');
      }
      result.rawPreview = rawString.slice(0, 1000);
      result.aiClassificationStandardVersion = 'llm_text_classification_v0.2';
      result.aiClassificationAnalyzedAt = new Date().toISOString();
      result.aiClassificationProvider = (jsonParseFailed || schemaInvalid) ? 'skeleton' : aiResponse.provider;
      result.aiClassificationModel = (jsonParseFailed || schemaInvalid) ? 'skeleton' : aiResponse.model;
      result.aiClassificationInputHash = inputHash;
      result.aiClassificationV02 = resultV02;
      
      result.aiClassificationRawTrace = rawTrace;
      
      // 兼容旧字段
      if (rawTrace.firstPass) {
        result.aiClassificationRawObjectName = rawTrace.firstPass.objectName;
        result.aiClassificationRawContentHash = rawTrace.firstPass.contentHash;
      }
      if (rawTrace.repairPass) {
        result.aiClassificationRepairRawObjectName = rawTrace.repairPass.objectName;
      }
      if (rawTrace.repairRetryPass) {
        result.aiClassificationRepairRetryRawObjectName = rawTrace.repairRetryPass.objectName;
      }
      
      if (aiResponse.persistMeta && aiResponse.persistMeta.rawPersistFailedReason) {
        result.aiClassificationRawPersistFailedReason = aiResponse.persistMeta.rawPersistFailedReason;
      }
      
      if (twoPassAttempted) {
        result.aiClassificationTwoPassAttempted = true;
        result.aiClassificationRepairSucceeded = repairSucceeded;
        
        if (repairRetryAttempted) {
          result.aiClassificationRepairRetryAttempted = true;
          result.aiClassificationRepairRetryReason = repairRetryReason;
          result.aiClassificationRepairRetrySucceeded = repairRetrySucceeded;
        }

        if (!repairSucceeded) {
          result.aiClassificationRepairFailedReason = repairFailedReason || 'Parse or validate failed';
          if (firstFailureDetails) {
            result.aiClassificationRepairFirstFailureDetails = firstFailureDetails;
          }
          if (repairProviderDetails) {
            result.aiClassificationRepairProviderDetails = repairProviderDetails;
          }
        }
      }

      if (aiClassificationDegraded) {
        result.aiClassificationDegraded = true;
        result.aiClassificationDegradedReason = aiClassificationDegradedReason;
        result.aiClassificationErrorSource = aiClassificationErrorSource;
      }

      // 从 v0.2 标准获取置信度和 review 状态
      const confidence = resultV02.governance.confidence === 'high' ? 90 : resultV02.governance.confidence === 'medium' ? 60 : 30;
      const needsReview = resultV02.governance.human_review_required === true || resultV02.governance.confidence !== 'high';

      // 7. 完成任务（Canonical 终态：confirmed / review-pending）
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
  async degradeToSkeleton(job, reason, prebuiltSourceMeta = null) {
    console.warn(`[ai-worker] Degrading job ${job.id}: ${reason}`);
    
    await logTaskEvent({
      taskId: job.parseTaskId,
      event: 'ai-skeleton-fallback',
      level: 'warn',
      message: reason,
      payload: { aiJobId: job.id }
    });

    let sourceMeta = prebuiltSourceMeta;
    if (!sourceMeta) {
      let parseTask = null;
      if (job.parseTaskId) {
        try {
          parseTask = await getTaskById(job.parseTaskId);
        } catch (e) {
          // ignore
        }
      }
      sourceMeta = await this._buildSourceMeta(job, parseTask);
    }
    
    const v02Skeleton = getDefaultV02Skeleton(sourceMeta, 'low', reason);
    const simulatedResult = this.normalizeResult(v02Skeleton);
    simulatedResult.aiClassificationStandardVersion = 'llm_text_classification_v0.2';
    simulatedResult.aiClassificationAnalyzedAt = new Date().toISOString();
    simulatedResult.aiClassificationProvider = 'skeleton';
    simulatedResult.aiClassificationModel = 'skeleton';
    simulatedResult.aiClassificationInputHash = '';
    simulatedResult.aiClassificationV02 = v02Skeleton;
    simulatedResult.aiClassificationDegraded = true;
    simulatedResult.aiClassificationDegradedReason = reason;
    simulatedResult.aiClassificationErrorSource = 'ollama-json-parse-failed';

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
            const {
              systemPrompt,
              expectJson,
              temperature,
              top_p,
              num_predict,
              ...rest
            } = aiSettings;

            const resp = await provider.extractMetadata(markdown, { 
                ...rest,
                systemPrompt: systemPrompt || generateV02Prompt(),
                expectJson,
                temperature,
                top_p,
                num_predict: num_predict || 512
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
    // Docker 部署环境下，访问宿主机 Ollama 推荐使用 host.docker.internal
    const HOST_DOCKER_OLLAMA = 'http://host.docker.internal:11434/v1/chat/completions';
    const MAC_MINI_OLLAMA = 'http://192.168.31.33:11434/v1/chat/completions';
    
    // 优先使用配置的地址，否则尝试 host.docker.internal，最后兜底 Mac mini 默认 IP
    let url = aiSettings.ollamaBaseUrl || aiSettings.baseUrl || aiSettings.apiEndpoint || HOST_DOCKER_OLLAMA;
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

    // 兜底返回 host.docker.internal Ollama
    return new OpenAiCompatibleProvider({
      baseUrl: 'http://host.docker.internal:11434',
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
  normalizeResult(v02Result) {
    if (!v02Result || !v02Result.primary_facets) {
      return { title: '解析失败', subject: '', grade: '' };
    }
    const p = v02Result.primary_facets;
    const d = v02Result.descriptive_metadata;
    const g = v02Result.governance;
    const s = v02Result.search_tags || {};
    const sysTags = v02Result.system_tags || { format_tags: [], engine_tags: [], artifact_tags: [] };
    
    const c = v02Result.controlled_classification || {};
    const nt = v02Result.normalized_tags || { topic_tags: [], skill_tags: [] };
    
    // 优先使用 controlled_classification，fallback 到 primary_facets
    const subjectZh = c.subject?.zh || p.subject?.zh || '';
    const stageZh = c.stage?.zh || p.stage?.zh || '';
    const levelZh = c.level?.zh || p.level?.zh || '';
    const resTypeZh = c.resource_type?.zh || p.resource_type?.zh || '';
    const compRoleZh = c.component_role?.zh || p.component_role?.zh || '';
    const currZh = c.curriculum?.zh || p.curriculum?.zh || '';
    
    const components = [subjectZh, stageZh, levelZh, resTypeZh].filter(Boolean);
    const generatedSummary = components.length > 0 ? components.join(' · ') : '';
    
    // tags 只合并 normalized_tags 和 system_tags
    const normTopicTags = Array.isArray(nt.topic_tags) ? nt.topic_tags.map(t => typeof t === 'object' ? t.zh || t.en : t) : [];
    const normSkillTags = Array.isArray(nt.skill_tags) ? nt.skill_tags.map(t => typeof t === 'object' ? t.zh || t.en : t) : [];

    return {
      title: d.series_title || generatedSummary || subjectZh || '',
      subject: subjectZh,
      grade: levelZh || stageZh,
      semester: '', // v0.2 dropped semester, but we keep it empty for compatibility
      materialType: resTypeZh || compRoleZh,
      language: d.language || '中文',
      curriculum: currZh,
      tags: [
        ...normTopicTags, 
        ...normSkillTags,
        ...(sysTags.format_tags || []).map(t => t.zh || t.en || t),
        ...(sysTags.engine_tags || []).map(t => t.zh || t.en || t),
        ...(sysTags.artifact_tags || []).map(t => t.zh || t.en || t)
      ],
      summary: generatedSummary,
      confidence: g.confidence === 'high' ? 90 : g.confidence === 'medium' ? 60 : 30,
      fieldConfidence: {},
      needsReview: g.human_review_required === true || g.confidence !== 'high'
    };
  }

  getDefaultPrompt() {
    return generateV02Prompt();
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

      // SSE 广播（PRD v0.4 §10.2.2）：将 AI Job 状态变更以 ParseTask 维度推送
      if (this.eventBus?.emit && job.parseTaskId) {
        try {
          this.eventBus.emit('task-update', {
            taskId: job.parseTaskId,
            event: eventName,
            level,
            update: { aiJobId: job.id, aiJobState: update.state, ...update },
            at: new Date().toISOString(),
          });
        } catch (e) {
          console.warn(`[ai-worker] eventBus emit failed: ${e.message}`);
        }
      }
    }
  }

  /**
   * 鲁棒的 JSON 提取逻辑
   * 1. 处理 ```json ... ``` 代码块
   * 2. 处理 ``` ... ``` 原始代码块
   * 3. 兜底尝试提取第一个 { 到最后一个 } 之间的内容
   */
  extractJson(raw) {
    if (typeof raw === 'object' && raw !== null) {
      if (raw.content && typeof raw.content === 'string' && raw.content.trim().startsWith('{')) {
         try {
           return this.extractJson(raw.content);
         } catch(e) {
           return raw;
         }
      }
      return raw;
    }
    if (!raw || typeof raw !== 'string') return {};

    let content = raw.trim();
    
    // 1. 预处理：去除 <think>...</think> 标签及其内容 (Qwen/DeepSeek 常用)
    content = content.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
    
    // 2. 匹配 JSON 块
    const jsonBlockMatch = content.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    if (jsonBlockMatch && jsonBlockMatch[1]) {
      content = jsonBlockMatch[1].trim();
    } else {
      const firstBrace = content.indexOf('{');
      const lastBrace = content.lastIndexOf('}');
      if (firstBrace !== -1 && lastBrace !== -1 && lastBrace >= firstBrace) {
        content = content.slice(firstBrace, lastBrace + 1).trim();
      }
    }

    try {
      let parsed = JSON.parse(content);
      
      // 3. 递归处理：如果解析出的对象包含 content 且 content 看起来像 JSON (某些 Provider 的嵌套行为)
      if (parsed && typeof parsed.content === 'string' && parsed.content.trim().startsWith('{')) {
        try {
          const inner = JSON.parse(parsed.content);
          parsed = { ...parsed, ...inner };
        } catch (e) { /* ignore */ }
      }
      
      return parsed;
    } catch (err) {
      // 4. 兜底：尝试清理结尾杂质
      try {
        const lastBrace = content.lastIndexOf('}');
        if (lastBrace !== -1) {
          return JSON.parse(content.slice(0, lastBrace + 1));
        }
      } catch (innerErr) { /* ignore */ }
      throw err;
    }
  }

  async streamToString(stream) {
    const chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    return Buffer.concat(chunks).toString('utf-8');
  }
}