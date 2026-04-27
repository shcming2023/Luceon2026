/**
 * local-adapter.mjs
 * 从现有 upload-server.mjs 中抽取的真实本地 MinerU 执行逻辑。
 * 由 ParseTaskWorker 调用，用于长耗时后台处理。
 */

import JSZip from 'jszip';

/**
 * MinerU 本地等待超时错误。
 * 表示 Luceon 本地轮询 MinerU 超时，但并不代表 MinerU 任务本身失败。
 *
 * @property {string} mineruTaskId - 对应的 MinerU 内部任务 ID
 * @property {string} lastKnownStatus - 超时前最后一次查询到的 MinerU 状态
 */
export class MineruTimeoutError extends Error {
  constructor(mineruTaskId, lastKnownStatus, timeoutMs) {
    super(`本地等待 MinerU 超时 (${Math.round(timeoutMs / 1000)}s)，但 MinerU 任务 ${mineruTaskId} 最后状态为 ${lastKnownStatus}，不代表业务失败`);
    this.name = 'MineruTimeoutError';
    this.mineruTaskId = mineruTaskId;
    this.lastKnownStatus = lastKnownStatus;
  }
}

/**
 * MinerU 仍在处理错误。
 * 表示 Luceon 等待超时后确认 MinerU 仍在 queued/processing，任务应保持 running 而非 failed。
 *
 * @property {string} mineruTaskId - 对应的 MinerU 内部任务 ID
 * @property {string} mineruStatus - MinerU 当前确认的状态（queued/processing）
 */
export class MineruStillProcessingError extends Error {
  constructor(mineruTaskId, mineruStatus) {
    super(`MinerU 任务 ${mineruTaskId} 仍在 ${mineruStatus}，Luceon 本地等待已超时但任务未失败`);
    this.name = 'MineruStillProcessingError';
    this.mineruTaskId = mineruTaskId;
    this.mineruStatus = mineruStatus;
  }
}

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
    ['response_format_zip', 'true'],
    ['return_md', 'true'],
    ['return_middle_json', 'true'],
    ['return_model_output', 'true'],
    ['return_content_list', 'true'],
    ['return_images', 'true'],
    ['return_original_file', 'true']
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
    const rawBody = await fastApiResponse.text().catch(() => '');
    let payload = null;
    try {
      payload = JSON.parse(rawBody);
    } catch (e) {
      // payload remains null
    }

    if (!fastApiResponse.ok) {
      const errorDetail = payload?.error || payload?.message || rawBody.slice(0, 500);
      const configInfo = `backend=${effectiveBackend}, parse_method=${finalParseMethod}, zip=${responseFormatZip}`;
      throw new Error(`MinerU 提交失败: ${fastApiResponse.status} | Endpoint: ${localEndpoint}/tasks | Body: ${errorDetail} | Config: ${configInfo}`);
    }
    mineruTaskId = payload?.task_id || payload?.taskid || payload?.taskId || '';
    if (!mineruTaskId) throw new Error('MinerU 未返回任务 id');

    // P0 Task 1: 拿到 mineruTaskId 后立即更新 metadata
    const executionProfile = {
      ...(task.metadata?.mineruExecutionProfile || {}),
      backendEffective: effectiveBackend,
      backendRequested: backend,
      parseMethod: finalParseMethod,
      enableOcr,
      enableFormula,
      enableTable,
      ocrLanguage,
      maxPages
    };

    await updateProgress({
      progress: 20,
      message: `任务已提交，内部ID: ${mineruTaskId}`,
      metadata: {
        ...(task.metadata || {}),
        mineruTaskId,
        mineruStatus: 'submitted',
        mineruSubmittedAt: new Date().toISOString(),
        mineruExecutionProfile: executionProfile
      }
    });

    // 3. Poll (P0 Task 2: 区分 queued 与 processing)
    // 如果本地超时但 MinerU 仍在处理，抛出 MineruStillProcessingError 由 task-worker 裁决
    try {
      await waitMinerUTask(localEndpoint, mineruTaskId, timeoutMs, async (statusPayload) => {
        const status = String(statusPayload?.status || '').toLowerCase();
        const queuedAhead = statusPayload?.queued_ahead ?? statusPayload?.queue_ahead ?? 0;
        const startedAt = statusPayload?.started_at || null;

        let stage = 'mineru-processing';
        let msg = 'MinerU 正在解析';
        let progress = 50;
        let mineruStatus = 'processing';

        const isDone = ['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(status);
        if (isDone) return; // waitMinerUTask will handle done states

        if (status === 'pending' || status === 'queued' || (!startedAt && status !== 'processing') || queuedAhead > 0) {
          stage = 'mineru-queued';
          msg = `MinerU 排队中 (前方 ${queuedAhead} 个任务)`;
          progress = 20;
          mineruStatus = 'queued';
        }

        // Sidecar log parsing
        let observation = null;
        if (mineruStatus === 'processing') {
          try {
            const { parseLatestMineruProgress } = await import('../lib/ops-mineru-log-parser.mjs');
            observation = await parseLatestMineruProgress(
               startedAt || task.metadata?.mineruStartedAt || new Date().toISOString(),
               task.metadata?.mineruObservedProgress,
               executionProfile
            );
          } catch (e) {
            // ignore
          }
        }

        await updateProgress({
          stage,
          state: 'running',
          progress,
          message: msg,
          metadata: {
            ...(task.metadata || {}),
            mineruTaskId,
            mineruStatus,
            mineruQueuedAhead: queuedAhead,
            mineruStartedAt: startedAt,
            mineruLastStatusAt: new Date().toISOString(),
            mineruExecutionProfile: executionProfile,
            ...(observation ? { mineruObservedProgress: observation } : {}),
            ...(statusPayload._synthetic_warn ? { _synthetic_warn: statusPayload._synthetic_warn, _synthetic_warn_msg: statusPayload._synthetic_warn_msg } : {})
          }
        });
      });
    } catch (pollErr) {
      if (pollErr instanceof MineruTimeoutError) {
        // 本地等待超时：查询一次 MinerU 确认最新状态
        try {
          const checkRes = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(mineruTaskId)}`, { signal: AbortSignal.timeout(5000) });
          if (checkRes.ok) {
            const checkData = await checkRes.json();
            const actualStatus = String(checkData?.status || '').toLowerCase();
            if (['queued', 'pending', 'processing', 'running'].includes(actualStatus)) {
              throw new MineruStillProcessingError(mineruTaskId, actualStatus);
            }
            if (['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(actualStatus)) {
              // MinerU 已经完成了，继续执行结果拉取
              // 不抛异常，跳出 catch 继续后续逻辑
            } else {
              // MinerU 返回了明确失败
              throw new Error(`MinerU API 明确返回 ${actualStatus}: ${checkData?.error || checkData?.message || 'MinerU 任务失败'}`);
            }
          } else {
            throw new MineruStillProcessingError(mineruTaskId, pollErr.lastKnownStatus || 'unknown');
          }
        } catch (confirmErr) {
          if (confirmErr instanceof MineruStillProcessingError) throw confirmErr;
          if (confirmErr.message?.includes('MinerU API 明确返回')) throw confirmErr;
          // 网络不可达等：保守处理，保持 running
          throw new MineruStillProcessingError(mineruTaskId, pollErr.lastKnownStatus || 'unknown');
        }
      } else {
        throw pollErr;
      }
    }

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

    const realArtifacts2 = parsedArtifacts.filter((a) => {
      const rp = String(a.relativePath || '');
      if (rp === 'full.md') return false;
      if (rp === 'mineru-result.json') return false;
      if (rp === 'mineru-result.zip') return false;
      return true;
    });

    const artifactIncomplete2 = realArtifacts2.length === 0;

    // completed-empty 语义：MinerU 已完成但 Markdown 为空
    // 不抛异常，返回结构化结果供 task-worker 判断
    if (!markdown) {
      return {
        markdown: '',
        markdownEmpty: true,
        mineruTaskId,
        objectName: fullMdObjectName,
        parsedPrefix,
        parsedFilesCount: parsedArtifacts.length,
        parsedArtifacts,
        zipObjectName: hasMineruZip ? zipObjectName : null,
        artifactIncomplete: artifactIncomplete2,
      };
    }

    await updateProgress({ stage: 'store', state: 'result-store', progress: 90, message: '正在保存产物到 MinIO...' });

    await minioContext.saveMarkdown(fullMdObjectName, markdown);
    pushArtifact('full.md', fullMdObjectName, Buffer.byteLength(markdown, 'utf-8'), 'text/markdown');

    return {
      markdown,
      mineruTaskId,
      objectName: fullMdObjectName,
      parsedPrefix,
      parsedFilesCount: parsedArtifacts.length,
      parsedArtifacts,
      zipObjectName: hasMineruZip ? zipObjectName : null,
      artifactIncomplete: artifactIncomplete2,
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

/**
 * 从给定的 MinerU 任务 ID 无缝恢复任务执行，不再重复上传文件。
 *
 * @param {Object} params 参数对象
 * @param {Object} params.task 当前执行的 Luceon 任务对象
 * @param {Object} params.material 当前任务关联的资料对象
 * @param {string} params.mineruTaskId 要恢复的 MinerU 内部任务 ID
 * @param {number} params.timeoutMs 轮询与等待超时时间（毫秒）
 * @param {Object} params.minioContext MinIO 上下文客户端，用于产物存储
 * @param {Function} params.updateProgress 进度与状态回调函数
 * @returns {Promise<Object>} 包含 markdown 文本和解析产物元数据的对象
 */
export async function resumeWithLocalMinerU({ task, material, mineruTaskId, timeoutMs, minioContext, updateProgress }) {
  const options = task.optionsSnapshot || {};
  let localEndpoint = options.localEndpoint;
  if (!localEndpoint) throw new Error('缺少 localEndpoint');

  // docker 内部网络地址重写
  if (localEndpoint.includes('localhost') || localEndpoint.includes('127.0.0.1')) {
    localEndpoint = localEndpoint.replace(/localhost|127\.0\.0\.1/g, 'host.docker.internal');
  }
  localEndpoint = localEndpoint.replace(/\/+$/, '');

  const responseFormatZip = (options.responseFormatZip ?? options.response_format_zip) == null
    ? true
    : isEnabledFlag(options.responseFormatZip ?? options.response_format_zip);

  const isHealthy = await checkHealth(localEndpoint);
  if (!isHealthy) throw new Error(`本地 MinerU 不可达: ${localEndpoint}`);

  await updateProgress({ message: `恢复排队/处理中的任务: ${mineruTaskId}` });
  let markdown = '';

  await waitMinerUTask(localEndpoint, mineruTaskId, timeoutMs, async (statusPayload) => {
    const status = String(statusPayload?.status || '').toLowerCase();
    const queuedAhead = statusPayload?.queued_ahead ?? statusPayload?.queue_ahead ?? 0;
    const startedAt = statusPayload?.started_at || null;

    let stage = 'mineru-processing';
    let msg = 'MinerU 正在解析';
    let progress = 50;
    let mineruStatus = 'processing';

    const isDone = ['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(status);
    if (isDone) return; 

    if (status === 'pending' || status === 'queued' || (!startedAt && status !== 'processing') || queuedAhead > 0) {
      stage = 'mineru-queued';
      msg = `MinerU 排队中 (前方 ${queuedAhead} 个任务)`;
      progress = 20;
      mineruStatus = 'queued';
    }

    // Sidecar log parsing
    let observation = null;
    if (mineruStatus === 'processing') {
      try {
        const { parseLatestMineruProgress } = await import('../lib/ops-mineru-log-parser.mjs');
        observation = await parseLatestMineruProgress(
           startedAt || task.metadata?.mineruStartedAt || new Date().toISOString(),
           task.metadata?.mineruObservedProgress,
           task.metadata?.mineruExecutionProfile
        );
      } catch (e) {
        // ignore
      }
    }

    await updateProgress({
      stage,
      state: 'running',
      progress,
      message: msg,
      metadata: {
        ...(task.metadata || {}),
        mineruTaskId,
        mineruStatus,
        mineruQueuedAhead: queuedAhead,
        mineruStartedAt: startedAt,
        mineruLastStatusAt: new Date().toISOString(),
        ...(observation ? { mineruObservedProgress: observation } : {})
      }
    });
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

  const realArtifacts2 = parsedArtifacts.filter((a) => {
    const rp = String(a.relativePath || '');
    if (rp === 'full.md') return false;
    if (rp === 'mineru-result.json') return false;
    if (rp === 'mineru-result.zip') return false;
    return true;
  });

  const artifactIncomplete2 = realArtifacts2.length === 0;

  // completed-empty 语义：MinerU 已完成但 Markdown 为空
  if (!markdown) {
    return {
      markdown: '',
      markdownEmpty: true,
      mineruTaskId,
      objectName: fullMdObjectName,
      parsedPrefix,
      parsedFilesCount: parsedArtifacts.length,
      parsedArtifacts,
      zipObjectName: hasMineruZip ? zipObjectName : null,
      artifactIncomplete: artifactIncomplete2,
    };
  }

  await updateProgress({ stage: 'store', state: 'result-store', progress: 90, message: '正在保存产物到 MinIO...' });

  await minioContext.saveMarkdown(fullMdObjectName, markdown);
  pushArtifact('full.md', fullMdObjectName, Buffer.byteLength(markdown, 'utf-8'), 'text/markdown');

  return {
    markdown,
    mineruTaskId,
    objectName: fullMdObjectName,
    parsedPrefix,
    parsedFilesCount: parsedArtifacts.length,
    parsedArtifacts,
    zipObjectName: hasMineruZip ? zipObjectName : null,
    artifactIncomplete: artifactIncomplete2,
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
  let lastKnownStatus = 'unknown';
  let lastPayload = { status: 'processing' };

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${localEndpoint}/tasks/${encodeURIComponent(taskId)}`, { signal: AbortSignal.timeout(10000) });
      const payload = await response.json();
      if (!response.ok) throw new Error(`查询状态失败: HTTP ${response.status}`);
      
      lastPayload = payload;
      const status = String(payload?.status || payload?.state || payload?.task_status || payload?.data?.status || payload?.data?.state).toLowerCase();
      lastKnownStatus = status;

      if (onProgress) await onProgress(payload);
      
      if (['done', 'success', 'completed', 'succeeded', 'finished', 'complete'].includes(status)) return payload;
      if (['failed', 'error', 'failure', 'canceled', 'cancelled'].includes(status)) throw new Error(payload?.error || payload?.message || '任务执行失败');
    } catch (err) {
      if (err.name === 'AbortError' || err.name === 'TimeoutError' || String(err.message).includes('fetch failed')) {
        // Option A: Catch aborts and continue, using last known payload
        if (onProgress) {
          await onProgress({ ...lastPayload, _synthetic_warn: 'mineru-status-query-timeout', _synthetic_warn_msg: err.message });
        }
      } else {
        throw err;
      }
    }
    
    await new Promise(r => setTimeout(r, 1500));
  }
  throw new MineruTimeoutError(taskId, lastKnownStatus, timeoutMs);
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
