/**
 * upload-server.mjs — 文件上传代理服务
 *
 * 端口：8788（通过 UPLOAD_PORT 环境变量覆盖）
 *
 * 存储后端（通过 STORAGE_BACKEND 环境变量切换）：
 *   minio    — MinIO 私有对象存储（默认），返回预签名 URL
 *   tmpfiles — tmpfiles.org 公开临时存储（降级 fallback）
 *
 * 路由总览：
 *   GET    /health                  → 健康检查
 *   POST   /upload                  → 上传单文件，返回 { url, fileName, size, mimeType, provider }
 *   POST   /upload-multiple         → 上传多文件（最多 20 个），返回 results[]
 *   GET    /parse/status/:taskId    → 查询 MinerU 解析任务状态
 *   POST   /parse/download          → 下载 MinerU 解析结果（ZIP）并转存到 MinIO
 *   POST   /parse/analyze           → 解析已上传文件（MinerU OCR + MD 提取）
 *
 * 环境变量（详见 .env.example）：
 *   UPLOAD_PORT          — 服务端口（默认 8788）
 *   STORAGE_BACKEND      — 存储后端（minio | tmpfiles，默认 tmpfiles）
 *   MINIO_ENDPOINT       — MinIO 端点（默认 minio）
 *   MINIO_PORT           — MinIO API 端口（默认 9000）
 *   MINIO_ACCESS_KEY     — MinIO 访问密钥
 *   MINIO_SECRET_KEY     — MinIO 私钥
 *   MINIO_BUCKET         — 存储桶名称（默认 eduassets）
 *   MINIO_PRESIGNED_EXPIRY — 预签名 URL 有效期秒数（默认 3600）
 */

import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { Client } from 'minio';
import JSZip from 'jszip';

const app = express();
const port = Number(process.env.UPLOAD_PORT || 8788);

app.use(cors());
app.use(express.json());

// ─── 存储后端配置 ──────────────────────────────────────────────
const STORAGE_BACKEND = process.env.STORAGE_BACKEND || 'tmpfiles';

// ─── MinIO 配置 ────────────────────────────────────────────────
const minioClient = new Client({
  endPoint: process.env.MINIO_ENDPOINT || 'minio',
  port: Number(process.env.MINIO_PORT || 9000),
  useSSL: process.env.MINIO_USE_SSL === 'true',
  accessKey: process.env.MINIO_ACCESS_KEY || 'minioadmin',
  secretKey: process.env.MINIO_SECRET_KEY || 'minioadmin',
});
const MINIO_BUCKET = process.env.MINIO_BUCKET || 'eduassets';

// Presigned URL 有效期（秒），默认 1 小时
const PRESIGNED_EXPIRY = Number(process.env.MINIO_PRESIGNED_EXPIRY || 3600);

// ─── multer（仅内存存储，限 200MB）────────────────────────────
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 200 * 1024 * 1024 },
});

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

/** 确保 MinIO Bucket 存在 */
async function ensureBucket() {
  const exists = await minioClient.bucketExists(MINIO_BUCKET);
  if (!exists) {
    await minioClient.makeBucket(MINIO_BUCKET, 'us-east-1');
    console.log(`[upload-server] Bucket "${MINIO_BUCKET}" created`);
  }
}

/**
 * 上传 buffer 到 MinIO
 * @returns {{ objectName: string, presignedUrl: string }}
 */
async function uploadBufferToMinIO(buffer, mimeType, prefix, fileName, retries = 3) {
  let lastError = null;

  for (let i = 1; i <= retries; i++) {
    try {
      await ensureBucket();

      const objectName = `${prefix}/${Date.now()}-${fileName}`;
      await minioClient.putObject(
        MINIO_BUCKET,
        objectName,
        buffer,
        buffer.length,
        { 'Content-Type': mimeType || 'application/octet-stream' },
      );

      const presignedUrl = await minioClient.presignedGetObject(
        MINIO_BUCKET,
        objectName,
        PRESIGNED_EXPIRY,
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
// 响应：{ url, objectName, fileName, size, mimeType, provider }
app.post('/upload', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      res.status(400).json({ error: '缺少文件字段 `file`' });
      return;
    }

    const safeFileName = normalizeFileName(req.file.originalname);
    let result;

    if (STORAGE_BACKEND === 'minio') {
      try {
        const { objectName, presignedUrl } = await uploadBufferToMinIO(
          req.file.buffer,
          req.file.mimetype,
          'originals',
          safeFileName,
        );
        result = {
          provider: 'minio',
          url: presignedUrl,       // 用于 MinerU 直接下载（presigned，有效期1h）
          objectName,              // 用于内部持久引用
        };
      } catch (minioError) {
        console.error('[upload-server] MinIO failed, fallback to tmpfiles:', minioError.message);
        result = await uploadToTmpfiles(req.file.buffer, req.file.mimetype, safeFileName);
        result.objectName = null;
      }
    } else {
      result = await uploadToTmpfiles(req.file.buffer, req.file.mimetype, safeFileName);
      result.objectName = null;
    }

    res.json({
      url: result.url,
      objectName: result.objectName,
      fileName: req.file.originalname,
      size: req.file.size,
      mimeType: req.file.mimetype,
      provider: result.provider,
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
    const url = await minioClient.presignedGetObject(MINIO_BUCKET, objectName, PRESIGNED_EXPIRY);
    res.json({ url, objectName, expiresIn: PRESIGNED_EXPIRY });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /presign failed:', message);
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
    await ensureBucket();

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

    for (const [name, zipEntry] of Object.entries(zip.files)) {
      if (zipEntry.dir) continue;

      const lower = name.toLowerCase();
      const isMd = lower.endsWith('.md');
      const isImage = /\.(png|jpg|jpeg|gif|webp|svg)$/.test(lower);

      if (!isMd && !isImage) continue;

      const content = await zipEntry.async('nodebuffer');
      const mimeType = isMd ? 'text/markdown'
        : lower.endsWith('.png') ? 'image/png'
        : lower.endsWith('.svg') ? 'image/svg+xml'
        : 'image/jpeg';

      const objectName = `${prefix}/${name}`;

      await minioClient.putObject(
        MINIO_BUCKET,
        objectName,
        content,
        content.length,
        { 'Content-Type': mimeType },
      );

      const presignedUrl = await minioClient.presignedGetObject(
        MINIO_BUCKET,
        objectName,
        PRESIGNED_EXPIRY,
      );

      uploadedFiles.push({ objectName, presignedUrl, name });

      if (isMd && (name === 'full.md' || name.endsWith('/full.md'))) {
        markdownObjectName = objectName;
        markdownUrl = presignedUrl;
      }

      console.log(`[upload-server] Stored: ${objectName}`);
    }

    res.json({
      files: uploadedFiles,
      markdownObjectName,
      markdownUrl,
      totalFiles: uploadedFiles.length,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/download failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：POST /parse/analyze ────────────────────────────────
// 读取 MinIO 中的 full.md，调用大模型 API，提取结构化元数据
// Body: { markdownObjectName?: string, markdownUrl?: string, materialId: string|number,
//         aiApiEndpoint: string, aiApiKey: string, aiModel: string, prompts?: object }
// 响应: { title, subject, grade, materialType, tags, summary, confidence }
app.post('/parse/analyze', async (req, res) => {
  const {
    markdownObjectName,
    markdownUrl,
    materialId,
    aiApiEndpoint,
    aiApiKey,
    aiModel,
    prompts,
  } = req.body;

  if (!materialId) {
    res.status(400).json({ error: '缺少 materialId' });
    return;
  }
  if (!aiApiEndpoint || !aiApiKey || !aiModel) {
    res.status(400).json({ error: '缺少 AI API 配置（aiApiEndpoint / aiApiKey / aiModel）' });
    return;
  }

  try {
    // ── 1. 读取 Markdown 内容 ──────────────────────────────────
    let markdownText = '';

    if (markdownObjectName) {
      // 从 MinIO 读取
      try {
        const stream = await minioClient.getObject(MINIO_BUCKET, markdownObjectName);
        const chunks = [];
        for await (const chunk of stream) chunks.push(chunk);
        markdownText = Buffer.concat(chunks).toString('utf-8');
        console.log(`[upload-server] Read markdown from MinIO: ${markdownObjectName} (${markdownText.length} chars)`);
      } catch (minioErr) {
        console.warn('[upload-server] MinIO read failed, fallback to markdownUrl:', minioErr.message);
      }
    }

    if (!markdownText && markdownUrl) {
      // 兜底：直接下载 presigned URL
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

    // 截断：大模型上下文限制，取前 8000 字符（~4000 token），足够提取元数据
    const mdSnippet = markdownText.slice(0, 8000);

    // ── 2. 构建 Prompt ─────────────────────────────────────────
    const defaultPrompts = {
      title: '根据内容识别资料的准确名称/标题',
      subject: '识别学科（数学/语文/英语/物理/化学/生物/历史/地理/政治/科学/综合等）',
      grade: '识别适用年级（小学一年级~六年级，初中七年级~九年级，高中一年级~三年级，或"通用"）',
      materialType: '识别资料类型（教材/试卷/讲义/课件/习题/答案/大纲/教案/其他）',
      tags: '提取3-8个关键标签，用于检索和分类（返回数组）',
      summary: '用2-3句话简要概括资料核心内容',
    };

    const p = { ...defaultPrompts, ...(prompts || {}) };

    const systemPrompt = `你是一个专业的教育资源元数据提取助手。请根据提供的文档内容，提取结构化的元数据信息。
严格按照 JSON 格式返回，不要输出任何其他内容。`;

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
  "tags": ["${p.tags}"],
  "summary": "${p.summary}",
  "confidence": 0-100（整体识别置信度）
}

注意：
1. tags 必须是字符串数组
2. confidence 必须是 0-100 的整数
3. 仅返回 JSON，不要有任何前缀或解释文字`;

    // ── 3. 调用大模型 API ──────────────────────────────────────
    console.log(`[upload-server] Calling AI API: ${aiApiEndpoint} model=${aiModel}`);

    const aiController = new AbortController();
    const aiTimer = setTimeout(() => aiController.abort(), 60_000);

    const aiResp = await fetch(`${aiApiEndpoint}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${aiApiKey}`,
      },
      body: JSON.stringify({
        model: aiModel,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.1,
        max_tokens: 1024,
        response_format: { type: 'json_object' },
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
      // 有些模型会在 JSON 外面包裹 markdown 代码块
      const jsonStr = rawContent.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim();
      extracted = JSON.parse(jsonStr);
    } catch {
      console.error('[upload-server] AI response is not valid JSON:', rawContent.slice(0, 500));
      throw new Error(`AI 返回格式异常，无法解析为 JSON。原始响应：${rawContent.slice(0, 200)}`);
    }

    // 归一化字段
    const result = {
      title: String(extracted.title || '').trim(),
      subject: String(extracted.subject || '').trim(),
      grade: String(extracted.grade || '').trim(),
      materialType: String(extracted.materialType || extracted.type || '').trim(),
      tags: Array.isArray(extracted.tags) ? extracted.tags.map(String).filter(Boolean) : [],
      summary: String(extracted.summary || '').trim(),
      confidence: Math.min(100, Math.max(0, Number(extracted.confidence) || 0)),
      materialId,
      analyzedAt: new Date().toISOString(),
    };

    console.log(`[upload-server] AI analysis done for material ${materialId}:`, {
      subject: result.subject,
      grade: result.grade,
      confidence: result.confidence,
    });

    res.json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[upload-server] /parse/analyze failed:', message);
    res.status(500).json({ error: message });
  }
});

// ─── 接口：GET /file ───────────────────────────────────────────
// 获取 MinIO 中某个对象的内容（供前端读取 full.md 内容等）
// Query: ?objectName=parsed/xxx/full.md
app.get('/file', async (req, res) => {
  const { objectName } = req.query;
  if (!objectName || typeof objectName !== 'string') {
    res.status(400).json({ error: '缺少 objectName 参数' });
    return;
  }

  try {
    const stream = await minioClient.getObject(MINIO_BUCKET, objectName);
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

app.listen(port, () => {
  console.log(`[upload-server] listening on http://localhost:${port}`);
  console.log(`[upload-server] storage backend: ${STORAGE_BACKEND}`);
  if (STORAGE_BACKEND === 'minio') {
    console.log(`[upload-server] MinIO: ${process.env.MINIO_ENDPOINT || 'minio'}:${process.env.MINIO_PORT || 9000}`);
  }
});
