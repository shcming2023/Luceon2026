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
 *   POST   /parse/analyze               → 解析已上传文件（MinerU OCR + MD 提取），支持多策略 AI fallback
 *   POST   /parsed-zip                  → 将 parsed/{materialId}/ 目录打包成 ZIP 返回
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
import {
  initBatchQueue, restoreBatchQueue, getQueueStatus,
  addJobs, startQueue, pauseQueue, resumeQueue, stopQueue,
  cancelJob, cancelCurrentJob,
  readAlerts,
  retryFailed, retryJob, removeJob, clearCompleted, clearAll,
  shutdown as shutdownBatchQueue,
} from './batch-queue.mjs';

const app = express();
const port = Number(process.env.UPLOAD_PORT || 8788);

// db-server 地址（用于持久化 MinIO 配置）
const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';

const debugLogs = [];

function pushDebugLog(entry) {
  debugLogs.push(entry);
  if (debugLogs.length > 500) debugLogs.splice(0, debugLogs.length - 500);
}

/**
 * 修复文件名编码（处理 UTF-8 字节被当作 Latin-1 解析的情况）
 *
 * 问题描述：当文件名包含中文字符时，如果 UTF-8 编码的字节被错误地当作 Latin-1 解析，
 * 会出现类似 "2025_2026å­¦å¹´å¯åè¯¾ç¨IGCSE_English__0500__Extract.pdf" 的情况。
 *
 * 例如："学年" 的 UTF-8 编码是 \xE5\xAD\xA6，被当作 Latin-1 解析后变成 "å­¦"
 *
 * @param {string} filename - 可能存在编码问题的文件名
 * @returns {string} 修复后的文件名
 */
function fixFilenameEncoding(filename) {
  if (!filename) return filename;

  // 检测是否包含典型的编码错误字符（连续的 Latin-1 扩展字符）
  const hasMojiChars = /[\u00C0-\u00FF]{3,}/.test(filename);
  if (!hasMojiChars) return filename;

  try {
    // 将 Latin-1 解析的字符串重新编码为 UTF-8
    const latin1Buffer = Buffer.from(filename, 'latin1');
    const utf8String = latin1Buffer.toString('utf8');

    // 验证修复后的字符串是否包含中文字符（确认修复成功）
    if (/[\u4E00-\u9FFF]/.test(utf8String)) {
      console.log(`[upload-server] Fixed filename encoding: "${filename}" → "${utf8String}"`);
      return utf8String;
    }
  } catch (error) {
    console.warn('[upload-server] Failed to fix filename encoding:', error.message);
  }

  return filename;
}

// ─── CORS 配置 ────────────────────────────────────────────────
// 生产部署时请通过 CORS_ORIGIN 环境变量指定允许的来源（如 http://192.168.1.100:8081）
// 多个来源用逗号分隔。未配置时默认仅允许 localhost 开发端口。
const CORS_ORIGIN_RAW = process.env.CORS_ORIGIN || '';
const ALLOWED_CORS_ORIGINS = CORS_ORIGIN_RAW
  ? CORS_ORIGIN_RAW.split(',').map((s) => s.trim()).filter(Boolean)
  : [];

app.use(cors(ALLOWED_CORS_ORIGINS.length > 0 ? {
  origin: (origin, callback) => {
    // 无 Origin 头（服务间调用、curl）直接放行
    if (!origin) return callback(null, true);
    if (ALLOWED_CORS_ORIGINS.includes(origin)) return callback(null, true);
    return callback(new Error(`CORS: origin ${origin} not allowed`));
  },
  credentials: true,
} : undefined)); // CORS_ORIGIN 未配置时保持原有行为（兼容开发环境）

app.use(express.json());
app.use((req, res, next) => {
  const requestId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  req.requestId = requestId;
  res.setHeader('X-Request-Id', requestId);
  next();
});

app.get('/debug/logs', (req, res) => {
  const limit = Math.max(1, Math.min(500, Number(req.query?.limit || 200)));
  const since = Number(req.query?.since || 0);
  const requestId = String(req.query?.requestId || '').trim();

  let logs = debugLogs;
  if (Number.isFinite(since) && since > 0) logs = logs.filter((l) => Number(l?.ts || 0) >= since);
  if (requestId) logs = logs.filter((l) => String(l?.requestId || '') === requestId);

  res.json({ logs: logs.slice(-limit) });
});

// ─── SSRF 防御工具 ────────────────────────────────────────────

/**
 * 检查 URL 是否为私网/回环地址（防止 SSRF 内网横向访问）
 * 拦截：localhost、127.x.x.x、10.x.x.x、172.16-31.x.x、192.168.x.x、::1 等
 */
function isPrivateOrLoopback(hostname) {
  if (!hostname) return true;
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, ''); // 去除 IPv6 括号
  if (h === 'localhost' || h === '::1' || h === '0.0.0.0') return true;
  if (/^127\./.test(h)) return true;
  if (/^10\./.test(h)) return true;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(h)) return true;
  if (/^192\.168\./.test(h)) return true;
  if (/^169\.254\./.test(h)) return true; // link-local
  if (/^fd[0-9a-f]{2}:/i.test(h)) return true; // IPv6 ULA
  return false;
}

/**
 * 校验 ossUrl 是否合法（仅允许 HTTPS 且目标为阿里云 OSS 域名）
 * 阿里云 OSS presigned URL 格式：https://<bucket>.oss-<region>.aliyuncs.com/...
 * MinerU 的 OSS 也在 aliyuncs.com 下。
 *
 * 生产部署如需放行其他 OSS 域名，可通过 ALLOWED_OSS_DOMAINS 环境变量（逗号分隔）追加。
 */
const EXTRA_OSS_DOMAINS = (process.env.ALLOWED_OSS_DOMAINS || '')
  .split(',').map((s) => s.trim()).filter(Boolean);

function validateOssUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, reason: 'URL 格式无效' };
  }
  if (parsed.protocol !== 'https:') {
    return { ok: false, reason: '仅允许 HTTPS 协议' };
  }
  const host = parsed.hostname.toLowerCase();
  if (isPrivateOrLoopback(host)) {
    return { ok: false, reason: '不允许访问私网或回环地址' };
  }
  const ossAllowed = host.endsWith('.aliyuncs.com') || EXTRA_OSS_DOMAINS.some((d) => host.endsWith(d));
  if (!ossAllowed) {
    return { ok: false, reason: `域名 ${host} 不在 OSS 白名单内（允许 *.aliyuncs.com）` };
  }
  return { ok: true };
}

/**
 * 校验 AI API endpoint 是否合法（防止 SSRF 任意外部请求转发）
 *
 * 策略：
 * - 仅允许 http/https 协议
 * - 拒绝私网地址（除非显式配置 ALLOW_LOCAL_AI_ENDPOINT=true，用于 Ollama 本地模式）
 * - 可通过 ALLOWED_AI_HOSTS 环境变量（逗号分隔）限制允许的外部主机
 */
const ALLOW_LOCAL_AI = process.env.ALLOW_LOCAL_AI_ENDPOINT === 'true';
const ALLOWED_AI_HOSTS_RAW = (process.env.ALLOWED_AI_HOSTS || '').split(',').map((s) => s.trim()).filter(Boolean);

function validateAiEndpoint(endpoint) {
  let parsed;
  try {
    parsed = new URL(endpoint);
  } catch {
    return { ok: false, reason: 'AI endpoint URL 格式无效' };
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, reason: '仅允许 http/https 协议' };
  }
  const host = parsed.hostname.toLowerCase();
  if (isPrivateOrLoopback(host) && !ALLOW_LOCAL_AI) {
    return { ok: false, reason: `AI endpoint 不允许访问本地/私网地址（${host}）。如需使用本地 Ollama，请设置 ALLOW_LOCAL_AI_ENDPOINT=true` };
  }
  // 如果配置了主机白名单，必须在白名单内
  if (ALLOWED_AI_HOSTS_RAW.length > 0) {
    const allowed = ALLOWED_AI_HOSTS_RAW.some((h) => host === h || host.endsWith(`.${h}`));
    if (!allowed) {
      return { ok: false, reason: `AI endpoint 主机 ${host} 不在白名单内` };
    }
  }
  return { ok: true };
}

/**
 * 校验任意 fetch URL（通用，用于 zipUrl 等场景）
 * 允许 https，拒绝私网地址。
 */
function validateFetchUrl(url, { allowHttp = false } = {}) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, reason: 'URL 格式无效' };
  }
  if (parsed.protocol === 'file:' || parsed.protocol === 'data:') {
    return { ok: false, reason: `不允许 ${parsed.protocol} 协议` };
  }
  if (!allowHttp && parsed.protocol !== 'https:') {
    return { ok: false, reason: '仅允许 HTTPS 协议' };
  }
  const host = parsed.hostname.toLowerCase();
  if (isPrivateOrLoopback(host)) {
    return { ok: false, reason: '不允许访问私网或回环地址' };
  }
  return { ok: true };
}

// ─── 原型链污染防御（与 db-server 共享同一逻辑）──────────────

const DANGEROUS_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

/**
 * 递归检查对象中是否包含危险键名（原型链污染防御）
 * 比 JSON.stringify().includes() 更精确，避免误拦截合法的 "constructor" 字段值
 */
function hasDangerousKey(value, depth = 0) {
  if (depth > 20) return false; // 防止超深嵌套消耗过多资源
  if (value === null || typeof value !== 'object') return false;
  for (const key of Object.keys(value)) {
    if (DANGEROUS_KEYS.has(key)) return true;
    if (hasDangerousKey(value[key], depth + 1)) return true;
  }
  return false;
}

function rejectProtoPollution(req, res, next) {
  if (req.body && hasDangerousKey(req.body)) {
    res.status(400).json({ error: '请求体包含不允许的属性名' });
    return;
  }
  next();
}

// 对所有写操作应用原型链污染防护
app.use((req, res, next) => {
  if (['POST', 'PUT', 'PATCH'].includes(req.method) && req.body && typeof req.body === 'object') {
    rejectProtoPollution(req, res, next);
  } else {
    next();
  }
});

// ─── MinIO Presigned URL 公开地址重写 ─────────────────────────
// 当配置了 MINIO_PUBLIC_ENDPOINT（如 http://192.168.31.33:8081/minio）时，
// 将 MinIO SDK 生成的内部预签名 URL（http://minio:9000/...）替换为可被
// 局域网浏览器直接访问的公开地址，通过 Nginx /minio/ 反向代理透传。
// 未配置时原样返回（向后兼容本地开发环境）。
const MINIO_PUBLIC_ENDPOINT = (process.env.MINIO_PUBLIC_ENDPOINT || '').replace(/\/$/, '');

/**
 * 将 MinIO 内部预签名 URL 重写为外部可访问的公开地址。
 * @param {string} url - MinIO SDK presignedGetObject 返回的原始 URL
 * @returns {string} 替换主机+端口后的公开 URL，或原始 URL（未配置时）
 */
function rewritePresignedUrl(url) {
  if (!MINIO_PUBLIC_ENDPOINT || !url) return url;
  return url.replace(/^https?:\/\/[^/?#]+/, MINIO_PUBLIC_ENDPOINT);
}

function inferContentTypeByExt(fileName) {
  const ext = String(fileName || '').split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'application/pdf';
  if (ext === 'md' || ext === 'markdown') return 'text/markdown; charset=utf-8';
  if (ext === 'txt') return 'text/plain; charset=utf-8';
  if (ext === 'json') return 'application/json; charset=utf-8';
  if (ext === 'png') return 'image/png';
  if (ext === 'jpg' || ext === 'jpeg') return 'image/jpeg';
  if (ext === 'gif') return 'image/gif';
  if (ext === 'webp') return 'image/webp';
  if (ext === 'svg') return 'image/svg+xml';
  return '';
}

function isGenericContentType(type) {
  const t = String(type || '').trim().toLowerCase();
  if (!t) return true;
  return t.includes('octet-stream');
}

function encodeRFC5987ValueChars(str) {
  return encodeURIComponent(str)
    .replace(/['()]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)
    .replace(/\*/g, '%2A')
    .replace(/%(7C|60|5E)/g, (m) => m.toLowerCase());
}

function buildContentDisposition(disposition, fileName) {
  const raw = String(fileName || 'file');
  const fallback = raw
    .replace(/[\r\n]/g, ' ')
    .replace(/["\\]/g, '_')
    .replace(/[^\x20-\x7E]/g, '_');
  const encoded = encodeRFC5987ValueChars(raw);
  return `${disposition}; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}

function resolveBucketForObject(objectName, bucketParam) {
  const rawBucket = getMinioBucket();
  const parsedBucket = getParsedBucket();

  if (bucketParam && typeof bucketParam === 'string') {
    if (bucketParam === 'raw') return rawBucket;
    if (bucketParam === 'parsed') return parsedBucket;
    if (bucketParam === rawBucket || bucketParam === parsedBucket) return bucketParam;
  }

  if (String(objectName || '').startsWith('parsed/')) return parsedBucket;
  return rawBucket;
}

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

/**
 * 流式上传 multer 磁盘文件到 MinIO，避免将整个文件加载到内存。
 * 用于大文件场景（如 > 50MB 的 PDF），上传完成后自动清理临时文件。
 * @param {object} file - multer file 对象
 * @param {string} bucket - MinIO bucket 名称
 * @param {string} objectName - MinIO 对象路径
 * @param {string} mimeType - MIME 类型
 * @returns {Promise<void>}
 */
async function streamUploadToMinIO(file, bucket, objectName, mimeType) {
  const client = getMinioClient();
  await ensureBucket(client, bucket);
  const fileStream = fs.createReadStream(file.path);
  const fileSize = file.size || fs.statSync(file.path).size;
  await client.putObject(bucket, objectName, fileStream, fileSize, {
    'Content-Type': mimeType || 'application/octet-stream',
  });
  // 上传完成后清理临时文件
  fs.unlink(file.path, () => {});
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
  // 移除危险字符，但保留中文等 UTF-8 字符
  // 使用 NFC（组合形式）而非 NFKD，避免破坏中文字符
  const normalized = (name || 'upload.bin')
    .normalize('NFC')
    .replace(/[<>:"|?*\\\/\x00-\x1F]/g, '_')  // 只移除文件系统不支持的字符
    .replace(/\s+/g, '_')                      // 空格替换为下划线
    .replace(/^_+|_+$/g, '');                  // 去除首尾下划线
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

/**
 * Docker 环境下自动将 localhost/127.0.0.1 替换为 host.docker.internal
 * Docker 容器内 localhost 指向容器自身，而非宿主机。
 * 当用户填写 localhost/127.0.0.1 且运行在 Docker 中时，自动替换为 host.docker.internal
 * 检测方式：/.dockerenv 文件存在，或 DB_BASE_URL 包含 Docker 服务名
 */
const IS_DOCKER = fs.existsSync('/.dockerenv') || (process.env.DB_BASE_URL || '').includes('db-server');

function dockerRewriteEndpoint(endpoint) {
  if (!IS_DOCKER || !endpoint) return endpoint;
  return endpoint
    .replace(/\/\/localhost([:/])/g, '//host.docker.internal$1')
    .replace(/\/\/127\.0\.0\.1([:/])/g, '//host.docker.internal$1');
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
    dockerRewriteEndpoint(String(params?.serverUrl || 'http://localhost:30000')),
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

async function waitMinerUTask(localEndpoint, taskId, timeoutMs, onProgress, signal = null) {
  const isAbortTimeout = (err) => {
    const name = err && typeof err === 'object' && 'name' in err ? String(err.name || '') : '';
    const msg = err instanceof Error ? err.message : String(err || '');
    return name === 'AbortError' || /aborted due to timeout/i.test(msg) || /operation was aborted/i.test(msg);
  };
  const mergeSignals = (a, b) => {
    const list = [a, b].filter(Boolean);
    if (list.length === 0) return null;
    if (list.length === 1) return list[0];
    const anyFn = AbortSignal && typeof AbortSignal.any === 'function' ? AbortSignal.any.bind(AbortSignal) : null;
    if (anyFn) return anyFn(list);
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    for (const s of list) {
      if (s.aborted) { controller.abort(); break; }
      s.addEventListener('abort', onAbort, { once: true });
    }
    return controller.signal;
  };

  const silenceTimeoutMs = 15 * 60 * 1000;
  let lastSuccessfulPollAt = Date.now();
  let lastStatus = '';
  let lastPayload = null;
  while (true) {
    if (signal?.aborted) {
      const err = new Error('已取消');
      err.name = 'AbortError';
      throw err;
    }
    if (Date.now() - lastSuccessfulPollAt > silenceTimeoutMs) {
      const snippet = lastPayload ? JSON.stringify(lastPayload).slice(0, 200) : '';
      throw new Error(`MinerU 状态查询超时（连续 ${Math.round(silenceTimeoutMs / 60000)}min 无成功响应，taskId=${taskId}，lastStatus=${lastStatus || '-'}）${snippet ? `：${snippet}` : ''}`);
    }
    const pollTimeoutMs = Math.min(30_000, timeoutMs);
    let response;
    try {
      response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}`, {
        signal: mergeSignals(signal, AbortSignal.timeout(pollTimeoutMs)),
      });
    } catch (err) {
      if (isAbortTimeout(err)) {
        if (signal?.aborted) throw err;
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }
      throw err;
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof payload?.detail === 'string' ? payload.detail : '';
      throw new Error(`查询任务状态失败: HTTP ${response.status} ${detail}`.trim());
    }

    lastSuccessfulPollAt = Date.now();
    lastPayload = payload;
    const statusValue =
      payload?.status ??
      payload?.state ??
      payload?.task_status ??
      payload?.data?.status ??
      payload?.data?.state;
    const status = String(statusValue || '').toLowerCase();
    lastStatus = status;
    if (typeof onProgress === 'function') {
      onProgress(payload);
    }
    if (status === 'done' || status === 'success' || status === 'completed' || status === 'succeeded' || status === 'finished' || status === 'complete') {
      return payload;
    }
    if (status === 'failed' || status === 'error' || status === 'failure' || status === 'canceled' || status === 'cancelled') {
      throw new Error(String(payload?.error || payload?.message || '任务执行失败'));
    }
    if (!status) {
      const snippet = JSON.stringify(payload).slice(0, 200);
      throw new Error(`任务状态字段缺失（taskId=${taskId}）：${snippet}`);
    }

    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

async function fetchMinerUResult(localEndpoint, taskId, timeoutMs, signal = null) {
  const isAbortTimeout = (err) => {
    const name = err && typeof err === 'object' && 'name' in err ? String(err.name || '') : '';
    const msg = err instanceof Error ? err.message : String(err || '');
    return name === 'AbortError' || /aborted due to timeout/i.test(msg) || /operation was aborted/i.test(msg);
  };
  const mergeSignals = (a, b) => {
    const list = [a, b].filter(Boolean);
    if (list.length === 0) return null;
    if (list.length === 1) return list[0];
    const anyFn = AbortSignal && typeof AbortSignal.any === 'function' ? AbortSignal.any.bind(AbortSignal) : null;
    if (anyFn) return anyFn(list);
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    for (const s of list) {
      if (s.aborted) { controller.abort(); break; }
      s.addEventListener('abort', onAbort, { once: true });
    }
    return controller.signal;
  };

  const resultTimeoutMs = Math.min(300_000, timeoutMs);

  for (let attempt = 1; attempt <= 3; attempt++) {
    let response;
    try {
      response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}/result`, {
        signal: mergeSignals(signal, AbortSignal.timeout(resultTimeoutMs)),
      });
    } catch (err) {
      if (isAbortTimeout(err)) {
        if (attempt < 3) {
          await new Promise((resolve) => setTimeout(resolve, 1000 * attempt));
          continue;
        }
        throw new Error(`获取任务结果超时（${Math.round(resultTimeoutMs / 1000)}s）`);
      }
      throw err;
    }

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

  throw new Error(`获取任务结果超时（${Math.round(resultTimeoutMs / 1000)}s）`);
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

      const presignedUrl = rewritePresignedUrl(await client.presignedGetObject(
        bucket,
        objectName,
        getPresignedExpiry(),
      ));

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

    // 先修复编码（处理中文文件名被 Latin-1 误解析的问题），再 normalize
    const fixedOriginalName = fixFilenameEncoding(req.file.originalname);
    const safeFileName = normalizeFileName(fixedOriginalName);

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

    // 设置 UTF-8 响应头，fileName 使用已修复编码的文件名
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.json({
      url: result.url,
      objectName: result.objectName,
      fileName: fixedOriginalName,
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
app.get('/presign', async (req, res) => {
  const { objectName, bucket: bucketParam } = req.query;
  if (!objectName || typeof objectName !== 'string') {
    res.status(400).json({ error: '缺少 objectName 参数' });
    return;
  }

  const bucket = resolveBucketForObject(objectName, bucketParam);
  const proxyUrl = `/__proxy/upload/proxy-file?objectName=${encodeURIComponent(objectName)}&bucket=${encodeURIComponent(bucket)}`;

  try {
    let presignedUrl = '';
    try {
      presignedUrl = rewritePresignedUrl(await getMinioClient().presignedGetObject(bucket, objectName, getPresignedExpiry()));
    } catch {
      presignedUrl = '';
    }

    res.json({
      url: proxyUrl,
      proxyUrl,
      presignedUrl: presignedUrl || undefined,
      objectName,
      bucket,
      expiresIn: getPresignedExpiry(),
    });
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
    cleanupTempFile(req.file);
    return;
  }

  // ── SSRF 校验：ossUrl 必须是阿里云 OSS HTTPS 地址 ─────────
  const ossCheck = validateOssUrl(ossUrl);
  if (!ossCheck.ok) {
    cleanupTempFile(req.file);
    console.warn(`[upload-server] /parse/oss-put rejected ossUrl: ${ossCheck.reason}`);
    res.status(400).json({ error: `ossUrl 校验失败: ${ossCheck.reason}` });
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

  // ── 校验 zipUrl（防止 SSRF）──────────────────────────────────
  const zipCheck = validateFetchUrl(zipUrl, { allowHttp: false });
  if (!zipCheck.ok) {
    console.warn(`[upload-server] /parse/download rejected zipUrl: ${zipCheck.reason}`);
    res.status(400).json({ error: `zipUrl 校验失败: ${zipCheck.reason}` });
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
          const presignedUrl = rewritePresignedUrl(await client.presignedGetObject(bucket, objectName, getPresignedExpiry()));
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
  const localEndpoint = dockerRewriteEndpoint(normalizeEndpoint(req.body?.localEndpoint));
  if (!localEndpoint) {
    res.status(400).json({ ok: false, message: '缺少 localEndpoint' });
    return;
  }

  const candidates = [
    `${localEndpoint}/health`,
    localEndpoint,
    `${localEndpoint}/gradio_api/info`,
  ];

  pushDebugLog({
    ts: Date.now(),
    level: 'info',
    requestId: req.requestId,
    route: '/parse/local-mineru/health',
    message: `health check start: ${localEndpoint}`,
  });

  let lastMessage = '连接失败';
  for (const target of candidates) {
    try {
      const response = await fetch(target, { signal: AbortSignal.timeout(5000) });
      if (response.status === 200) {
        pushDebugLog({
          ts: Date.now(),
          level: 'success',
          requestId: req.requestId,
          route: '/parse/local-mineru/health',
          message: `health ok: ${target}`,
        });
        res.json({ ok: true, message: `本地 MinerU 在线：${target}`, requestId: req.requestId });
        return;
      }
      lastMessage = `${target} 返回 HTTP ${response.status}`;
      pushDebugLog({
        ts: Date.now(),
        level: 'error',
        requestId: req.requestId,
        route: '/parse/local-mineru/health',
        message: lastMessage,
      });
    } catch (error) {
      lastMessage = error instanceof Error ? error.message : String(error);
      pushDebugLog({
        ts: Date.now(),
        level: 'error',
        requestId: req.requestId,
        route: '/parse/local-mineru/health',
        message: `health error: ${lastMessage}`,
      });
    }
  }

  res.json({ ok: false, message: `本地 MinerU 不可达：${lastMessage}`, requestId: req.requestId });
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

    // ── 前置校验：manifest 完整性 ──────────────────────────────
    if (typeof manifest.version !== 'string' || !manifest.version) {
      res.status(400).json({ error: '备份包 manifest.json 缺少 version 字段' });
      return;
    }
    if (typeof manifest.materialsCount !== 'number' || manifest.materialsCount < 0) {
      res.status(400).json({ error: '备份包 manifest.json 的 materialsCount 字段无效' });
      return;
    }
    if (typeof manifest.dbFile !== 'string' || !manifest.dbFile) {
      res.status(400).json({ error: '备份包 manifest.json 缺少 dbFile 字段' });
      return;
    }

    // ── 前置校验：db 数据结构合法性 ───────────────────────────
    if (!dbData || typeof dbData !== 'object' || Array.isArray(dbData)) {
      res.status(400).json({ error: '备份包 db-data.json 不是合法的 JSON 对象' });
      return;
    }
    if (!('materials' in dbData) || typeof dbData.materials !== 'object' || Array.isArray(dbData.materials)) {
      res.status(400).json({ error: '备份包 db-data.json 缺少合法的 materials 字段' });
      return;
    }

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
  const localEndpoint = dockerRewriteEndpoint(normalizeEndpoint(req.body?.localEndpoint));
  const materialId = String(req.body?.materialId || '').trim();
  const localTimeout = Number(req.body?.localTimeout || 3600);
  const startedAt = Date.now();
  const wantsSse = String(req.headers?.accept || '').includes('text/event-stream');

  if (!req.file) {
    res.status(400).json({ error: '缺少文件字段 `file`' });
    return;
  }
  if (!localEndpoint) {
    res.status(400).json({ error: '缺少 localEndpoint' });
    return;
  }

  try {
    if (wantsSse) {
      res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');
      res.flushHeaders?.();
    }

    const sendEvent = (type, data) => {
      if (!wantsSse || res.writableEnded || res.destroyed) return;
      res.write(`event: ${type}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const backend = String(req.body?.backend || 'hybrid-auto-engine');
    const maxPages = Number(req.body?.maxPages || 1000);
    const ocrLanguage = String(req.body?.ocrLanguage || req.body?.language || 'ch');
    const enableOcr = isEnabledFlag(req.body?.enableOcr);
    const enableFormula = isEnabledFlag(req.body?.enableFormula);
    const enableTable = isEnabledFlag(req.body?.enableTable);
    const timeoutMs = Math.max(localTimeout * 1000, 30_000);
    const parseMethod = String(req.body?.parseMethod || req.body?.parse_method || '').trim();
    const rawServerUrl = String(req.body?.serverUrl || req.body?.server_url || req.body?.url || '').trim();
    const serverUrl = dockerRewriteEndpoint(rawServerUrl || (/vlm|hybrid/i.test(backend) ? 'http://localhost:30000' : ''));

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
    sendEvent('progress', { pct: 5, msg: '已连接本地 MinerU，开始提交任务...' });

    let markdown = '';
    let mineruTaskId = '';

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
    let finalParseMethod = parseMethod || 'auto';
    if (enableOcr && !parseMethod) {
      finalParseMethod = 'ocr';
    }
    fastApiForm.append('parse_method', finalParseMethod);
    fastApiForm.append('formula_enable', String(enableFormula));
    fastApiForm.append('table_enable', String(enableTable));
    if (serverUrl) fastApiForm.append('server_url', serverUrl);
    fastApiForm.append('return_md', 'true');
    fastApiForm.append('response_format_zip', 'false');
    if (Number.isFinite(maxPages) && maxPages > 0) {
      fastApiForm.append('end_page_id', String(Math.max(0, Math.floor(maxPages) - 1)));
    }

    const fastApiResponse = await fetch(`${localEndpoint}/tasks`, {
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
        const error = String(payload?.error || payload?.message || payload?.detail || '');
        const message = /hub|snapshot folder|huggingface/i.test(error)
          ? `本地 MinerU 任务提交失败（请检查模型）: ${error}`
          : `本地 MinerU 任务提交失败: ${error}`;
        throw new Error(message.trim());
      }

      mineruTaskId = String(payload?.task_id || '').trim();
      if (!mineruTaskId) {
        throw new Error(`本地 MinerU 未返回 task_id，响应内容: ${JSON.stringify(payload).slice(0, 300)}`);
      }

      console.log(`[upload-server] /parse/local-mineru task submitted taskId=${mineruTaskId} parseMethod=${finalParseMethod}`);
      console.log(`[upload-server] /parse/local-mineru start polling taskId=${mineruTaskId}`);

      sendEvent('progress', { pct: 20, msg: `任务已提交，分配ID: ${mineruTaskId}` });
      await waitMinerUTask(localEndpoint, mineruTaskId, timeoutMs, (statusPayload) => {
        const status = String(statusPayload?.status || '').toLowerCase();
        const queued = statusPayload?.queued_ahead || statusPayload?.queue_ahead || 0;
        let msg = `处理中 (${status})...`;
        if (status === 'pending' || status === 'queued') {
          msg = `排队中 (前方还有 ${queued} 个任务等待)`;
        } else if (status === 'processing') {
          msg = 'MinerU 正在执行 OCR 与解析...';
        }
        sendEvent('progress', { pct: 50, msg });
      });

      sendEvent('progress', { pct: 80, msg: '解析完成，正在提取结果...' });
      const resultPayload = await fetchMinerUResult(localEndpoint, mineruTaskId, timeoutMs);
      markdown = extractLocalMarkdown(resultPayload);
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
      sendEvent('progress', { pct: 50, msg: '使用降级 Gradio 引擎解析中...' });

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
      markdownUrl = rewritePresignedUrl(await client.presignedGetObject(bucket, objectName, getPresignedExpiry()));
    } catch (storageError) {
      console.warn('[upload-server] local MinerU markdown store failed:', storageError.message);
    }

    sendEvent('progress', { pct: 90, msg: '结果已安全存入资源库' });

    const result = {
      taskId: mineruTaskId || taskId,
      state: 'done',
      markdown,
      markdownObjectName: markdownObjectName || undefined,
      markdownUrl: markdownUrl || undefined,
      parsedFilesCount: markdownObjectName ? 1 : 0,
    };

    if (wantsSse) {
      sendEvent('complete', result);
      res.end();
      return;
    }

    res.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/local-mineru failed:', message, `elapsedMs=${Date.now() - startedAt}`);
    if (wantsSse) {
      res.write(`event: error\ndata: ${JSON.stringify({ error: message })}\n\n`);
      res.end();
      return;
    }
    res.status(500).json({ error: message });
  }
});

// ─── 接口：POST /parse/analyze ────────────────────────────────
// 读取 MinIO 中的 full.md，调用大模型 API，提取结构化元数据
// Body: { markdownObjectName?, markdownUrl?, markdownContent?, materialId,
//         aiProviders?: AiProvider[],              ← 新格式（多提供商）
//         aiApiEndpoint?, aiApiKey?, aiModel?,     ← 旧格式（向后兼容）
//         prompts?, maxRetries?, retryDelay? }
// 响应: { title, subject, grade, materialType, language, country, tags, summary, confidence, _meta }

/**
 * 调用单个 AI 提供商
 * @param {object} provider - 提供商配置 { id, name, apiEndpoint, apiKey, model, timeout }
 * @param {string} systemPrompt
 * @param {string} userPrompt
 * @param {{ enableThinking?: boolean }} opts - 可选参数
 * @returns {Promise<object>} 解析后的 JSON 结果
 */
async function callAiProvider(provider, systemPrompt, userPrompt, opts = {}) {
  const enableThinking = opts.enableThinking ?? false;
  const externalSignal = opts.signal || null;
  const trimmedKey = (provider.apiKey || '').trim();
  let trimmedEndpoint = (provider.apiEndpoint || '').trim();
  const trimmedModel = (provider.model || '').trim();

  if (!trimmedEndpoint) {
    throw new Error('AI endpoint 未配置');
  }

  // Docker 环境自动转换：localhost/127.0.0.1 → host.docker.internal
  trimmedEndpoint = dockerRewriteEndpoint(trimmedEndpoint);

  // ── SSRF 校验：aiEndpoint 必须通过安全检查 ────────────────
  const endpointCheck = validateAiEndpoint(trimmedEndpoint);
  if (!endpointCheck.ok) {
    const err = new Error(`AI endpoint 校验失败: ${endpointCheck.reason}`);
    err.httpStatus = 400;
    throw err;
  }

  const aiFullUrl = /\/chat\/completions\/?$/.test(trimmedEndpoint)
    ? trimmedEndpoint
    : `${trimmedEndpoint.replace(/\/$/, '')}/chat/completions`;

  const timeoutMs = (provider.timeout || 120) * 1000;
  const aiController = new AbortController();
  const aiTimer = setTimeout(() => aiController.abort(), timeoutMs);

  let aiResp;
  try {
    const onAbort = () => {
      try { aiController.abort(); } catch {}
    };
    if (externalSignal) {
      if (externalSignal.aborted) onAbort();
      else externalSignal.addEventListener('abort', onAbort, { once: true });
    }

    aiResp = await fetch(aiFullUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Ollama 本地无需 Authorization，apiKey 为空时跳过
        ...(trimmedKey ? { Authorization: `Bearer ${trimmedKey}` } : {}),
      },
      body: JSON.stringify({
        model: trimmedModel,
        messages: [
          {
            role: 'system',
            // enableThinking=false 时添加 /no_think 指令，禁用 Qwen3 的 thinking mode
            // enableThinking=true 时保持原始 prompt，允许模型深度思考
            content: enableThinking ? systemPrompt : (systemPrompt + '\n/no_think'),
          },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.1,
        // thinking mode 开启时需要更多 token（思考过程 + 实际回答）
        max_tokens: enableThinking ? 4096 : 2048,
        // Ollama 特有参数：控制 thinking mode（对非 Ollama 服务无影响）
        think: enableThinking,
        // 显式禁用流式响应，防止 Ollama 返回 SSE 流导致 .json() 解析失败
        stream: false,
      }),
      signal: aiController.signal,
    });
  } finally {
    clearTimeout(aiTimer);
  }

  if (!aiResp.ok) {
    const errText = await aiResp.text().catch(() => '');
    const err = new Error(`HTTP ${aiResp.status}: ${errText.slice(0, 200)}`);
    err.httpStatus = aiResp.status;
    err.responseBody = errText;
    throw err;
  }

  const aiJson = await aiResp.json();
  // Ollama Qwen3 可能将思考内容放在 content 中，实际回答可能在同一字段或单独字段
  const message = aiJson.choices?.[0]?.message ?? {};
  const rawContent = (message.content ?? '') || (message.reasoning_content ?? '') || (message.reasoning ?? '');
  if (!message.content && (message.reasoning_content || message.reasoning)) {
    console.warn('[upload-server] AI response content empty; using reasoning_content/reasoning as fallback');
  }

  // ── 健壮的 JSON 提取：兼容 Qwen3 thinking mode、markdown 代码块等 ──
  const extracted = extractJsonFromAiResponse(rawContent);
  if (!extracted || typeof extracted !== 'object') {
    throw new Error(`AI 返回格式异常，无法解析为 JSON。原始响应：${rawContent.slice(0, 300)}`);
  }
  return extracted;
}

/**
 * 从 AI 响应文本中健壮地提取 JSON 对象。
 * 兼容以下情况：
 *   1. 纯 JSON 字符串
 *   2. Markdown 代码块包裹的 JSON（```json ... ```）
 *   3. Qwen3 thinking mode（<think>...</think> 前缀）
 *   4. 混合了思考文本和 JSON 的响应
 *   5. 多余的前后文字说明
 */
function extractJsonFromAiResponse(raw) {
  if (!raw || typeof raw !== 'string') return null;

  // Step 1: 去除 <think>...</think> 标签（Qwen3 thinking mode）
  // 支持多个 think 块和嵌套换行
  let cleaned = raw.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();

  // Step 2: 如果清理后为空，尝试用原始内容
  if (!cleaned) cleaned = raw.trim();

  // Step 3: 尝试直接解析（最理想情况）
  try {
    const direct = JSON.parse(cleaned);
    if (typeof direct === 'object' && direct !== null) return direct;
  } catch { /* continue */ }

  // Step 4: 去除 markdown 代码块标记
  const codeBlockMatch = cleaned.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
  if (codeBlockMatch) {
    try {
      const fromBlock = JSON.parse(codeBlockMatch[1].trim());
      if (typeof fromBlock === 'object' && fromBlock !== null) return fromBlock;
    } catch { /* continue */ }
  }

  // Step 5: 查找第一个 { 和最后一个 } 之间的内容（贪心匹配）
  const firstBrace = cleaned.indexOf('{');
  const lastBrace = cleaned.lastIndexOf('}');
  if (firstBrace !== -1 && lastBrace > firstBrace) {
    const candidate = cleaned.slice(firstBrace, lastBrace + 1);
    try {
      const fromBrace = JSON.parse(candidate);
      if (typeof fromBrace === 'object' && fromBrace !== null) return fromBrace;
    } catch { /* continue */ }
  }

  // Step 6: 最后尝试在原始文本中查找（可能 think 标签去除不完整）
  const firstBraceRaw = raw.indexOf('{');
  const lastBraceRaw = raw.lastIndexOf('}');
  if (firstBraceRaw !== -1 && lastBraceRaw > firstBraceRaw) {
    const candidateRaw = raw.slice(firstBraceRaw, lastBraceRaw + 1);
    try {
      const fromRaw = JSON.parse(candidateRaw);
      if (typeof fromRaw === 'object' && fromRaw !== null) return fromRaw;
    } catch { /* continue */ }
  }

  return null;
}

/**
 * 按优先级顺序尝试多个 AI 提供商，第一个成功立即返回
 * @param {object[]} providers - 已过滤且按 priority 排序的提供商列表
 * @param {string} systemPrompt
 * @param {string} userPrompt
 * @param {{ maxRetries?: number, retryDelay?: number, enableThinking?: boolean }} opts
 * @returns {Promise<{ result: object, providerId: string, providerName: string }>}
 */
async function analyzeWithFallback(providers, systemPrompt, userPrompt, opts = {}) {
  const maxRetries = opts.maxRetries ?? 2;
  const retryDelay = opts.retryDelay ?? 1000;
  const enableThinking = opts.enableThinking ?? false;
  const signal = opts.signal || null;
  const errors = [];

  for (const provider of providers) {
    console.log(`[upload-server] Trying AI provider: ${provider.name} (${provider.apiEndpoint}, model=${provider.model})`);

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const result = await callAiProvider(provider, systemPrompt, userPrompt, { enableThinking, signal });
        console.log(`[upload-server] AI provider ${provider.name} succeeded on attempt ${attempt}`);
        return { result, providerId: provider.id, providerName: provider.name };
      } catch (err) {
        const httpStatus = err.httpStatus;
        const errMsg = err.message || String(err);
        console.warn(`[upload-server] AI provider ${provider.name} attempt ${attempt} failed (HTTP ${httpStatus ?? 'network'}):`, errMsg.slice(0, 200));

        // 4xx 错误（限流/认证）不重试，直接跳下一个提供商
        const isNonRetryable = httpStatus && httpStatus >= 400 && httpStatus < 500;
        if (isNonRetryable) {
          errors.push({ providerId: provider.id, providerName: provider.name, error: errMsg, httpStatus });
          break;
        }

        // 网络/超时可重试
        if (attempt < maxRetries) {
          await new Promise((resolve) => setTimeout(resolve, retryDelay * attempt));
        } else {
          errors.push({ providerId: provider.id, providerName: provider.name, error: errMsg });
        }
      }
    }
  }

  // 全部失败，聚合错误信息
  const summary = errors.map((e) => `[${e.providerName}${e.httpStatus ? ` HTTP ${e.httpStatus}` : ''}] ${e.error}`).join('\n');
  throw new Error(`所有 AI 提供商均失败：\n${summary}`);
}

function buildMarkdownContext(markdownText, maxChars) {
  const max = Number.isFinite(Number(maxChars)) ? Number(maxChars) : 100000;
  const limit = Math.max(10000, Math.min(200000, Math.floor(max)));
  const text = String(markdownText || '');

  if (text.length <= limit) return { context: text, totalChars: text.length, truncated: false };

  const marker = '\n\n...[内容已截断]...\n\n';
  const head = Math.floor(limit * 0.6);
  const tail = Math.max(0, limit - head - marker.length);
  const context = `${text.slice(0, head)}${marker}${text.slice(text.length - tail)}`;
  return { context, totalChars: text.length, truncated: true };
}

app.post('/ai/test', async (req, res) => {
  const provider = req.body?.provider && typeof req.body.provider === 'object'
    ? req.body.provider
    : req.body;

  const apiEndpoint = String(provider?.apiEndpoint || '').trim();
  const model = String(provider?.model || '').trim();
  const name = String(provider?.name || provider?.id || 'AI');
  const mode = String(req.body?.mode || provider?.mode || 'json').trim();

  if (!apiEndpoint || !model) {
    res.status(400).json({ ok: false, message: '缺少 apiEndpoint 或 model', requestId: req.requestId });
    return;
  }

  const rawTimeoutSec = Number(provider?.timeout ?? 120);
  const timeoutSec = Math.max(3, Math.min(600, Number.isFinite(rawTimeoutSec) ? rawTimeoutSec : 120));

  const rewrittenEndpoint = dockerRewriteEndpoint(apiEndpoint);
  const aiFullUrl = /\/chat\/completions\/?$/.test(rewrittenEndpoint)
    ? rewrittenEndpoint
    : `${rewrittenEndpoint.replace(/\/$/, '')}/chat/completions`;

  pushDebugLog({
    ts: Date.now(),
    level: 'info',
    requestId: req.requestId,
    route: '/ai/test',
    message: `ai test start: ${name} mode=${mode} model=${model} timeoutSec=${timeoutSec} url=${aiFullUrl}`,
  });

  const startedAt = Date.now();
  try {
    if (mode === 'connectivity') {
      const trimmedKey = String(provider?.apiKey || '').trim();
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutSec * 1000);
      let resp;
      let respText = '';
      try {
        resp = await fetch(aiFullUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(trimmedKey ? { Authorization: `Bearer ${trimmedKey}` } : {}),
          },
          body: JSON.stringify({
            model,
            messages: [{ role: 'user', content: 'ping' }],
            temperature: 0,
            max_tokens: 16,
            stream: false,
          }),
          signal: controller.signal,
        });
        respText = await resp.text().catch(() => '');
      } finally {
        clearTimeout(timer);
      }

      const elapsedMs = Date.now() - startedAt;
      const ok = !!resp?.ok;
      pushDebugLog({
        ts: Date.now(),
        level: ok ? 'success' : 'error',
        requestId: req.requestId,
        route: '/ai/test',
        message: `${ok ? 'ai connectivity ok' : 'ai connectivity failed'} http=${resp?.status ?? 'network'} elapsedMs=${elapsedMs}`,
      });

      res.status(200).json({
        ok,
        message: ok ? `连通性正常：${name}` : `连通性异常：HTTP ${resp?.status ?? 'network'}`,
        elapsedMs,
        timeoutSec,
        requestId: req.requestId,
        url: aiFullUrl,
        httpStatus: resp?.status ?? null,
        contentType: resp?.headers?.get?.('content-type') || '',
        bodySnippet: respText.slice(0, 300),
      });
      return;
    }

    const systemPrompt = '你是一个测试助手，只需要输出 JSON。';
    const userPrompt = '请仅返回：{\"ok\":true}';
    const result = await callAiProvider(
      {
        id: String(provider?.id || 'test'),
        name,
        enabled: true,
        apiEndpoint,
        apiKey: String(provider?.apiKey || ''),
        model,
        timeout: timeoutSec,
        priority: 1,
      },
      systemPrompt,
      userPrompt,
      // 测试时始终禁用 thinking mode，确保快速响应
      { enableThinking: false },
    );
    const ok = result && typeof result === 'object' && result.ok === true;
    const elapsedMs = Date.now() - startedAt;
    pushDebugLog({
      ts: Date.now(),
      level: ok ? 'success' : 'error',
      requestId: req.requestId,
      route: '/ai/test',
      message: `${ok ? 'ai test ok' : 'ai test responded but not ok'} elapsedMs=${elapsedMs}`,
    });
    res.json({
      ok,
      message: ok ? `连接成功：${name}（${model}）` : `连接成功但返回不符合预期：${name}（${model}）`,
      elapsedMs,
      timeoutSec,
      requestId: req.requestId,
      url: aiFullUrl,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const elapsedMs = Date.now() - startedAt;
    pushDebugLog({
      ts: Date.now(),
      level: 'error',
      requestId: req.requestId,
      route: '/ai/test',
      message: `ai test failed: ${message} elapsedMs=${elapsedMs}`,
    });
    res.status(200).json({ ok: false, message, elapsedMs, timeoutSec, requestId: req.requestId, url: aiFullUrl });
  }
});

app.post('/parse/analyze', async (req, res) => {
  const {
    markdownObjectName,
    markdownUrl,
    markdownContent,
    materialId,
    maxMarkdownChars,
    // 新格式
    aiProviders,
    // 旧格式（向后兼容）
    aiApiEndpoint,
    aiApiKey,
    aiModel,
    prompts,
    maxRetries,
    retryDelay,
    enableThinking,
  } = req.body;

  if (!materialId) {
    res.status(400).json({ error: '缺少 materialId' });
    return;
  }

  // ── 确定提供商列表 ─────────────────────────────────────────
  let providers;
  if (Array.isArray(aiProviders) && aiProviders.length > 0) {
    // 新格式：过滤启用并按 priority 排序
    providers = aiProviders
      .filter((p) => p.enabled !== false)
      .sort((a, b) => (a.priority ?? 999) - (b.priority ?? 999));
  } else {
    // 旧格式兼容
    const trimmedEndpoint = (aiApiEndpoint || '').trim();
    const trimmedModel = (aiModel || '').trim();
    if (!trimmedEndpoint || !trimmedModel) {
      res.status(400).json({ error: '缺少 AI API 配置（aiProviders 或 aiApiEndpoint / aiModel）' });
      return;
    }
    providers = [{
      id: 'legacy',
      name: 'API',
      enabled: true,
      apiEndpoint: trimmedEndpoint,
      apiKey: (aiApiKey || '').trim(),
      model: trimmedModel,
      timeout: 120,
      priority: 1,
    }];
  }

  if (providers.length === 0) {
    res.status(400).json({ error: '未启用任何 AI 提供商' });
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

    const { context: mdContext, totalChars, truncated } = buildMarkdownContext(markdownText, maxMarkdownChars);

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

    console.log(`[upload-server] AI markdown context: total=${totalChars} chars, used=${mdContext.length} chars, truncated=${truncated}`);

    // ── 3. 多策略调用 AI ───────────────────────────────────────
    const { result: extracted, providerId, providerName } = await analyzeWithFallback(
      providers,
      systemPrompt,
      userPrompt,
      { maxRetries: maxRetries ?? 2, retryDelay: retryDelay ?? 1000, enableThinking: enableThinking === true },
    );

    // ── 4. 组装响应 ────────────────────────────────────────────
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
      _meta: { providerId, providerName },
    };

    console.log(`[upload-server] AI analysis done via ${providerName} for material ${materialId}:`, {
      subject: result.subject, grade: result.grade, language: result.language, confidence: result.confidence,
    });

    res.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/analyze failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：POST /parsed-zip ────────────────────────────────────
// 将指定 materialId 的 MinerU 解析产物（parsed/{materialId}/）打包成 ZIP 返回
// Body: { materialId: string | number }
// Response: application/zip 文件流
app.post('/parsed-zip', async (req, res) => {
  const { materialId } = req.body;
  if (!materialId) {
    res.status(400).json({ error: '缺少 materialId' });
    return;
  }

  try {
    const parsedBucket = getParsedBucket();
    const prefix = `parsed/${materialId}/`;

    const objects = await listAllObjects(parsedBucket, prefix);
    if (!objects || objects.length === 0) {
      res.status(400).json({ error: `parsed/${materialId}/ 目录下暂无文件` });
      return;
    }

    console.log(`[upload-server] /parsed-zip: packing ${objects.length} files for material ${materialId}`);

    const zip = new JSZip();

    // 并发读取，每批最多 10 个
    const BATCH_SIZE = 10;
    for (let i = 0; i < objects.length; i += BATCH_SIZE) {
      const batch = objects.slice(i, i + BATCH_SIZE);
      await Promise.all(batch.map(async (obj) => {
        const buffer = await getObjectBuffer(parsedBucket, obj.name);
        // 去掉 parsed/{materialId}/ 前缀，保留相对路径
        const relativePath = obj.name.startsWith(prefix) ? obj.name.slice(prefix.length) : obj.name;
        zip.file(relativePath, buffer);
      }));
    }

    const zipBuffer = await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });

    res.setHeader('Content-Type', 'application/zip');
    res.setHeader('Content-Disposition', `attachment; filename="parsed-${materialId}.zip"`);
    res.setHeader('Content-Length', String(zipBuffer.length));
    res.send(zipBuffer);
    console.log(`[upload-server] /parsed-zip: sent ${(zipBuffer.length / 1024).toFixed(1)} KB for material ${materialId}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parsed-zip failed:', message);
    if (!res.headersSent) res.status(500).json({ error: message });
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

  const bucket = resolveBucketForObject(objectName, bucketParam);

  try {
    console.log(`[upload-server] proxy-file: ${bucket}/${objectName}`);
    const client = getMinioClient();

    // 获取对象 stat 以设置 Content-Type / Content-Length，并用于 Range 支持
    let stat;
    try {
      stat = await client.statObject(bucket, objectName);
    } catch {
      stat = null;
    }

    const fileName = objectName.split('/').pop() || 'file';
    const ext = fileName.split('.').pop()?.toLowerCase() || '';
    const meta = stat?.metaData || {};
    const metaContentType = meta['content-type'] || meta['Content-Type'] || meta['Content-type'] || '';
    const inferredContentType = inferContentTypeByExt(fileName);
    const contentType = inferredContentType && isGenericContentType(metaContentType)
      ? inferredContentType
      : (metaContentType || inferredContentType || 'application/octet-stream');
    res.setHeader('Content-Type', contentType);
    res.setHeader('Cache-Control', 'private, max-age=300');

    const inlineTypes = new Set(['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'txt', 'md', 'json']);
    const disposition = inlineTypes.has(ext) ? 'inline' : 'attachment';
    res.setHeader('Content-Disposition', buildContentDisposition(disposition, fileName));

    const size = typeof stat?.size === 'number' ? stat.size : null;
    if (size != null) res.setHeader('Accept-Ranges', 'bytes');

    const rangeHeader = req.headers.range;
    const range = typeof rangeHeader === 'string' ? rangeHeader : '';

    if (size != null && range.startsWith('bytes=')) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!match) {
        res.status(416);
        res.setHeader('Content-Range', `bytes */${size}`);
        res.end();
        return;
      }

      const startRaw = match[1];
      const endRaw = match[2];
      const start = startRaw === '' ? 0 : Number(startRaw);
      const end = endRaw === '' ? (size - 1) : Number(endRaw);

      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < start || start >= size) {
        res.status(416);
        res.setHeader('Content-Range', `bytes */${size}`);
        res.end();
        return;
      }

      const clampedEnd = Math.min(end, size - 1);
      const length = clampedEnd - start + 1;

      res.status(206);
      res.setHeader('Content-Range', `bytes ${start}-${clampedEnd}/${size}`);
      res.setHeader('Content-Length', String(length));

      const stream = await client.getPartialObject(bucket, objectName, start, length);
      stream.on('error', (err) => {
        console.error('[upload-server] proxy-file stream error:', err.message);
        if (!res.headersSent) res.status(500).json({ error: err.message });
      });
      stream.pipe(res);
      return;
    }

    if (size != null) res.setHeader('Content-Length', String(size));

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

app.post('/settings/ai-models', async (req, res) => {
  try {
    const endpoint = normalizeEndpoint(req.body?.endpoint);
    if (!endpoint) {
      res.status(400).json({ success: false, error: '未提供 API 地址' });
      return;
    }

    const rewrittenEndpoint = dockerRewriteEndpoint(endpoint);
    const endpointCheck = validateAiEndpoint(rewrittenEndpoint);
    if (!endpointCheck.ok) {
      res.status(400).json({ success: false, error: `API 地址校验失败: ${endpointCheck.reason}` });
      return;
    }

    let parsed;
    try {
      parsed = new URL(rewrittenEndpoint);
    } catch {
      res.status(400).json({ success: false, error: 'API 地址格式无效' });
      return;
    }

    const tagsUrl = `${parsed.protocol}//${parsed.host}/api/tags`;
    console.log(`[upload-server] Fetching Ollama models: ${tagsUrl}`);
    pushDebugLog({
      ts: Date.now(),
      level: 'info',
      requestId: req.requestId,
      route: '/settings/ai-models',
      message: `models fetch start: ${tagsUrl}`,
    });

    const response = await fetch(tagsUrl, { signal: AbortSignal.timeout(3000) });
    const rawText = await response.text();
    if (!response.ok) {
      pushDebugLog({
        ts: Date.now(),
        level: 'error',
        requestId: req.requestId,
        route: '/settings/ai-models',
        message: `models fetch http ${response.status}`,
      });
      res.json({
        success: false,
        error: `AI 服务返回 HTTP ${response.status}`,
        details: rawText.slice(0, 300),
        requestId: req.requestId,
      });
      return;
    }

    let data;
    try {
      data = JSON.parse(rawText);
    } catch {
      pushDebugLog({
        ts: Date.now(),
        level: 'error',
        requestId: req.requestId,
        route: '/settings/ai-models',
        message: 'models fetch non-json response',
      });
      res.json({ success: false, error: 'AI 服务返回非 JSON 格式', requestId: req.requestId });
      return;
    }

    if (data && data.models && Array.isArray(data.models)) {
      const modelNames = data.models
        .map((m) => m?.name)
        .filter((name) => typeof name === 'string' && name.trim() !== '');
      pushDebugLog({
        ts: Date.now(),
        level: 'success',
        requestId: req.requestId,
        route: '/settings/ai-models',
        message: `models fetch ok: ${modelNames.length} models`,
      });
      res.json({ success: true, models: modelNames, requestId: req.requestId });
      return;
    }

    res.json({ success: false, error: '返回数据格式非 Ollama 标准格式', requestId: req.requestId });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /settings/ai-models failed:', message);
    pushDebugLog({
      ts: Date.now(),
      level: 'error',
      requestId: req.requestId,
      route: '/settings/ai-models',
      message: `models fetch failed: ${message}`,
    });
    res.status(200).json({ success: false, error: `无法连接到 AI 服务: ${message}`, requestId: req.requestId });
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
          presignedUrl = rewritePresignedUrl(await client.presignedGetObject(bucket, obj.name, getPresignedExpiry()));
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

// ─── 审计：孤儿对象检测与清理 ─────────────────────────────────

/**
 * 从 MinIO 对象路径中提取 materialId（数值）
 * 路径格式：originals/{materialId}/... 或 parsed/{materialId}/...
 * 返回 null 表示路径不符合预期格式
 */
function extractMaterialIdFromPath(objectName) {
  const parts = objectName.split('/');
  // parts[0] = 'originals' | 'parsed', parts[1] = materialId（数字字符串）
  if (parts.length < 2) return null;
  const id = Number(parts[1]);
  return Number.isFinite(id) && id > 0 ? id : null;
}

/**
 * GET /audit/orphans
 * 扫描 MinIO 中路径前缀为 originals/ 或 parsed/ 的对象，
 * 对比 db-server 中的全量 materials，返回无对应 DB 记录的孤儿对象列表。
 */
app.get('/audit/orphans', async (_req, res) => {
  if (getStorageBackend() !== 'minio') {
    res.json({ ok: true, orphans: [], totalCount: 0, totalSize: 0, note: 'non-minio backend' });
    return;
  }

  try {
    // 1. 获取 DB 中全量 materials 的 ID 集合
    const dbResp = await fetch(`${DB_BASE_URL}/materials`, { signal: AbortSignal.timeout(5000) });
    if (!dbResp.ok) throw new Error(`db-server /materials 返回 ${dbResp.status}`);
    const dbMaterials = await dbResp.json(); // 数组
    const knownIds = new Set(
      (Array.isArray(dbMaterials) ? dbMaterials : [])
        .map((m) => Number(m?.id))
        .filter((id) => Number.isFinite(id) && id > 0),
    );

    // 2. 扫描 MinIO 两个桶
    const rawBucket = getMinioBucket();
    const parsedBucket = getParsedBucket();
    const [rawObjects, parsedObjects] = await Promise.all([
      listAllObjects(rawBucket, 'originals/'),
      listAllObjects(parsedBucket, 'parsed/'),
    ]);

    // 3. 筛选孤儿：路径中的 materialId 不在 DB 中，或路径格式不合法
    const orphans = [];
    for (const obj of rawObjects) {
      const id = extractMaterialIdFromPath(obj.name);
      if (id === null || !knownIds.has(id)) {
        orphans.push({ bucket: rawBucket, objectName: obj.name, size: obj.size || 0 });
      }
    }
    for (const obj of parsedObjects) {
      const id = extractMaterialIdFromPath(obj.name);
      if (id === null || !knownIds.has(id)) {
        orphans.push({ bucket: parsedBucket, objectName: obj.name, size: obj.size || 0 });
      }
    }

    const totalSize = orphans.reduce((sum, o) => sum + o.size, 0);
    res.json({ ok: true, orphans, totalCount: orphans.length, totalSize });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /audit/orphans failed:', message);
    res.status(500).json({ ok: false, error: message });
  }
});

/**
 * POST /audit/cleanup-orphans
 * 重新扫描孤儿对象（不依赖前端传入列表），批量删除后返回清理结果。
 * 安全起见：后端独立重新扫描，不依赖前端提交的 objectNames 列表。
 */
app.post('/audit/cleanup-orphans', async (_req, res) => {
  if (getStorageBackend() !== 'minio') {
    res.status(400).json({ error: '孤儿对象清理仅支持 MinIO 存储后端' });
    return;
  }

  try {
    // 重新扫描（与 GET /audit/orphans 逻辑相同，保证安全性）
    const dbResp = await fetch(`${DB_BASE_URL}/materials`, { signal: AbortSignal.timeout(5000) });
    if (!dbResp.ok) throw new Error(`db-server /materials 返回 ${dbResp.status}`);
    const dbMaterials = await dbResp.json();
    const knownIds = new Set(
      (Array.isArray(dbMaterials) ? dbMaterials : [])
        .map((m) => Number(m?.id))
        .filter((id) => Number.isFinite(id) && id > 0),
    );

    const rawBucket = getMinioBucket();
    const parsedBucket = getParsedBucket();
    const [rawObjects, parsedObjects] = await Promise.all([
      listAllObjects(rawBucket, 'originals/'),
      listAllObjects(parsedBucket, 'parsed/'),
    ]);

    const orphans = [];
    for (const obj of rawObjects) {
      const id = extractMaterialIdFromPath(obj.name);
      if (id === null || !knownIds.has(id)) {
        orphans.push({ bucket: rawBucket, objectName: obj.name, size: obj.size || 0 });
      }
    }
    for (const obj of parsedObjects) {
      const id = extractMaterialIdFromPath(obj.name);
      if (id === null || !knownIds.has(id)) {
        orphans.push({ bucket: parsedBucket, objectName: obj.name, size: obj.size || 0 });
      }
    }

    if (orphans.length === 0) {
      res.json({ ok: true, removed: 0, errors: [], totalSize: 0, note: '无孤儿对象' });
      return;
    }

    // 批量删除
    let removed = 0;
    let totalSize = 0;
    const errors = [];
    for (const orphan of orphans) {
      try {
        await getMinioClient().removeObject(orphan.bucket, orphan.objectName);
        removed += 1;
        totalSize += orphan.size;
      } catch (err) {
        errors.push({ objectName: orphan.objectName, error: err.message });
      }
    }

    console.log(`[upload-server] /audit/cleanup-orphans: removed=${removed} errors=${errors.length}`);
    res.json({ ok: true, removed, errors, totalSize });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /audit/cleanup-orphans failed:', message);
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

// ══════════════════════════════════════════════════════════════════
// ─── 后端批处理队列 REST API ─────────────────────────────────────
// ══════════════════════════════════════════════════════════════════

// GET /batch/status - 获取队列状态
app.get('/batch/status', (_req, res) => {
  res.json(getQueueStatus());
});

// POST /batch/jobs - 添加任务到队列
// Body: { jobs: [{ id, fileName, fileSize, path, objectName, mimeType, materialId }] }
app.post('/batch/jobs', (req, res) => {
  const { jobs } = req.body;
  if (!Array.isArray(jobs) || jobs.length === 0) {
    res.status(400).json({ error: '缺少 jobs 数组' });
    return;
  }
  const ids = addJobs(jobs);
  res.json({ ok: true, added: ids.length, ids });
});

// POST /batch/start - 启动队列处理
// Body: { autoMinerU?: boolean, autoAI?: boolean }
app.post('/batch/start', (req, res) => {
  const result = startQueue(req.body || {});
  res.json(result);
});

// POST /batch/pause - 暂停队列
app.post('/batch/pause', (_req, res) => {
  res.json(pauseQueue());
});

// POST /batch/resume - 恢复队列
app.post('/batch/resume', (_req, res) => {
  res.json(resumeQueue());
});

// POST /batch/stop - 停止队列
app.post('/batch/stop', (_req, res) => {
  res.json(stopQueue());
});

// POST /batch/cancel/:jobId - 取消指定任务（pending 直接标记取消；running 尝试 abort）
app.post('/batch/cancel/:jobId', (req, res) => {
  res.json(cancelJob(req.params.jobId));
});

// POST /batch/cancel-current - 取消当前运行任务
app.post('/batch/cancel-current', (_req, res) => {
  res.json(cancelCurrentJob());
});

// POST /batch/retry-failed - 重试所有失败任务
app.post('/batch/retry-failed', (_req, res) => {
  res.json(retryFailed());
});

// POST /batch/retry/:jobId - 重试指定任务
app.post('/batch/retry/:jobId', (req, res) => {
  res.json(retryJob(req.params.jobId));
});

// DELETE /batch/job/:jobId - 移除指定任务
app.delete('/batch/job/:jobId', (req, res) => {
  res.json(removeJob(req.params.jobId));
});

// POST /batch/alerts/read - 标记告警已读
// Body: { ids?: string[] }
app.post('/batch/alerts/read', (req, res) => {
  const ids = Array.isArray(req.body?.ids) ? req.body.ids : [];
  res.json(readAlerts(ids));
});

// POST /batch/clear-completed - 清空已完成和失败的任务
app.post('/batch/clear-completed', (_req, res) => {
  res.json(clearCompleted());
});

// POST /batch/clear-all - 清空全部任务（停止队列）
app.post('/batch/clear-all', (_req, res) => {
  res.json(clearAll());
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

  // 初始化后端批处理队列引擎
  initBatchQueue({
    dbBaseUrl: DB_BASE_URL,
    getMinioClient,
    getMinioBucket,
    getParsedBucket,
    getPresignedExpiry,
    ensureBucket,
    rewritePresignedUrl,
    getStorageBackend,
    uploadBufferToMinIO,
    extractLocalMarkdown,
    buildMarkdownContext,
    callGradioToMarkdown,
    waitMinerUTask,
    fetchMinerUResult,
    analyzeWithFallback,
    calcPages,
    detectFormat,
    normalizeFileName,
    fixFilenameEncoding,
    isEnabledFlag,
    getFileBuffer,
    cleanupTempFile,
  });
  await restoreBatchQueue();
  console.log('[upload-server] Batch queue engine initialized');
});

// ─── 优雅停机 ─────────────────────────────────────────────────

async function gracefulShutdown(signal) {
  console.log(`[upload-server] Received ${signal}, shutting down...`);
  // 先持久化批处理队列状态
  await shutdownBatchQueue().catch(e => console.warn('[upload-server] batch queue shutdown error:', e.message));
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
  // 不立即退出，保持服务稳定性，但记录异常
});
process.on('unhandledRejection', (reason) => {
  console.error('[upload-server] Unhandled rejection:', reason);
  // 不立即退出，保持服务稳定性
});
