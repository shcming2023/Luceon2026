/**
 * batch-queue.mjs — 后端批处理队列引擎
 *
 * 将批处理循环从浏览器前端下沉到 Node.js 后端，实现浏览器无关的
 * 不间断处理。支持断点续传、指数退避重试、内存保护、进度持久化。
 *
 * 设计原则：
 *   - 串行处理（一次只处理一个文件），避免 MinerU/Ollama 内存争抢
 *   - 队列状态持久化到 db-server，进程重启后自动恢复
 *   - 运行中任务在重启后自动回退为 pending 并重试
 *   - 指数退避重试（最多 3 次），避免瞬时故障导致永久失败
 *   - 内存水位监控，超过阈值自动暂停
 *   - 文件间冷却期，给 GC 和 MinerU 释放资源的时间
 */

import fs from 'fs';
import os from 'os';

// ─── Docker 环境检测与地址重写 ────────────────────────────────────
const IS_DOCKER = fs.existsSync('/.dockerenv') || (process.env.DB_BASE_URL || '').includes('db-server');

/**
 * Docker 环境下自动将 localhost/127.0.0.1 替换为 host.docker.internal
 * Docker 容器内 localhost 指向容器自身，而非宿主机。
 */
function dockerRewriteEndpoint(endpoint) {
  if (!IS_DOCKER || !endpoint) return endpoint;
  return endpoint
    .replace(/\/\/localhost([:/])/g, '//host.docker.internal$1')
    .replace(/\/\/127\.0\.0\.1([:/])/g, '//host.docker.internal$1');
}

// ─── 常量 ─────────────────────────────────────────────────────
const BATCH_PERSIST_INTERVAL = 5000;   // 每 5 秒持久化一次
const BATCH_MAX_RETRIES = 3;
const BATCH_RETRY_BASE_DELAY = 15000;  // 15s 基础退避
const BATCH_INTER_FILE_DELAY = 3000;   // 文件间 3s 冷却
const BATCH_MEMORY_THRESHOLD = 0.85;   // 系统内存使用超过 85% 时暂停

// ─── 队列状态 ─────────────────────────────────────────────────
const batchQueue = {
  items: [],        // Array<BatchJob>
  alerts: [],
  running: false,
  paused: false,
  autoMinerU: true,
  autoAI: true,
  createdAt: 0,
  updatedAt: 0,
};

/**
 * BatchJob 结构：
 * {
 *   id: string,              // 唯一标识（前端生成）
 *   fileName: string,
 *   fileSize: number,
 *   path: string,            // 相对路径（用于溯源）
 *   objectName: string,      // MinIO 中原始文件的 objectName
 *   mimeType: string,
 *   materialId: number,      // 关联的 material ID
 *   status: 'pending'|'uploaded'|'mineru'|'ai'|'completed'|'error'|'skipped',
 *   progress: number,        // 0-100
 *   message: string,
 *   error: string,
 *   retries: number,
 *   maxRetries: number,
 *   lastRetryAt: number,
 *   markdownObjectName: string,
 *   markdownUrl: string,
 *   createdAt: number,
 *   updatedAt: number,
 * }
 */

let batchWorkerRunning = false;
let batchPersistTimer = null;
let dbBaseUrl = 'http://localhost:8789';
let currentJobId = '';
let currentAbortController = null;
const cancelRequested = new Set();
let alertCounter = 0;

// ─── 外部依赖注入（由 upload-server 在初始化时提供）────────────
let _deps = null;

/**
 * 初始化批处理队列引擎
 * @param {object} deps - 外部依赖
 * @param {string} deps.dbBaseUrl - db-server 地址
 * @param {function} deps.getMinioClient - 获取 MinIO client
 * @param {function} deps.getMinioBucket - 获取原始资料 bucket
 * @param {function} deps.getParsedBucket - 获取解析产物 bucket
 * @param {function} deps.getPresignedExpiry - 获取预签名有效期
 * @param {function} deps.ensureBucket - 确保 bucket 存在
 * @param {function} deps.rewritePresignedUrl - 重写预签名 URL
 * @param {function} deps.getStorageBackend - 获取存储后端类型
 * @param {function} deps.uploadBufferToMinIO - 上传 buffer 到 MinIO
 * @param {function} deps.extractLocalMarkdown - 提取 markdown
 * @param {function} deps.buildMarkdownContext - 构建 markdown 上下文
 * @param {function} deps.callGradioToMarkdown - Gradio 解析
 * @param {function} deps.waitMinerUTask - 等待 MinerU 任务
 * @param {function} deps.fetchMinerUResult - 获取 MinerU 结果
 * @param {function} deps.analyzeWithFallback - AI 分析
 * @param {function} deps.calcPages - 计算页数
 * @param {function} deps.detectFormat - 检测格式
 * @param {function} deps.normalizeFileName - 规范化文件名
 * @param {function} deps.fixFilenameEncoding - 修复文件名编码
 * @param {function} deps.isEnabledFlag - 检查布尔标志
 * @param {function} deps.getFileBuffer - 获取文件 buffer
 * @param {function} deps.cleanupTempFile - 清理临时文件
 */
export function initBatchQueue(deps) {
  _deps = deps;
  dbBaseUrl = deps.dbBaseUrl || dbBaseUrl;
}

// ─── 持久化 ───────────────────────────────────────────────────

async function persistBatchQueue() {
  try {
    const snapshot = {
      items: batchQueue.items.map(j => ({
        id: j.id, fileName: j.fileName, fileSize: j.fileSize, path: j.path,
        objectName: j.objectName, mimeType: j.mimeType, materialId: j.materialId,
        status: j.status, progress: j.progress, message: j.message || '',
        error: j.error || '', retries: j.retries, maxRetries: j.maxRetries,
        markdownObjectName: j.markdownObjectName || '',
        markdownUrl: j.markdownUrl || '',
        mineruTaskId: j.mineruTaskId || '',
        mineruSubmittedAt: j.mineruSubmittedAt || 0,
        errorType: j.errorType || '',
        createdAt: j.createdAt, updatedAt: j.updatedAt,
      })),
      alerts: Array.isArray(batchQueue.alerts) ? batchQueue.alerts.map(a => ({
        id: a.id,
        ts: a.ts,
        level: a.level,
        message: a.message,
        jobId: a.jobId || '',
        read: a.read === true,
      })) : [],
      running: batchQueue.running,
      paused: batchQueue.paused,
      autoMinerU: batchQueue.autoMinerU,
      autoAI: batchQueue.autoAI,
      updatedAt: Date.now(),
    };
    await fetch(`${dbBaseUrl}/settings/serverBatchQueue`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(snapshot),
      signal: AbortSignal.timeout(5000),
    });
  } catch (e) {
    console.warn('[batch-queue] persist failed:', e.message);
  }
}

/** 从 db-server 恢复队列状态（启动时调用） */
export async function restoreBatchQueue() {
  try {
    const res = await fetch(`${dbBaseUrl}/settings`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return;
    const settings = await res.json();
    const saved = settings?.serverBatchQueue;
    if (!saved || !Array.isArray(saved.items)) return;

    batchQueue.items = saved.items.map(j => {
      const mineruTaskId = String(j.mineruTaskId || '').trim();
      const status = String(j.status || '');
      return {
        ...j,
        mineruTaskId,
        mineruSubmittedAt: Number(j.mineruSubmittedAt || 0),
        errorType: String(j.errorType || ''),
        status:
          status === 'mineru' && mineruTaskId
            ? 'mineru'
            : ['uploading', 'ai'].includes(status)
              ? 'pending'
              : status,
        retries: j.retries || 0,
        maxRetries: j.maxRetries || BATCH_MAX_RETRIES,
      };
    });
    batchQueue.alerts = Array.isArray(saved.alerts)
      ? saved.alerts
          .map((a) => ({
            id: String(a?.id || '').trim() || `alert-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            ts: Number(a?.ts || 0) || Date.now(),
            level: String(a?.level || 'info'),
            message: String(a?.message || ''),
            jobId: String(a?.jobId || ''),
            read: a?.read === true,
          }))
          .slice(-200)
      : [];
    alertCounter = batchQueue.alerts.length;
    batchQueue.running = saved.running || false;
    batchQueue.paused = saved.paused || false;
    batchQueue.autoMinerU = saved.autoMinerU !== false;
    batchQueue.autoAI = saved.autoAI !== false;
    batchQueue.updatedAt = saved.updatedAt || Date.now();

    const pendingCount = batchQueue.items.filter(j => j.status === 'pending').length;
    const totalCount = batchQueue.items.length;
    console.log(`[batch-queue] Restored ${totalCount} jobs (${pendingCount} pending), running=${batchQueue.running}`);

    // 如果恢复时队列是 running 状态，自动启动 worker
    if (batchQueue.running && pendingCount > 0) {
      console.log('[batch-queue] Auto-resuming worker after restart...');
      startBatchWorker();
    }
  } catch (e) {
    console.warn('[batch-queue] restore failed:', e.message);
  }
}

function startPersistTimer() {
  if (batchPersistTimer) return;
  batchPersistTimer = setInterval(() => {
    if (batchQueue.items.length > 0 || (batchQueue.alerts?.length ?? 0) > 0) persistBatchQueue();
  }, BATCH_PERSIST_INTERVAL);
}

function stopPersistTimer() {
  if (batchPersistTimer) {
    clearInterval(batchPersistTimer);
    batchPersistTimer = null;
  }
}

// ─── 内存监控 ─────────────────────────────────────────────────

function readTextFileSafe(path) {
  try {
    return fs.readFileSync(path, 'utf8').trim();
  } catch {
    return null;
  }
}

function parseBytesFromText(text) {
  if (!text) return null;
  if (text === 'max') return null;
  const n = Number.parseInt(text, 10);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}

function getCgroupMemoryBytes() {
  const v2Max = readTextFileSafe('/sys/fs/cgroup/memory.max');
  const v2Cur = readTextFileSafe('/sys/fs/cgroup/memory.current');
  const v2Limit = parseBytesFromText(v2Max);
  const v2Usage = parseBytesFromText(v2Cur);
  if (v2Limit && v2Usage != null && v2Limit < 2 ** 60) {
    return { limitBytes: v2Limit, usageBytes: v2Usage };
  }

  const v1LimitText =
    readTextFileSafe('/sys/fs/cgroup/memory/memory.limit_in_bytes') ??
    readTextFileSafe('/sys/fs/cgroup/memory.limit_in_bytes');
  const v1UsageText =
    readTextFileSafe('/sys/fs/cgroup/memory/memory.usage_in_bytes') ??
    readTextFileSafe('/sys/fs/cgroup/memory.usage_in_bytes');
  const v1Limit = parseBytesFromText(v1LimitText);
  const v1Usage = parseBytesFromText(v1UsageText);
  if (v1Limit && v1Usage != null && v1Limit < 2 ** 60) {
    return { limitBytes: v1Limit, usageBytes: v1Usage };
  }

  return null;
}

function mergeAbortSignals(signals) {
  const list = (Array.isArray(signals) ? signals : []).filter(Boolean);
  if (list.length === 0) return null;
  if (list.length === 1) return list[0];

  const anyFn = AbortSignal && typeof AbortSignal.any === 'function' ? AbortSignal.any.bind(AbortSignal) : null;
  if (anyFn) return anyFn(list);

  const controller = new AbortController();
  const onAbort = () => controller.abort();
  for (const s of list) {
    if (s.aborted) {
      controller.abort();
      break;
    }
    s.addEventListener('abort', onAbort, { once: true });
  }
  return controller.signal;
}

function isAbortError(err) {
  const name = err && typeof err === 'object' && 'name' in err ? String(err.name || '') : '';
  const msg = err instanceof Error ? err.message : String(err || '');
  return name === 'AbortError' || /aborted due to timeout/i.test(msg) || /operation was aborted/i.test(msg);
}

function classifyJobError(message) {
  const msg = String(message || '');
  const lower = msg.toLowerCase();
  if (/无成功响应/.test(msg) || /状态查询超时/.test(msg) || /silence/.test(lower)) {
    return 'service_down';
  }
  if (
    /hub\s*(connection|connect)?\s*(fail|error)/i.test(msg) ||
    /huggingface/i.test(msg) ||
    /hf\s*hub/i.test(lower) ||
    (/download/.test(lower) && /(model|weight)/.test(lower) && /hub/.test(lower))
  ) {
    return 'config';
  }
  return 'transient';
}

function shouldResetMineruTaskId(message) {
  const msg = String(message || '');
  const lower = msg.toLowerCase();
  return (
    /http 404/.test(lower) ||
    /not found/.test(lower) ||
    /task.*not.*found/.test(lower) ||
    /任务.*不存在/.test(msg)
  );
}

function pushAlert(level, message, jobId = '') {
  const id = `alert-${Date.now()}-${(alertCounter++).toString(36)}`;
  const item = {
    id,
    ts: Date.now(),
    level: String(level || 'info'),
    message: String(message || ''),
    jobId: String(jobId || ''),
    read: false,
  };
  batchQueue.alerts = Array.isArray(batchQueue.alerts) ? batchQueue.alerts : [];
  batchQueue.alerts.push(item);
  if (batchQueue.alerts.length > 200) batchQueue.alerts.splice(0, batchQueue.alerts.length - 200);
  batchQueue.updatedAt = Date.now();
}

export function readAlerts(ids = []) {
  const list = Array.isArray(batchQueue.alerts) ? batchQueue.alerts : [];
  if (list.length === 0) return { ok: true, read: 0 };
  const targets = Array.isArray(ids) && ids.length > 0 ? new Set(ids.map(String)) : null;
  let changed = 0;
  for (const a of list) {
    if (a.read === true) continue;
    if (targets && !targets.has(String(a.id))) continue;
    a.read = true;
    changed++;
  }
  if (changed > 0) persistBatchQueue();
  return { ok: true, read: changed };
}

function checkMemoryPressure() {
  const cgroup = getCgroupMemoryBytes();
  const totalMem = cgroup?.limitBytes ?? os.totalmem();
  const usedMem = cgroup?.usageBytes ?? Math.max(0, totalMem - os.freemem());
  const freeMem = Math.max(0, totalMem - usedMem);
  const usedRatio = totalMem > 0 ? usedMem / totalMem : 0;
  return {
    usedRatio,
    freeMB: Math.round(freeMem / 1024 / 1024),
    totalMB: Math.round(totalMem / 1024 / 1024),
    pressure: usedRatio > BATCH_MEMORY_THRESHOLD,
  };
}

// ─── 队列更新辅助 ─────────────────────────────────────────────

function updateJob(id, updates) {
  const job = batchQueue.items.find(j => j.id === id);
  if (!job) return null;
  Object.assign(job, updates, { updatedAt: Date.now() });
  batchQueue.updatedAt = Date.now();
  return job;
}

function findNextPending() {
  return batchQueue.items.find(j =>
    j.status === 'pending' ||
    (j.status === 'mineru' && String(j.mineruTaskId || '').trim())
  );
}

// ─── 同步 material 到 db-server ──────────────────────────────

async function syncMaterialToDb(materialId, updates) {
  try {
    await fetch(`${dbBaseUrl}/materials/${materialId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
      signal: AbortSignal.timeout(5000),
    });
  } catch (e) {
    console.warn(`[batch-queue] syncMaterial ${materialId} failed:`, e.message);
  }
}

async function addMaterialToDb(material) {
  try {
    await fetch(`${dbBaseUrl}/materials`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(material),
      signal: AbortSignal.timeout(5000),
    });
  } catch (e) {
    console.warn(`[batch-queue] addMaterial ${material.id} failed:`, e.message);
  }
}

// ─── 加载配置（从 db-server 读取 AI/MinerU 配置）─────────────

async function loadConfigs() {
  try {
    const res = await fetch(`${dbBaseUrl}/settings`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const settings = await res.json();
    return {
      aiConfig: settings?.aiConfig || {},
      mineruConfig: settings?.mineruConfig || {},
      minioConfig: settings?.minioConfig || {},
    };
  } catch (e) {
    console.warn('[batch-queue] loadConfigs failed:', e.message);
    return { aiConfig: {}, mineruConfig: {}, minioConfig: {} };
  }
}

// ─── 核心处理：处理单个文件 ──────────────────────────────────

async function processOneJob(job) {
  const deps = _deps;
  if (!deps) throw new Error('batch-queue not initialized');

  currentJobId = job.id;
  currentAbortController = new AbortController();
  try {
    const { mineruConfig, aiConfig } = await loadConfigs();

    // ── 阶段 1：检查文件是否已上传到 MinIO ──
    if (!job.objectName) {
      throw new Error('文件未上传到 MinIO（objectName 为空）。请通过前端上传文件后再提交到后端队列。');
    }

  updateJob(job.id, { status: 'uploaded', progress: 30, message: '文件已在 MinIO 中' });

  // ── 阶段 2：MinerU 解析 ──
  if (!batchQueue.autoMinerU) {
    updateJob(job.id, { status: 'completed', progress: 100, message: '已完成（跳过 MinerU）' });
    return;
  }

  const localEndpoint = dockerRewriteEndpoint(String(mineruConfig.localEndpoint || 'http://mineru:8010').trim());
  const localTimeout = Number(mineruConfig.localTimeout || 3600);
  const timeoutMs = Math.max(localTimeout * 1000, 30_000);
  const backend = String(mineruConfig.localBackend || 'hybrid-auto-engine');
  const maxPages = Number(mineruConfig.localMaxPages || 1000);
  const ocrLanguage = String(mineruConfig.localOcrLanguage || 'ch');
  const enableOcr = Boolean(mineruConfig.localEnableOcr);
  const enableFormula = Boolean(mineruConfig.localEnableFormula);
  const enableTable = mineruConfig.localEnableTable !== false;

  // 检查 MinerU 可达性
  let mineruReachable = false;
  const candidates = [`${localEndpoint}/health`, localEndpoint, `${localEndpoint}/gradio_api/info`];
  for (const target of candidates) {
    try {
      const r = await fetch(target, { signal: AbortSignal.timeout(5000) });
      if (r.status === 200) { mineruReachable = true; break; }
    } catch { /* ignore */ }
  }
  if (!mineruReachable) {
    throw new Error(`本地 MinerU 不可达：${localEndpoint}`);
  }

  updateJob(job.id, { status: 'mineru', progress: 40, message: 'MinerU 解析中...' });

  // 从 MinIO 读取原始文件
  const bucket = deps.getMinioBucket();
  const fileStream = await deps.getMinioClient().getObject(bucket, job.objectName);
  const chunks = [];
  for await (const chunk of fileStream) chunks.push(chunk);
  const fileBuffer = Buffer.concat(chunks);

  console.log(`[batch-queue] Processing ${job.fileName} (${(fileBuffer.length / 1024 / 1024).toFixed(1)} MB) via MinerU`);

  const abortSignal = currentAbortController?.signal || null;
  let mineruTaskId = String(job.mineruTaskId || '').trim();

  if (!mineruTaskId) {
    const fastApiForm = new FormData();
    fastApiForm.append(
      'files',
      new Blob([fileBuffer], { type: job.mimeType || 'application/octet-stream' }),
      job.fileName,
    );
    fastApiForm.append('backend', backend);
    for (const lang of String(ocrLanguage || 'ch').split(',').map(s => s.trim()).filter(Boolean)) {
      fastApiForm.append('lang_list', lang);
    }
    let parseMethod = 'auto';
    if (enableOcr) parseMethod = 'ocr';
    fastApiForm.append('parse_method', parseMethod);
    fastApiForm.append('formula_enable', String(enableFormula));
    fastApiForm.append('table_enable', String(enableTable));
    const rawServerUrl = String(mineruConfig.localServerUrl || '').trim();
    const serverUrl = dockerRewriteEndpoint(rawServerUrl || (/vlm|hybrid/i.test(backend) ? 'http://localhost:30000' : ''));
    if (serverUrl) fastApiForm.append('server_url', serverUrl);
    fastApiForm.append('return_md', 'true');
    fastApiForm.append('response_format_zip', 'false');
    if (Number.isFinite(maxPages) && maxPages > 0) {
      fastApiForm.append('end_page_id', String(Math.max(0, Math.floor(maxPages) - 1)));
    }

    const mineruSubmitSignal = mergeAbortSignals([abortSignal, AbortSignal.timeout(timeoutMs)]);
    const fastApiResponse = await fetch(`${localEndpoint}/tasks`, {
      method: 'POST',
      body: fastApiForm,
      signal: mineruSubmitSignal,
    });

    if (!fastApiResponse.ok) {
      const payload = await fastApiResponse.json().catch(() => ({}));
      const error = String(payload?.error || payload?.message || payload?.detail || '');
      throw new Error(`MinerU 任务提交失败: ${error || `HTTP ${fastApiResponse.status}`}`);
    }

    const taskPayload = await fastApiResponse.json().catch(() => null);
    mineruTaskId = String(taskPayload?.task_id || '').trim();
    if (!mineruTaskId) {
      throw new Error(`MinerU 未返回 task_id`);
    }

    updateJob(job.id, { mineruTaskId, mineruSubmittedAt: Date.now() });
    persistBatchQueue();
    console.log(`[batch-queue] MinerU task submitted: ${mineruTaskId} for ${job.fileName}`);
  } else {
    console.log(`[batch-queue] Reusing MinerU task_id=${mineruTaskId} for ${job.fileName}`);
  }

  updateJob(job.id, { progress: 50, message: `MinerU 任务 ${mineruTaskId} 处理中...` });

  // 轮询等待完成
  await deps.waitMinerUTask(localEndpoint, mineruTaskId, timeoutMs, (statusPayload) => {
    const status = String(statusPayload?.status || '').toLowerCase();
    if (status === 'processing') {
      updateJob(job.id, { progress: 60, message: 'MinerU 正在执行 OCR 与解析...' });
    }
  }, abortSignal);

  // 提取结果
  updateJob(job.id, { progress: 70, message: '解析完成，提取结果...' });
  const resultPayload = await deps.fetchMinerUResult(localEndpoint, mineruTaskId, timeoutMs, abortSignal);
  const markdown = deps.extractLocalMarkdown(resultPayload);

  if (!markdown) {
    throw new Error('MinerU 未返回 Markdown 内容');
  }

  // 存储 markdown 到 MinIO
  const parsedBucket = deps.getParsedBucket();
  const mdObjectName = job.materialId ? `parsed/${job.materialId}/full.md` : `parsed/local-${Date.now()}/full.md`;
  let markdownObjectName = '';
  let markdownUrl = '';

  try {
    const client = deps.getMinioClient();
    await deps.ensureBucket(client, parsedBucket);
    const mdBuffer = Buffer.from(markdown, 'utf-8');
    await client.putObject(parsedBucket, mdObjectName, mdBuffer, mdBuffer.length, {
      'Content-Type': 'text/markdown; charset=utf-8',
    });
    markdownObjectName = mdObjectName;
    markdownUrl = deps.rewritePresignedUrl(
      await client.presignedGetObject(parsedBucket, mdObjectName, deps.getPresignedExpiry())
    );
  } catch (storageErr) {
    console.warn('[batch-queue] markdown store failed:', storageErr.message);
  }

  updateJob(job.id, {
    progress: 75,
    message: 'MinerU 解析完成',
    markdownObjectName,
    markdownUrl,
  });

  // 同步 material 状态
  if (job.materialId) {
    await syncMaterialToDb(job.materialId, {
      mineruStatus: 'completed',
      metadata: {
        markdownObjectName,
        markdownUrl,
        processingStage: batchQueue.autoAI ? 'ai' : '',
        processingMsg: 'MinerU 解析完成',
        processingProgress: '75',
        processingUpdatedAt: new Date().toISOString(),
      },
    });
  }

  // 释放 fileBuffer 引用，帮助 GC
  // (fileBuffer 在此作用域结束后自然释放)

  // ── 阶段 3：AI 分析 ──
  if (!batchQueue.autoAI || (!markdownObjectName && !markdownUrl)) {
    updateJob(job.id, { status: 'completed', progress: 100, message: '已完成（跳过 AI）' });
    if (job.materialId) {
      await syncMaterialToDb(job.materialId, {
        status: 'completed',
        metadata: {
          processingStage: '',
          processingMsg: '已完成（跳过 AI）',
          processingProgress: '100',
          processingUpdatedAt: new Date().toISOString(),
        },
      });
    }
    return;
  }

  updateJob(job.id, { status: 'ai', progress: 80, message: 'AI 分析中...' });
  if (job.materialId) {
    await syncMaterialToDb(job.materialId, {
      aiStatus: 'analyzing',
      metadata: {
        processingStage: 'ai',
        processingMsg: 'AI 分析中...',
        processingProgress: '80',
        processingUpdatedAt: new Date().toISOString(),
      },
    });
  }

  // 读取 markdown 内容
  let markdownText = '';
  if (markdownObjectName) {
    try {
      const mdStream = await deps.getMinioClient().getObject(parsedBucket, markdownObjectName);
      const mdChunks = [];
      for await (const chunk of mdStream) mdChunks.push(chunk);
      markdownText = Buffer.concat(mdChunks).toString('utf-8');
    } catch (e) {
      console.warn('[batch-queue] read markdown from MinIO failed:', e.message);
    }
  }

  if (!markdownText) {
    // 降级：直接使用之前提取的 markdown
    markdownText = markdown;
  }

  const maxMarkdownChars = Number(aiConfig.maxMarkdownChars || 100000);
  const { context: mdContext } = deps.buildMarkdownContext(markdownText, maxMarkdownChars);

  // 构建 AI providers
  let providers = [];
  if (Array.isArray(aiConfig.providers) && aiConfig.providers.length > 0) {
    providers = aiConfig.providers
      .filter(p => p.enabled !== false)
      .sort((a, b) => (a.priority ?? 999) - (b.priority ?? 999));
  } else if (aiConfig.apiEndpoint && aiConfig.model) {
    providers = [{
      id: 'legacy',
      name: 'API',
      enabled: true,
      apiEndpoint: aiConfig.apiEndpoint,
      apiKey: aiConfig.apiKey || '',
      model: aiConfig.model,
      timeout: aiConfig.timeout || 600,
      priority: 1,
    }];
  }

  if (providers.length === 0) {
    updateJob(job.id, { status: 'completed', progress: 100, message: '已完成（未配置 AI）' });
    return;
  }

  // 构建 prompt
  const defaultPrompts = {
    title: '根据内容识别资料的准确名称/标题',
    subject: '识别学科（数学/语文/英语/物理/化学/生物/历史/地理/政治/科学/综合/其他）',
    grade: '识别适用年级，返回 G1~G12（G1=小学一年级，G7=初中一年级，G10=高中一年级），或"通用"',
    materialType: '识别资料类型（课本/讲义/练习册/试卷/答案/教案/课件/大纲/其他）',
    tags: '提取3-8个关键标签，用于检索和分类（返回数组）',
    summary: '用2-3句话简要概括资料核心内容',
    language: '识别资料使用的语言（中文/英文/双语/其他）',
    country: '根据课程体系和内容判断适用国家/地区（中国/英国/美国/新加坡/澳大利亚/加拿大/其他）',
  };
  const p = { ...defaultPrompts, ...(aiConfig.prompts || {}) };

  const systemPrompt = `你是一个专业的教育资源元数据提取助手。请根据提供的文档内容，提取结构化的元数据信息。
严格按照 JSON 格式返回，不要输出任何其他内容，不要包含 markdown 代码块标记。`;

  const userPrompt = `请分析以下教育资料内容，并按照指定格式返回结构化元数据：

---文档内容开始---
${mdContext}
---文档内容结束---

请提取以下信息并以 JSON 格式返回：
{
  "title": "${p.title}",
  "subject": "${p.subject}",
  "grade": "${p.grade}",
  "materialType": "${p.materialType}",
  "language": "${p.language}",
  "country": "${p.country}",
  "tags": ["${p.tags}"],
  "summary": "${p.summary}",
  "confidence": 0-100（整体识别置信度）
}

注意：
1. tags 必须是字符串数组
2. confidence 必须是 0-100 的整数
3. 仅返回 JSON，不要有任何前缀或解释文字`;

  const enableThinking = aiConfig.enableThinking === true;
  const { result: extracted, providerId, providerName } = await deps.analyzeWithFallback(
    providers,
    systemPrompt,
    userPrompt,
    { maxRetries: 2, retryDelay: 1000, enableThinking, signal: abortSignal },
  );

  const aiResult = {
    title:        String(extracted.title || '').trim(),
    subject:      String(extracted.subject || '').trim(),
    grade:        String(extracted.grade || '').trim(),
    materialType: String(extracted.materialType || extracted.type || '').trim(),
    language:     String(extracted.language || '').trim(),
    country:      String(extracted.country || '').trim(),
    tags:         Array.isArray(extracted.tags) ? extracted.tags.map(String).filter(Boolean) : [],
    summary:      String(extracted.summary || '').trim(),
    confidence:   Math.min(100, Math.max(0, Number(extracted.confidence) || 0)),
  };

  console.log(`[batch-queue] AI analysis done for ${job.fileName} via ${providerName}: subject=${aiResult.subject}, grade=${aiResult.grade}`);

  updateJob(job.id, { status: 'completed', progress: 100, message: '全部完成' });

  // 同步 AI 结果到 material
  if (job.materialId) {
    await syncMaterialToDb(job.materialId, {
      status: 'completed',
      aiStatus: 'analyzed',
      ...(aiResult.title ? { title: aiResult.title } : {}),
      tags: aiResult.tags.length ? aiResult.tags : undefined,
      metadata: {
        subject: aiResult.subject,
        grade: aiResult.grade,
        type: aiResult.materialType,
        language: aiResult.language,
        country: aiResult.country,
        summary: aiResult.summary,
        aiConfidence: String(aiResult.confidence),
        aiAnalyzedAt: new Date().toISOString(),
        processingStage: '',
        processingMsg: '',
        processingProgress: '100',
        processingUpdatedAt: new Date().toISOString(),
      },
    });
  }
  } finally {
    if (currentJobId === job.id) currentJobId = '';
    if (currentAbortController) currentAbortController = null;
  }
}

// ─── Worker 循环 ──────────────────────────────────────────────

async function batchWorkerLoop() {
  if (batchWorkerRunning) return;
  batchWorkerRunning = true;
  startPersistTimer();

  console.log('[batch-queue] Worker started');

  while (batchQueue.running && !batchQueue.paused) {
    // 内存检查
    const mem = checkMemoryPressure();
    if (mem.pressure) {
      console.warn(`[batch-queue] Memory pressure detected: ${mem.freeMB}MB free / ${mem.totalMB}MB total (${(mem.usedRatio * 100).toFixed(1)}% used). Pausing...`);
      pushAlert('warn', `内存压力过大，队列已自动暂停：${mem.freeMB}MB 空闲 / ${mem.totalMB}MB 总计（${(mem.usedRatio * 100).toFixed(1)}% 已用）`);
      batchQueue.paused = true;
      batchQueue.updatedAt = Date.now();
      await persistBatchQueue();
      break;
    }

    const job = findNextPending();
    if (!job) {
      // 队列处理完毕
      console.log('[batch-queue] All jobs completed');
      batchQueue.running = false;
      batchQueue.updatedAt = Date.now();
      await persistBatchQueue();
      break;
    }

    try {
      await processOneJob(job);
      console.log(`[batch-queue] Job ${job.id} (${job.fileName}) completed successfully`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[batch-queue] Job ${job.id} (${job.fileName}) failed:`, message);

      if (cancelRequested.has(job.id) || isAbortError(err)) {
        cancelRequested.delete(job.id);
        updateJob(job.id, {
          status: 'skipped',
          progress: 0,
          message: '已取消',
          error: '',
        });
        if (job.materialId) {
          await syncMaterialToDb(job.materialId, {
            status: 'pending',
            metadata: {
              processingStage: '',
              processingMsg: '已取消',
              processingUpdatedAt: new Date().toISOString(),
            },
          });
        }
        continue;
      }

      const errType = classifyJobError(message);
      if (errType === 'service_down') {
        updateJob(job.id, {
          status: 'pending',
          message: 'MinerU 服务无响应，5 分钟后重试...',
          error: '',
          errorType: '',
        });
        pushAlert('warn', `MinerU 服务无响应：${job.fileName}（5 分钟后重试）`, job.id);
        await new Promise(r => setTimeout(r, 5 * 60 * 1000));
        continue;
      }
      if (errType === 'config') {
        updateJob(job.id, {
          status: 'error',
          message: `处理失败（需人工处理）: ${message}`,
          error: message,
          errorType: 'config',
        });
        pushAlert('error', `任务失败（需人工处理）：${job.fileName} — ${message}`, job.id);
        if (job.materialId) {
          await syncMaterialToDb(job.materialId, {
            status: 'failed',
            metadata: {
              processingStage: '',
              processingMsg: `处理失败（需人工处理）: ${message}`,
              processingUpdatedAt: new Date().toISOString(),
            },
          });
        }
        continue;
      }

      job.retries = (job.retries || 0) + 1;
      if (job.retries < (job.maxRetries || BATCH_MAX_RETRIES)) {
        const resetMineruTaskId = String(job.mineruTaskId || '').trim() && shouldResetMineruTaskId(message);
        if (resetMineruTaskId) {
          updateJob(job.id, { mineruTaskId: '', mineruSubmittedAt: 0 });
        }
        // 指数退避重试
        const delay = BATCH_RETRY_BASE_DELAY * Math.pow(2, job.retries - 1);
        console.log(`[batch-queue] Will retry job ${job.id} in ${delay / 1000}s (attempt ${job.retries + 1}/${job.maxRetries || BATCH_MAX_RETRIES})`);
        updateJob(job.id, {
          status: 'pending',
          message: `第 ${job.retries} 次失败，${delay / 1000}s 后重试: ${message}`,
          lastRetryAt: Date.now(),
          ...(resetMineruTaskId ? { message: `第 ${job.retries} 次失败，${delay / 1000}s 后重试（task_id 已重置）: ${message}` } : {}),
        });
        // 同步失败状态到 material
        if (job.materialId) {
          await syncMaterialToDb(job.materialId, {
            metadata: {
              processingMsg: `第 ${job.retries} 次失败，等待重试: ${message}`,
              processingUpdatedAt: new Date().toISOString(),
            },
          });
        }
        await new Promise(r => setTimeout(r, delay));
      } else {
        // 超过最大重试次数
        updateJob(job.id, {
          status: 'error',
          message: `处理失败（已重试 ${job.retries} 次）: ${message}`,
          error: message,
          errorType: 'transient',
        });
        pushAlert('error', `任务失败（已重试 ${job.retries} 次）：${job.fileName} — ${message}`, job.id);
        if (job.materialId) {
          await syncMaterialToDb(job.materialId, {
            status: 'failed',
            metadata: {
              processingStage: '',
              processingMsg: `处理失败（已重试 ${job.retries} 次）: ${message}`,
              processingUpdatedAt: new Date().toISOString(),
            },
          });
        }
      }
    }

    // 文件间冷却
    if (batchQueue.running && !batchQueue.paused) {
      await new Promise(r => setTimeout(r, BATCH_INTER_FILE_DELAY));
    }
  }

  batchWorkerRunning = false;
  stopPersistTimer();
  await persistBatchQueue();
  console.log('[batch-queue] Worker stopped');
}

function startBatchWorker() {
  if (batchWorkerRunning) return;
  batchQueue.running = true;
  batchQueue.paused = false;
  batchQueue.updatedAt = Date.now();
  batchWorkerLoop().catch(err => {
    console.error('[batch-queue] Worker loop crashed:', err);
    batchWorkerRunning = false;
    stopPersistTimer();
  });
}

// ─── 对外 API ─────────────────────────────────────────────────

/** 获取队列状态（供 REST API 调用） */
export function getQueueStatus() {
  const items = batchQueue.items;
  const pending = items.filter(j => j.status === 'pending').length;
  const processing = items.filter(j => ['uploaded', 'mineru', 'ai'].includes(j.status)).length;
  const completed = items.filter(j => j.status === 'completed').length;
  const errors = items.filter(j => j.status === 'error').length;
  const mem = checkMemoryPressure();
  const alerts = Array.isArray(batchQueue.alerts) ? batchQueue.alerts : [];
  const unreadAlerts = alerts.filter((a) => a && a.read !== true).length;

  return {
    running: batchQueue.running,
    paused: batchQueue.paused,
    autoMinerU: batchQueue.autoMinerU,
    autoAI: batchQueue.autoAI,
    total: items.length,
    pending,
    processing,
    completed,
    errors,
    items: items.map(j => ({
      id: j.id, fileName: j.fileName, fileSize: j.fileSize, path: j.path,
      materialId: j.materialId, status: j.status, progress: j.progress,
      message: j.message || '', error: j.error || '',
      mineruTaskId: j.mineruTaskId || '',
      mineruSubmittedAt: j.mineruSubmittedAt || 0,
      retries: j.retries, maxRetries: j.maxRetries,
      errorType: j.errorType || '',
      createdAt: j.createdAt, updatedAt: j.updatedAt,
    })),
    alerts: alerts.slice(-20),
    unreadAlerts,
    memory: mem,
    updatedAt: batchQueue.updatedAt,
  };
}

/** 添加任务到队列 */
export function addJobs(jobs) {
  const now = Date.now();
  const newJobs = jobs.map(j => ({
    id: j.id || `job-${now}-${Math.random().toString(36).slice(2, 8)}`,
    fileName: j.fileName,
    fileSize: j.fileSize || 0,
    path: j.path || j.fileName,
    objectName: j.objectName || '',
    mimeType: j.mimeType || 'application/pdf',
    materialId: j.materialId || 0,
    status: 'pending',
    progress: 0,
    message: '等待处理',
    error: '',
    retries: 0,
    maxRetries: j.maxRetries || BATCH_MAX_RETRIES,
    lastRetryAt: 0,
    markdownObjectName: '',
    markdownUrl: '',
    mineruTaskId: '',
    mineruSubmittedAt: 0,
    errorType: '',
    createdAt: now,
    updatedAt: now,
  }));
  batchQueue.items.push(...newJobs);
  batchQueue.updatedAt = now;
  console.log(`[batch-queue] Added ${newJobs.length} jobs, total: ${batchQueue.items.length}`);
  return newJobs.map(j => j.id);
}

/** 启动队列处理 */
export function startQueue(options = {}) {
  if (options.autoMinerU !== undefined) batchQueue.autoMinerU = options.autoMinerU;
  if (options.autoAI !== undefined) batchQueue.autoAI = options.autoAI;
  startBatchWorker();
  return { ok: true, message: '队列已启动' };
}

/** 暂停队列 */
export function pauseQueue() {
  batchQueue.paused = true;
  batchQueue.updatedAt = Date.now();
  persistBatchQueue();
  return { ok: true, message: '队列已暂停' };
}

/** 恢复队列 */
export function resumeQueue() {
  batchQueue.paused = false;
  batchQueue.updatedAt = Date.now();
  if (batchQueue.running) {
    startBatchWorker();
  }
  return { ok: true, message: '队列已恢复' };
}

/** 停止队列 */
export function stopQueue() {
  batchQueue.running = false;
  batchQueue.paused = false;
  batchQueue.updatedAt = Date.now();
  persistBatchQueue();
  return { ok: true, message: '队列已停止' };
}

export function cancelJob(jobId) {
  const job = batchQueue.items.find(j => j.id === jobId);
  if (!job) return { ok: false, error: '任务不存在' };

  if (['uploaded', 'mineru', 'ai'].includes(job.status)) {
    cancelRequested.add(job.id);
    updateJob(job.id, { message: '正在取消...' });
    if (job.id === currentJobId && currentAbortController) {
      try { currentAbortController.abort(); } catch {}
      return { ok: true, cancelled: true };
    }
    return { ok: false, error: '当前任务不在本进程执行中，无法取消' };
  }

  if (job.status === 'pending') {
    updateJob(job.id, { status: 'skipped', progress: 0, message: '已取消', error: '' });
    persistBatchQueue();
    return { ok: true, cancelled: true };
  }

  return { ok: false, error: '当前状态不支持取消' };
}

export function cancelCurrentJob() {
  if (!currentJobId) return { ok: false, error: '当前无运行任务' };
  return cancelJob(currentJobId);
}

/** 重试失败的任务 */
export function retryFailed() {
  let count = 0;
  for (const job of batchQueue.items) {
    if (job.status === 'error') {
      job.status = 'pending';
      job.retries = 0;
      job.error = '';
      job.message = '等待重试';
      job.updatedAt = Date.now();
      count++;
    }
  }
  batchQueue.updatedAt = Date.now();
  if (count > 0) persistBatchQueue();
  return { ok: true, retried: count };
}

/** 重试指定任务 */
export function retryJob(jobId) {
  const job = batchQueue.items.find(j => j.id === jobId);
  if (!job) return { ok: false, error: '任务不存在' };
  if (job.status !== 'error') return { ok: false, error: '只能重试失败的任务' };
  job.status = 'pending';
  job.retries = 0;
  job.error = '';
  job.message = '等待重试';
  job.updatedAt = Date.now();
  batchQueue.updatedAt = Date.now();
  persistBatchQueue();
  return { ok: true };
}

/** 移除任务 */
export function removeJob(jobId) {
  const idx = batchQueue.items.findIndex(j => j.id === jobId);
  if (idx === -1) return { ok: false, error: '任务不存在' };
  batchQueue.items.splice(idx, 1);
  batchQueue.updatedAt = Date.now();
  persistBatchQueue();
  return { ok: true };
}

/** 清空已完成和失败的任务 */
export function clearCompleted() {
  const before = batchQueue.items.length;
  batchQueue.items = batchQueue.items.filter(j => j.status !== 'completed' && j.status !== 'error' && j.status !== 'skipped');
  const removed = before - batchQueue.items.length;
  batchQueue.updatedAt = Date.now();
  if (removed > 0) persistBatchQueue();
  return { ok: true, removed };
}

/** 清空全部任务（停止队列） */
export function clearAll() {
  batchQueue.running = false;
  batchQueue.paused = false;
  batchQueue.items = [];
  batchQueue.updatedAt = Date.now();
  persistBatchQueue();
  return { ok: true };
}

/** 优雅停机时调用 */
export async function shutdown() {
  batchQueue.running = false;
  stopPersistTimer();
  await persistBatchQueue();
  console.log('[batch-queue] Shutdown: state persisted');
}
