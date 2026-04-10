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

/**
 * 将内存缓存原子写入磁盘（#5 write-tmp + rename）
 * 写入失败时抛出异常，调用方负责返回 HTTP 500（#9）
 */
function writeDB() {
  const dir = path.dirname(DATA_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const tmpPath = DATA_PATH + '.tmp';
  writeFileSync(tmpPath, JSON.stringify(dbCache, null, 2), 'utf-8');
  renameSync(tmpPath, DATA_PATH);
}

console.log(`[db-server] Data file: ${DATA_PATH}`);

// ─── Health ───────────────────────────────────────────────────

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'db-server', dataPath: DATA_PATH });
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
  try {
    dbCache.materials[item.id] = item;
    writeDB();
    res.json({ ok: true, id: item.id });
  } catch (e) {
    console.error('[db-server] POST /materials write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.put('/materials/:id', (req, res) => {
  const id = req.params.id;
  try {
    dbCache.materials[id] = { ...req.body, id: req.body.id ?? id };
    writeDB();
    res.json({ ok: true, id });
  } catch (e) {
    console.error('[db-server] PUT /materials write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.patch('/materials/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.materials[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  try {
    const merged = {
      ...existing,
      ...req.body,
      ...(req.body.metadata ? { metadata: { ...existing.metadata, ...req.body.metadata } } : {}),
    };
    dbCache.materials[id] = merged;
    writeDB();
    res.json({ ok: true, id, data: merged });
  } catch (e) {
    console.error('[db-server] PATCH /materials write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.delete('/materials', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  try {
    for (const id of ids) {
      delete dbCache.materials[id];
      delete dbCache.assetDetails[id]; // 联动删除
    }
    writeDB();
    res.json({ ok: true, deleted: ids.length });
  } catch (e) {
    console.error('[db-server] DELETE /materials write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
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
  try {
    dbCache.assetDetails[id] = { ...req.body, id: req.body.id ?? id };
    writeDB();
    res.json({ ok: true, id });
  } catch (e) {
    console.error('[db-server] PUT /asset-details write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

// ─── Process Tasks ────────────────────────────────────────────

app.get('/process-tasks', (_req, res) => {
  const list = Object.values(dbCache.processTasks).sort((a, b) => b.id - a.id);
  res.json(list);
});

app.post('/process-tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  try {
    dbCache.processTasks[item.id] = item;
    writeDB();
    res.json({ ok: true, id: item.id });
  } catch (e) {
    console.error('[db-server] POST /process-tasks write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.patch('/process-tasks/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.processTasks[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  try {
    dbCache.processTasks[id] = { ...existing, ...req.body };
    writeDB();
    res.json({ ok: true, id });
  } catch (e) {
    console.error('[db-server] PATCH /process-tasks write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

// ─── Tasks ────────────────────────────────────────────────────

app.get('/tasks', (_req, res) => {
  res.json(Object.values(dbCache.tasks));
});

app.post('/tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  try {
    dbCache.tasks[item.id] = item;
    writeDB();
    res.json({ ok: true, id: item.id });
  } catch (e) {
    console.error('[db-server] POST /tasks write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.patch('/tasks/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.tasks[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  try {
    dbCache.tasks[id] = { ...existing, ...req.body };
    writeDB();
    res.json({ ok: true, id });
  } catch (e) {
    console.error('[db-server] PATCH /tasks write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

// ─── Products ─────────────────────────────────────────────────

app.get('/products', (_req, res) => {
  const list = Object.values(dbCache.products).sort((a, b) => b.id - a.id);
  res.json(list);
});

app.post('/products', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  try {
    dbCache.products[item.id] = item;
    writeDB();
    res.json({ ok: true, id: item.id });
  } catch (e) {
    console.error('[db-server] POST /products write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.delete('/products', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  try {
    for (const id of ids) delete dbCache.products[id];
    writeDB();
    res.json({ ok: true, deleted: ids.length });
  } catch (e) {
    console.error('[db-server] DELETE /products write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

// ─── Flexible Tags ────────────────────────────────────────────

app.get('/flexible-tags', (_req, res) => {
  res.json(Object.values(dbCache.flexibleTags).sort((a, b) => a.id - b.id));
});

app.post('/flexible-tags', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  try {
    dbCache.flexibleTags[item.id] = item;
    writeDB();
    res.json({ ok: true, id: item.id });
  } catch (e) {
    console.error('[db-server] POST /flexible-tags write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.delete('/flexible-tags', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  try {
    for (const id of ids) delete dbCache.flexibleTags[id];
    writeDB();
    res.json({ ok: true, deleted: ids.length });
  } catch (e) {
    console.error('[db-server] DELETE /flexible-tags write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

// ─── AI Rules ─────────────────────────────────────────────────

app.get('/ai-rules', (_req, res) => {
  res.json(Object.values(dbCache.aiRules).sort((a, b) => a.id - b.id));
});

app.post('/ai-rules', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  try {
    dbCache.aiRules[item.id] = item;
    writeDB();
    res.json({ ok: true, id: item.id });
  } catch (e) {
    console.error('[db-server] POST /ai-rules write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.patch('/ai-rules/:id', (req, res) => {
  const id = req.params.id;
  const existing = dbCache.aiRules[id];
  if (!existing) { res.status(404).json({ error: 'not found' }); return; }
  try {
    dbCache.aiRules[id] = { ...existing, ...req.body };
    writeDB();
    res.json({ ok: true, id });
  } catch (e) {
    console.error('[db-server] PATCH /ai-rules write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

app.delete('/ai-rules', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  try {
    for (const id of ids) delete dbCache.aiRules[id];
    writeDB();
    res.json({ ok: true, deleted: ids.length });
  } catch (e) {
    console.error('[db-server] DELETE /ai-rules write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
});

// ─── Settings ─────────────────────────────────────────────────

app.get('/settings', (_req, res) => {
  res.json(dbCache.settings);
});

app.put('/settings/:key', (req, res) => {
  const { key } = req.params;
  try {
    dbCache.settings[key] = req.body;
    writeDB();
    res.json({ ok: true, key });
  } catch (e) {
    console.error('[db-server] PUT /settings write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
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

  try {
    writeDB();
    res.json({ ok: true, message: 'bulk restore completed (existing rows skipped)' });
  } catch (e) {
    console.error('[db-server] POST /bulk-restore write failed:', e.message);
    res.status(500).json({ error: 'write failed', detail: e.message });
  }
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
