export function registerConsistencyRoutes(app, deps) {
  const {
    DB_BASE_URL,
    getStorageBackend,
    getMinioBucket,
    getParsedBucket,
    listAllObjects,
    getMinioClient,
  } = deps;

  function extractMaterialIdFromPath(objectName) {
    const parts = String(objectName || '').split('/');
    if (parts.length < 2) return null;
    const id = Number(parts[1]);
    return Number.isFinite(id) && id > 0 ? id : null;
  }

  async function scanOrphansInternal() {
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
        orphans.push({ bucket: rawBucket, objectName: obj.name, size: obj.size || 0, lastModified: obj.lastModified });
      }
    }
    for (const obj of parsedObjects) {
      const id = extractMaterialIdFromPath(obj.name);
      if (id === null || !knownIds.has(id)) {
        orphans.push({ bucket: parsedBucket, objectName: obj.name, size: obj.size || 0, lastModified: obj.lastModified });
      }
    }

    const totalSize = orphans.reduce((sum, o) => sum + o.size, 0);
    return { orphans, totalCount: orphans.length, totalSize };
  }

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

