/**
 * upload-server.mjs — 文件上传代理服务
 *
 * 端口：8788（通过 UPLOAD_PORT 环境变量覆盖）
 *
 * 存储后端（通过 STORAGE_BACKEND 环境变量切换，可在运行时通过 /settings/storage 接口更新）：
 *   minio    — MinIO 私有对象存储（默认），返回预签名 URL
 *   tmpfiles — tmpfiles.org 公开临时存储（降级 fallback）
 *
 * 路由总览：
 *   GET    /health                      → 健康检查
 *   POST   /upload                      → 上传单文件，返回 { url, fileName, size, mimeType, provider, pages, format }
 *   POST   /upload-multiple             → 上传多文件（最多 20 个），返回 results[]
 *   GET    /parse/status/:taskId        → 查询 MinerU 解析任务状态
 *   POST   /parse/download              → 下载 MinerU 解析结果（ZIP）并转存到 MinIO
 *   POST   /parse/analyze               → 解析已上传文件（MinerU OCR + MD 提取）
 *   GET    /settings/storage            → 读取当前 MinIO 配置（密钥脱敏）
 *   PUT    /settings/storage            → 更新运行时 MinIO 配置并持久化到 db-server
 *   POST   /settings/storage/test       → 测试 MinIO 连接（bucketExists）
 *
 * 环境变量（详见 .env.example）：
 *   UPLOAD_PORT              — 服务端口（默认 8788）
 *   STORAGE_BACKEND          — 存储后端（minio | tmpfiles，默认 tmpfiles）
 *   MINIO_ENDPOINT           — MinIO 端点（默认 minio）
 *   MINIO_PORT               — MinIO API 端口（默认 9000）
 *   MINIO_ACCESS_KEY         — MinIO 访问密钥
 *   MINIO_SECRET_KEY         — MinIO 私钥
 *   MINIO_BUCKET             — 原始资料存储桶名称（默认 eduassets）
 *   MINIO_PARSED_BUCKET      — MinerU 解析产物存储桶名称（默认 eduassets-parsed）
 *   MINIO_PRESIGNED_EXPIRY   — 预签名 URL 有效期秒数（默认 3600）
 *   DB_BASE_URL              — db-server 地址（默认 http://localhost:8789）
 */

import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { Client } from 'minio';
import JSZip from 'jszip';
import { PDFDocument } from 'pdf-lib';
import fs from 'fs';
import path from 'path';
import os from 'os';

const app = express();
const port = Number(process.env.UPLOAD_PORT || 8788);

// db-server 地址（用于持久化 MinIO 配置）
const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';

app.use(cors());
app.use(express.json());
app.use((req, res, next) => {
  const requestId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  req.requestId = requestId;
  res.setHeader('X-Request-Id', requestId);
  next();
});

// ─── MinIO 动态配置（可在运行时通过 /settings/storage 接口更新）─
let minioState = {
  storageBackend: process.env.STORAGE_BACKEND || 'tmpfiles',
  endpoint:       process.env.MINIO_ENDPOINT || 'minio',
  port:           Number(process.env.MINIO_PORT || 9000),
  useSSL:         process.env.MINIO_USE_SSL === 'true',
  accessKey:      process.env.MINIO_ACCESS_KEY || 'minioadmin',
  secretKey:      process.env.MINIO_SECRET_KEY || 'minioadmin',
  bucket:         process.env.MINIO_BUCKET || 'eduassets',
  parsedBucket:   process.env.MINIO_PARSED_BUCKET || 'eduassets-parsed',
  presignedExpiry: Number(process.env.MINIO_PRESIGNED_EXPIRY || 3600),
};

/** 根据 minioState 创建 MinIO Client */
function createMinioClient(state = minioState) {
  return new Client({
    endPoint:  state.endpoint,
    port:      state.port,
    useSSL:    state.useSSL,
    accessKey: state.accessKey,
    secretKey: state.secretKey,
  });
}

let minioClient = createMinioClient();

function getMinioClient()  { return minioClient; }
function getMinioBucket()  { return minioState.bucket; }
function getParsedBucket() { return minioState.parsedBucket || minioState.bucket; }
function getPresignedExpiry() { return minioState.presignedExpiry; }
function getStorageBackend() { return minioState.storageBackend; }

//// ─── multer（磁盘存储，避免大文件 OOM）──────────────────
const UPLOAD_TMP_DIR = path.join(os.tmpdir(), 'upload-server-tmp');
fs.mkdirSync(UPLOAD_TMP_DIR, { recursive: true });

const diskStorage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, UPLOAD_TMP_DIR),
  filename: (_req, file, cb) => cb(null, `${Date.now()}-${Math.random().toString(36).slice(2)}-${file.originalname}`),
});

const upload = multer({
  storage: diskStorage,
  limits: { fileSize: 200 * 1024 * 1024 },
});

const backupUpload = multer({
  storage: diskStorage,
  limits: { fileSize: 500 * 1024 * 1024 },
});

/**
 * 读取 multer 磁盘文件为 Buffer，读取完成后自动删除临时文件。
 * 兼容原有 req.file.buffer 的使用方式，最小化代码变动。
 */
function getFileBuffer(file) {
  if (file.buffer) return file.buffer; // 兼容内存存储模式
  const buf = fs.readFileSync(file.path);
  // 异步删除临时文件，不阻塞请求
  fs.unlink(file.path, () => {});
  return buf;
}

/** 安全清理 multer 临时文件（用于错误路径或提前返回场景） */
function cleanupTempFile(file) {
  if (file?.path) fs.unlink(file.path, () => {});
}

// ─── 工具函数 ─────────────────────────────────────────────────

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'upload-server' });
});

function toDownloadUrl(url) {
  if (!url) return '';
  return String(url).replace(/^https?:\/\/tmpfiles\.org\/(?:dl\/)?/, 'https://tmpfiles.org/dl/');
}

function normalizeFileName(name) {
  const normalized = (name || 'upload.bin')
    .normalize('NFKD')
    .replace(/[^\w.-]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || `upload_${Date.now()}.bin`;
}

/**
 * 从文件 buffer 计算页数
 * - PDF：使用 pdf-lib 解析
 * - 图片：返回 1
 * - 其他：返回 null
 */
async function calcPages(buffer, mimeType) {
  try {
    if (mimeType === 'application/pdf') {
      const pdf = await PDFDocument.load(buffer, { ignoreEncryption: true });
      return pdf.getPageCount();
    }
    if (mimeType && mimeType.startsWith('image/')) {
      return 1;
    }
    return null;
  } catch (e) {
    console.warn('[upload-server] calcPages failed:', e.message);
    return null;
  }
}

/**
 * 从 MIME 类型或文件名推断格式标签
 */
function detectFormat(mimeType, fileName) {
  const ext = (fileName || '').split('.').pop()?.toLowerCase() || '';
  const mime = (mimeType || '').toLowerCase();

  if (mime === 'application/pdf' || ext === 'pdf') return 'PDF';
  if (mime === 'image/jpeg' || ext === 'jpg' || ext === 'jpeg') return 'JPG';
  if (mime === 'image/png' || ext === 'png') return 'PNG';
  if (mime === 'application/msword' || ext === 'doc') return 'DOC';
  if (mime === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' || ext === 'docx') return 'DOCX';
  if (mime === 'application/vnd.ms-powerpoint' || ext === 'ppt') return 'PPT';
  if (mime === 'application/vnd.openxmlformats-officedocument.presentationml.presentation' || ext === 'pptx') return 'PPTX';
  if (ext) return ext.toUpperCase();
  return '未知';
}

function normalizeEndpoint(endpoint) {
  return String(endpoint || '').trim().replace(/\/+$/, '');
}

function isEnabledFlag(value) {
  return value === true || value === 'true' || value === '1' || value === 1;
}

function extractLocalMarkdown(payload) {
  if (typeof payload === 'string') return payload.trim();
  if (!payload || typeof payload !== 'object') return '';

  if (payload.results && typeof payload.results === 'object') {
    const firstKey = Object.keys(payload.results)[0];
    const md = firstKey ? payload.results?.[firstKey]?.md_content : '';
    if (typeof md === 'string' && md.trim() !== '') return md.trim();
  }

  if (Array.isArray(payload) && payload.length > 0 && typeof payload[0]?.text === 'string') {
    return payload[0].text.trim();
  }

  const candidates = [
    payload.md_text,
    payload.markdown,
    payload.text,
    payload.data?.md_text,
    payload.data?.markdown,
    payload.data?.text,
    payload.output?.md_text,
    payload.output?.markdown,
    Array.isArray(payload.data) ? payload.data.find((item) => typeof item === 'string') : '',
  ];

  const value = candidates.find((item) => typeof item === 'string' && item.trim() !== '');
  return typeof value === 'string' ? value.trim() : '';
}

/** 确保 MinIO Bucket 存在 */
async function ensureBucket(client = minioClient, bucket = getMinioBucket()) {
  const exists = await client.bucketExists(bucket);
  if (!exists) {
    await client.makeBucket(bucket, 'us-east-1');
    console.log(`[upload-server] Bucket "${bucket}" created`);
  }
}

async function streamToBuffer(stream) {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks);
}

async function getObjectBuffer(bucket, objectName) {
  const stream = await getMinioClient().getObject(bucket, objectName);
  return streamToBuffer(stream);
}

async function objectExists(bucket, objectName) {
  try {
    await getMinioClient().statObject(bucket, objectName);
    return true;
  } catch {
    return false;
  }
}

const gradioInfoCache = new Map();

async function fetchGradioInfo(localEndpoint) {
  const cacheKey = String(localEndpoint || '');
  const cached = gradioInfoCache.get(cacheKey);
  if (cached && Date.now() - cached.cachedAt < 60_000) return cached.data;

  const response = await fetch(`${localEndpoint}/gradio_api/info`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`读取 Gradio info 失败: HTTP ${response.status} ${detail.slice(0, 200)}`);
  }
  const data = await response.json();
  gradioInfoCache.set(cacheKey, { cachedAt: Date.now(), data });
  return data;
}

async function resolveGradioOcrLanguage(localEndpoint, rawLanguage) {
  const fallback = 'ch (Chinese, English, Chinese Traditional)';
  const lang = String(rawLanguage || '').trim();
  if (!lang) return fallback;

  try {
    const info = await fetchGradioInfo(localEndpoint);
    const endpoint = info?.named_endpoints?.['/to_markdown'];
    const langParam = endpoint?.parameters?.find((p) => p.parameter_name === 'language');
    const options = Array.isArray(langParam?.type?.enum) ? langParam.type.enum : [];
    if (options.includes(lang)) return lang;
    const byPrefix = options.find((opt) => typeof opt === 'string' && opt.startsWith(`${lang} `));
    if (byPrefix) return byPrefix;
  } catch {
  }

  if (lang === 'en') return 'en (English)';
  if (lang === 'ch' || lang === 'zh') return fallback;
  return fallback;
}

async function uploadFileToGradio(localEndpoint, buffer, fileName, mimeType, timeoutMs) {
  const form = new FormData();
  form.append('files', new Blob([buffer], { type: mimeType || 'application/octet-stream' }), fileName || 'upload.bin');
  const response = await fetch(`${localEndpoint}/gradio_api/upload`, {
    method: 'POST',
    body: form,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`Gradio upload 失败: HTTP ${response.status} ${detail.slice(0, 200)}`);
  }
  const payload = await response.json().catch(() => null);
  const filePath = Array.isArray(payload) ? payload[0] : '';
  if (!filePath) throw new Error('Gradio upload 未返回文件路径');
  return String(filePath);
}

async function readSseFinalData(response, timeoutMs) {
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`SSE 订阅失败: HTTP ${response.status} ${detail.slice(0, 200)}`);
  }
  if (!response.body) throw new Error('SSE 响应缺少 body');

  const reader = response.body.getReader();
  const start = Date.now();
  let buffer = '';
  while (Date.now() - start < timeoutMs) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += Buffer.from(value).toString('utf8');

    let splitIndex = buffer.indexOf('\n\n');
    while (splitIndex >= 0) {
      const block = buffer.slice(0, splitIndex);
      buffer = buffer.slice(splitIndex + 2);
      splitIndex = buffer.indexOf('\n\n');

      const lines = block.split('\n');
      let eventName = '';
      let dataValue = '';
      for (const line of lines) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        if (line.startsWith('data:')) dataValue += line.slice(5).trim();
      }

      if (eventName === 'error') {
        throw new Error(`Gradio 解析失败: ${dataValue || 'unknown error'}`);
      }
      if (eventName === 'complete') {
        return dataValue;
      }
    }
  }

  throw new Error('Gradio 解析超时（未收到 complete 事件）');
}

async function callGradioToMarkdown(localEndpoint, fileBuffer, fileName, mimeType, params) {
  const timeoutMs = Math.max(Number(params?.timeoutMs || 30_000), 30_000);
  const uploadedPath = await uploadFileToGradio(localEndpoint, fileBuffer, fileName, mimeType, timeoutMs);
  const language = await resolveGradioOcrLanguage(localEndpoint, params?.ocrLanguage);

  const data = [
    { path: uploadedPath, orig_name: fileName, mime_type: mimeType, meta: { _type: 'gradio.FileData' } },
    Number(params?.maxPages || 1000),
    Boolean(params?.enableOcr),
    Boolean(params?.enableFormula),
    Boolean(params?.enableTable),
    language,
    String(params?.backend || 'hybrid-auto-engine'),
    String(params?.serverUrl || 'http://localhost:30000'),
  ];

  const callResp = await fetch(`${localEndpoint}/gradio_api/call/to_markdown`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const callPayload = await callResp.json().catch(() => null);
  if (!callResp.ok) {
    throw new Error(`Gradio call 失败: HTTP ${callResp.status} ${(callPayload?.detail || '').slice(0, 200)}`);
  }
  const eventId = callPayload?.event_id;
  if (!eventId) throw new Error('Gradio call 未返回 event_id');

  const sseResp = await fetch(`${localEndpoint}/gradio_api/call/to_markdown/${eventId}`, {
    signal: AbortSignal.timeout(timeoutMs),
  });
  const finalData = await readSseFinalData(sseResp, timeoutMs);
  let result;
  try {
    result = JSON.parse(finalData);
  } catch {
    result = finalData;
  }

  const fileData = Array.isArray(result) ? result[1] : result?.data?.[1] || result?.[1] || null;
  const fileUrl = fileData?.url;
  const filePath = fileData?.path;

  let downloadUrl = '';
  if (typeof fileUrl === 'string' && fileUrl) {
    downloadUrl = fileUrl.startsWith('http') ? fileUrl : `${localEndpoint}${fileUrl.startsWith('/') ? '' : '/'}${fileUrl}`;
  } else if (typeof filePath === 'string' && filePath) {
    downloadUrl = `${localEndpoint}/gradio_api/file=${encodeURIComponent(filePath)}`;
  } else {
    throw new Error('Gradio 未返回可下载的 Markdown 文件');
  }

  const mdResp = await fetch(downloadUrl, { signal: AbortSignal.timeout(timeoutMs) });
  if (!mdResp.ok) {
    const detail = await mdResp.text().catch(() => '');
    throw new Error(`下载 Markdown 失败: HTTP ${mdResp.status} ${detail.slice(0, 200)}`);
  }
  return mdResp.text();
}

async function waitMinerUTask(localEndpoint, taskId, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}`, {
      signal: AbortSignal.timeout(Math.min(10_000, timeoutMs)),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof payload?.detail === 'string' ? payload.detail : '';
      throw new Error(`查询任务状态失败: HTTP ${response.status} ${detail}`.trim());
    }

    const status = String(payload?.status || '').toLowerCase();
    if (status === 'done' || status === 'success' || status === 'completed') return payload;
    if (status === 'failed' || status === 'error') {
      throw new Error(String(payload?.error || payload?.message || '任务执行失败'));
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error('等待 MinerU 任务超时');
}

async function fetchMinerUResult(localEndpoint, taskId, timeoutMs) {
  const response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}/result`, {
    signal: AbortSignal.timeout(Math.min(30_000, timeoutMs)),
  });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => null)
    : await response.text().catch(() => '');
  if (!response.ok) {
    const detail = typeof payload === 'string'
      ? payload
      : String(payload?.detail || payload?.error || payload?.message || '');
    throw new Error(`获取任务结果失败: HTTP ${response.status} ${detail}`.trim());
  }
  return payload;
}

async function fetchDbBackupSnapshot() {
  const response = await fetch(`${DB_BASE_URL}/backup/export`, {
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`读取数据库快照失败: HTTP ${response.status} ${detail.slice(0, 200)}`);
  }
  return response.json();
}

function normalizeDbDataForBulkRestore(data) {
  return {
    materials: Object.values(data?.materials || {}),
    assetDetails: data?.assetDetails || {},
    processTasks: Object.values(data?.processTasks || {}),
    tasks: Object.values(data?.tasks || {}),
    products: Object.values(data?.products || {}),
    flexibleTags: Object.values(data?.flexibleTags || {}),
    aiRules: Object.values(data?.aiRules || {}),
    aiRuleSettings: data?.settings?.aiRuleSettings,
    aiConfig: data?.settings?.aiConfig,
    mineruConfig: data?.settings?.mineruConfig,
    minioConfig: data?.settings?.minioConfig,
    settings: data?.settings || {},
  };
}

function getBackupRelativePath(name) {
  const parts = String(name || '').split('/').filter(Boolean);
  if (parts.length <= 1) return '';
  return parts.slice(1).join('/');
}

/**
 * 上传 buffer 到 MinIO
 * @param {string} [materialId] - 可选，传入时在 prefix 下创建以 materialId 命名的子目录（提升溯源能力）
 * @returns {{ objectName: string, presignedUrl: string }}
 */
async function uploadBufferToMinIO(buffer, mimeType, prefix, fileName, retries = 3, materialId = '') {
  let lastError = null;

  // 净化 materialId，防止路径注入
  const safeId = materialId ? String(materialId).replace(/[^a-zA-Z0-9_-]/g, '') : '';

  for (let i = 1; i <= retries; i++) {
    try {
      const client = getMinioClient();
      const bucket = getMinioBucket();
      await ensureBucket(client, bucket);

      const objectName = safeId
        ? `${prefix}/${safeId}/${Date.now()}-${fileName}`
        : `${prefix}/${Date.now()}-${fileName}`;
      await client.putObject(
        bucket,
        objectName,
        buffer,
        buffer.length,
        { 'Content-Type': mimeType || 'application/octet-stream' },
      );

      const presignedUrl = await client.presignedGetObject(
        bucket,
        objectName,
        getPresignedExpiry(),
      );

      console.log(`[upload-server] MinIO put: ${objectName}`);
      return { objectName, presignedUrl };
    } catch (error) {
      lastError = error;
      console.warn(`[upload-server] MinIO attempt ${i} failed:`, error.message);
      if (i < retries) await new Promise((r) => setTimeout(r, 700 * i));
    }
  }

  throw lastError || new Error('MinIO 上传失败');
}

/**
 * 上传到 tmpfiles（降级后端）
 */
async function uploadToTmpfiles(buffer, mimeType, fileName, retries = 3) {
  let lastError = null;

  for (let i = 1; i <= retries; i++) {
    try {
      const form = new FormData();
      form.append(
        'file',
        new Blob([buffer], { type: mimeType || 'application/octet-stream' }),
        fileName,
      );

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30_000);
      const resp = await fetch('https://tmpfiles.org/api/v1/upload', {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`tmpfiles HTTP ${resp.status}: ${detail}`);
      }

      const json = await resp.json();
      const rawUrl = json?.data?.url || '';
      const publicUrl = toDownloadUrl(rawUrl);
      if (!publicUrl) throw new Error('tmpfiles 未返回可用 URL');

      return { provider: 'tmpfiles', url: publicUrl, objectName: null };
    } catch (error) {
      lastError = error;
      if (i < retries) await new Promise((r) => setTimeout(r, 700 * i));
    }
  }

  throw lastError || new Error('tmpfiles 上传失败');
}

// ─── 接口：POST /upload ────────────────────────────────────────
// 上传原始文件（PDF / 图片等）到 MinIO originals/ 目录
// 响应：{ url, objectName, fileName, size, mimeType, provider, pages, format }
app.post('/upload', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      res.status(400).json({ error: '缺少文件字段 `file`' });
      return;
    }

    const safeFileName = normalizeFileName(req.file.originalname);

    // 并行计算 pages 和 format（不阻塞上传流程）
    const fileBuffer = getFileBuffer(req.file);
    const [pages, format] = await Promise.all([
      calcPages(fileBuffer, req.file.mimetype),
      Promise.resolve(detectFormat(req.file.mimetype, req.file.originalname)),
    ]);

    let result;

    // materialId 通过 multipart body 字段传入（可选），用于构造分层目录
    const materialId = req.body?.materialId || '';

    if (getStorageBackend() === 'minio') {
      try {
        const { objectName, presignedUrl } = await uploadBufferToMinIO(
          fileBuffer,
          req.file.mimetype,
          'originals',
          safeFileName,
          3,
          materialId,
        );
        result = {
          provider: 'minio',
          url: presignedUrl,
          objectName,
        };
      } catch (minioError) {
        console.error('[upload-server] MinIO failed, fallback to tmpfiles:', minioError.message);
        result = await uploadToTmpfiles(fileBuffer, req.file.mimetype, safeFileName);
        result.objectName = null;
      }
    } else {
      result = await uploadToTmpfiles(fileBuffer, req.file.mimetype, safeFileName);
      result.objectName = null;
    }

    res.json({
      url: result.url,
      objectName: result.objectName,
      fileName: req.file.originalname,
      size: req.file.size,
      mimeType: req.file.mimetype,
      provider: result.provider,
      pages,
      format,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /upload failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：GET /presign ────────────────────────────────────────
// 为已有 objectName 重新生成 presigned URL（避免前端拿到的 URL 过期）
// Query: ?objectName=originals/xxx.pdf
app.get('/presign', async (req, res) => {
  const { objectName } = req.query;
  if (!objectName || typeof objectName !== 'string') {
    res.status(400).json({ error: '缺少 objectName 参数' });
    return;
  }

  try {
    const url = await getMinioClient().presignedGetObject(getMinioBucket(), objectName, getPresignedExpiry());
    res.json({ url, objectName, expiresIn: getPresignedExpiry() });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /presign failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：POST /parse/oss-put ────────────────────────────────
// 服务端代理：把浏览器传来的文件 buffer PUT 到 MinerU 返回的阿里云 OSS 预签名 URL
// 解决浏览器直接 PUT OSS 时的 CORS / 网络访问问题
// Body (multipart): file=<binary>, ossUrl=<string>
// Response: { ok: true }
app.post('/parse/oss-put', upload.single('file'), async (req, res) => {
  const ossUrl = req.body?.ossUrl || req.query?.ossUrl;
  if (!ossUrl) {
    res.status(400).json({ error: '缺少 ossUrl 参数' });
    return;
  }
  if (!req.file) {
    res.status(400).json({ error: '缺少文件字段 file' });
    return;
  }

  try {
    console.log(`[upload-server] OSS PUT proxy → ${ossUrl.slice(0, 80)}… (${req.file.size} bytes)`);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120_000);

    // 注意：阿里云 OSS 预签名 URL 签名时未包含 Content-Type/Content-Length，
    // 额外传这些 header 会导致 SignatureDoesNotMatch (403)，所以只发送裸 body
    const putRes = await fetch(ossUrl, {
      method: 'PUT',
      body: getFileBuffer(req.file),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (!putRes.ok) {
      const text = await putRes.text();
      throw new Error(`OSS PUT 失败: HTTP ${putRes.status} — ${text.slice(0, 300)}`);
    }

    console.log(`[upload-server] OSS PUT success: HTTP ${putRes.status}`);
    res.json({ ok: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/oss-put failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：POST /parse/download ───────────────────────────────
// 下载 MinerU 解析结果 ZIP，解压后将 full.md 和图片存入 MinIO parsed/ 目录
// Body: { zipUrl: string, materialId: string|number }
// 响应: { files: [{ objectName, presignedUrl, name }], markdownObjectName, markdownUrl }
app.post('/parse/download', async (req, res) => {
  const { zipUrl, materialId } = req.body;

  if (!zipUrl || !materialId) {
    res.status(400).json({ error: '缺少 zipUrl 或 materialId' });
    return;
  }

  try {
    console.log(`[upload-server] Downloading MinerU ZIP for material ${materialId}: ${zipUrl}`);

    // 1. 下载 ZIP
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120_000);
    const zipResp = await fetch(zipUrl, { signal: controller.signal });
    clearTimeout(timer);

    if (!zipResp.ok) {
      throw new Error(`下载 ZIP 失败: HTTP ${zipResp.status}`);
    }

    const zipBuffer = Buffer.from(await zipResp.arrayBuffer());
    console.log(`[upload-server] ZIP downloaded, size: ${(zipBuffer.length / 1024).toFixed(1)} KB`);

    // 2. 用 JSZip 解压 buffer
    const zip = await JSZip.loadAsync(zipBuffer);
    const prefix = `parsed/${materialId}`;
    const uploadedFiles = [];
    let markdownObjectName = null;
    let markdownUrl = null;
    let markdownContent = null;

    // 先尝试 MinIO；若 MinIO 不可用则跳过
    let minioAvailable = true;
    try {
      await ensureBucket(minioClient, getParsedBucket());
    } catch (bucketErr) {
      minioAvailable = false;
      console.warn('[upload-server] MinIO unavailable, will inline markdownContent as fallback:', bucketErr.message);
    }

    for (const [name, zipEntry] of Object.entries(zip.files)) {
      if (zipEntry.dir) continue;

      const lower = name.toLowerCase();
      const isMd = lower.endsWith('.md');
      const isJson = lower.endsWith('.json');
      const isImage = /\.(png|jpg|jpeg|gif|webp|svg)$/.test(lower);

      if (!isMd && !isJson && !isImage) continue;

      const content = await zipEntry.async('nodebuffer');
      const mimeType = isMd ? 'text/markdown'
        : isJson ? 'application/json'
        : lower.endsWith('.png') ? 'image/png'
        : lower.endsWith('.svg') ? 'image/svg+xml'
        : 'image/jpeg';

      if (isMd && (name === 'full.md' || name.endsWith('/full.md')) && markdownContent === null) {
        markdownContent = content.toString('utf-8');
        console.log(`[upload-server] Captured full.md inline (${markdownContent.length} chars)`);
      }

      if (minioAvailable) {
        try {
          const client = getMinioClient();
          const bucket = getParsedBucket();
          const objectName = `${prefix}/${name}`;
          await client.putObject(bucket, objectName, content, content.length, { 'Content-Type': mimeType });
          const presignedUrl = await client.presignedGetObject(bucket, objectName, getPresignedExpiry());
          uploadedFiles.push({ objectName, presignedUrl, name });
          if (isMd && (name === 'full.md' || name.endsWith('/full.md'))) {
            markdownObjectName = objectName;
            markdownUrl = presignedUrl;
          }
          console.log(`[upload-server] Stored to MinIO (${bucket}): ${objectName}`);
        } catch (minioErr) {
          console.warn(`[upload-server] MinIO put failed for ${name}:`, minioErr.message);
          minioAvailable = false;
        }
      }

      if (!minioAvailable && isMd && (name === 'full.md' || name.endsWith('/full.md')) && !markdownUrl) {
        try {
          const tmpResult = await uploadToTmpfiles(content, 'text/plain', 'full.txt');
          markdownUrl = tmpResult.url;
          uploadedFiles.push({ objectName: null, presignedUrl: markdownUrl, name });
          console.log(`[upload-server] full.md fallback to tmpfiles: ${markdownUrl}`);
        } catch (tmpErr) {
          console.warn('[upload-server] tmpfiles fallback for full.md failed (will use inline content):', tmpErr.message);
        }
      }
    }

    res.json({
      files: uploadedFiles,
      markdownObjectName,
      markdownUrl,
      markdownContent,
      totalFiles: uploadedFiles.length,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/download failed:', message);
    res.status(500).json({ error: message });
  }
});

app.post('/parse/local-mineru/health', async (req, res) => {
  const localEndpoint = normalizeEndpoint(req.body?.localEndpoint);
  if (!localEndpoint) {
    res.status(400).json({ ok: false, message: '缺少 localEndpoint' });
    return;
  }

  const candidates = [
    `${localEndpoint}/health`,
    localEndpoint,
    `${localEndpoint}/gradio_api/info`,
  ];

  let lastMessage = '连接失败';
  for (const target of candidates) {
    try {
      const response = await fetch(target, { signal: AbortSignal.timeout(5000) });
      if (response.status === 200) {
        res.json({ ok: true, message: `本地 MinerU 在线：${target}` });
        return;
      }
      lastMessage = `${target} 返回 HTTP ${response.status}`;
    } catch (error) {
      lastMessage = error instanceof Error ? error.message : String(error);
    }
  }

  res.json({ ok: false, message: `本地 MinerU 不可达：${lastMessage}` });
});

app.post('/backup/full-export', async (_req, res) => {
  if (getStorageBackend() !== 'minio') {
    res.status(400).json({ error: '完整资产备份仅支持 MinIO 存储后端' });
    return;
  }

  try {
    const dbData = await fetchDbBackupSnapshot();
    const rawBucket = getMinioBucket();
    const parsedBucket = getParsedBucket();
    const materials = Object.values(dbData?.materials || {});
    const rawObjectMap = new Map();
    const parsedObjectMap = new Map();
    let skippedNonMinioMaterials = 0;

    for (const material of materials) {
      const materialId = String(material?.id || '').trim();
      if (material?.metadata?.provider !== 'minio') {
        skippedNonMinioMaterials += 1;
        continue;
      }
      if (material?.metadata?.objectName) {
        rawObjectMap.set(material.metadata.objectName, { bucket: rawBucket, objectName: material.metadata.objectName });
      }
      if (material?.metadata?.markdownObjectName) {
        parsedObjectMap.set(material.metadata.markdownObjectName, { bucket: parsedBucket, objectName: material.metadata.markdownObjectName });
      }
      if (materialId) {
        const [rawObjects, parsedObjects] = await Promise.all([
          listAllObjects(rawBucket, `originals/${materialId}/`),
          listAllObjects(parsedBucket, `parsed/${materialId}/`),
        ]);
        for (const item of rawObjects) {
          rawObjectMap.set(item.name, { bucket: rawBucket, objectName: item.name });
        }
        for (const item of parsedObjects) {
          parsedObjectMap.set(item.name, { bucket: parsedBucket, objectName: item.name });
        }
      }
    }

    const createdAt = new Date().toISOString();
    const folderName = `luceon2026-full-backup-${createdAt.replace(/[:.]/g, '-')}`;
    const zip = new JSZip();

    zip.file(`${folderName}/db/db-data.json`, JSON.stringify(dbData, null, 2));

    await Promise.all([
      ...Array.from(rawObjectMap.values()).map(async ({ bucket, objectName }) => {
        const buffer = await getObjectBuffer(bucket, objectName);
        zip.file(`${folderName}/${objectName}`, buffer);
      }),
      ...Array.from(parsedObjectMap.values()).map(async ({ bucket, objectName }) => {
        const buffer = await getObjectBuffer(bucket, objectName);
        zip.file(`${folderName}/${objectName}`, buffer);
      }),
    ]);

    const manifest = {
      version: '1.0.0',
      createdAt,
      materialsCount: materials.length,
      rawObjectCount: rawObjectMap.size,
      parsedObjectCount: parsedObjectMap.size,
      skippedNonMinioMaterials,
      dbFile: 'db/db-data.json',
    };

    zip.file(`${folderName}/manifest.json`, JSON.stringify(manifest, null, 2));

    const buffer = await zip.generateAsync({
      type: 'nodebuffer',
      compression: 'DEFLATE',
      compressionOptions: { level: 6 },
    });

    res.setHeader('Content-Type', 'application/zip');
    res.setHeader('Content-Disposition', `attachment; filename="${folderName}.zip"`);
    res.send(buffer);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /backup/full-export failed:', message);
    res.status(500).json({ error: message });
  }
});

app.post('/backup/full-import', backupUpload.single('file'), async (req, res) => {
  if (getStorageBackend() !== 'minio') {
    res.status(400).json({ error: '完整资产恢复仅支持 MinIO 存储后端' });
    return;
  }
  if (!req.file) {
    res.status(400).json({ error: '缺少备份文件' });
    return;
  }

  const mode = req.body?.mode === 'merge' ? 'merge' : 'replace';

  try {
    const zip = await JSZip.loadAsync(getFileBuffer(req.file));
    const manifestEntry = Object.values(zip.files).find((entry) => entry.name.endsWith('/manifest.json') || entry.name === 'manifest.json');
    if (!manifestEntry) {
      throw new Error('备份包缺少 manifest.json');
    }
    const manifest = JSON.parse(await manifestEntry.async('string'));
    const dbEntryPath = manifest?.dbFile
      ? Object.values(zip.files).find((entry) => entry.name.endsWith(`/${manifest.dbFile}`) || entry.name === manifest.dbFile)?.name
      : Object.values(zip.files).find((entry) => entry.name.endsWith('/db/db-data.json') || entry.name === 'db/db-data.json')?.name;
    if (!dbEntryPath) {
      throw new Error('备份包缺少 db/db-data.json');
    }

    const dbData = JSON.parse(await zip.file(dbEntryPath).async('string'));
    const rawBucket = getMinioBucket();
    const parsedBucket = getParsedBucket();
    await Promise.all([ensureBucket(getMinioClient(), rawBucket), ensureBucket(getMinioClient(), parsedBucket)]);

    let importedObjects = 0;
    let removedExistingObjects = 0;
    let skippedObjects = 0;

    if (mode === 'replace') {
      const [removedRawObjects, removedParsedObjects] = await Promise.all([
        removeAllObjects(rawBucket),
        removeAllObjects(parsedBucket),
      ]);
      removedExistingObjects = removedRawObjects + removedParsedObjects;
    }

    for (const entry of Object.values(zip.files)) {
      if (entry.dir) continue;
      const relativePath = getBackupRelativePath(entry.name);
      if (!relativePath || relativePath === 'manifest.json' || relativePath === 'db/db-data.json') continue;
      const bucket = relativePath.startsWith('originals/') ? rawBucket : relativePath.startsWith('parsed/') ? parsedBucket : '';
      if (!bucket) continue;
      if (mode === 'merge' && await objectExists(bucket, relativePath)) {
        skippedObjects += 1;
        continue;
      }
      const buffer = await entry.async('nodebuffer');
      await getMinioClient().putObject(bucket, relativePath, buffer, buffer.length);
      importedObjects += 1;
    }

    const dbResponse = await fetch(
      `${DB_BASE_URL}${mode === 'replace' ? '/backup/import' : '/bulk-restore'}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          mode === 'replace'
            ? { confirm: true, data: dbData }
            : normalizeDbDataForBulkRestore(dbData),
        ),
        signal: AbortSignal.timeout(30_000),
      },
    );
    const dbResult = await dbResponse.json().catch(() => null);
    if (!dbResponse.ok) {
      throw new Error(dbResult?.error || `数据库恢复失败: HTTP ${dbResponse.status}`);
    }

    res.json({
      ok: true,
      mode,
      importedObjects,
      removedExistingObjects,
      skippedObjects,
      materialsCount: Object.keys(dbData?.materials || {}).length,
      backupPath: dbResult?.backupPath,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /backup/full-import failed:', message);
    res.status(500).json({ error: message });
  }
});

app.post('/parse/local-mineru', upload.single('file'), async (req, res) => {
  const localEndpoint = normalizeEndpoint(req.body?.localEndpoint);
  const materialId = String(req.body?.materialId || '').trim();
  const localTimeout = Number(req.body?.localTimeout || 300);
  const startedAt = Date.now();

  if (!req.file) {
    res.status(400).json({ error: '缺少文件字段 `file`' });
    return;
  }
  if (!localEndpoint) {
    res.status(400).json({ error: '缺少 localEndpoint' });
    return;
  }

  try {
    const backend = String(req.body?.backend || 'hybrid-auto-engine');
    const maxPages = Number(req.body?.maxPages || 1000);
    const ocrLanguage = String(req.body?.ocrLanguage || req.body?.language || 'ch');
    const enableOcr = isEnabledFlag(req.body?.enableOcr);
    const enableFormula = isEnabledFlag(req.body?.enableFormula);
    const enableTable = isEnabledFlag(req.body?.enableTable);
    const timeoutMs = Math.max(localTimeout * 1000, 30_000);
    const parseMethod = String(req.body?.parseMethod || req.body?.parse_method || '').trim();
    const rawServerUrl = String(req.body?.serverUrl || req.body?.server_url || req.body?.url || '').trim();
    const serverUrl = rawServerUrl || (/vlm|hybrid/i.test(backend) ? 'http://localhost:30000' : '');

    const candidates = [
      `${localEndpoint}/health`,
      localEndpoint,
      `${localEndpoint}/gradio_api/info`,
    ];
    let lastMessage = '连接失败';
    let reachableTarget = '';
    for (const target of candidates) {
      try {
        const response = await fetch(target, { signal: AbortSignal.timeout(5000) });
        if (response.status === 200) {
          reachableTarget = target;
          break;
        }
        lastMessage = `${target} 返回 HTTP ${response.status}`;
      } catch (error) {
        lastMessage = error instanceof Error ? error.message : String(error);
      }
    }
    if (!reachableTarget) {
      throw new Error(`本地 MinerU 不可达：${lastMessage}`);
    }

    console.log(`[upload-server] /parse/local-mineru start materialId=${materialId || '-'} file=${req.file.originalname} size=${req.file.size}B endpoint=${localEndpoint} timeout=${localTimeout}s backend=${backend}`);

    let markdown = '';

    const fastApiForm = new FormData();
    const localFileBuffer = getFileBuffer(req.file);
    fastApiForm.append(
      'files',
      new Blob([localFileBuffer], { type: req.file.mimetype || 'application/octet-stream' }),
      req.file.originalname,
    );
    fastApiForm.append('backend', backend);
    for (const lang of String(ocrLanguage || 'ch').split(',').map((item) => item.trim()).filter(Boolean)) {
      fastApiForm.append('lang_list', lang);
    }
    if (parseMethod) fastApiForm.append('parse_method', parseMethod);
    fastApiForm.append('formula_enable', String(enableFormula));
    fastApiForm.append('table_enable', String(enableTable));
    if (serverUrl) fastApiForm.append('server_url', serverUrl);
    fastApiForm.append('return_md', 'true');
    fastApiForm.append('response_format_zip', 'false');
    fastApiForm.append('is_ocr', String(enableOcr));
    if (Number.isFinite(maxPages) && maxPages > 0) {
      fastApiForm.append('end_page_id', String(Math.max(0, Math.floor(maxPages) - 1)));
    }

    const fastApiResponse = await fetch(`${localEndpoint}/file_parse`, {
      method: 'POST',
      body: fastApiForm,
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (fastApiResponse.status !== 404 && fastApiResponse.status !== 405) {
      const contentType = fastApiResponse.headers.get('content-type') || '';
      const payload = contentType.includes('application/json')
        ? await fastApiResponse.json().catch(() => null)
        : await fastApiResponse.text().catch(() => '');

      if (!fastApiResponse.ok) {
        const taskId = payload?.task_id;
        const status = String(payload?.status || '').toLowerCase();
        const error = String(payload?.error || payload?.message || payload?.detail || '');
        if (taskId && (status === 'failed' || status === 'error')) {
          const message = /hub|snapshot folder|huggingface/i.test(error)
            ? `本地 MinerU 任务失败（可能无法下载模型，请检查网络/镜像或预下载模型）: ${error}`
            : `本地 MinerU 任务失败: ${error}`;
          throw new Error(message.trim());
        }
        if (taskId && (status === 'queued' || status === 'processing' || status === 'running')) {
          const finished = await waitMinerUTask(localEndpoint, taskId, timeoutMs);
          const resultPayload = await fetchMinerUResult(localEndpoint, taskId, timeoutMs);
          markdown = extractLocalMarkdown(resultPayload) || extractLocalMarkdown(finished);
        } else {
          throw new Error(`本地 MinerU 返回 HTTP ${fastApiResponse.status}: ${error || JSON.stringify(payload).slice(0, 300)}`);
        }
      } else if (payload?.task_id) {
        const taskId = String(payload.task_id);
        const status = String(payload?.status || '').toLowerCase();
        if (status && status !== 'done' && status !== 'success' && status !== 'completed') {
          await waitMinerUTask(localEndpoint, taskId, timeoutMs);
        }
        const resultPayload = await fetchMinerUResult(localEndpoint, taskId, timeoutMs);
        markdown = extractLocalMarkdown(resultPayload) || extractLocalMarkdown(payload);
      } else {
        markdown = extractLocalMarkdown(payload);
      }
    } else {
      const form = new FormData();
      form.append(
        'file',
        new Blob([localFileBuffer], { type: req.file.mimetype || 'application/octet-stream' }),
        req.file.originalname,
      );
      form.append('backend', backend);
      form.append('max_pages', String(maxPages));
      form.append('ocr_language', ocrLanguage);
      form.append('table_enable', String(enableTable));
      form.append('formula_enable', String(enableFormula));
      form.append('language', req.body?.language || ocrLanguage);
      form.append('enableOcr', String(enableOcr));
      form.append('enableFormula', String(enableFormula));
      form.append('enableTable', String(enableTable));
      form.append('enable_ocr', String(enableOcr));
      form.append('enable_formula', String(enableFormula));
      form.append('enable_table', String(enableTable));

      const response = await fetch(`${localEndpoint}/gradio_api/to_markdown`, {
        method: 'POST',
        body: form,
        signal: AbortSignal.timeout(timeoutMs),
      });

      if (!response.ok && response.status !== 404) {
        const detail = await response.text().catch(() => '');
        throw new Error(`本地 MinerU 返回 HTTP ${response.status}: ${detail.slice(0, 300)}`);
      }

      if (response.ok) {
        const contentType = response.headers.get('content-type') || '';
        const payload = contentType.includes('application/json')
          ? await response.json()
          : await response.text();
        markdown = extractLocalMarkdown(payload);
      } else {
        markdown = await callGradioToMarkdown(localEndpoint, localFileBuffer, req.file.originalname, req.file.mimetype, {
          backend,
          maxPages,
          ocrLanguage,
          enableOcr,
          enableFormula,
          enableTable,
          timeoutMs,
        });
      }
    }

    if (!markdown) {
      throw new Error('本地 MinerU 未返回 Markdown 内容');
    }

    const taskId = `local-${Date.now()}`;
    const objectName = materialId ? `parsed/${materialId}/full.md` : `parsed/${taskId}/full.md`;
    let markdownObjectName = '';
    let markdownUrl = '';

    try {
      const bucket = getParsedBucket();
      const client = getMinioClient();
      await ensureBucket(client, bucket);
      const markdownBuffer = Buffer.from(markdown, 'utf-8');
      await client.putObject(bucket, objectName, markdownBuffer, markdownBuffer.length, { 'Content-Type': 'text/markdown; charset=utf-8' });
      markdownObjectName = objectName;
      markdownUrl = await client.presignedGetObject(bucket, objectName, getPresignedExpiry());
    } catch (storageError) {
      console.warn('[upload-server] local MinerU markdown store failed:', storageError.message);
    }

    res.json({
      taskId,
      state: 'done',
      markdown,
      markdownObjectName: markdownObjectName || undefined,
      markdownUrl: markdownUrl || undefined,
      parsedFilesCount: markdownObjectName ? 1 : 0,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/local-mineru failed:', message, `elapsedMs=${Date.now() - startedAt}`);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：POST /parse/analyze ────────────────────────────────
// 读取 MinIO 中的 full.md，调用大模型 API，提取结构化元数据
// Body: { markdownObjectName?, markdownUrl?, markdownContent?, materialId, aiApiEndpoint, aiApiKey, aiModel, prompts? }
// 响应: { title, subject, grade, materialType, language, country, tags, summary, confidence }
app.post('/parse/analyze', async (req, res) => {
  const {
    markdownObjectName,
    markdownUrl,
    markdownContent,
    materialId,
    aiApiEndpoint,
    aiApiKey,
    aiModel,
    prompts,
  } = req.body;

  const trimmedKey = (aiApiKey || '').trim();
  const trimmedEndpoint = (aiApiEndpoint || '').trim();
  const trimmedModel = (aiModel || '').trim();

  if (!materialId) {
    res.status(400).json({ error: '缺少 materialId' });
    return;
  }
  if (!trimmedEndpoint || !trimmedKey || !trimmedModel) {
    res.status(400).json({ error: '缺少 AI API 配置（aiApiEndpoint / aiApiKey / aiModel）' });
    return;
  }

  try {
    // ── 1. 读取 Markdown 内容 ──────────────────────────────────
    let markdownText = '';

    if (markdownContent && typeof markdownContent === 'string' && markdownContent.trim()) {
      markdownText = markdownContent;
      console.log(`[upload-server] Using inline markdownContent (${markdownText.length} chars)`);
    }

    if (!markdownText && markdownObjectName) {
      try {
        const stream = await getMinioClient().getObject(getParsedBucket(), markdownObjectName);
        const chunks = [];
        for await (const chunk of stream) chunks.push(chunk);
        markdownText = Buffer.concat(chunks).toString('utf-8');
        console.log(`[upload-server] Read markdown from MinIO (${getParsedBucket()}): ${markdownObjectName} (${markdownText.length} chars)`);
      } catch (minioErr) {
        console.warn('[upload-server] MinIO read failed, fallback to markdownUrl:', minioErr.message);
      }
    }

    if (!markdownText && markdownUrl) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30_000);
      const mdResp = await fetch(markdownUrl, { signal: controller.signal });
      clearTimeout(timer);
      if (mdResp.ok) {
        markdownText = await mdResp.text();
        console.log(`[upload-server] Read markdown from URL (${markdownText.length} chars)`);
      }
    }

    if (!markdownText) {
      res.status(400).json({ error: '无法获取 Markdown 内容，请先完成 MinerU 解析并回存' });
      return;
    }

    const mdSnippet = markdownText.slice(0, 8000);

    // ── 2. 构建 Prompt ─────────────────────────────────────────
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

    const p = { ...defaultPrompts, ...(prompts || {}) };

    const systemPrompt = `你是一个专业的教育资源元数据提取助手。请根据提供的文档内容，提取结构化的元数据信息。
严格按照 JSON 格式返回，不要输出任何其他内容，不要包含 markdown 代码块标记。`;

    const userPrompt = `请分析以下教育资料内容，并按照指定格式返回结构化元数据：

---文档内容开始---
${mdSnippet}
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

    // ── 3. 调用大模型 API ──────────────────────────────────────
    console.log(`[upload-server] Calling AI API: ${trimmedEndpoint} model=${trimmedModel}`);

    const aiController = new AbortController();
    const aiTimer = setTimeout(() => aiController.abort(), 120_000);

    const aiFullUrl = /\/chat\/completions\/?$/.test(trimmedEndpoint)
      ? trimmedEndpoint
      : `${trimmedEndpoint.replace(/\/$/, '')}/chat/completions`;

    const aiResp = await fetch(aiFullUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${trimmedKey}`,
      },
      body: JSON.stringify({
        model: trimmedModel,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.1,
        max_tokens: 1024,
      }),
      signal: aiController.signal,
    });

    clearTimeout(aiTimer);

    if (!aiResp.ok) {
      const errText = await aiResp.text();
      throw new Error(`AI API 调用失败: HTTP ${aiResp.status} — ${errText.slice(0, 200)}`);
    }

    const aiJson = await aiResp.json();
    const rawContent = aiJson.choices?.[0]?.message?.content ?? '';

    // ── 4. 解析 AI 返回的 JSON ─────────────────────────────────
    let extracted;
    try {
      const jsonStr = rawContent.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim();
      extracted = JSON.parse(jsonStr);
    } catch {
      console.error('[upload-server] AI response is not valid JSON:', rawContent.slice(0, 500));
      throw new Error(`AI 返回格式异常，无法解析为 JSON。原始响应：${rawContent.slice(0, 200)}`);
    }

    const result = {
      title:        String(extracted.title || '').trim(),
      subject:      String(extracted.subject || '').trim(),
      grade:        String(extracted.grade || '').trim(),
      materialType: String(extracted.materialType || extracted.type || '').trim(),
      language:     String(extracted.language || '').trim(),
      country:      String(extracted.country || '').trim(),
      tags:         Array.isArray(extracted.tags) ? extracted.tags.map(String).filter(Boolean) : [],
      summary:      String(extracted.summary || '').trim(),
      confidence:   Math.min(100, Math.max(0, Number(extracted.confidence) || 0)),
      materialId,
      analyzedAt:   new Date().toISOString(),
    };

    console.log(`[upload-server] AI analysis done for material ${materialId}:`, {
      subject: result.subject, grade: result.grade, language: result.language, confidence: result.confidence,
    });

    res.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/analyze failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：GET /proxy-file ─────────────────────────────────────
// 服务端代理：从 MinIO 流式读取文件并回传给浏览器，解决浏览器直接访问 MinIO presigned URL 的 CORS/网络问题
// Query: ?objectName=originals/xxx.pdf[&bucket=eduassets]
// Response: 原始文件二进制流
app.get('/proxy-file', async (req, res) => {
  const { objectName, bucket: bucketParam } = req.query;
  if (!objectName || typeof objectName !== 'string') {
    res.status(400).json({ error: '缺少 objectName 参数' });
    return;
  }

  const bucket = (bucketParam && typeof bucketParam === 'string') ? bucketParam : getMinioBucket();

  try {
    console.log(`[upload-server] proxy-file: ${bucket}/${objectName}`);
    const client = getMinioClient();

    // 获取对象 stat 以设置 Content-Type 和 Content-Length
    let stat;
    try {
      stat = await client.statObject(bucket, objectName);
    } catch {
      stat = null;
    }

    const contentType = stat?.metaData?.['content-type'] || 'application/octet-stream';
    res.setHeader('Content-Type', contentType);
    if (stat?.size) res.setHeader('Content-Length', String(stat.size));
    res.setHeader('Cache-Control', 'private, max-age=300');

    const stream = await client.getObject(bucket, objectName);
    stream.on('error', (err) => {
      console.error('[upload-server] proxy-file stream error:', err.message);
      if (!res.headersSent) res.status(500).json({ error: err.message });
    });
    stream.pipe(res);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /proxy-file failed:', message);
    if (!res.headersSent) res.status(500).json({ error: message });
  }
});

// ─── 接口：GET /file ───────────────────────────────────────────
app.get('/file', async (req, res) => {
  const { objectName } = req.query;
  if (!objectName || typeof objectName !== 'string') {
    res.status(400).json({ error: '缺少 objectName 参数' });
    return;
  }

  try {
    const stream = await getMinioClient().getObject(getMinioBucket(), objectName);
    const chunks = [];
    for await (const chunk of stream) {
      chunks.push(chunk);
    }
    const content = Buffer.concat(chunks).toString('utf-8');
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.send(content);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /file failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：GET /settings/storage ──────────────────────────────
// 返回当前 MinIO 配置（密钥脱敏）
app.get('/settings/storage', (_req, res) => {
  res.json({
    storageBackend:  minioState.storageBackend,
    endpoint:        minioState.endpoint,
    port:            minioState.port,
    useSSL:          minioState.useSSL,
    accessKey:       minioState.accessKey ? '***' : '',
    secretKey:       minioState.secretKey ? '***' : '',
    bucket:          minioState.bucket,
    parsedBucket:    minioState.parsedBucket,
    presignedExpiry: minioState.presignedExpiry,
  });
});

// ─── 接口：PUT /settings/storage ──────────────────────────────
// 更新运行时 MinIO 配置，并持久化到 db-server
// Body: MinioConfig（密钥字段为 *** 时保留原值）
app.put('/settings/storage', async (req, res) => {
  const body = req.body || {};

  const newState = {
    storageBackend:  body.storageBackend  ?? minioState.storageBackend,
    endpoint:        body.endpoint        ?? minioState.endpoint,
    port:            Number(body.port     ?? minioState.port),
    useSSL:          body.useSSL          ?? minioState.useSSL,
    accessKey:       (body.accessKey && body.accessKey !== '***') ? body.accessKey : minioState.accessKey,
    secretKey:       (body.secretKey && body.secretKey !== '***') ? body.secretKey : minioState.secretKey,
    bucket:          body.bucket          ?? minioState.bucket,
    parsedBucket:    body.parsedBucket    ?? minioState.parsedBucket,
    presignedExpiry: Number(body.presignedExpiry ?? minioState.presignedExpiry),
  };

  // 更新运行时状态
  minioState = newState;
  minioClient = createMinioClient(newState);
  console.log(`[upload-server] MinIO config updated: ${newState.endpoint}:${newState.port} backend=${newState.storageBackend} bucket=${newState.bucket} parsedBucket=${newState.parsedBucket}`);

  // 异步持久化到 db-server
  try {
    await fetch(`${DB_BASE_URL}/settings/minioConfig`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newState),
      signal: AbortSignal.timeout(5000),
    });
    console.log('[upload-server] MinIO config persisted to db-server');
  } catch (e) {
    console.warn('[upload-server] Failed to persist MinIO config to db-server:', e.message);
  }

  res.json({ ok: true, message: 'MinIO 配置已更新' });
});

// ─── 接口：POST /settings/storage/test ────────────────────────
// 测试 MinIO 连接（使用传入配置创建临时 client 测试 bucketExists）
// Body: MinioConfig
app.post('/settings/storage/test', async (req, res) => {
  const body = req.body || {};

  const testState = {
    endpoint:  body.endpoint  || minioState.endpoint,
    port:      Number(body.port || minioState.port),
    useSSL:    body.useSSL    ?? minioState.useSSL,
    accessKey: (body.accessKey && body.accessKey !== '***') ? body.accessKey : minioState.accessKey,
    secretKey: (body.secretKey && body.secretKey !== '***') ? body.secretKey : minioState.secretKey,
  };
  const testBucket       = body.bucket       || minioState.bucket;
  const testParsedBucket = body.parsedBucket || minioState.parsedBucket || testBucket;

  console.log(`[upload-server] Testing MinIO connection: ${testState.endpoint}:${testState.port} bucket=${testBucket} parsedBucket=${testParsedBucket}`);

  try {
    const testClient = createMinioClient(testState);

    // 设置 5 秒超时，同时测试两个 bucket
    const timeout = (ms) => new Promise((_, reject) =>
      setTimeout(() => reject(new Error('连接超时（5s）')), ms),
    );
    const [rawExists, parsedExists] = await Promise.race([
      Promise.all([
        testClient.bucketExists(testBucket),
        testClient.bucketExists(testParsedBucket),
      ]),
      timeout(5000),
    ]);

    const rawMsg    = rawExists    ? `"${testBucket}" 存在` : `"${testBucket}" 不存在（将在首次上传时自动创建）`;
    const parsedMsg = parsedExists ? `"${testParsedBucket}" 存在` : `"${testParsedBucket}" 不存在（将在首次解析时自动创建）`;

    res.json({
      ok: true,
      message: `连接成功！原始资料 Bucket ${rawMsg}；解析产物 Bucket ${parsedMsg}`,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] MinIO test failed:', message);
    res.status(200).json({ ok: false, message: `连接失败：${message}` });
  }
});

// ─── 接口：GET /list ───────────────────────────────────────────
// 列出 MinIO 指定 prefix 下的所有文件，并为每个对象生成预签名 URL
// Query: ?prefix=parsed/123
// Response: { objects: [{ objectName, name, size, lastModified, presignedUrl }], total }
app.get('/list', async (req, res) => {
  const { prefix } = req.query;
  if (!prefix || typeof prefix !== 'string') {
    res.status(400).json({ error: '缺少 prefix 参数' });
    return;
  }

  try {
    const client = getMinioClient();
    const bucket = getParsedBucket();
    const objects = [];

    await new Promise((resolve, reject) => {
      const stream = client.listObjectsV2(bucket, prefix, true);
      stream.on('data', (obj) => {
        if (obj.name) objects.push(obj);
      });
      stream.on('end', resolve);
      stream.on('error', reject);
    });

    // 为每个对象生成预签名 URL
    const result = await Promise.all(
      objects.map(async (obj) => {
        let presignedUrl = '';
        try {
          presignedUrl = await client.presignedGetObject(bucket, obj.name, getPresignedExpiry());
        } catch (e) {
          console.warn(`[upload-server] presign failed for ${obj.name}:`, e.message);
        }
        // 提取短文件名（去掉 prefix 部分）
        const name = obj.name.replace(/^.*\//, '');
        return {
          objectName: obj.name,
          name: name || obj.name,
          size: obj.size ?? 0,
          lastModified: obj.lastModified ? new Date(obj.lastModified).toISOString() : '',
          presignedUrl,
        };
      }),
    );

    res.json({ objects: result, total: result.length });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn('[upload-server] /list failed (returning empty):', message);
    // MinIO 不可用时返回空列表，防止前端溯源卡片崩溃
    res.json({ objects: [], total: 0 });
  }
});

app.get('/storage-stats', async (_req, res) => {
  if (getStorageBackend() !== 'minio') {
    res.json({
      ok: true,
      backend: getStorageBackend(),
      buckets: [],
      totalObjects: 0,
      totalSize: 0,
    });
    return;
  }

  try {
    const rawBucket = getMinioBucket();
    const parsedBucket = getParsedBucket();
    const [rawObjects, parsedObjects] = await Promise.all([
      listAllObjects(rawBucket, ''),
      listAllObjects(parsedBucket, ''),
    ]);

    const rawSize = rawObjects.reduce((sum, item) => sum + (item.size || 0), 0);
    const parsedSize = parsedObjects.reduce((sum, item) => sum + (item.size || 0), 0);

    res.json({
      ok: true,
      backend: getStorageBackend(),
      buckets: [
        { name: rawBucket, objectCount: rawObjects.length, totalSize: rawSize },
        { name: parsedBucket, objectCount: parsedObjects.length, totalSize: parsedSize },
      ],
      totalObjects: rawObjects.length + parsedObjects.length,
      totalSize: rawSize + parsedSize,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    res.status(500).json({ ok: false, error: message });
  }
});

// ─── 启动时从 db-server 恢复持久化配置 ────────────────────────
async function loadPersistedConfig() {
  try {
    const res = await fetch(`${DB_BASE_URL}/settings`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      console.warn(`[upload-server] loadPersistedConfig: db-server returned ${res.status}`);
      return;
    }
    const settings = await res.json();
    const saved = settings?.minioConfig;
    if (!saved || typeof saved !== 'object') return;

    minioState = {
      storageBackend:  saved.storageBackend  || minioState.storageBackend,
      endpoint:        saved.endpoint        || minioState.endpoint,
      port:            Number(saved.port     || minioState.port),
      useSSL:          saved.useSSL          ?? minioState.useSSL,
      accessKey:       saved.accessKey       || minioState.accessKey,
      secretKey:       saved.secretKey       || minioState.secretKey,
      bucket:          saved.bucket          || minioState.bucket,
      parsedBucket:    saved.parsedBucket    || minioState.parsedBucket,
      presignedExpiry: Number(saved.presignedExpiry || minioState.presignedExpiry),
    };
    minioClient = createMinioClient(minioState);
    console.log(`[upload-server] Restored config from db-server: endpoint=${minioState.endpoint} bucket=${minioState.bucket} parsedBucket=${minioState.parsedBucket}`);
  } catch (e) {
    console.warn('[upload-server] Could not load persisted config:', e.message);
  }
}

// ─── 辅助：列出桶内指定前缀下的所有对象 ──────────────────────
async function listAllObjects(bucket, prefix) {
  return new Promise((resolve, reject) => {
    const objects = [];
    const stream = getMinioClient().listObjectsV2(bucket, prefix, true);
    stream.on('data', (obj) => { if (obj.name) objects.push(obj); });
    stream.on('end', () => resolve(objects));
    stream.on('error', reject);
  });
}

async function removeAllObjects(bucket, prefix = '') {
  const objects = await listAllObjects(bucket, prefix);
  let removed = 0;

  for (let index = 0; index < objects.length; index += 50) {
    const batch = objects.slice(index, index + 50);
    await Promise.all(batch.map((item) => getMinioClient().removeObject(bucket, item.name)));
    removed += batch.length;
  }

  return removed;
}

// ─── 接口：POST /delete-material ──────────────────────────────
// 清理指定 material 列表在 MinIO 中的所有原始文件和解析产物
// 新格式 Body: { materialIds: number[], materials: { id: number, metadata: { provider?, objectName?, ... } }[] }
// 旧格式 Body: { materialIds: number[] }（向后兼容：降级为全局 storageBackend 判断）
app.post('/delete-material', async (req, res) => {
  const { materialIds, materials } = req.body;
  if (!Array.isArray(materialIds) || materialIds.length === 0) {
    res.status(400).json({ error: '缺少 materialIds 数组' }); return;
  }

  // ── 旧格式兼容：没有传 materials 时，降级为原来的全局 storageBackend 判断 ──
  if (!Array.isArray(materials) || materials.length === 0) {
    if (getStorageBackend() !== 'minio') {
      res.json({ ok: true, skipped: true, reason: 'non-minio backend (legacy mode)' }); return;
    }
    const results = [];
    const errors = [];
    for (const id of materialIds) {
      try {
        const rawBucket = getMinioBucket();
        const parsedBucket = getParsedBucket();
        const [originals, parsed] = await Promise.all([
          listAllObjects(rawBucket, `originals/${id}/`),
          listAllObjects(parsedBucket, `parsed/${id}/`),
        ]);
        await Promise.all([
          ...originals.map((o) => getMinioClient().removeObject(rawBucket, o.name)),
          ...parsed.map((o) => getMinioClient().removeObject(parsedBucket, o.name)),
        ]);
        results.push({ id, originals: originals.length, parsed: parsed.length });
      } catch (err) {
        errors.push({ id, error: err.message });
      }
    }
    res.json({ ok: true, results, errors }); return;
  }

  // ── 新格式：按每条 material 自身的 metadata.provider 决定是否清理 MinIO ──
  const results = [];
  const errors = [];
  for (const m of materials) {
    const id = m.id;
    const provider = m.metadata?.provider;
    try {
      if (provider !== 'minio') {
        // 非 minio 存储的资料，直接跳过，不报错
        results.push({ id, skipped: true, reason: `provider is '${provider ?? 'unknown'}'` });
        continue;
      }
      const rawBucket = getMinioBucket();
      const parsedBucket = getParsedBucket();
      const [originals, parsed] = await Promise.all([
        listAllObjects(rawBucket, `originals/${id}/`),
        listAllObjects(parsedBucket, `parsed/${id}/`),
      ]);
      await Promise.all([
        ...originals.map((o) => getMinioClient().removeObject(rawBucket, o.name)),
        ...parsed.map((o) => getMinioClient().removeObject(parsedBucket, o.name)),
      ]);
      results.push({ id, originals: originals.length, parsed: parsed.length });
    } catch (err) {
      errors.push({ id, error: err.message });
    }
  }
  res.json({ ok: true, results, errors });
});

app.use((err, req, res, _next) => {
  const requestId = req.requestId || '-';
  if (err instanceof multer.MulterError) {
    const message = err.code === 'LIMIT_FILE_SIZE'
      ? '上传文件过大，单文件上限为 200MB'
      : `上传请求无效：${err.message}`;
    console.error(`[upload-server] multer error [${requestId}] ${req.method} ${req.path}:`, message);
    if (!res.headersSent) res.status(400).json({ error: message, requestId });
    return;
  }

  const status = Number(err?.statusCode || err?.status || 500);
  const message = err instanceof Error ? err.message : String(err || '未知错误');
  console.error(`[upload-server] unhandled route error [${requestId}] ${req.method} ${req.path}:`, message);
  if (!res.headersSent) res.status(status).json({ error: message, requestId });
});

const server = app.listen(port, async () => {
  console.log(`[upload-server] listening on http://localhost:${port}`);
  await loadPersistedConfig();
  console.log(`[upload-server] storage backend: ${getStorageBackend()}`);
  if (getStorageBackend() === 'minio') {
    console.log(`[upload-server] MinIO: ${minioState.endpoint}:${minioState.port}`);
  }
});

// ─── 优雅停机 ─────────────────────────────────────────────────

function gracefulShutdown(signal) {
  console.log(`[upload-server] Received ${signal}, shutting down...`);
  server.close(() => {
    console.log(`[upload-server] Server closed after ${signal}.`);
    process.exit(0);
  });
  setTimeout(() => {
    console.error('[upload-server] Forced exit after timeout.');
    process.exit(1);
  }, 10000);
}

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT',  () => gracefulShutdown('SIGINT'));

process.on('uncaughtException', (err) => {
  console.error('[upload-server] Uncaught exception:', err);
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[upload-server] Unhandled rejection:', reason);
  process.exit(1);
});
