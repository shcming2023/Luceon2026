/**
 * consistency-routes.mjs — 一致性扫描与对象清理路由（PRD v0.4 §9 改造版）
 *
 * 改动要点（对比 v0.3 实现）：
 *  1. ID 比较一律按字符串严格相等，去掉 Number(parts[1]) 的隐式数字化；
 *  2. 新增状态与引用不变量扫描（§9.1 / §9.3），生成结构化修复建议；
 *  3. 默认"只扫描、只生成修复建议"，写动作需显式 apply=true 才会执行（§13.1 安全缓解）；
 *  4. 保留原有 /audit/orphans、/audit/cleanup-orphans、/check-orphaned-files、/repair-consistency
 *     作为对象存储清理入口，向后兼容前端已有调用；
 *  5. 新增 /audit/consistency、/audit/consistency/apply 作为统一入口。
 */

const DEFAULT_CANONICAL_TASK_STATES = new Set([
  'uploading',
  'pending',
  'running',
  'result-store',
  'ai-pending',
  'ai-running',
  'review-pending',
  'completed',
  'failed',
  'canceled',
]);

const DEFAULT_CANONICAL_AI_JOB_STATES = new Set([
  'pending',
  'running',
  'confirmed',
  'review-pending',
  'failed',
]);

export function registerConsistencyRoutes(app, deps) {
  const {
    DB_BASE_URL,
    getStorageBackend,
    getMinioBucket,
    getParsedBucket,
    listAllObjects,
    getMinioClient,
  } = deps;

  // ── util ─────────────────────────────────────────────────────
  function extractMaterialIdFromPath(objectName) {
    // 允许 originals/{id}/... 与 parsed/{id}/... 两种形式
    const parts = String(objectName || '').split('/');
    if (parts.length < 2) return null;
    const id = String(parts[1] || '').trim();
    if (!id) return null;
    // 字符串 ID 全匹配，不再数字化
    return id;
  }

  async function dbGet(path) {
    const resp = await fetch(`${DB_BASE_URL}${path}`, { signal: AbortSignal.timeout(10_000) });
    if (!resp.ok) throw new Error(`db-server ${path} 返回 ${resp.status}`);
    return resp.json();
  }

  // ── 对象孤儿扫描（保留，切换为字符串 ID）────────────────────
  async function scanOrphansInternal() {
    const dbMaterials = await dbGet('/materials');
    const knownIds = new Set(
      (Array.isArray(dbMaterials) ? dbMaterials : [])
        .map((m) => (m?.id != null ? String(m.id) : null))
        .filter(Boolean),
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
      if (!id || !knownIds.has(id)) {
        orphans.push({ bucket: rawBucket, objectName: obj.name, size: obj.size || 0, lastModified: obj.lastModified });
      }
    }
    for (const obj of parsedObjects) {
      const id = extractMaterialIdFromPath(obj.name);
      if (!id || !knownIds.has(id)) {
        orphans.push({ bucket: parsedBucket, objectName: obj.name, size: obj.size || 0, lastModified: obj.lastModified });
      }
    }

    const totalSize = orphans.reduce((sum, o) => sum + o.size, 0);
    return { orphans, totalCount: orphans.length, totalSize };
  }

  // ── 新增：状态与引用不变量扫描（PRD v0.4 §9.1 / §9.3）────
  /**
   * 扫描返回的是结构化"发现 + 建议修复动作"的清单，apply=true 时才执行修复。
   * 每项：{ kind, severity, targetType, targetId, message, suggestion, repair?: { method, path, body } }
   */
  async function scanInvariantsInternal() {
    const findings = [];

    const [materials, tasks, aiJobs] = await Promise.all([
      dbGet('/materials').catch(() => []),
      dbGet('/tasks').catch(() => []),
      dbGet('/ai-metadata-jobs').catch(() => []),
    ]);

    const materialsById = new Map(
      (Array.isArray(materials) ? materials : []).map((m) => [String(m?.id ?? ''), m]),
    );
    const tasksById = new Map(
      (Array.isArray(tasks) ? tasks : []).map((t) => [String(t?.id ?? ''), t]),
    );
    const aiJobsById = new Map(
      (Array.isArray(aiJobs) ? aiJobs : []).map((j) => [String(j?.id ?? ''), j]),
    );

    // 1) Canonical 状态检查
    for (const t of tasksById.values()) {
      if (t?.state && !DEFAULT_CANONICAL_TASK_STATES.has(String(t.state))) {
        findings.push({
          kind: 'non-canonical-task-state',
          severity: 'warn',
          targetType: 'ParseTask',
          targetId: t.id,
          message: `ParseTask ${t.id} 使用了非 canonical 状态: ${t.state}`,
          suggestion: '按 PRD v0.4 §6.1 迁移该任务状态（success/succeeded/analyzed → completed 等）',
        });
      }
    }
    for (const j of aiJobsById.values()) {
      if (j?.state && !DEFAULT_CANONICAL_AI_JOB_STATES.has(String(j.state))) {
        findings.push({
          kind: 'non-canonical-ai-state',
          severity: 'warn',
          targetType: 'AiMetadataJob',
          targetId: j.id,
          message: `AiMetadataJob ${j.id} 使用了非 canonical 状态: ${j.state}`,
          suggestion: 'PRD v0.4 §7.3：统一为 pending/running/confirmed/review-pending/failed',
          repair: j.state === 'succeeded' ? {
            method: 'PATCH',
            path: `/ai-metadata-jobs/${encodeURIComponent(j.id)}`,
            body: { state: 'confirmed', message: '一致性扫描：succeeded → confirmed' },
          } : null,
        });
      }
    }

    // 2) ParseTask.materialId 必须对应 Material.id（字符串）
    for (const t of tasksById.values()) {
      if (!t?.materialId) continue;
      const mid = String(t.materialId);
      if (!materialsById.has(mid)) {
        const wasCompleted = t.state === 'completed' || t.state === 'review-pending';
        findings.push({
          kind: 'orphan-task',
          severity: 'error',
          targetType: 'ParseTask',
          targetId: t.id,
          message: `ParseTask ${t.id} 引用的 Material ${mid} 不存在`,
          suggestion: wasCompleted
            ? '已完成但原始资料已丢失：标记为 canceled + resourceLost（结果可查看但不可重跑）'
            : 'PRD v0.4 §9.1：将该任务置为 failed，并提示运行 Retry',
          // 已完成任务 Material 缺失 → canceled + resourceLost（保留结果可查看性，明确不可重跑）
          // 其他状态孤儿任务 → failed（可审计）或 DELETE（已 failed 的脏数据）
          repair: wasCompleted ? {
            method: 'PATCH',
            path: `/tasks/${encodeURIComponent(t.id)}`,
            body: {
              state: 'canceled',
              message: '一致性扫描：已完成任务关联的 Material 已删除，标记为资源缺失',
              metadata: { ...(t.metadata || {}), resourceLost: true },
            },
          } : t.state !== 'failed' ? {
            method: 'PATCH',
            path: `/tasks/${encodeURIComponent(t.id)}`,
            body: {
              state: 'failed',
              errorMessage: 'orphan-task: material missing',
              message: '一致性扫描：关联的 Material 不存在',
            },
          } : {
            method: 'DELETE',
            path: `/tasks/${encodeURIComponent(t.id)}`,
            reason: 'PRD §9.1 补充：清理已审计的孤儿任务',
          },
        });
      }
    }

    // 3) AiMetadataJob.parseTaskId 必须对应 ParseTask.id
    for (const j of aiJobsById.values()) {
      if (!j?.parseTaskId) continue;
      const tid = String(j.parseTaskId);
      if (!tasksById.has(tid)) {
        findings.push({
          kind: 'orphan-ai-job',
          severity: 'error',
          targetType: 'AiMetadataJob',
          targetId: j.id,
          message: `AiMetadataJob ${j.id} 引用的 ParseTask ${tid} 不存在`,
          suggestion: 'PRD v0.4 §9.1：将该 Job 置为 failed',
          repair: j.state !== 'failed' ? {
            method: 'PATCH',
            path: `/ai-metadata-jobs/${encodeURIComponent(j.id)}`,
            body: { state: 'failed', message: 'orphan-ai-job: parse task missing' },
          } : {
            method: 'DELETE',
            path: `/ai-metadata-jobs/${encodeURIComponent(j.id)}`,
            reason: 'PRD §9.1 补充：清理已审计的孤儿 Job',
          },
        });
      }
    }

    // 4) Material.metadata.aiJobId 必须对应真实 Job
    for (const m of materialsById.values()) {
      const aid = m?.metadata?.aiJobId;
      if (!aid) continue;
      if (!aiJobsById.has(String(aid))) {
        findings.push({
          kind: 'dangling-material-ai-job',
          severity: 'warn',
          targetType: 'Material',
          targetId: m.id,
          message: `Material ${m.id}.metadata.aiJobId=${aid} 在 ai-metadata-jobs 中不存在`,
          suggestion: 'PRD v0.4 §9.1：清空 aiJobId 并把 aiStatus 重置为 pending',
          repair: {
            method: 'PATCH',
            path: `/materials/${encodeURIComponent(m.id)}`,
            body: {
              aiStatus: 'pending',
              metadata: { ...(m.metadata || {}), aiJobId: null },
            },
          },
        });
      }
    }

    // 5) ParseTask.state=ai-running 必存在 pending/running 的 AI Job
    const jobsByParseTask = new Map();
    for (const j of aiJobsById.values()) {
      if (!j?.parseTaskId) continue;
      const k = String(j.parseTaskId);
      if (!jobsByParseTask.has(k)) jobsByParseTask.set(k, []);
      jobsByParseTask.get(k).push(j);
    }
    for (const t of tasksById.values()) {
      if (t?.state !== 'ai-running') continue;
      const linked = jobsByParseTask.get(String(t.id)) || [];
      const hasActive = linked.some((j) => j.state === 'pending' || j.state === 'running');
      if (!hasActive) {
        findings.push({
          kind: 'ai-running-without-active-job',
          severity: 'warn',
          targetType: 'ParseTask',
          targetId: t.id,
          message: `ParseTask ${t.id} 处于 ai-running 但无 pending/running 的关联 AI Job`,
          suggestion: 'PRD v0.4 §9.3：置回 ai-pending，交由 AI Worker 重新创建 Job',
          repair: {
            method: 'PATCH',
            path: `/tasks/${encodeURIComponent(t.id)}`,
            body: {
              state: 'ai-pending',
              message: '一致性扫描：ai-running 无活跃 AI Job，重置为 ai-pending',
            },
          },
        });
      }
    }

    // 6) 对象存储 key 前缀 (PRD §9.2)
    for (const m of materialsById.values()) {
      const objectName = m?.metadata?.objectName;
      if (objectName && !String(objectName).startsWith(`originals/${m.id}/`)) {
        findings.push({
          kind: 'bad-original-prefix',
          severity: 'info',
          targetType: 'Material',
          targetId: m.id,
          message: `Material ${m.id}.metadata.objectName=${objectName} 不以 originals/{id}/ 开头`,
          suggestion: 'PRD v0.4 §9.2：告警但不自动移动文件，等待人工确认',
        });
      }
      const mdObject = m?.metadata?.markdownObjectName;
      if (mdObject && !String(mdObject).startsWith(`parsed/${m.id}/`)) {
        findings.push({
          kind: 'bad-parsed-prefix',
          severity: 'info',
          targetType: 'Material',
          targetId: m.id,
          message: `Material ${m.id}.metadata.markdownObjectName=${mdObject} 不以 parsed/{id}/ 开头`,
          suggestion: 'PRD v0.4 §9.2：告警但不自动移动文件',
        });
      }
    }

    // 7) 物理文件存在性检查 (PRD §11.5)
    if (getStorageBackend() === 'minio') {
      const minio = getMinioClient();
      const bucket = getMinioBucket();
      const parsedBucket = getParsedBucket();

      for (const m of materialsById.values()) {
        const obj = m?.metadata?.objectName;
        if (obj) {
          try {
            await minio.statObject(bucket, obj);
          } catch (e) {
            // 查找该 Material 关联的已完成任务
            const completedTasks = Array.from(tasksById.values())
              .filter(t => String(t.materialId) === String(m.id) && (t.state === 'completed' || t.state === 'review-pending'));
            
            if (completedTasks.length > 0) {
              // 有关联的已完成任务 → 标记任务为 canceled + resourceLost（保留 AI 结果可查看性）
              for (const ct of completedTasks) {
                findings.push({
                  kind: 'original-file-missing-with-completed-task',
                  severity: 'error',
                  targetType: 'ParseTask',
                  targetId: ct.id,
                  message: `原始对象 ${obj} 在 MinIO 中不存在，但关联任务 ${ct.id} 已完成`,
                  suggestion: '标记任务为资源缺失（canceled + resourceLost），结果可查看但不可重跑',
                  repair: {
                    method: 'PATCH',
                    path: `/tasks/${encodeURIComponent(ct.id)}`,
                    body: {
                      state: 'canceled',
                      message: '一致性扫描：原始文件丢失，标记为资源缺失',
                      metadata: { ...(ct.metadata || {}), resourceLost: true },
                    },
                  },
                });
              }
            } else {
              // 无关联已完成任务 → 级联删除整个资料
              findings.push({
                kind: 'original-file-missing',
                severity: 'error',
                targetType: 'Material',
                targetId: m.id,
                message: `原始对象 ${obj} 在 MinIO 中不存在`,
                suggestion: 'PRD §11.5：该资料已失效，建议级联删除',
                repair: {
                  method: 'DELETE',
                  path: `/__proxy/upload/materials/${encodeURIComponent(m.id)}`,
                  reason: '一致性扫描：原始文件丢失',
                }
              });
            }
          }
        }
        
        const mdObj = m?.metadata?.markdownObjectName;
        if (mdObj) {
          try {
            await minio.statObject(parsedBucket, mdObj);
          } catch (e) {
            // 找到该 Material 对应的 ParseTask (若存在且为终态)
            const relatedTask = Array.from(tasksById.values()).find(t => String(t.materialId) === String(m.id) && t.state === 'completed');
            
            findings.push({
              kind: 'parsed-file-missing',
              severity: 'warn',
              targetType: 'Material',
              targetId: m.id,
              message: `解析产物 ${mdObj} 在 MinIO 中不存在`,
              suggestion: relatedTask ? '标记任务为资源缺失，或重解析（需原始文件存在）' : 'PRD §11.5：重置相关任务为 pending 重新解析',
              repair: relatedTask ? {
                method: 'PATCH',
                path: `/tasks/${encodeURIComponent(relatedTask.id)}`,
                body: {
                  state: 'canceled',
                  message: '一致性扫描：解析产物丢失，标记为资源缺失',
                  metadata: { ...(relatedTask.metadata || {}), resourceLost: true },
                },
              } : null
              } : null
            });
          }
        }
      }
    }

    return {
      findings,
      counters: {
        materials: materialsById.size,
        tasks: tasksById.size,
        aiJobs: aiJobsById.size,
        findings: findings.length,
      },
    };
  }

  async function applyRepairs(findings) {
    const results = [];
    for (const f of findings) {
      if (!f.repair) {
        results.push({ finding: f.kind, targetId: f.targetId, ok: false, skipped: 'no-repair' });
        continue;
      }
      try {
        const resp = await fetch(`${DB_BASE_URL}${f.repair.path}`, {
          method: f.repair.method || 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(f.repair.body || {}),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        results.push({ finding: f.kind, targetId: f.targetId, ok: true });
      } catch (err) {
        results.push({ finding: f.kind, targetId: f.targetId, ok: false, error: err.message });
      }
    }
    return results;
  }

  // ── 新增路由：统一一致性扫描 ────────────────────────────────
  app.get('/audit/consistency', async (_req, res) => {
    try {
      const report = await scanInvariantsInternal();
      res.json({ ok: true, ...report });
    } catch (err) {
      res.status(500).json({ ok: false, error: err.message });
    }
  });

  app.post('/audit/consistency/apply', async (req, res) => {
    try {
      const { findings: input } = req.body || {};
      let findings;
      if (Array.isArray(input) && input.length > 0) {
        findings = input;
      } else {
        findings = (await scanInvariantsInternal()).findings;
      }
      const results = await applyRepairs(findings);
      res.json({ ok: true, applied: results.filter((r) => r.ok).length, results });
    } catch (err) {
      res.status(500).json({ ok: false, error: err.message });
    }
  });

  // ── 保留：孤儿对象相关路由（字符串 ID 版） ────────────────
  app.get('/audit/orphans', async (_req, res) => {
    if (getStorageBackend() !== 'minio') {
      res.json({ ok: true, orphans: [], totalCount: 0, totalSize: 0, note: 'non-minio backend' });
      return;
    }
    try {
      const { orphans, totalCount, totalSize } = await scanOrphansInternal();
      res.json({ ok: true, orphans, totalCount, totalSize });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      res.status(500).json({ ok: false, error: message });
    }
  });

  app.post('/audit/cleanup-orphans', async (_req, res) => {
    if (getStorageBackend() !== 'minio') {
      res.status(400).json({ error: '孤儿对象清理仅支持 MinIO 存储后端' });
      return;
    }
    try {
      const { orphans } = await scanOrphansInternal();
      if (orphans.length === 0) {
        res.json({ ok: true, removed: 0, errors: [], totalSize: 0, note: '无孤儿对象' });
        return;
      }
      let removed = 0;
      let totalSize = 0;
      const errors = [];
      for (const orphan of orphans) {
        try {
          await getMinioClient().removeObject(orphan.bucket, orphan.objectName);
          removed += 1;
          totalSize += orphan.size;
        } catch (err) {
          errors.push({ objectName: orphan.objectName, error: err?.message || String(err) });
        }
      }
      res.json({ ok: true, removed, errors, totalSize });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      res.status(500).json({ ok: false, error: message });
    }
  });

  app.get('/check-orphaned-files', async (_req, res) => {
    if (getStorageBackend() !== 'minio') {
      res.json({ ok: true, orphaned: [], totalObjects: 0, validObjects: 0, note: 'non-minio backend' });
      return;
    }
    try {
      const { orphans, totalCount, totalSize } = await scanOrphansInternal();
      const orphaned = orphans.map((o) => ({
        name: o.objectName,
        size: o.size,
        bucket: o.bucket,
        lastModified: o.lastModified,
      }));
      res.json({
        ok: true,
        orphaned,
        orphans,
        totalCount,
        totalSize,
        totalObjects: totalCount,
        validObjects: 0,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      res.status(500).json({ ok: false, error: message });
    }
  });

  app.post('/repair-consistency', async (req, res) => {
    if (getStorageBackend() !== 'minio') {
      res.status(400).json({ error: '一致性修复仅支持 MinIO 存储后端' });
      return;
    }
    const requested = Array.isArray(req.body?.orphanedFiles) ? req.body.orphanedFiles : [];
    const requestedNames = new Set(
      requested.map((f) => String(f?.name || f?.objectName || '').trim()).filter(Boolean),
    );
    try {
      const { orphans } = await scanOrphansInternal();
      const targets = requestedNames.size > 0 ? orphans.filter((o) => requestedNames.has(o.objectName)) : orphans;
      if (targets.length === 0) {
        res.json({ ok: true, removed: 0, errors: [], totalSize: 0, note: '无可清理的孤儿对象' });
        return;
      }
      let removed = 0;
      let totalSize = 0;
      const errors = [];
      for (const orphan of targets) {
        try {
          await getMinioClient().removeObject(orphan.bucket, orphan.objectName);
          removed += 1;
          totalSize += orphan.size;
        } catch (err) {
          errors.push({ objectName: orphan.objectName, error: err?.message || String(err) });
        }
      }
      res.json({ ok: true, removed, errors, totalSize });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      res.status(500).json({ ok: false, error: message });
    }
  });
}
