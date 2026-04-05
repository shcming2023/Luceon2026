import express from 'express';
import cors from 'cors';
import multer from 'multer';

const app = express();
const port = Number(process.env.UPLOAD_PORT || 8788);

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 200 * 1024 * 1024 },
});

app.use(cors());

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

async function uploadToTmpfiles(buffer, mimeType, fileName, retries = 3) {
  let lastError = null;

  for (let i = 1; i <= retries; i += 1) {
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
        throw new Error(`tmpfiles 上传失败: HTTP ${resp.status} ${detail || resp.statusText}`);
      }

      const json = await resp.json();
      const rawUrl = json?.data?.url || '';
      const publicUrl = toDownloadUrl(rawUrl);
      if (!publicUrl) {
        throw new Error('tmpfiles 未返回可用 URL');
      }

      return { provider: 'tmpfiles', url: publicUrl };
    } catch (error) {
      lastError = error;
      if (i < retries) {
        await new Promise((resolve) => setTimeout(resolve, 700 * i));
      }
    }
  }

  throw lastError || new Error('上传失败');
}

app.post('/upload', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      res.status(400).json({ error: '缺少文件字段 `file`' });
      return;
    }

    const safeFileName = normalizeFileName(req.file.originalname);
    const uploaded = await uploadToTmpfiles(req.file.buffer, req.file.mimetype, safeFileName, 3);

    res.json({
      url: uploaded.url,
      fileName: req.file.originalname,
      size: req.file.size,
      mimeType: req.file.mimetype,
      provider: uploaded.provider,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误';
    console.error('[upload-server] upload failed:', message);
    res.status(500).json({ error: message });
  }
});

app.listen(port, () => {
  console.log(`[upload-server] listening on http://localhost:${port}`);
});
