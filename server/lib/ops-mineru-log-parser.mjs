/**
 * ops-mineru-log-parser.mjs
 *
 * MinerU 日志结构化活性信号解析器（v1.1）。
 *
 * 解析 MinerU API 日志中的以下信号类型：
 * - progress:        tqdm 进度条（Predict, OCR-rec Predict, Processing pages 等）
 * - stage-change:    阶段切换信号（phase 变化）
 * - window:          Hybrid processing window 信号
 * - document-shape:  文档结构信号（page_count, window_size, total_windows）
 * - engine-config:   引擎/批处理配置信号（Using transformers, hybrid batch ratio）
 * - api-noise:       GET /health, GET /tasks/{id} 等轮询噪声（不计入进度）
 * - error:           错误信号
 *
 * 输出活性等级：
 * - active-progress:       tqdm 进度有变化
 * - active-stage-change:   阶段切换但百分比不变
 * - active-business-log:   有 window/document-shape/engine-config 等业务日志
 * - api-alive-only:        只有 health/task 轮询日志
 * - no-business-signal:    无任何业务信号
 * - suspected-stale:       曾有业务信号但近期无更新
 * - stale-critical:        长时间无业务信号更新
 * - failed-confirmed:      检测到明确错误信号
 * - log-observation-stale:  MinerU 仍在处理但日志文件观测通道滞后
 */

import fs from 'fs';
import path from 'path';

/** 日志文件新鲜度阈值（毫秒），超过此时间视为观测通道滞后。可通过环境变量覆盖。 */
export const MINERU_LOG_STALE_MS = Number(process.env.MINERU_LOG_STALE_MS) || 120_000;

/**
 * 解析 tqdm 进度行。
 *
 * @param {string} line - 日志行文本
 * @returns {{ source: string, phase: string, percent: number, current: number, total: number, rawLine: string, signalType: string } | null}
 *   解析成功返回进度对象，失败返回 null
 */
export function parseTqdmLine(line) {
  const match = line.match(/([a-zA-Z0-9\s_-]+?):\s*(\d+)%\|.*?\|\s*(\d+)\/(\d+)/);
  if (!match) return null;

  const phase = match[1].trim();
  const percent = parseInt(match[2], 10);
  const current = parseInt(match[3], 10);
  const total = parseInt(match[4], 10);

  return {
    source: 'mineru-log',
    phase,
    percent,
    current,
    total,
    rawLine: line.trim(),
    signalType: 'progress'
  };
}

// ───── 结构化信号分类正则 ─────

/** Hybrid processing window 信号 */
const RE_WINDOW = /Hybrid processing window\s+(\d+)\/(\d+):\s*pages\s+(\d+)-(\d+)\/(\d+)/i;
/** 文档结构信号 */
const RE_DOC_SHAPE = /page_count\s*=\s*(\d+)|window_size\s*=\s*(\d+)|total_windows\s*=\s*(\d+)/i;
/** 引擎/批处理配置信号 */
const RE_ENGINE_CONFIG = /Using\s+(transformers|pytorch|onnx)|hybrid\s+batch\s+ratio|batch_size\s*=|model_path\s*=/i;
/** API 轮询噪声：GET /health, GET /tasks/{id} */
const RE_API_NOISE = /(?:GET|"GET)\s+\/(?:health|tasks\/[\w-]+)/i;
/** 错误信号 */
const RE_ERROR = /\b(?:ERROR|FATAL|Exception|Traceback|OutOfMemoryError|CUDA\s*error|segfault|killed)\b/i;
/** 日志行时间戳 */
const RE_TIMESTAMP = /(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})/;

/**
 * 对单行日志进行结构化信号分类。
 *
 * @param {string} line - 日志行文本
 * @returns {{ signalType: string, detail?: object, timestamp?: string } | null}
 *   分类结果，若行为空或无法分类返回 null
 */
export function classifyLogLine(line) {
  if (!line || !line.trim()) return null;

  const tsMatch = line.match(RE_TIMESTAMP);
  const timestamp = tsMatch ? tsMatch[1] : null;

  // 1. tqdm 进度（最高优先级）
  const tqdm = parseTqdmLine(line);
  if (tqdm) {
    return { signalType: 'progress', detail: tqdm, timestamp };
  }

  // 2. 错误信号
  if (RE_ERROR.test(line)) {
    return { signalType: 'error', detail: { rawLine: line.trim() }, timestamp };
  }

  // 3. Hybrid window 信号
  const windowMatch = line.match(RE_WINDOW);
  if (windowMatch) {
    return {
      signalType: 'window',
      detail: {
        windowCurrent: parseInt(windowMatch[1], 10),
        windowTotal: parseInt(windowMatch[2], 10),
        pageStart: parseInt(windowMatch[3], 10),
        pageEnd: parseInt(windowMatch[4], 10),
        pageTotal: parseInt(windowMatch[5], 10)
      },
      timestamp
    };
  }

  // 4. 文档结构信号
  if (RE_DOC_SHAPE.test(line)) {
    return { signalType: 'document-shape', detail: { rawLine: line.trim() }, timestamp };
  }

  // 5. 引擎/批处理配置信号
  if (RE_ENGINE_CONFIG.test(line)) {
    return { signalType: 'engine-config', detail: { rawLine: line.trim() }, timestamp };
  }

  // 6. API 轮询噪声（GET /health, GET /tasks/...）——最低优先级
  if (RE_API_NOISE.test(line)) {
    return { signalType: 'api-noise', detail: null, timestamp };
  }

  return null;
}

/**
 * 从日志文件尾部读取指定字节数。
 *
 * @param {string} filePath - 日志文件路径
 * @param {number} [bytes=8192] - 读取字节数（从文件末尾计算）
 * @returns {Promise<string>} 读取到的文本内容
 */
export async function readTail(filePath, bytes = 8192) {
  return new Promise((resolve) => {
    fs.stat(filePath, (err, stats) => {
      if (err || !stats.isFile()) { resolve(''); return; }
      const size = stats.size;
      const readSize = Math.min(size, bytes);
      const position = size - readSize;

      fs.open(filePath, 'r', (err2, fd) => {
        if (err2) { resolve(''); return; }
        const buffer = Buffer.alloc(readSize);
        fs.read(fd, buffer, 0, readSize, position, (err3, _bytesRead, buf) => {
          fs.close(fd, () => {
            if (err3) resolve('');
            else resolve(buf.toString('utf-8'));
          });
        });
      });
    });
  });
}

/**
 * 裁决活性等级。
 *
 * 输入为各类信号的统计和最后观测时间，输出活性等级字符串。
 *
 * @param {object} signalSummary - 信号摘要
 * @param {number} signalSummary.progressCount - tqdm 进度信号数量
 * @param {number} signalSummary.stageChangeCount - 阶段切换信号数量
 * @param {number} signalSummary.businessLogCount - 业务日志信号数量（window + document-shape + engine-config）
 * @param {number} signalSummary.apiNoiseCount - API 噪声信号数量
 * @param {number} signalSummary.errorCount - 错误信号数量
 * @param {string|null} signalSummary.lastBusinessSignalTime - 最后业务信号时间（ISO）
 * @param {object|null} previousObservation - 上次观测结果（用于判断进度是否变化）
 * @param {object|null} currentProgress - 当前 tqdm 进度
 * @returns {string} 活性等级
 */
export function determineActivityLevel(signalSummary, previousObservation, currentProgress) {
  const { progressCount, stageChangeCount, businessLogCount, apiNoiseCount, errorCount } = signalSummary;

  // 明确错误
  if (errorCount > 0) return 'failed-confirmed';

  // 有 tqdm 进度
  if (progressCount > 0 && currentProgress) {
    // 与上次比较：是否有真实变化
    if (previousObservation &&
        previousObservation.phase === currentProgress.phase &&
        previousObservation.percent === currentProgress.percent &&
        previousObservation.current === currentProgress.current) {
      // 进度数值未变——但仍然有进度行输出，看其他业务信号
      if (stageChangeCount > 0) return 'active-stage-change';
      if (businessLogCount > 0) return 'active-business-log';
      // 无其他信号，判 suspected-stale
      return 'suspected-stale';
    }
    return 'active-progress';
  }

  // 无 tqdm 但有阶段切换
  if (stageChangeCount > 0) return 'active-stage-change';

  // 无 tqdm、无阶段切换但有业务日志
  if (businessLogCount > 0) return 'active-business-log';

  // 只有 API 噪声
  if (apiNoiseCount > 0) return 'api-alive-only';

  // 什么都没有
  return 'no-business-signal';
}

/**
 * 解析 MinerU 日志，返回结构化活性观测结果。
 *
 * 替代旧版仅解析 tqdm 的 parseLatestMineruProgress()，新增：
 * - 结构化信号分类（progress / window / document-shape / engine-config / api-noise / error）
 * - 活性等级裁决
 * - GET /health、GET /tasks/{id} 不刷新 lastProgressObservedAt
 * - 兼容旧版返回格式（phase / percent / current / total / observedAt / lastProgressObservedAt）
 *
 * @param {string|null} minObservedAt - 当前任务的最早观测时间（用于排除旧任务日志）
 * @param {object|null} previousObservation - 上次观测结果（用于判断进度变化和活性裁决）
 * @returns {Promise<object|null>} 结构化观测结果，无日志或全部过期返回 null
 */
export async function parseLatestMineruProgress(minObservedAt, previousObservation = null, executionProfile = null) {
  const logPaths = [
    process.env.MINERU_ERR_LOG_PATH || '/Users/concm/ops/logs/mineru-api.err.log',
    process.env.MINERU_LOG_PATH || '/Users/concm/ops/logs/mineru-api.log',
    path.join(process.cwd(), 'uat', 'scratch', 'mineru-api.err.log'),
    path.join(process.cwd(), 'uat', 'scratch', 'mineru-api.log')
  ];

  let bestResult = null;
  let candidates = [];
  let selectedLogSource = null;
  let fallbackLogSource = null;
  const observerCheckedAt = new Date().toISOString();

  for (const filePath of logPaths) {
    const logSource = {
      logSourcePath: filePath,
      logSourceExists: false,
      logSourceReadable: false,
      logSourceSize: 0,
      logSourceMtime: null,
      logSourceAgeMs: null,
      observerCheckedAt
    };

    try {
      if (!fs.existsSync(filePath)) {
        if (!fallbackLogSource) {
          fallbackLogSource = { ...logSource, logSourceSelectedReason: 'file-not-found' };
        }
        continue;
      }
      
      logSource.logSourceExists = true;
      const stats = fs.statSync(filePath);
      logSource.logSourceSize = stats.size;
      logSource.logSourceMtime = new Date(stats.mtimeMs).toISOString();
      logSource.logSourceAgeMs = Date.now() - stats.mtimeMs;

      const content = await readTail(filePath, 16384); // 读取 16KB 以覆盖更多信号
      if (!content) {
        if (!fallbackLogSource || !fallbackLogSource.logSourceExists) {
          fallbackLogSource = { ...logSource, logSourceSelectedReason: 'unreadable-or-empty' };
        }
        continue;
      }
      
      logSource.logSourceReadable = true;
      const lines = content.split(/[\r\n]+/);

      // 信号统计
      let progressCount = 0;
      let stageChangeCount = 0;
      let businessLogCount = 0;
      let apiNoiseCount = 0;
      let errorCount = 0;

      let latestProgress = null;
      let latestWindow = null;
      let latestError = null;
      let lastBusinessSignalTime = null;
      let lastContextTime = null;
      let previousPhase = previousObservation?.phase || null;
      const businessSignals = [];

      // ── 任务段切片：按 minObservedAt 丢弃旧任务日志行 ──
      // 核心逻辑：
      // 1. 带时间戳的行 → 更新 lastContextTime；若早于 minObservedAt → 跳过
      // 2. 无时间戳的 tqdm 行 → 继承 lastContextTime；若 lastContextTime 早于 minObservedAt 或无上下文 → 跳过
      // 3. mtime 只用于日志源可达性/新鲜度判定，不用于业务信号归因
      const minObservedMs = minObservedAt ? new Date(minObservedAt).getTime() : 0;

      for (const line of lines) {
        const classified = classifyLogLine(line);
        if (!classified) continue;

        // 更新时间戳上下文（无论是否被过滤，都要维护 lastContextTime）
        if (classified.timestamp) {
          lastContextTime = classified.timestamp;
        }

        // ── 任务段切片过滤 ──
        // 计算当前行的有效时间戳：自身时间戳 > 继承最近上下文时间戳 > null
        const effectiveTime = classified.timestamp || lastContextTime;

        if (minObservedMs > 0) {
          if (effectiveTime) {
            // 有效时间戳早于当前任务开始时间 → 属于旧任务，丢弃
            const effectiveMs = new Date(effectiveTime).getTime();
            if (effectiveMs < minObservedMs) {
              continue;
            }
          } else {
            // 无任何时间戳上下文（文件开头的裸 tqdm 行）→ 无法归因，丢弃
            if (classified.signalType !== 'api-noise') {
              continue;
            }
          }
        }

        switch (classified.signalType) {
          case 'progress':
            progressCount++;
            // 检测阶段切换
            if (previousPhase && classified.detail.phase !== previousPhase) {
              stageChangeCount++;
            }
            previousPhase = classified.detail.phase;
            latestProgress = classified.detail;
            // 设置 contextTime：优先使用自身时间戳，其次继承上下文
            if (classified.timestamp) {
              latestProgress.contextTime = new Date(classified.timestamp).toISOString();
            } else if (lastContextTime) {
              latestProgress.contextTime = new Date(lastContextTime).toISOString();
            }
            lastBusinessSignalTime = classified.timestamp || lastContextTime;
            break;

          case 'window':
            businessLogCount++;
            latestWindow = classified.detail;
            lastBusinessSignalTime = classified.timestamp || lastContextTime;
            businessSignals.push({ type: 'window', ...classified.detail });
            break;

          case 'document-shape':
          case 'engine-config':
            businessLogCount++;
            lastBusinessSignalTime = classified.timestamp || lastContextTime;
            businessSignals.push({ type: classified.signalType, raw: classified.detail?.rawLine });
            break;

          case 'error':
            errorCount++;
            latestError = classified.detail;
            lastBusinessSignalTime = classified.timestamp || lastContextTime;
            break;

          case 'api-noise':
            apiNoiseCount++;
            // 不更新 lastBusinessSignalTime
            break;
        }
      }

      const signalSummary = { progressCount, stageChangeCount, businessLogCount, apiNoiseCount, errorCount, lastBusinessSignalTime };
      let activityLevel = determineActivityLevel(signalSummary, previousObservation, latestProgress);
      
      if (activityLevel === 'no-business-signal') {
        activityLevel = 'log-observation-no-business-signal';
      }

      if (activityLevel === 'no-business-signal') {
        activityLevel = 'log-observation-no-business-signal';
      }

      let backendProfile = executionProfile?.backendEffective || executionProfile?.backendRequested || executionProfile?.backend || 'pipeline';

      let document = {
        totalPages: null,
        currentPages: null
      };
      if (latestWindow && latestWindow.pageTotal) {
        document.totalPages = latestWindow.pageTotal;
      } else {
        for (let i = businessSignals.length - 1; i >= 0; i--) {
          const sig = businessSignals[i];
          if (sig.type === 'document-shape' && sig.raw) {
            const m = sig.raw.match(/page_count\s*=\s*(\d+)/i);
            if (m) {
              document.totalPages = parseInt(m[1], 10);
              break;
            }
          }
        }
      }

      let unitType = 'unknown-units';
      let normalizedPhase = latestProgress?.phase || '';

      if (backendProfile.includes('hybrid')) {
        if (normalizedPhase.toLowerCase().includes('predict')) {
           if (latestWindow && latestProgress?.total === latestWindow.windowTotal) {
               unitType = 'window-pages';
           } else if (latestWindow && latestProgress?.total === (latestWindow.pageEnd - latestWindow.pageStart + 1)) {
               unitType = 'document-pages';
           } else if (document.totalPages && latestProgress?.total === document.totalPages) {
               unitType = 'document-pages';
           } else if (latestProgress?.total > 100 && (!document.totalPages || latestProgress?.total > document.totalPages)) {
               unitType = 'model-units';
           } else {
               unitType = 'model-units';
           }
        } else if (normalizedPhase === 'OCR-rec' || normalizedPhase.includes('OCR')) {
           unitType = 'ocr-recognition-blocks';
        }
      } else {
        if (normalizedPhase === 'Processing pages' || normalizedPhase === 'Layout' || normalizedPhase.includes('Predict Layout') || normalizedPhase.includes('Layout Predict')) {
            unitType = 'document-pages';
        } else if (normalizedPhase.includes('Table')) {
            unitType = 'table-regions';
        } else if (normalizedPhase.includes('OCR')) {
            unitType = 'ocr-recognition-blocks';
        } else if (normalizedPhase === 'Seal') {
            unitType = 'seal-units';
        } else if (document.totalPages && latestProgress?.total === document.totalPages) {
            unitType = 'document-pages';
        }
      }

      let stage = null;
      if (latestProgress) {
          stage = {
             rawPhase: latestProgress.phase,
             normalizedPhase: normalizedPhase,
             unitType: unitType,
             current: latestProgress.current,
             total: latestProgress.total,
             percent: latestProgress.percent
          };
      }

      let signals = {
          hasBusinessSignal: businessLogCount > 0 || progressCount > 0 || stageChangeCount > 0,
          hasApiNoiseOnly: apiNoiseCount > 0 && progressCount === 0 && businessLogCount === 0 && errorCount === 0,
          hasErrorSignal: errorCount > 0
      };

      let windowObj = null;
      if (latestWindow) {
         windowObj = {
             index: latestWindow.windowCurrent,
             total: latestWindow.windowTotal,
             pageStart: latestWindow.pageStart,
             pageEnd: latestWindow.pageEnd,
             pageTotal: latestWindow.pageTotal
         };
      }

      let candidateResult = {
        source: 'mineru-log',
        phase: latestProgress?.phase || null,
        percent: latestProgress?.percent ?? null,
        current: latestProgress?.current ?? null,
        total: latestProgress?.total ?? null,
        rawLine: latestProgress?.rawLine || null,
        activityLevel,
        signalSummary,
        latestWindow,
        latestError,
        businessSignals: businessSignals.slice(-5),
        logFileUpdatedAt: new Date(stats.mtimeMs).toISOString(),
        contextTime: latestProgress?.contextTime || (lastBusinessSignalTime ? new Date(lastBusinessSignalTime).toISOString() : null),
        backendProfile,
        document,
        window: windowObj,
        stage,
        signals
      };

      candidates.push({ result: candidateResult, logSource });

    } catch (_e) {
      // 忽略读取错误
    }
  }

  if (candidates.length > 0) {
    let minObservedMs = minObservedAt ? new Date(minObservedAt).getTime() : 0;
    
    for (const c of candidates) {
      const res = c.result;
      let businessMs = 0;
      if (res.contextTime) {
        businessMs = new Date(res.contextTime).getTime();
      }
      const mtimeMs = new Date(res.logFileUpdatedAt).getTime();
      c.businessMs = businessMs;
      c.mtimeMs = mtimeMs;
      // 一个日志文件在行级切片后仍有业务信号 → 合法业务信号
      // 注意：mtime 仅用于日志源可达性/新鲜度，不用于业务信号归因
      c.hasValidBusiness = res.signals.hasBusinessSignal;
    }
    
    candidates.sort((a, b) => {
      // 1. 优先选择有合法业务信号的
      if (a.hasValidBusiness && !b.hasValidBusiness) return -1;
      if (!a.hasValidBusiness && b.hasValidBusiness) return 1;
      
      // 2. 如果都有或都没有，比较业务信号时间 / mtime
      if (a.hasValidBusiness) {
        if (a.businessMs > 0 && b.businessMs > 0) {
          return b.businessMs - a.businessMs;
        }
      }
      return b.mtimeMs - a.mtimeMs;
    });

    bestResult = candidates[0].result;
    selectedLogSource = { ...candidates[0].logSource, logSourceSelectedReason: candidates[0].hasValidBusiness ? 'latest-valid-business-signal' : 'latest-mtime-fallback' };
  }

  // 兜底日志不可达状态
  if (!bestResult) {
    const finalLogSource = fallbackLogSource || {
      logSourcePath: logPaths[1],
      logSourceExists: false,
      logSourceReadable: false,
      logSourceSize: 0,
      logSourceMtime: null,
      logSourceAgeMs: null,
      logSourceSelectedReason: 'default-fallback',
      observerCheckedAt
    };

    let activityLevel = 'log-observation-unavailable';
    let observationStaleReason = 'no valid business signal found';
    
    if (!finalLogSource.logSourceExists) {
      activityLevel = 'log-observation-missing';
      observationStaleReason = 'log file does not exist';
    } else if (!finalLogSource.logSourceReadable) {
      activityLevel = 'log-observation-unreadable';
      observationStaleReason = 'log file is unreadable or empty';
    }

    return {
      activityLevel,
      observationStale: true,
      observationStaleReason,
      logSource: finalLogSource,
      observerCheckedAt
    };
  }

  // 计算 lastProgressObservedAt（只看业务信号时间，不看 API 噪声时间）
  const now = new Date().toISOString();
  let lastProgressObservedAt;

  if (bestResult.contextTime) {
    lastProgressObservedAt = bestResult.contextTime;
  } else if (previousObservation) {
    if (previousObservation.phase === bestResult.phase &&
        previousObservation.percent === bestResult.percent &&
        previousObservation.current === bestResult.current) {
      // 无变化：沿用上次时间
      lastProgressObservedAt = previousObservation.lastProgressObservedAt || previousObservation.observedAt || bestResult.logFileUpdatedAt;
    } else {
      lastProgressObservedAt = now;
    }
  } else {
    lastProgressObservedAt = now;
  }

  bestResult.lastProgressObservedAt = lastProgressObservedAt;
  bestResult.observedAt = lastProgressObservedAt; // 兼容旧版

  // 排除旧任务日志
  if (minObservedAt) {
    const logTime = new Date(bestResult.logFileUpdatedAt).getTime();
    const minTime = new Date(minObservedAt).getTime();
    if (logTime < minTime) {
      // 保留之前可用的信息，但修改 activityLevel 为 unattributed
      bestResult.activityLevel = 'log-observation-unattributed';
      bestResult.observationStale = true;
      bestResult.observationStaleReason = 'log file mtime is older than task start time, likely from previous task';
      bestResult.logSource = selectedLogSource;
      bestResult.observerCheckedAt = observerCheckedAt;
      return bestResult;
    }
  }

  // 日志观测新鲜度裁决：日志文件 mtime 距当前超过阈值 → log-observation-stale
  const logAge = Date.now() - new Date(bestResult.logFileUpdatedAt).getTime();
  bestResult.observerCheckedAt = observerCheckedAt;
  bestResult.logSource = selectedLogSource;

  if (logAge > MINERU_LOG_STALE_MS && bestResult.activityLevel !== 'failed-confirmed') {
    bestResult.observationStale = true;
    bestResult.observationStaleReason = 'container-visible MinerU log file is stale while MinerU API is still processing';
    bestResult.activityLevel = 'log-observation-stale';
    // Patch 16.2.6: 挂载一致性诊断信息，帮助运维判断是 Docker bind mount 延迟还是真实停滞
    bestResult.mountDiagnostic = {
      logAgeMs: logAge,
      logStaleThresholdMs: MINERU_LOG_STALE_MS,
      containerPath: bestResult.logSource?.logSourcePath || null,
      containerMtime: bestResult.logFileUpdatedAt,
      containerSize: bestResult.logSource?.logSourceSize || null,
      suggestion: 'Check host file with: ls -li /Users/concm/ops/logs/mineru-api.err.log && docker exec cms-upload-server ls -li /host/mineru-logs/mineru-api.err.log — if inode/size/mtime differ, the bind mount is not consistent. Ensure docker-compose uses :consistent flag and start-mineru-api.sh touches files before exec.'
    };
  } else {
    bestResult.observationStale = false;
  }

  return bestResult;
}
