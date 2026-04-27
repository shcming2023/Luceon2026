console.log("Starting db-server.mjs");
/**
 * db-server.mjs — 持久化 REST API（JSON 文件存储）
 *
 * 端口：8789（通过 DB_PORT 环境变量覆盖）
 * 数据文件：server/db-data.json（本地开发）/ /data/db-data.json（Docker）
 *
 * 与原 SQLite 版本保持完全相同的 REST API 接口，
 * 底层改为 JSON 文件，避免 better-sqlite3 原生模块编译问题。
 */

import express from 'express';
import cors from 'cors';
import { readFileSync, writeFileSync, renameSync, mkdirSync, existsSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
const port = Number(process.env.DB_PORT || 8789);

// 数据文件路径：优先 /data（Docker volume），否则 server/ 目录
const DATA_PATH = (() => {
  if (process.env.DB_PATH) return process.env.DB_PATH;
  try {
    mkdirSync('/data', { recursive: true });
    return '/data/db-data.json';
  } catch {
    return path.join(__dirname, 'db-data.json');
  }
})();

const SECRETS_PATH = (() => {
  if (process.env.SECRETS_PATH) return process.env.SECRETS_PATH;
  const dir = path.dirname(DATA_PATH);
  return path.join(dir, 'secrets.json');
})();

// ─── CORS 配置 ────────────────────────────────────────────────
// 生产部署时通过 CORS_ORIGIN 环境变量指定允许的来源（逗号分隔）
const CORS_ORIGIN_RAW = process.env.CORS_ORIGIN || '';
const ALLOWED_CORS_ORIGINS = CORS_ORIGIN_RAW
  ? CORS_ORIGIN_RAW.split(',').map((s) => s.trim()).filter(Boolean)
  : [];

app.use(cors(ALLOWED_CORS_ORIGINS.length > 0 ? {
  origin: (origin, callback) => {
    if (!origin) return callback(null, true);
    if (ALLOWED_CORS_ORIGINS.includes(origin)) return callback(null, true);
    return callback(new Error(`CORS: origin ${origin} not allowed`));
  },
  credentials: true,
} : undefined));
app.use(express.json({ limit: '20mb' }));

function redactSensitive(value) {
  if (Array.isArray(value)) return value.map(redactSensitive);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        /apiKey|accessKey|secretKey|token|authorization/i.test(key)
          ? '<redacted>'
          : redactSensitive(item),
      ]),
    );
  }
  return value;
}

// 请求日志（方便排查）
app.use((req, _res, next) => {
  if (req.method !== 'GET') {
    console.log(`[db-server] ${req.method} ${req.path}`, (JSON.stringify(redactSensitive(req.body)) ?? '').slice(0, 200));
  }
  next();
});

// ─── JSON 文件读写 ─────────────────────────────────────────────

// P0 Patch 2: DB 运行期大 payload 防御，防止 metadata.parsedArtifacts 进入 DB 事实源
function sanitizeDbPayload(obj) {
  if (!obj) return obj;
  
  // 1. 清理顶层
  if (Array.isArray(obj.parsedArtifacts)) {
    obj.parsedFilesCount = obj.parsedFilesCount || obj.parsedArtifacts.length;
    delete obj.parsedArtifacts;
  } else if ('parsedArtifacts' in obj) {
    delete obj.parsedArtifacts;
  }
  
  // 2. 清理 metadata 层
  if (obj.metadata && typeof obj.metadata === 'object') {
    if (Array.isArray(obj.metadata.parsedArtifacts)) {
      obj.metadata.parsedFilesCount = obj.metadata.parsedFilesCount || obj.metadata.parsedArtifacts.length;
      delete obj.metadata.parsedArtifacts;
    }
    // [关键防御] 如果 metadata.parsedArtifacts 明确被设置为 null/空/占位符（例如在 PATCH 中用来作为删除标记）
    // 或者即使是旧数据残留，只要经过写入防御，一律剔除
    else if ('parsedArtifacts' in obj.metadata) {
      delete obj.metadata.parsedArtifacts;
    }
  }
  return obj;
}

const EMPTY_DB = {
  materials: {},       // id → Material
  assetDetails: {},    // id → AssetDetail
  processTasks: {},    // id → ProcessTask
  tasks: {},           // id → Task
  parseTasks: {},      // id → ParseTask
  aiMetadataJobs: {},  // id → AiMetadataJob
  taskEvents: {},      // id → TaskEvent
  products: {},        // id → Product
  flexibleTags: {},    // id → FlexibleTag
  aiRules: {},         // id → AiRule
  settings: {},        // key → value
};

// 模块级内存缓存，启动时一次性从磁盘加载（#6 消除全量读磁盘）
let dbCache = (() => {
  try {
    if (!existsSync(DATA_PATH)) {
      // 尝试恢复备份
      const bakPath = DATA_PATH + '.bak';
      if (existsSync(bakPath)) {
        console.log(`[db-server] Main DB file missing, restoring from backup: ${bakPath}`);
        const raw = readFileSync(bakPath, 'utf-8');
        return { ...EMPTY_DB, ...JSON.parse(raw) };
      }
      console.log('[db-server] No data file found, starting with empty DB');
      return structuredClone(EMPTY_DB);
    }
    const raw = readFileSync(DATA_PATH, 'utf-8');
    if (!raw.trim()) throw new Error('Data file is empty');
    const parsed = JSON.parse(raw);
    return { ...EMPTY_DB, ...parsed };
  } catch (err) {
    console.error(`[db-server] CRITICAL: Failed to load DB from ${DATA_PATH}: ${err.message}`);
    // 如果主文件加载失败，尝试从备份恢复
    const bakPath = DATA_PATH + '.bak';
    if (existsSync(bakPath)) {
      try {
        console.log(`[db-server] Attempting recovery from backup: ${bakPath}`);
        const raw = readFileSync(bakPath, 'utf-8');
        return { ...EMPTY_DB, ...JSON.parse(raw) };
      } catch (bakErr) {
        console.error(`[db-server] CRITICAL: Backup recovery also failed: ${bakErr.message}`);
      }
    }
    // 强制退出，防止覆盖现有数据
    console.error('[db-server] Shutting down to prevent data loss due to corruption.');
    process.exit(1);
  }
})();

let secretsCache = (() => {
  try {
    if (!existsSync(SECRETS_PATH)) return {};
    const raw = readFileSync(SECRETS_PATH, 'utf-8');
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
})();

// ─── Health ───────────────────────────────────────────────────

// ─── writeDB debounce 计时器 ──────────────────────────────────
let writeTimer = null;
let maxWriteTimer = null;
let writeQueuedAt = 0;
let secretsWriteTimer = null;
let secretsMaxWriteTimer = null;
let secretsWriteQueuedAt = 0;
let writeChain = Promise.resolve();
const WRITE_DEBOUNCE_MS = 100;
const WRITE_MAX_WAIT_MS = 5000;

/**
 * 将内存缓存原子写入磁盘（debounce 100ms）
 * - dbCache 由各路由 handler 实时更新，GET 请求始终读内存，保证一致性
 * - 真正的磁盘 I/O 在 100ms 后触发，期间重复调用重置计时器
 * - 磁盘错误在回调内 console.error 记录，不向请求方返回 500
 */
function enqueueWrite(task) {
  writeChain = writeChain
    .then(() => task())
    .catch((e) => console.error('[db-server] queued write failed:', e?.message || String(e)));
}

function atomicWriteJson(filePath, payload) {
  const dir = path.dirname(filePath);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const tmpPath = filePath + '.tmp';
  const bakPath = filePath + '.bak';
  
  // 1. 写入临时文件
  const content = JSON.stringify(payload, null, 2);
  writeFileSync(tmpPath, content, 'utf-8');
  
  // 2. 如果主文件存在，先创建备份
  if (existsSync(filePath)) {
    try {
      // 简单拷贝作为备份
      const current = readFileSync(filePath);
      if (current.length > 0) {
        writeFileSync(bakPath, current);
      }
    } catch (e) {
      console.warn('[db-server] Failed to create backup:', e.message);
    }
  }
  
  // 3. 原子更名
  renameSync(tmpPath, filePath);
}

function flushDB() {
  if (writeTimer) clearTimeout(writeTimer);
  if (maxWriteTimer) clearTimeout(maxWriteTimer);
  writeTimer = null;
  maxWriteTimer = null;
  writeQueuedAt = 0;
  enqueueWrite(() => {
    try {
      atomicWriteJson(DATA_PATH, dbCache);
    } catch (e) {
      console.error('[db-server] writeDB flush failed:', e.message);
    }
  });
}

function writeDB() {
  const now = Date.now();
  if (!writeQueuedAt) {
    writeQueuedAt = now;
    maxWriteTimer = setTimeout(flushDB, WRITE_MAX_WAIT_MS);
  }
  if (writeTimer) clearTimeout(writeTimer);
  const remaining = Math.max(0, WRITE_MAX_WAIT_MS - (now - writeQueuedAt));
  writeTimer = setTimeout(flushDB, Math.min(WRITE_DEBOUNCE_MS, remaining));
}

function flushDBSync() {
  if (writeTimer) clearTimeout(writeTimer);
  if (maxWriteTimer) clearTimeout(maxWriteTimer);
  writeTimer = null;
  maxWriteTimer = null;
  writeQueuedAt = 0;
  try {
    atomicWriteJson(DATA_PATH, dbCache);
    console.log('[db-server] flushDBSync: Data written to disk');
  } catch (e) {
    console.error('[db-server] flushDBSync failed:', e.message);
  }
}

function flushSecretsSync() {
  if (secretsWriteTimer) clearTimeout(secretsWriteTimer);
  if (secretsMaxWriteTimer) clearTimeout(secretsMaxWriteTimer);
  secretsWriteTimer = null;
  secretsMaxWriteTimer = null;
  secretsWriteQueuedAt = 0;
  try {
    atomicWriteJson(SECRETS_PATH, secretsCache);
    console.log('[db-server] flushSecretsSync: Secrets written to disk');
  } catch (e) {
    console.error('[db-server] flushSecretsSync failed:', e.message);
  }
}


function flushSecrets() {
  if (secretsWriteTimer) clearTimeout(secretsWriteTimer);
  if (secretsMaxWriteTimer) clearTimeout(secretsMaxWriteTimer);
  secretsWriteTimer = null;
  secretsMaxWriteTimer = null;
  secretsWriteQueuedAt = 0;
  enqueueWrite(() => {
    try {
      atomicWriteJson(SECRETS_PATH, secretsCache);
    } catch (e) {
      console.error('[db-server] secrets flush failed:', e.message);
    }
  });
}

function writeSecrets() {
  const now = Date.now();
  if (!secretsWriteQueuedAt) {
    secretsWriteQueuedAt = now;
    secretsMaxWriteTimer = setTimeout(flushSecrets, WRITE_MAX_WAIT_MS);
  }
  if (secretsWriteTimer) clearTimeout(secretsWriteTimer);
  const remaining = Math.max(0, WRITE_MAX_WAIT_MS - (now - secretsWriteQueuedAt));
  secretsWriteTimer = setTimeout(flushSecrets, Math.min(WRITE_DEBOUNCE_MS, remaining));
}

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'db-server', dataPath: DATA_PATH, secretsPath: SECRETS_PATH });
});

app.get('/stats', (_req, res) => {
  const materials = Object.values(dbCache.materials);
  const materialsByStatus = {};
  const materialsBySubject = {};
  let materialsTotalSizeBytes = 0;

  for (const material of materials) {
    const status = material?.status || 'unknown';
    const subject = material?.metadata?.subject || '未标注';
    materialsByStatus[status] = (materialsByStatus[status] || 0) + 1;
    materialsBySubject[subject] = (materialsBySubject[subject] || 0) + 1;
    materialsTotalSizeBytes += Number(material?.sizeBytes || 0);
  }

  res.json({
    ok: true,
    dataPath: DATA_PATH,
    fileSize: Buffer.byteLength(JSON.stringify(dbCache, null, 2), 'utf-8'),
    materialsTotalSizeBytes,
    materialsByStatus,
    materialsBySubject,
    counts: {
      materials: Object.keys(dbCache.materials).length,
      assetDetails: Object.keys(dbCache.assetDetails).length,
      processTasks: Object.keys(dbCache.processTasks).length,
      tasks: Object.keys(dbCache.tasks).length,
      parseTasks: Object.keys(dbCache.parseTasks).length,
      aiMetadataJobs: Object.keys(dbCache.aiMetadataJobs).length,
      taskEvents: Object.keys(dbCache.taskEvents).length,
      products: Object.keys(dbCache.products).length,
      flexibleTags: Object.keys(dbCache.flexibleTags).length,
      aiRules: Object.keys(dbCache.aiRules).length,
      settings: Object.keys(dbCache.settings).length,
    },
  });
});

// ─── 内网 Token 校验中间件 ────────────────────────────────────
// 通过 INTERNAL_API_TOKEN 环境变量配置共享密钥，proxy-server 转发时注入 X-Internal-Token 头
// 未配置时跳过检查（向后兼容，开发环境无需配置）
const INTERNAL_API_TOKEN = process.env.INTERNAL_API_TOKEN || '';

app.use((req, res, next) => {
  if (!INTERNAL_API_TOKEN) return next(); // 未配置则不校验
  const token = req.headers['x-internal-token'];
  if (token !== INTERNAL_API_TOKEN) {
    res.status(401).json({ error: '未授权：缺少或无效的内部访问令牌' });
    return;
  }
  next();
});

// ─── 输入验证工具 ─────────────────────────────────────────

/**
 * 基础请求体验证：确保写入操作的 body 是合法对象，防止脏数据污染内存缓存。
 * 不做字段级验证（避免破坏前端灵活性），仅拒绝明显非法的请求体。
 */
function requireBody(req, res, next) {
  if (!req.body || typeof req.body !== 'object' || Array.isArray(req.body)) {
    res.status(400).json({ error: '请求体必须是 JSON 对象' });
    return;
  }
  next();
}

app.get('/secrets', (_req, res) => {
  res.json({ ok: true, secrets: secretsCache });
});

const ALLOWED_SECRETS_KEYS = new Set([
  'aiKeys', 'mineruKey', 'minioCredentials',
  'aiConfig_apiKey', 'mineru_apiKey', 'minio_accessKey', 'minio_secretKey'
]);

app.put('/secrets', requireBody, (req, res) => {
  for (const key of Object.keys(req.body)) {
    const isAllowed = ALLOWED_SECRETS_KEYS.has(key) || /^aiProvider_.*_apiKey$/.test(key);
    if (!isAllowed) {
      res.status(400).json({ error: `secrets key "${key}" 不在允许列表内` });
      return;
    }
  }

  const nextSecrets = { ...secretsCache };
  for (const [key, val] of Object.entries(req.body)) {
    if (typeof val === 'string') nextSecrets[key] = val;
    else if (val === null) delete nextSecrets[key];
  }
  secretsCache = nextSecrets;
  writeSecrets();
  res.json({ ok: true });
});

/**
 * 防止原型链污染：递归检查对象中是否包含危险键名。
 * 比 JSON.stringify().includes() 更精确：
 * - 避免误拦截合法字段值（如 "constructor" 出现在字符串值中）
 * - 避免漏检深层嵌套中的危险键
 */
const DANGEROUS_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

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

// 对所有写入操作应用基础验证
app.use((req, res, next) => {
  if (['POST', 'PUT', 'PATCH'].includes(req.method)) {
    requireBody(req, res, () => rejectProtoPollution(req, res, next));
  } else {
    next();
  }
});

// ─── Materials ────────────────────────────────────────────────

app.get('/materials', (_req, res) => {
  // PRD v0.4 §9.1 · 10.4.1：Material.id 为字符串，不允许数字排序。
  // 根据 updateTime DESC，其次 createTime DESC，最后 fallback id 的字典序倒序。
  const list = Object.values(dbCache.materials).sort((a, b) => {
    const ua = Number(a?.updateTime || 0);
    const ub = Number(b?.updateTime || 0);
    if (ua !== ub) return ub - ua;
    const ca = Number(a?.createTime || 0);
    const cb = Number(b?.createTime || 0);
    if (ca !== cb) return cb - ca;
    return String(b?.id || '').localeCompare(String(a?.id || ''));
  });
  res.json(list);
});

app.get('/materials/:id', (req, res) => {
  const id = String(req.params.id);
  const item = dbCache.materials[id];
  if (!item) { res.status(404).json({ error: 'not found' }); return; }
  res.json(item);
});

app.post('/materials', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  const sid = String(item.id);
  dbCache.materials[sid] = item;
  sanitizeDbPayload(dbCache.materials[sid]);
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.put('/materials/:id', (req, res) => {
  const id = String(req.params.id);
  dbCache.materials[id] = { ...req.body, id: req.body.id ?? id };
  sanitizeDbPayload(dbCache.materials[id]);
  writeDB();
  res.json({ ok: true, id });
});

app.patch('/materials/:id', (req, res) => {
  const id = String(req.params.id);
  const existing = dbCache.materials[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  const merged = {
    ...existing,
    ...req.body,
    ...(req.body.metadata ? { metadata: { ...existing.metadata, ...req.body.metadata } } : {}),
  };
  sanitizeDbPayload(merged);
  dbCache.materials[id] = merged;
  writeDB();
  res.json({ ok: true, id, data: merged });
});

// DELETE /materials/:id — 删除单个 material
// MinIO cleanup is coordinated by the caller via POST /delete-material; db-server only deletes data rows.
app.delete('/materials/:id', (req, res) => {
  const id = String(req.params.id);
  const existing = dbCache.materials[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  delete dbCache.materials[id];
  delete dbCache.assetDetails[id]; // 联动删除
  writeDB();
  res.json({ ok: true, id });
});

// DELETE /materials — 清空/批量删除 materials
// MinIO cleanup is coordinated by the caller via POST /delete-material; db-server only deletes data rows.
app.delete('/materials', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) {
    const sid = String(id);
    delete dbCache.materials[sid];
    delete dbCache.assetDetails[sid]; // 联动删除
  }
  writeDB();
  res.json({ ok: true, deleted: ids.length });
});

// POST /materials/bulk-patch
// Body: { ids: number[], updates: Partial<Material> }
app.post('/materials/bulk-patch', (req, res) => {
  const { ids, updates } = req.body || {};
  if (!Array.isArray(ids) || ids.length === 0) {
    return res.status(400).json({ error: '缺少 ids 数组' });
  }
  if (!updates || typeof updates !== 'object') {
    return res.status(400).json({ error: '缺少 updates 对象' });
  }

  const updated = [];
  for (const id of ids) {
    const sid = String(id);
    if (dbCache.materials[sid]) {
      // 锁定 id，防止意外修改
      const updatesCopy = { ...updates };
      delete updatesCopy.id;

      // metadata 浅合并，避免覆盖已有字段
      if (updates.metadata && typeof updates.metadata === 'object' && dbCache.materials[sid].metadata) {
        dbCache.materials[sid] = {
          ...dbCache.materials[sid],
          ...updatesCopy,
          metadata: {
            ...dbCache.materials[sid].metadata,
            ...updates.metadata,
          },
        };
      } else {
        dbCache.materials[sid] = {
          ...dbCache.materials[sid],
          ...updatesCopy,
        };
      }
      sanitizeDbPayload(dbCache.materials[sid]);
      updated.push(id);
    }
  }
  writeDB();
  res.json({ ok: true, updated, count: updated.length });
});

// ─── Asset Details ────────────────────────────────────────────

app.get('/asset-details', (_req, res) => {
  res.json(dbCache.assetDetails);
});

app.get('/asset-details/:id', (req, res) => {
  const item = dbCache.assetDetails[req.params.id];
  if (!item) { res.status(404).json({ error: 'not found' }); return; }
  res.json(item);
});

app.put('/asset-details/:id', (req, res) => {
  const id = req.params.id;
  dbCache.assetDetails[id] = { ...req.body, id: req.body.id ?? id };
  writeDB();
  res.json({ ok: true, id });
});

// ─── Process Tasks ────────────────────────────────────────────

app.get('/process-tasks', (_req, res) => {
  const list = Object.values(dbCache.processTasks).sort((a, b) => b.id - a.id);
  res.json(list);
});

app.post('/process-tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.processTasks[item.id] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.patch('/process-tasks/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.processTasks[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  dbCache.processTasks[id] = { ...existing, ...req.body };
  writeDB();
  res.json({ ok: true, id });
});

app.delete('/process-tasks', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) delete dbCache.processTasks[id];
  writeDB();
  res.json({ ok: true, deleted: ids.length });
});

// ─── Parse Tasks (exposed as /tasks as per PRD) ───────────────

app.get('/tasks', (_req, res) => {
  const list = Object.values(dbCache.parseTasks).sort((a, b) => {
    // 降序排序，新任务在前
    if (a.createdAt && b.createdAt) return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
    return 0;
  });
  res.json(list);
});

app.get('/tasks/:id', (req, res) => {
  const id = req.params.id;
  const item = dbCache.parseTasks[id];
  if (!item) { res.status(404).json({ error: 'not found' }); return; }
  res.json(item);
});

app.post('/tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  const sid = String(item.id);
  const isNew = !dbCache.parseTasks[sid];

  // [P0 并发幂等控制] 仅针对新任务创建
  if (isNew && item.materialId) {
    const activeStates = new Set(['pending', 'running', 'ai-pending', 'ai-running', 'review-pending']);
    const existingActiveTask = Object.values(dbCache.parseTasks).find(t => 
      String(t.materialId) === String(item.materialId) && activeStates.has(t.state)
    );

    if (existingActiveTask) {
      console.warn(`[db-server] 并发冲突拒绝：Material ${item.materialId} 已有活跃任务 ${existingActiveTask.id}`);
      return res.status(409).json({ 
        error: 'TASK_ALREADY_ACTIVE', 
        existingTaskId: existingActiveTask.id,
        state: existingActiveTask.state 
      });
    }
  }

  dbCache.parseTasks[sid] = {
    createdAt: new Date().toISOString(),
    ...item
  };
  sanitizeDbPayload(dbCache.parseTasks[sid]);
  writeDB();
  
  if (isNew) {
    // 记录一条创建事件
    const eventId = `evt-${Date.now()}`;
    dbCache.taskEvents[eventId] = {
      id: eventId,
      taskId: sid,
      taskType: 'parse',
      level: 'info',
      event: 'created',
      message: '解析任务已由 upload-server 创建，正进入处理队列',
      createdAt: new Date().toISOString()
    };
  } else {
    // 如果是覆盖场景，记一个 updated 事件（可选）
    const eventId = `evt-${Date.now()}-upd`;
    dbCache.taskEvents[eventId] = {
      id: eventId,
      taskId: sid,
      taskType: 'parse',
      level: 'info',
      event: 'updated',
      message: '解析任务配置已更新（Upsert 覆盖）',
      createdAt: new Date().toISOString()
    };
  }
  
  res.json({ ok: true, id: item.id });
});

app.patch('/tasks/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.parseTasks[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  
  let mergedMetadata = { ...(existing.metadata || {}) };
  if (req.body?.metadata) {
    mergedMetadata = { ...mergedMetadata, ...req.body.metadata };
    // Field-level merge for mineruExecutionProfile to prevent rollback of backendEffective
    if (existing.metadata?.mineruExecutionProfile && req.body.metadata.mineruExecutionProfile) {
      const incomingProfile = req.body.metadata.mineruExecutionProfile;
      const existingProfile = existing.metadata.mineruExecutionProfile;
      
      const mergedProfile = { ...existingProfile, ...incomingProfile };
      // Protect specific keys from being overwritten by null/undefined/empty string
      const protectedKeys = [
        'backendEffective', 'backendEffectiveReason', 'backendRequested',
        'parseMethod', 'enableOcr', 'enableFormula', 'enableTable',
        'ocrLanguage', 'maxPages'
      ];
      for (const key of protectedKeys) {
        if (incomingProfile[key] === null || incomingProfile[key] === undefined || incomingProfile[key] === '') {
          mergedProfile[key] = existingProfile[key]; // Restore existing
        }
      }
      mergedMetadata.mineruExecutionProfile = mergedProfile;
    }
  }

  const merged = {
    ...existing,
    ...req.body,
    metadata: mergedMetadata,
    updatedAt: new Date().toISOString(),
  };
  sanitizeDbPayload(merged);
  dbCache.parseTasks[id] = merged;
  writeDB();
  res.json({ ok: true, id });
});

app.delete('/tasks', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) delete dbCache.parseTasks[id];
  writeDB();
  res.json({ ok: true, deleted: ids.length });
});

app.get('/task-events', (req, res) => {
  const taskId = req.query.taskId;
  const list = Object.values(dbCache.taskEvents);
  if (taskId) {
    res.json(list.filter(e => String(e.taskId) === String(taskId)).sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()));
  } else {
    res.json(list);
  }
});

app.post('/task-events', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.taskEvents[item.id] = {
    createdAt: new Date().toISOString(),
    ...item
  };
  writeDB();
  res.json({ ok: true, id: item.id });
});

/**
 * 删除单个 taskEvent（P0 OOM Patch: 支持运维清理和自动化测试）
 * @route DELETE /task-events/:id
 * @param {string} id - 事件 ID
 * @returns {{ ok: boolean }} 删除结果
 */
app.delete('/task-events/:id', (req, res) => {
  const id = decodeURIComponent(req.params.id);
  if (!dbCache.taskEvents[id]) {
    res.status(404).json({ error: 'Not found' });
    return;
  }
  delete dbCache.taskEvents[id];
  writeDB();
  res.json({ ok: true });
});

/**
 * 批量删除 taskEvents（P0 OOM Patch: 支持运维压缩操作）
 * @route DELETE /task-events
 * @body {{ ids?: string[], taskId?: string }} - 按 id 列表或 taskId 删除
 * @returns {{ ok: boolean, deleted: number }} 删除结果
 */
app.delete('/task-events', (req, res) => {
  const { ids, taskId } = req.body || {};
  let deleted = 0;
  if (Array.isArray(ids)) {
    for (const id of ids) {
      if (dbCache.taskEvents[id]) {
        delete dbCache.taskEvents[id];
        deleted++;
      }
    }
  } else if (taskId) {
    for (const [id, e] of Object.entries(dbCache.taskEvents)) {
      if (String(e.taskId) === String(taskId)) {
        delete dbCache.taskEvents[id];
        deleted++;
      }
    }
  }
  if (deleted > 0) writeDB();
  res.json({ ok: true, deleted });
});

// ─── AI Metadata Jobs ─────────────────────────────────────────

app.get('/ai-metadata-jobs', (req, res) => {
  const parseTaskId = req.query.parseTaskId;
  const list = Object.values(dbCache.aiMetadataJobs);
  if (parseTaskId) {
    res.json(list.filter(j => String(j.parseTaskId) === String(parseTaskId)));
  } else {
    res.json(list);
  }
});

app.get('/ai-metadata-jobs/:id', (req, res) => {
  const id = req.params.id;
  const item = dbCache.aiMetadataJobs[id];
  if (!item) { res.status(404).json({ error: 'not found' }); return; }
  res.json(item);
});

app.post('/ai-metadata-jobs', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.aiMetadataJobs[item.id] = {
    createdAt: new Date().toISOString(),
    ...item
  };
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.patch('/ai-metadata-jobs/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.aiMetadataJobs[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  dbCache.aiMetadataJobs[id] = { ...existing, ...req.body };
  writeDB();
  res.json({ ok: true, id });
});

// ─── Products ─────────────────────────────────────────────────

app.get('/products', (_req, res) => {
  const list = Object.values(dbCache.products).sort((a, b) => b.id - a.id);
  res.json(list);
});

app.post('/products', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.products[item.id] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.delete('/products', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) delete dbCache.products[id];
  writeDB();
  res.json({ ok: true, deleted: ids.length });
});

// ─── Flexible Tags ────────────────────────────────────────────

app.get('/flexible-tags', (_req, res) => {
  res.json(Object.values(dbCache.flexibleTags).sort((a, b) => a.id - b.id));
});

app.post('/flexible-tags', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.flexibleTags[item.id] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.delete('/flexible-tags', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) delete dbCache.flexibleTags[id];
  writeDB();
  res.json({ ok: true, deleted: ids.length });
});

// ─── AI Rules ─────────────────────────────────────────────────

app.get('/ai-rules', (_req, res) => {
  res.json(Object.values(dbCache.aiRules).sort((a, b) => a.id - b.id));
});

app.post('/ai-rules', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.aiRules[item.id] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.patch('/ai-rules/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.aiRules[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  dbCache.aiRules[id] = { ...existing, ...req.body };
  writeDB();
  res.json({ ok: true, id });
});

app.delete('/ai-rules', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) delete dbCache.aiRules[id];
  writeDB();
  res.json({ ok: true, deleted: ids.length });
});

// ─── Settings ─────────────────────────────────────────────────

// settings key 白名单：只允许已知业务 key 写入，防止任意键污染
const ALLOWED_SETTINGS_KEYS = new Set([
  'aiConfig', 'aiRuleSettings', 'mineruConfig', 'minioConfig',
  'uiPreferences', 'systemConfig', 'backupConfig',
  'initialized',
  'batchProcessing',
  'batchProcessingUpdatedAt',
  'serverBatchQueue',
]);

app.get('/settings', (_req, res) => {
  res.json(dbCache.settings);
});

app.put('/settings/:key', (req, res) => {
  const { key } = req.params;
  if (!ALLOWED_SETTINGS_KEYS.has(key)) {
    res.status(400).json({ error: `settings key "${key}" 不在允许列表内` });
    return;
  }
  dbCache.settings[key] = req.body;
  writeDB();
  res.json({ ok: true, key });
});

app.get('/backup/export', (_req, res) => {
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="db-metadata-backup-${Date.now()}.json"`);
  res.send(JSON.stringify(dbCache, null, 2));
});

app.post('/backup/import', (req, res) => {
  const { confirm, data } = req.body || {};
  if (confirm !== true) {
    res.status(400).json({ error: '导入前必须传入 confirm=true' });
    return;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    res.status(400).json({ error: '缺少有效的 data 对象' });
    return;
  }

  const backupPath = `${DATA_PATH}.${Date.now()}.bak`;

  try {
    writeFileSync(backupPath, JSON.stringify(dbCache, null, 2), 'utf-8');
    dbCache = {
      ...structuredClone(EMPTY_DB),
      ...data,
      settings: { ...EMPTY_DB.settings, ...(data.settings || {}) },
    };
    flushDBSync();
    res.json({ ok: true, backupPath, message: '数据库已导入，原数据已备份' });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    res.status(500).json({ error: message });
  }
});

// ─── Bulk Restore ─────────────────────────────────────────────

app.post('/bulk-restore', (req, res) => {
  const {
    materials, assetDetails, processTasks, tasks, parseTasks, aiMetadataJobs, taskEvents,
    products, flexibleTags, aiRules,
    aiRuleSettings, aiConfig, mineruConfig, minioConfig, settings,
  } = req.body;

  for (const m of (materials || [])) {
    if (!dbCache.materials[String(m.id)]) dbCache.materials[String(m.id)] = m;
  }
  for (const [id, detail] of Object.entries(assetDetails || {})) {
    if (!dbCache.assetDetails[String(id)]) dbCache.assetDetails[String(id)] = detail;
  }
  for (const t of (processTasks || [])) {
    if (!dbCache.processTasks[String(t.id)]) dbCache.processTasks[String(t.id)] = t;
  }
  for (const t of (tasks || [])) {
    if (!dbCache.tasks[String(t.id)]) dbCache.tasks[String(t.id)] = t;
  }
  for (const t of (parseTasks || [])) {
    if (!dbCache.parseTasks[String(t.id)]) dbCache.parseTasks[String(t.id)] = t;
  }
  for (const j of (aiMetadataJobs || [])) {
    if (!dbCache.aiMetadataJobs[String(j.id)]) dbCache.aiMetadataJobs[String(j.id)] = j;
  }
  for (const e of (taskEvents || [])) {
    if (!dbCache.taskEvents[String(e.id)]) dbCache.taskEvents[String(e.id)] = e;
  }
  for (const p of (products || [])) {
    if (!dbCache.products[String(p.id)]) dbCache.products[String(p.id)] = p;
  }
  for (const tag of (flexibleTags || [])) {
    if (!dbCache.flexibleTags[String(tag.id)]) dbCache.flexibleTags[String(tag.id)] = tag;
  }
  for (const r of (aiRules || [])) {
    if (!dbCache.aiRules[String(r.id)]) dbCache.aiRules[String(r.id)] = r;
  }
  if (aiRuleSettings && !dbCache.settings.aiRuleSettings) dbCache.settings.aiRuleSettings = aiRuleSettings;
  if (aiConfig && !dbCache.settings.aiConfig) dbCache.settings.aiConfig = aiConfig;
  if (mineruConfig && !dbCache.settings.mineruConfig) dbCache.settings.mineruConfig = mineruConfig;
  if (minioConfig && !dbCache.settings.minioConfig) dbCache.settings.minioConfig = minioConfig;
  for (const [key, value] of Object.entries(settings || {})) {
    if (dbCache.settings[key] === undefined) dbCache.settings[key] = value;
  }

  writeDB();
  res.json({ ok: true, message: 'bulk restore completed (existing rows skipped)' });
});

// ─── 全局错误处理中间件（#13）─────────────────────────────────

// eslint-disable-next-line no-unused-vars
app.use((err, _req, res, _next) => {
  console.error('[db-server] Unhandled error:', err);
  res.status(500).json({ error: 'internal server error', detail: err.message });
});

// ─── 一次性状态字面量归一化迁移（PRD v0.4 §13.1 ‑ 缓解2）──────────────────────────────────
// 启动时将数据库中已知的旧状态字面量归一化为 Canonical 值。幂等、安全，查不到任何需要更改的字段时不写盘。
function normalizeCanonicalStatesOnStartup() {
  let dirty = false;
  // 1. ParseTask：success → completed
  for (const t of Object.values(dbCache.parseTasks || {})) {
    if (t?.state === 'success') {
      t.state = 'completed';
      t.message = (t.message || '') + ' [startup-migration: success→completed]';
      dirty = true;
    }
  }
  // 2. AiMetadataJob：succeeded → confirmed
  for (const j of Object.values(dbCache.aiMetadataJobs || {})) {
    if (j?.state === 'succeeded') {
      j.state = 'confirmed';
      j.message = (j.message || '') + ' [startup-migration: succeeded→confirmed]';
      dirty = true;
    }
  }

  // 3. 修复 Task 与 Material 状态不同步 (Requirement 5 & 历史自愈)
  // 当 Task 已完成且 AI 已确认，但 Material 仍停留在 processing 或缺少 mineruStatus 时进行补齐
  const confirmedAiJobsByTaskId = {};
  for (const j of Object.values(dbCache.aiMetadataJobs || {})) {
    if ((j.state === 'confirmed' || j.state === 'review-pending') && j.parseTaskId) {
      confirmedAiJobsByTaskId[j.parseTaskId] = j;
    }
  }

  for (const t of Object.values(dbCache.parseTasks || {})) {
    if (t.state === 'completed' && t.materialId) {
      const m = dbCache.materials[String(t.materialId)];
      const j = confirmedAiJobsByTaskId[t.id];
      
      if (m && j) {
        let matDirty = false;
        if (m.status === 'processing') {
          m.status = 'completed';
          matDirty = true;
        }
        if (m.mineruStatus !== 'completed') {
          m.mineruStatus = 'completed';
          matDirty = true;
        }
        if (m.aiStatus !== 'analyzed') {
          m.aiStatus = 'analyzed';
          matDirty = true;
        }
        if (matDirty) {
          console.log(`[db-server] startup-migration: synced material ${m.id} to completed state`);
          dirty = true;
        }
      }

      // 追加修复 ParseTask 自身的 stage/completedAt (历史数据自愈)
      if (t.stage !== 'done') {
        t.stage = 'done';
        dirty = true;
      }
      // 如果 ParseTask 的完成时间早于 AI Job 的完成时间，或者根本没设，则校准
      const aiTime = j?.updatedAt || j?.metadata?.aiCompletedAt;
      if (aiTime && (!t.completedAt || t.completedAt < aiTime)) {
        t.completedAt = aiTime;
        dirty = true;
      }
    }
  }

  // 4. 清理历史重复事件 (Requirement 4 追加)
  const seenCreatedEvents = new Set();
  const eventIdsToDelete = [];
  // 按时间升序排序，保留最早的那条 created 事件
  const sortedEvents = Object.values(dbCache.taskEvents || {}).sort((a, b) => 
    new Date(a.createdAt || 0).getTime() - new Date(b.createdAt || 0).getTime()
  );

  for (const e of sortedEvents) {
    if (e.event === 'created' && e.taskId) {
      if (seenCreatedEvents.has(e.taskId)) {
        eventIdsToDelete.push(e.id);
      } else {
        seenCreatedEvents.add(e.taskId);
      }
    }
  }

  if (eventIdsToDelete.length > 0) {
    console.log(`[db-server] startup-migration: deleting ${eventIdsToDelete.length} duplicate created events`);
    for (const id of eventIdsToDelete) {
      delete dbCache.taskEvents[id];
    }
    dirty = true;
  }

  // ── P0 OOM Patch 5: 清除 DB 中残留的 parsedArtifacts 大数组 ──
  // parsedArtifacts 现已外置到 MinIO artifact-manifest.json，
  // DB 只保留 parsedFilesCount + artifactManifestObjectName。
  let artifactsCleaned = 0;
  for (const t of Object.values(dbCache.parseTasks)) {
    if (Array.isArray(t.metadata?.parsedArtifacts) && t.metadata.parsedArtifacts.length > 10) {
      const count = t.metadata.parsedArtifacts.length;
      t.metadata.parsedFilesCount = t.metadata.parsedFilesCount || count;
      delete t.metadata.parsedArtifacts;
      artifactsCleaned++;
      dirty = true;
    }
  }
  for (const m of Object.values(dbCache.materials)) {
    if (Array.isArray(m.metadata?.parsedArtifacts) && m.metadata.parsedArtifacts.length > 10) {
      const count = m.metadata.parsedArtifacts.length;
      m.metadata.parsedFilesCount = m.metadata.parsedFilesCount || count;
      delete m.metadata.parsedArtifacts;
      artifactsCleaned++;
      dirty = true;
    }
  }
  if (artifactsCleaned > 0) {
    console.log(`[db-server] startup-migration: cleaned parsedArtifacts from ${artifactsCleaned} records (externalized to MinIO)`);
  }

  // ── P0 OOM Patch 6: taskEvents 压缩保留策略 ──
  // 每个 taskId 最多保留最近 50 条事件，防止 progress-update 写放大导致 10000+ 事件
  const MAX_EVENTS_PER_TASK = 50;
  const eventsByTask = {};
  for (const e of Object.values(dbCache.taskEvents || {})) {
    const tid = e.taskId || '_global';
    if (!eventsByTask[tid]) eventsByTask[tid] = [];
    eventsByTask[tid].push(e);
  }
  let eventsCompacted = 0;
  for (const [tid, events] of Object.entries(eventsByTask)) {
    if (events.length <= MAX_EVENTS_PER_TASK) continue;
    // 按时间降序，保留最新的 50 条
    events.sort((a, b) => new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime());
    const toDelete = events.slice(MAX_EVENTS_PER_TASK);
    for (const e of toDelete) {
      delete dbCache.taskEvents[e.id];
      eventsCompacted++;
    }
  }
  if (eventsCompacted > 0) {
    console.log(`[db-server] startup-compaction: pruned ${eventsCompacted} excess task events (max ${MAX_EVENTS_PER_TASK}/task)`);
    dirty = true;
  }

  if (dirty) {
    console.log('[db-server] startup-migration: normalized legacy states, synced materials, and deduplicated events');
    writeDB();
  }
}

// ─── 启动 ─────────────────────────────────────────────

try {
  normalizeCanonicalStatesOnStartup();
} catch (e) {
  console.error('[db-server] startup-migration failed (non-fatal):', e.message);
}

const server = app.listen(port, () => {
  console.log(`[db-server] listening on http://localhost:${port}`);
  console.log(`[db-server] Data file: ${DATA_PATH}`);
  // P0 OOM Patch: 启动体积诊断
  try {
    const dbSizeBytes = Buffer.byteLength(JSON.stringify(dbCache), 'utf-8');
    const dbSizeMB = (dbSizeBytes / 1024 / 1024).toFixed(1);
    const eventsCount = Object.keys(dbCache.taskEvents || {}).length;
    const tasksCount = Object.keys(dbCache.parseTasks || {}).length;
    const materialsCount = Object.keys(dbCache.materials || {}).length;
    console.log(`[db-server] DB size: ${dbSizeMB} MB | tasks=${tasksCount} materials=${materialsCount} events=${eventsCount}`);
    if (dbSizeBytes > 50 * 1024 * 1024) {
      console.warn(`[db-server] ⚠️  WARNING: DB in-memory size is ${dbSizeMB} MB — HIGH OOM RISK. Prune taskEvents or check for residual parsedArtifacts.`);
    } else if (dbSizeBytes > 20 * 1024 * 1024) {
      console.warn(`[db-server] ⚠️  NOTICE: DB in-memory size is ${dbSizeMB} MB — approaching limit. Monitor growth.`);
    }
  } catch (e) {
    console.warn('[db-server] Size diagnostic failed:', e.message);
  }
});

// ─── 优雅停机：确保进程退出前内存数据落盘 ─────────────────────

function gracefulShutdown(signal) {
  console.log(`[db-server] Received ${signal}, flushing data to disk...`);
  try {
    flushDBSync();
    flushSecretsSync();
    console.log('[db-server] Data flushed successfully.');
  } catch (e) {
    console.error('[db-server] Flush on shutdown failed:', e.message);
  }
  server.close(() => {
    console.log(`[db-server] Server closed after ${signal}.`);
    process.exit(0);
  });
  // 如果 server.close 超时 5 秒仍未完成，强制退出
  setTimeout(() => {
    console.error('[db-server] Forced exit after timeout.');
    process.exit(1);
  }, 5000);
}

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT',  () => gracefulShutdown('SIGINT'));

// 捕获未处理异常，尝试落盘后退出
process.on('uncaughtException', (err) => {
  console.error('[db-server] Uncaught exception:', err);
  try { flushDBSync(); flushSecretsSync(); } catch { /* best effort */ }
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[db-server] Unhandled rejection:', reason);
});
