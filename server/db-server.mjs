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
    if (!existsSync(DATA_PATH)) return structuredClone(EMPTY_DB);
    const raw = readFileSync(DATA_PATH, 'utf-8');
    const parsed = JSON.parse(raw);
    return { ...EMPTY_DB, ...parsed };
  } catch {
    return structuredClone(EMPTY_DB);
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
  writeFileSync(tmpPath, JSON.stringify(payload, null, 2), 'utf-8');
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
  flushDB();
}

function flushSecretsSync() {
  try {
    atomicWriteJson(SECRETS_PATH, secretsCache);
  } catch (e) {
    console.error('[db-server] secrets flush on shutdown failed:', e.message);
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

const ALLOWED_SECRETS_KEYS = new Set(['aiKeys', 'mineruKey', 'minioCredentials']);

app.put('/secrets', requireBody, (req, res) => {
  for (const key of Object.keys(req.body)) {
    if (!ALLOWED_SECRETS_KEYS.has(key)) {
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
  const list = Object.values(dbCache.materials).sort((a, b) => b.id - a.id);
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
  dbCache.materials[String(item.id)] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.put('/materials/:id', (req, res) => {
  const id = String(req.params.id);
  dbCache.materials[id] = { ...req.body, id: req.body.id ?? id };
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
  dbCache.parseTasks[item.id] = {
    createdAt: new Date().toISOString(),
    ...item
  };
  writeDB();
  
  // 记录一条创建事件
  const eventId = `evt-${Date.now()}`;
  dbCache.taskEvents[eventId] = {
    id: eventId,
    taskId: item.id,
    taskType: 'parse',
    level: 'info',
    event: 'created',
    message: '解析任务已由 upload-server 创建，正进入处理队列',
    createdAt: new Date().toISOString()
  };
  
  res.json({ ok: true, id: item.id });
});

app.patch('/tasks/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.parseTasks[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  dbCache.parseTasks[id] = { ...existing, ...req.body, updatedAt: new Date().toISOString() };
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

// ─── AI Metadata Jobs ─────────────────────────────────────────

app.get('/ai-metadata-jobs', (_req, res) => {
  res.json(Object.values(dbCache.aiMetadataJobs));
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

// ─── 启动 ─────────────────────────────────────────────────────

const server = app.listen(port, () => {
  console.log(`[db-server] listening on http://localhost:${port}`);
  console.log(`[db-server] Data file: ${DATA_PATH}`);
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
