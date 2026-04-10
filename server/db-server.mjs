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

app.use(cors());
app.use(express.json({ limit: '20mb' }));

// 请求日志（方便排查）
app.use((req, _res, next) => {
  if (req.method !== 'GET') {
    console.log(`[db-server] ${req.method} ${req.path}`, req.method !== 'GET' ? JSON.stringify(req.body).slice(0, 200) : '');
  }
  next();
});

// ─── JSON 文件读写 ─────────────────────────────────────────────

const EMPTY_DB = {
  materials: {},       // id → Material
  assetDetails: {},    // id → AssetDetail
  processTasks: {},    // id → ProcessTask
  tasks: {},           // id → Task
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

// ─── Health ───────────────────────────────────────────────────

// ─── writeDB debounce 计时器 ──────────────────────────────────
let writeTimer = null;

/**
 * 将内存缓存原子写入磁盘（debounce 100ms）
 * - dbCache 由各路由 handler 实时更新，GET 请求始终读内存，保证一致性
 * - 真正的磁盘 I/O 在 100ms 后触发，期间重复调用重置计时器
 * - 磁盘错误在回调内 console.error 记录，不向请求方返回 500
 */
function writeDB() {
  if (writeTimer) clearTimeout(writeTimer);
  writeTimer = setTimeout(() => {
    const dir = path.dirname(DATA_PATH);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    const tmpPath = DATA_PATH + '.tmp';
    try {
      writeFileSync(tmpPath, JSON.stringify(dbCache, null, 2), 'utf-8');
      renameSync(tmpPath, DATA_PATH);
    } catch (e) {
      console.error('[db-server] writeDB flush failed:', e.message);
    }
    writeTimer = null;
  }, 100);
}

function flushDBSync() {
  if (writeTimer) {
    clearTimeout(writeTimer);
    writeTimer = null;
  }
  const dir = path.dirname(DATA_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const tmpPath = DATA_PATH + '.tmp';
  writeFileSync(tmpPath, JSON.stringify(dbCache, null, 2), 'utf-8');
  renameSync(tmpPath, DATA_PATH);
}

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'db-server', dataPath: DATA_PATH });
});

app.get('/stats', (_req, res) => {
  res.json({
    ok: true,
    dataPath: DATA_PATH,
    fileSize: Buffer.byteLength(JSON.stringify(dbCache, null, 2), 'utf-8'),
    counts: {
      materials: Object.keys(dbCache.materials).length,
      assetDetails: Object.keys(dbCache.assetDetails).length,
      processTasks: Object.keys(dbCache.processTasks).length,
      tasks: Object.keys(dbCache.tasks).length,
      products: Object.keys(dbCache.products).length,
      flexibleTags: Object.keys(dbCache.flexibleTags).length,
      aiRules: Object.keys(dbCache.aiRules).length,
      settings: Object.keys(dbCache.settings).length,
    },
  });
});

// ─── Materials ────────────────────────────────────────────────

app.get('/materials', (_req, res) => {
  const list = Object.values(dbCache.materials).sort((a, b) => b.id - a.id);
  res.json(list);
});

app.get('/materials/:id', (req, res) => {
  const item = dbCache.materials[req.params.id];
  if (!item) { res.status(404).json({ error: 'not found' }); return; }
  res.json(item);
});

app.post('/materials', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.materials[item.id] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.put('/materials/:id', (req, res) => {
  const id = req.params.id;
  dbCache.materials[id] = { ...req.body, id: req.body.id ?? id };
  writeDB();
  res.json({ ok: true, id });
});

app.patch('/materials/:id', (req, res) => {
  const id = req.params.id;
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

app.delete('/materials', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) {
    delete dbCache.materials[id];
    delete dbCache.assetDetails[id]; // 联动删除
  }
  writeDB();
  res.json({ ok: true, deleted: ids.length });
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

// ─── Tasks ────────────────────────────────────────────────────

app.get('/tasks', (_req, res) => {
  res.json(Object.values(dbCache.tasks));
});

app.post('/tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  dbCache.tasks[item.id] = item;
  writeDB();
  res.json({ ok: true, id: item.id });
});

app.patch('/tasks/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.tasks[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  dbCache.tasks[id] = { ...existing, ...req.body };
  writeDB();
  res.json({ ok: true, id });
});

app.delete('/tasks', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  for (const id of ids) delete dbCache.tasks[id];
  writeDB();
  res.json({ ok: true, deleted: ids.length });
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

app.get('/settings', (_req, res) => {
  res.json(dbCache.settings);
});

app.put('/settings/:key', (req, res) => {
  const { key } = req.params;
  dbCache.settings[key] = req.body;
  writeDB();
  res.json({ ok: true, key });
});

app.get('/backup/export', (_req, res) => {
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="db-backup-${Date.now()}.json"`);
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
    materials, assetDetails, processTasks, tasks,
    products, flexibleTags, aiRules,
    aiRuleSettings, aiConfig, mineruConfig, minioConfig,
  } = req.body;

  for (const m of (materials || [])) {
    if (!dbCache.materials[m.id]) dbCache.materials[m.id] = m;
  }
  for (const [id, detail] of Object.entries(assetDetails || {})) {
    if (!dbCache.assetDetails[id]) dbCache.assetDetails[id] = detail;
  }
  for (const t of (processTasks || [])) {
    if (!dbCache.processTasks[t.id]) dbCache.processTasks[t.id] = t;
  }
  for (const t of (tasks || [])) {
    if (!dbCache.tasks[t.id]) dbCache.tasks[t.id] = t;
  }
  for (const p of (products || [])) {
    if (!dbCache.products[p.id]) dbCache.products[p.id] = p;
  }
  for (const tag of (flexibleTags || [])) {
    if (!dbCache.flexibleTags[tag.id]) dbCache.flexibleTags[tag.id] = tag;
  }
  for (const r of (aiRules || [])) {
    if (!dbCache.aiRules[r.id]) dbCache.aiRules[r.id] = r;
  }
  if (aiRuleSettings && !dbCache.settings.aiRuleSettings) dbCache.settings.aiRuleSettings = aiRuleSettings;
  if (aiConfig && !dbCache.settings.aiConfig) dbCache.settings.aiConfig = aiConfig;
  if (mineruConfig && !dbCache.settings.mineruConfig) dbCache.settings.mineruConfig = mineruConfig;
  if (minioConfig && !dbCache.settings.minioConfig) dbCache.settings.minioConfig = minioConfig;

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

app.listen(port, () => {
  console.log(`[db-server] listening on http://localhost:${port}`);
  console.log(`[db-server] Data file: ${DATA_PATH}`);
});
