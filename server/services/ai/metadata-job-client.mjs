/**
 * metadata-job-client.mjs — AI 元数据任务客户端
 *
 * 封装对 db-server /ai-metadata-jobs 端点的调用，负责创建和查询 AiMetadataJob。
 * 包含去重逻辑：若同一 parseTaskId 已有 pending/running/succeeded 状态的 job，不重复创建。
 */

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';

/**
 * 获取所有 AI Metadata Jobs 列表
 * @returns {Promise<Array>} AI Metadata Jobs 列表
 */
export async function getAllJobs() {
  try {
    const resp = await fetch(`${DB_BASE_URL}/ai-metadata-jobs`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (error) {
    console.error(`[metadata-job-client] getAllJobs failed: ${error.message}`);
    return [];
  }
}

/**
 * 按 parseTaskId 查询已有的 AI Metadata Jobs 列表
 * @param {string} parseTaskId - 关联的 ParseTask ID
 * @returns {Promise<Array>} AI Metadata Jobs 列表，查询失败返回空数组
 */
export async function getJobsByParseTaskId(parseTaskId) {
  try {
    const resp = await fetch(
      `${DB_BASE_URL}/ai-metadata-jobs?parseTaskId=${encodeURIComponent(parseTaskId)}`
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (error) {
    console.error(`[metadata-job-client] getJobsByParseTaskId failed: ${error.message}`);
    return [];
  }
}

/**
 * 按 ID 查询单个 AI Metadata Job
 * @param {string} jobId - AI Metadata Job ID
 * @returns {Promise<object|null>} Job 对象或 null
 */
export async function getJobById(jobId) {
  try {
    const resp = await fetch(
      `${DB_BASE_URL}/ai-metadata-jobs/${encodeURIComponent(jobId)}`
    );
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (error) {
    console.error(`[metadata-job-client] getJobById failed: ${error.message}`);
    return null;
  }
}

/**
 * 更新一个 AI Metadata Job 的部分字段
 * @param {string} jobId - AI Metadata Job ID
 * @param {object} updateData - 要更新的字段
 * @returns {Promise<boolean>} 是否成功
 */
export async function updateJob(jobId, updateData) {
  try {
    const resp = await fetch(
      `${DB_BASE_URL}/ai-metadata-jobs/${encodeURIComponent(jobId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updateData),
      }
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return true;
  } catch (error) {
    console.error(`[metadata-job-client] updateJob failed: ${error.message}`);
    return false;
  }
}

/**
 * 为完成的 ParseTask 创建 AI Metadata Job（含去重保护）
 *
 * 去重规则：如果同一 parseTaskId 已有 pending / running / succeeded 状态的 job，跳过创建。
 *
 * @param {object} params - 创建参数
 * @param {string} params.parseTaskId - 关联的 ParseTask ID
 * @param {string} params.materialId - 关联的 Material ID
 * @param {string} [params.inputMarkdownObjectName] - Markdown 解析产物的 MinIO objectName
 * @returns {Promise<{created: boolean, jobId: string|null, reason: string}>} 创建结果
 */
export async function createAiMetadataJob({ parseTaskId, materialId, inputMarkdownObjectName }) {
  // ── 去重检查 ────────────────────────────────────────────────
  const NON_DUPLICATE_STATES = new Set(['pending', 'running', 'succeeded']);
  try {
    const existingJobs = await getJobsByParseTaskId(parseTaskId);
    const activeJob = existingJobs.find((j) => NON_DUPLICATE_STATES.has(j.state));
    if (activeJob) {
      console.log(
        `[metadata-job-client] Skipped: parseTaskId=${parseTaskId} already has active job ${activeJob.id} (state=${activeJob.state})`
      );
      return { created: false, jobId: activeJob.id, reason: 'duplicate' };
    }
  } catch (error) {
    // 去重查询失败不阻塞创建，但记录警告
    console.warn(`[metadata-job-client] Dedup check failed, proceeding: ${error.message}`);
  }

  // ── 创建 Job ────────────────────────────────────────────────
  const jobId = `ai-job-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
  const jobData = {
    id: jobId,
    materialId: materialId || null,
    parseTaskId,
    state: 'pending',
    progress: 0,
    providerId: null,
    model: null,
    inputMarkdownObjectName: inputMarkdownObjectName || null,
    confidence: null,
    needsReview: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  try {
    const resp = await fetch(`${DB_BASE_URL}/ai-metadata-jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(jobData),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    console.log(
      `[metadata-job-client] Created AI Metadata Job: ${jobId} for parseTask=${parseTaskId}`
    );
    return { created: true, jobId, reason: 'ok' };
  } catch (error) {
    console.error(`[metadata-job-client] createAiMetadataJob failed: ${error.message}`);
    return { created: false, jobId: null, reason: error.message };
  }
}
