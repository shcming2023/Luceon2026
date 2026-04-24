/**
 * local-adapter.mjs
 * 从现有 upload-server.mjs 中抽取的真实本地 MinerU 执行逻辑。
 * 由 ParseTaskWorker 调用，用于长耗时后台处理。
 */

import JSZip from 'jszip';

export async function processWithLocalMinerU({ task, material, fileStream, fileName, mimeType, timeoutMs, minioContext, updateProgress }) {
  const options = task.optionsSnapshot || {};
  let localEndpoint = options.localEndpoint;
  if (!localEndpoint) throw new Error('缺少 localEndpoint');

  // docker 内部网络地址重写
  if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
    localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
  }
  localEndpoint = localEndpoint.replace(/\/+$/, '');

  const backend = String(options.backend || 'pipeline');
  const maxPages = Number(options.maxPages || 1000);
  const ocrLanguage = String(options.ocrLanguage || options.language || 'ch');
  const enableOcr = isEnabledFlag(options.enableOcr);
  const enableFormula = isEnabledFlag(options.enableFormula);
  const enableTable = isEnabledFlag(options.enableTable);
  const parseMethod = String(options.parseMethod || options.parse_method || '').trim();
  const responseFormatZip = (options.responseFormatZip ?? options.response_format_zip) == null
    ? true
    : isEnabledFlag(options.responseFormatZip ?? options.response_format_zip);

  let serverUrl = String(options.serverUrl || options.server_url || options.url || '').trim();
  if (serverUrl && (serverUrl.includes('localhost') || serverUrl.includes('127.0.0.1'))) {
    serverUrl = serverUrl.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
  }

  // 1. Check health
  const isHealthy = await checkHealth(localEndpoint);
  if (!isHealthy) throw new Error(`本地 MinerU 不可达: ${localEndpoint}`);

  await updateProgress({ progress: 10, message: '已连接本地 MinerU，准备提交任务...' });

  const fileSize = material?.fileSize || 0;
  const submitTimeoutMs = Math.max(timeoutMs, Math.max(120_000, Math.ceil(fileSize / 1024) * 50));
  const effectiveBackend = (fileSize > 0 && fileSize < 2 * 1024 * 1024 && /hybrid/i.test(backend)) ? 'pipeline' : backend;
  
  let finalParseMethod = parseMethod || 'auto';
  if (enableOcr && !parseMethod) finalParseMethod = 'ocr';

  // 2. Submit task
  const boundary = `----luceon-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const fields = [
    ['backend', effectiveBackend],
    ['parse_method', finalParseMethod],
    ['formula_enable', enableFormula ? '1' : '0'],
    ['table_enable', enableTable ? '1' : '0'],
    ['response_format_zip', responseFormatZip ? 'true' : 'false']
  ];
  for (const lang of String(ocrLanguage).split(',').map((item) => item.trim()).filter(Boolean)) {
    fields.push(['lang_list', lang]);
  }
  if (serverUrl) fields.push(['server_url', serverUrl]);
  if (Number.isFinite(maxPages) && maxPages > 0) {
    const endPageId = String(Math.max(0, Math.floor(maxPages) - 1));
    fields.push(['end_page_id', endPageId]);
    fields.push(['endpageid', endPageId]);
  }

  const multipart = createMultipartStream({
    boundary, fields,
    fileFieldName: 'files',
    fileName: fileName,
    mimeType: mimeType || 'application/octet-stream',
    fileStream,
  });

  let mineruTaskId = '';
  let markdown = '';

  const fastApiResponse = await fetch(`${localEndpoint}/tasks`, {
    method: 'POST',
    headers: { 'content-type': multipart.contentType },
    body: multipart.body,
    duplex: 'half',
    signal: AbortSignal.timeout(submitTimeoutMs),
  }).catch(err => {
    throw new Error(`本地 MinerU 提交通讯失败: ${err.message}`);
  });

  if (fastApiResponse.status !== 404 && fastApiResponse.status !== 405) {
    const payload = await fastApiResponse.json().catch(() => null);
    if (!fastApiResponse.ok) {
      throw new Error(`MinerU 提交失败: ${payload?.error || payload?.message || fastApiResponse.status}`);
    }
    mineruTaskId = payload?.task_id || payload?.taskid || payload?.taskId || '';
    if (!mineruTaskId) throw new Error('MinerU 未返回任务 id');

    await updateProgress({ progress: 20, message: `任务已提交，内部ID: ${mineruTaskId}` });

    // 3. Poll
    await waitMinerUTask(localEndpoint, mineruTaskId, timeoutMs, async (statusPayload) => {
      const status = String(statusPayload?.status || '').toLowerCase();
      const queued = statusPayload?.queued_ahead || statusPayload?.queue_ahead || 0;
      let msg = `处理中 (${status})...`;
      if (status === 'pending' || status === 'queued') msg = `排队中 (前方 ${queued} 个任务)`;
      else if (status === 'processing') msg = '正在执行 OCR 与解析...';
      await updateProgress({ progress: 50, message: msg });
    });

    await updateProgress({ progress: 80, message: '解析完成，提取结果...' });
    const resultRaw = await fetchMinerUResultRaw(localEndpoint, mineruTaskId, timeoutMs);
    let resultPayload = resultRaw.kind === 'json' ? resultRaw.payload : null;

    const materialId = task.materialId || task.id;
    const parsedPrefix = `parsed/${materialId}/`;
    const fullMdObjectName = `${parsedPrefix}full.md`;
    const parsedArtifacts = [];
    const seen = new Set();

    const pushArtifact = (relativePath, objectName, size, mimeType) => {
      const key = `${relativePath}::${objectName}`;
      if (seen.has(key)) return;
      seen.add(key);
      parsedArtifacts.push({
        objectName,
        relativePath,
        size: typeof size === 'number' ? size : undefined,
        mimeType: mimeType || undefined,
      });
    };

    const saveObject = async (objectName, buffer, contentType) => {
      if (typeof minioContext?.saveObject !== 'function') return false;
      await minioContext.saveObject(objectName, buffer, contentType || 'application/octet-stream');
      return true;
    };

    let zipObjectName = null;
    let hasMineruZip = false;

    if (resultPayload) {
      const rawJson = Buffer.from(JSON.stringify(resultPayload), 'utf-8');
      const rawObjectName = `${parsedPrefix}mineru-result.json`;
      const ok = await saveObject(rawObjectName, rawJson, 'application/json; charset=utf-8');
      if (ok) pushArtifact('mineru-result.json', rawObjectName, rawJson.length, 'application/json');
    }

    let zipBuffer = resultRaw.kind === 'zip' ? resultRaw.buffer : null;
    if (!zipBuffer && resultPayload && responseFormatZip) {
      zipBuffer = await extractZipBufferFromJsonResult(resultPayload, timeoutMs);
    }

    if (zipBuffer) {
      hasMineruZip = true;
      zipObjectName = `${parsedPrefix}mineru-result.zip`;
      const ok = await saveObject(zipObjectName, zipBuffer, 'application/zip');
      if (ok) pushArtifact('mineru-result.zip', zipObjectName, zipBuffer.length, 'application/zip');

      const zip = await JSZip.loadAsync(zipBuffer);
      const entries = Object.entries(zip.files)
        .filter(([, entry]) => !entry.dir)
        .map(([name]) => name);

      const mdCandidates = [];
      for (const name of entries) {
        const safeRelativePath = sanitizeRelativePath(name);
        if (!safeRelativePath) continue;
        const lower = safeRelativePath.toLowerCase();
        if (!lower.endsWith('.md')) continue;
        if (safeRelativePath === 'mineru-result.zip' || safeRelativePath === 'mineru-result.json') continue;
        mdCandidates.push({ name, relativePath: safeRelativePath });
      }

      const isFullMd = (rel) => {
        const lower = String(rel || '').toLowerCase();
        return lower === 'full.md' || lower.endsWith('/full.md');
      };

      const pickPrimaryMarkdown = (candidates) => {
        if (!Array.isArray(candidates) || candidates.length === 0) return null;
        const full = candidates.find((c) => isFullMd(c.relativePath));
        if (full) return full;
        if (candidates.length === 1) return candidates[0];

        const auto = candidates.filter((c) => String(c.relativePath || '').toLowerCase().includes('/auto/'));
        const pool = auto.length > 0 ? auto : candidates;
        return pool.slice().sort((a, b) => String(a.relativePath).length - String(b.relativePath).length)[0];
      };

      const primary = pickPrimaryMarkdown(mdCandidates);
      if (primary && !markdown) {
        const content = await zip.file(primary.name).async('nodebuffer');
        markdown = content.toString('utf-8').trim();
      }

      for (const name of entries) {
        const safeRelativePath = sanitizeRelativePath(name);
        if (!safeRelativePath) continue;
        if (safeRelativePath === 'full.md') continue;
        if (safeRelativePath === 'mineru-result.zip' || safeRelativePath === 'mineru-result.json') continue;

        const content = await zip.file(name).async('nodebuffer');
        const objectName = `${parsedPrefix}${safeRelativePath}`;
        const contentType = inferContentTypeByExt(safeRelativePath);
        const ok = await saveObject(objectName, content, contentType);
        if (ok) pushArtifact(safeRelativePath, objectName, content.length, contentType);
      }
    } else if (resultPayload) {
      markdown = extractLocalMarkdown(resultPayload);
      const extra = await extractArtifactsFromJsonResult(resultPayload, timeoutMs);
      for (const item of extra) {
        const safeRelativePath = sanitizeRelativePath(item.relativePath);
        if (!safeRelativePath) continue;
        if (safeRelativePath === 'full.md') continue;
        const objectName = `${parsedPrefix}${safeRelativePath}`;
        const contentType = item.mimeType || inferContentTypeByExt(safeRelativePath);
        const ok = await saveObject(objectName, item.buffer, contentType);
        if (ok) pushArtifact(safeRelativePath, objectName, item.buffer.length, contentType);
      }
    }

    if (!markdown) throw new Error('提取到的 Markdown 内容为空');

    await updateProgress({ stage: 'store', state: 'result-store', progress: 90, message: '正在保存产物到 MinIO...' });

    await minioContext.saveMarkdown(fullMdObjectName, markdown);
    pushArtifact('full.md', fullMdObjectName, Buffer.byteLength(markdown, 'utf-8'), 'text/markdown');

    const realArtifacts = parsedArtifacts.filter((a) => {
      const rp = String(a.relativePath || '');
      if (rp === 'full.md') return false;
      if (rp === 'mineru-result.json') return false;
      if (rp === 'mineru-result.zip') return false;
      return true;
    });

    const artifactIncomplete = realArtifacts.length === 0;

    return {
      markdown,
      mineruTaskId,
      objectName: fullMdObjectName,
      parsedPrefix,
      parsedFilesCount: parsedArtifacts.length,
      parsedArtifacts,
      zipObjectName: hasMineruZip ? zipObjectName : null,
      artifactIncomplete,
    };

  } else {
    // 降级 Gradio
    await updateProgress({ progress: 50, message: '降级为 Gradio 接口解析...' });
    throw new Error('当前版本 Worker 暂不支持 Gradio 降级流式上传，请更新 MinerU API');
  }

  if (!markdown) throw new Error('提取到的 Markdown 内容为空');

  await updateProgress({ stage: 'store', state: 'result-store', progress: 90, message: '正在保存产物到 MinIO...' });
  const objectName = `parsed/${task.materialId || task.id}/full.md`;
  await minioContext.saveMarkdown(objectName, markdown);

  return {
    markdown,
    mineruTaskId,
    objectName,
    parsedPrefix: `parsed/${task.materialId || task.id}/`,
    parsedFilesCount: 1,
    parsedArtifacts: [{ objectName, relativePath: 'full.md', size: Buffer.byteLength(markdown, 'utf-8'), mimeType: 'text/markdown' }],
    zipObjectName: null,
    artifactIncomplete: true,
  };
}

// ─── Utils ───────────────────────────────────

function isEnabledFlag(value) {
  return value === true || value === 'true' || value === '1' || value === 1;
}

async function checkHealth(endpoint) {
  try {
    const res = await fetch(`${endpoint}/health`, { signal: AbortSignal.timeout(3000) });
    return res.status === 200;
  } catch {
    return false;
  }
}

function createMultipartStream({ boundary, fields, fileFieldName, fileName, mimeType, fileStream }) {
  const enc = new TextEncoder();
  const safeName = String(fileName || 'upload.bin').replace(/"/g, '_');
  const parts = Array.isArray(fields) ? fields : [];
  const fileHeader = `--${boundary}\r\nContent-Disposition: form-data; name="${fileFieldName}"; filename="${safeName}"\r\nContent-Type: ${mimeType}\r\n\r\n`;
  const fileFooter = `\r\n--${boundary}--\r\n`;
  const fieldChunk = (name, value) => enc.encode(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${String(value ?? '')}\r\n`);

  async function* gen() {
    for (const [k, v] of parts) yield fieldChunk(k, v);
    yield enc.encode(fileHeader);
    for await (const chunk of fileStream) yield chunk;
    yield enc.encode(fileFooter);
  }
  return { contentType: `multipart/form-data; boundary=${boundary}`, body: gen() };
}

async function waitMinerUTask(localEndpoint, taskId, timeoutMs, onProgress) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}`, { signal: AbortSignal.timeout(10000) });
    const payload = await response.json();
    if (!response.ok) throw new Error(`查询状态失败: HTTP ${response.status}`);
    
    if (onProgress) await onProgress(payload);
    
    const status = String(payload?.status || payload?.state || payload?.task_status || payload?.data?.status || payload?.data?.state).toLowerCase();
    if (['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(status)) return payload;
    if (['failed', 'error', 'failure', 'canceled', 'cancelled'].includes(status)) throw new Error(payload?.error || payload?.message || '任务执行失败');
    
    await new Promise(r => setTimeout(r, 1500));
  }
  throw new Error(`超时未完成 (等待超过 ${Math.round(timeoutMs/1000)}s)`);
}

async function fetchMinerUResult(localEndpoint, taskId, timeoutMs) {
  const response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}/result`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!response.ok) throw new Error(`获取结果失败: HTTP ${response.status}`);
  return await response.json();
}

function extractLocalMarkdown(payload) {
  if (typeof payload === 'string') return payload.trim();
  if (payload?.results) {
    const firstKey = Object.keys(payload.results)[0];
    const md = firstKey ? (payload.results[firstKey]?.md_content || payload.results[firstKey]?.mdcontent || payload.results[firstKey]?.mdContent) : '';
    if (typeof md === 'string') return md.trim();
  }
  const candidates = [payload?.md_content, payload?.markdown, payload?.text, payload?.data?.md_content, payload?.data?.markdown, payload?.data?.text];
  return (candidates.find(i => typeof i === 'string' && i.trim() !== '') || '').trim();
}

async function fetchMinerUResultRaw(localEndpoint, taskId, timeoutMs) {
  const response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}/result`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!response.ok) throw new Error(`获取结果失败: HTTP ${response.status}`);
  const contentType = String(response.headers.get('content-type') || '').toLowerCase();
  if (contentType.includes('application/json') || contentType.includes('text/json')) {
    return { kind: 'json', payload: await response.json() };
  }

  const buffer = Buffer.from(await response.arrayBuffer());
  const asText = buffer.slice(0, Math.min(200, buffer.length)).toString('utf-8');
  if (asText.trim().startsWith('{') || asText.trim().startsWith('[')) {
    try {
      return { kind: 'json', payload: JSON.parse(buffer.toString('utf-8')) };
    } catch {
      return { kind: 'zip', buffer };
    }
  }
  return { kind: 'zip', buffer };
}

function inferContentTypeByExt(filePath) {
  const lower = String(filePath || '').toLowerCase();
  if (lower.endsWith('.md')) return 'text/markdown; charset=utf-8';
  if (lower.endsWith('.json')) return 'application/json; charset=utf-8';
  if (lower.endsWith('.txt')) return 'text/plain; charset=utf-8';
  if (lower.endsWith('.html')) return 'text/html; charset=utf-8';
  if (lower.endsWith('.css')) return 'text/css; charset=utf-8';
  if (lower.endsWith('.js')) return 'application/javascript; charset=utf-8';
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
  if (lower.endsWith('.gif')) return 'image/gif';
  if (lower.endsWith('.webp')) return 'image/webp';
  if (lower.endsWith('.svg')) return 'image/svg+xml';
  if (lower.endsWith('.pdf')) return 'application/pdf';
  if (lower.endsWith('.zip')) return 'application/zip';
  if (lower.endsWith('.xml')) return 'application/xml; charset=utf-8';
  return 'application/octet-stream';
}

function sanitizeRelativePath(input) {
  const raw = String(input || '').replace(/\\/g, '/').replace(/^\/+/, '').trim();
  if (!raw) return '';
  const parts = raw.split('/').filter(Boolean);
  const safe = [];
  for (const p of parts) {
    if (p === '.' || p === '') continue;
    if (p === '..') return '';
    safe.push(p.replace(/\0/g, ''));
  }
  return safe.join('/');
}

async function extractZipBufferFromJsonResult(payload, timeoutMs) {
  const candidates = [
    payload?.zip_url, payload?.zipUrl, payload?.result_zip_url, payload?.resultZipUrl,
    payload?.data?.zip_url, payload?.data?.zipUrl, payload?.data?.result_zip_url, payload?.data?.resultZipUrl,
  ].filter((v) => typeof v === 'string' && v.trim() !== '');

  const url = candidates.find((u) => /^https?:\/\//i.test(u));
  if (url) {
    const resp = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    if (!resp.ok) return null;
    return Buffer.from(await resp.arrayBuffer());
  }

  const base64Candidates = [
    payload?.zip_base64, payload?.zipBase64, payload?.result_zip_base64, payload?.resultZipBase64,
    payload?.data?.zip_base64, payload?.data?.zipBase64, payload?.data?.result_zip_base64, payload?.data?.resultZipBase64,
  ].filter((v) => typeof v === 'string' && v.trim() !== '');

  const b64 = base64Candidates.find((s) => s.length > 100);
  if (b64) {
    try {
      return Buffer.from(b64, 'base64');
    } catch {
      return null;
    }
  }
  return null;
}

async function extractArtifactsFromJsonResult(payload, timeoutMs) {
  const result = [];
  const candidates = [
    payload?.artifacts,
    payload?.files,
    payload?.data?.artifacts,
    payload?.data?.files,
    payload?.results?.artifacts,
    payload?.results?.files,
    Array.isArray(payload?.results) ? payload.results.flatMap((r) => r?.artifacts || r?.files || []) : null,
    Array.isArray(payload?.data) ? payload.data.flatMap((r) => r?.artifacts || r?.files || []) : null,
  ].flat().filter(Boolean);

  const items = Array.isArray(candidates) ? candidates : [];

  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const relativePath = item.relativePath || item.path || item.name || item.filename || item.file_name;
    if (typeof relativePath !== 'string' || relativePath.trim() === '') continue;

    const mimeType = (item.mimeType || item.mimetype || item.contentType || item['Content-Type'] || '').toString().trim();
    const base64 = item.base64 || item.content_base64 || item.data_base64 || item.contentBase64 || item.dataBase64;
    const url = item.url || item.download_url || item.downloadUrl || item.presignedUrl;

    if (typeof base64 === 'string' && base64.trim() !== '') {
      try {
        const buffer = Buffer.from(base64, 'base64');
        result.push({ relativePath, buffer, mimeType: mimeType || null });
        continue;
      } catch {
        continue;
      }
    }

    if (typeof url === 'string' && /^https?:\/\//i.test(url)) {
      try {
        const resp = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
        if (!resp.ok) continue;
        const buffer = Buffer.from(await resp.arrayBuffer());
        const ct = mimeType || String(resp.headers.get('content-type') || '').trim() || null;
        result.push({ relativePath, buffer, mimeType: ct });
      } catch {
        continue;
      }
    }
  }

  return result;
}
