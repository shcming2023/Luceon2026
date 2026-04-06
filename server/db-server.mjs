/**
 * db-server.mjs — SQLite 持久化 REST API
 *
 * 端口：8789（通过 DB_PORT 环境变量覆盖）
 * 数据库文件：/data/cms.db（Docker volume 挂载，可通过 DB_PATH 覆盖）
 *
 * 路由总览：
 *   GET    /health
 *
 *   GET    /materials              → 全量列表
 *   POST   /materials              → 新增
 *   GET    /materials/:id          → 单条
 *   PUT    /materials/:id          → 全量替换
 *   PATCH  /materials/:id          → 部分更新
 *   DELETE /materials              → 批量删除  body: { ids: number[] }
 *
 *   GET    /asset-details/:id      → 单条
 *   PUT    /asset-details/:id      → upsert
 *
 *   GET    /process-tasks
 *   POST   /process-tasks
 *   PATCH  /process-tasks/:id
 *
 *   GET    /tasks
 *   PATCH  /tasks/:id
 *
 *   GET    /products
 *   DELETE /products               body: { ids: number[] }
 *
 *   GET    /flexible-tags
 *   DELETE /flexible-tags          body: { ids: number[] }
 *
 *   GET    /ai-rules
 *   PATCH  /ai-rules/:id
 *   DELETE /ai-rules               body: { ids: number[] }
 *
 *   GET    /settings               → 返回所有 key-value 配置
 *   PUT    /settings/:key          → 存储 JSON 字符串
 *
 *   POST   /bulk-restore           → 批量导入（页面刷新后的全量同步）
 */

import express from 'express';
import cors from 'cors';
import Database from 'better-sqlite3';
import { mkdirSync, existsSync } from 'fs';
import { dirname } from 'path';

const app = express();
const port = Number(process.env.DB_PORT || 8789);
const DB_PATH = process.env.DB_PATH || '/data/cms.db';

app.use(cors());
app.use(express.json({ limit: '20mb' }));

// ─── 初始化数据库 ──────────────────────────────────────────────

// 确保目录存在
const dbDir = dirname(DB_PATH);
if (!existsSync(dbDir)) {
  mkdirSync(dbDir, { recursive: true });
}

const db = new Database(DB_PATH);

// 启用 WAL 模式（写性能更好，读写不互斥）
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

// 建表（JSON 列存储复杂结构，避免过度范式化）
db.exec(`
  CREATE TABLE IF NOT EXISTS materials (
    id            INTEGER PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: Material
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  CREATE TABLE IF NOT EXISTS asset_details (
    id            INTEGER PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: AssetDetail
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  CREATE TABLE IF NOT EXISTS process_tasks (
    id            INTEGER PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: ProcessTask
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT    PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: Task
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: Product
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  CREATE TABLE IF NOT EXISTS flexible_tags (
    id            INTEGER PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: FlexibleTag
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  CREATE TABLE IF NOT EXISTS ai_rules (
    id            INTEGER PRIMARY KEY,
    data          TEXT    NOT NULL,   -- JSON: AiRule
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );

  -- key-value 配置表（存储 aiConfig / mineruConfig / aiRuleSettings）
  CREATE TABLE IF NOT EXISTS settings (
    key           TEXT    PRIMARY KEY,
    value         TEXT    NOT NULL,   -- JSON string
    updated_at    INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
  );
`);

console.log(`[db-server] Database initialized at ${DB_PATH}`);

// ─── 工具函数 ─────────────────────────────────────────────────

function now() {
  return Date.now();
}

function parseRows(rows) {
  return rows.map((r) => JSON.parse(r.data));
}

// ─── Health ───────────────────────────────────────────────────

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'db-server', dbPath: DB_PATH });
});

// ─── Materials ────────────────────────────────────────────────

app.get('/materials', (_req, res) => {
  const rows = db.prepare('SELECT data FROM materials ORDER BY id DESC').all();
  res.json(parseRows(rows));
});

app.get('/materials/:id', (req, res) => {
  const row = db.prepare('SELECT data FROM materials WHERE id = ?').get(Number(req.params.id));
  if (!row) { res.status(404).json({ error: 'not found' }); return; }
  res.json(JSON.parse(row.data));
});

app.post('/materials', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  db.prepare(
    'INSERT OR REPLACE INTO materials (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(item.id, JSON.stringify(item), now());
  res.json({ ok: true, id: item.id });
});

app.put('/materials/:id', (req, res) => {
  const id = Number(req.params.id);
  const item = { ...req.body, id };
  db.prepare(
    'INSERT OR REPLACE INTO materials (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(id, JSON.stringify(item), now());
  res.json({ ok: true, id });
});

app.patch('/materials/:id', (req, res) => {
  const id = Number(req.params.id);
  const row = db.prepare('SELECT data FROM materials WHERE id = ?').get(id);
  if (!row) { res.status(404).json({ error: 'not found' }); return; }

  const existing = JSON.parse(row.data);
  const updates = req.body;

  // 深合并 metadata
  const merged = {
    ...existing,
    ...updates,
    ...(updates.metadata
      ? { metadata: { ...existing.metadata, ...updates.metadata } }
      : {}),
  };

  db.prepare(
    'UPDATE materials SET data = ?, updated_at = ? WHERE id = ?',
  ).run(JSON.stringify(merged), now(), id);
  res.json({ ok: true, id, data: merged });
});

app.delete('/materials', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  const placeholders = ids.map(() => '?').join(',');
  db.prepare(`DELETE FROM materials WHERE id IN (${placeholders})`).run(...ids);
  // 同步删除 asset_details
  db.prepare(`DELETE FROM asset_details WHERE id IN (${placeholders})`).run(...ids);
  res.json({ ok: true, deleted: ids.length });
});

// ─── Asset Details ────────────────────────────────────────────

app.get('/asset-details/:id', (req, res) => {
  const row = db.prepare('SELECT data FROM asset_details WHERE id = ?').get(Number(req.params.id));
  if (!row) { res.status(404).json({ error: 'not found' }); return; }
  res.json(JSON.parse(row.data));
});

app.get('/asset-details', (_req, res) => {
  const rows = db.prepare('SELECT data FROM asset_details').all();
  // 返回 Record<number, AssetDetail>
  const result = {};
  for (const row of rows) {
    const item = JSON.parse(row.data);
    result[item.id] = item;
  }
  res.json(result);
});

app.put('/asset-details/:id', (req, res) => {
  const id = Number(req.params.id);
  const item = { ...req.body, id };
  db.prepare(
    'INSERT OR REPLACE INTO asset_details (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(id, JSON.stringify(item), now());
  res.json({ ok: true, id });
});

// ─── Process Tasks ────────────────────────────────────────────

app.get('/process-tasks', (_req, res) => {
  const rows = db.prepare('SELECT data FROM process_tasks ORDER BY id DESC').all();
  res.json(parseRows(rows));
});

app.post('/process-tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  db.prepare(
    'INSERT OR REPLACE INTO process_tasks (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(item.id, JSON.stringify(item), now());
  res.json({ ok: true, id: item.id });
});

app.patch('/process-tasks/:id', (req, res) => {
  const id = Number(req.params.id);
  const row = db.prepare('SELECT data FROM process_tasks WHERE id = ?').get(id);
  if (!row) { res.status(404).json({ error: 'not found' }); return; }
  const merged = { ...JSON.parse(row.data), ...req.body };
  db.prepare(
    'UPDATE process_tasks SET data = ?, updated_at = ? WHERE id = ?',
  ).run(JSON.stringify(merged), now(), id);
  res.json({ ok: true, id });
});

// ─── Tasks ────────────────────────────────────────────────────

app.get('/tasks', (_req, res) => {
  const rows = db.prepare('SELECT data FROM tasks ORDER BY rowid DESC').all();
  res.json(parseRows(rows));
});

app.post('/tasks', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  db.prepare(
    'INSERT OR REPLACE INTO tasks (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(item.id, JSON.stringify(item), now());
  res.json({ ok: true, id: item.id });
});

app.patch('/tasks/:id', (req, res) => {
  const id = req.params.id;
  const row = db.prepare('SELECT data FROM tasks WHERE id = ?').get(id);
  if (!row) { res.status(404).json({ error: 'not found' }); return; }
  const merged = { ...JSON.parse(row.data), ...req.body };
  db.prepare(
    'UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?',
  ).run(JSON.stringify(merged), now(), id);
  res.json({ ok: true, id });
});

// ─── Products ─────────────────────────────────────────────────

app.get('/products', (_req, res) => {
  const rows = db.prepare('SELECT data FROM products ORDER BY id DESC').all();
  res.json(parseRows(rows));
});

app.post('/products', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  db.prepare(
    'INSERT OR REPLACE INTO products (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(item.id, JSON.stringify(item), now());
  res.json({ ok: true, id: item.id });
});

app.delete('/products', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  const ph = ids.map(() => '?').join(',');
  db.prepare(`DELETE FROM products WHERE id IN (${ph})`).run(...ids);
  res.json({ ok: true, deleted: ids.length });
});

// ─── Flexible Tags ────────────────────────────────────────────

app.get('/flexible-tags', (_req, res) => {
  const rows = db.prepare('SELECT data FROM flexible_tags ORDER BY id ASC').all();
  res.json(parseRows(rows));
});

app.post('/flexible-tags', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  db.prepare(
    'INSERT OR REPLACE INTO flexible_tags (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(item.id, JSON.stringify(item), now());
  res.json({ ok: true, id: item.id });
});

app.delete('/flexible-tags', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  const ph = ids.map(() => '?').join(',');
  db.prepare(`DELETE FROM flexible_tags WHERE id IN (${ph})`).run(...ids);
  res.json({ ok: true, deleted: ids.length });
});

// ─── AI Rules ────────────────────────────────────────────────

app.get('/ai-rules', (_req, res) => {
  const rows = db.prepare('SELECT data FROM ai_rules ORDER BY id ASC').all();
  res.json(parseRows(rows));
});

app.post('/ai-rules', (req, res) => {
  const item = req.body;
  if (!item?.id) { res.status(400).json({ error: '缺少 id' }); return; }
  db.prepare(
    'INSERT OR REPLACE INTO ai_rules (id, data, updated_at) VALUES (?, ?, ?)',
  ).run(item.id, JSON.stringify(item), now());
  res.json({ ok: true, id: item.id });
});

app.patch('/ai-rules/:id', (req, res) => {
  const id = Number(req.params.id);
  const row = db.prepare('SELECT data FROM ai_rules WHERE id = ?').get(id);
  if (!row) { res.status(404).json({ error: 'not found' }); return; }
  const merged = { ...JSON.parse(row.data), ...req.body };
  db.prepare(
    'UPDATE ai_rules SET data = ?, updated_at = ? WHERE id = ?',
  ).run(JSON.stringify(merged), now(), id);
  res.json({ ok: true, id });
});

app.delete('/ai-rules', (req, res) => {
  const { ids } = req.body;
  if (!Array.isArray(ids) || ids.length === 0) {
    res.status(400).json({ error: '缺少 ids 数组' }); return;
  }
  const ph = ids.map(() => '?').join(',');
  db.prepare(`DELETE FROM ai_rules WHERE id IN (${ph})`).run(...ids);
  res.json({ ok: true, deleted: ids.length });
});

// ─── Settings ─────────────────────────────────────────────────

app.get('/settings', (_req, res) => {
  const rows = db.prepare('SELECT key, value FROM settings').all();
  const result = {};
  for (const row of rows) {
    try { result[row.key] = JSON.parse(row.value); } catch { result[row.key] = row.value; }
  }
  res.json(result);
});

app.put('/settings/:key', (req, res) => {
  const { key } = req.params;
  const value = JSON.stringify(req.body);
  db.prepare(
    'INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)',
  ).run(key, value, now());
  res.json({ ok: true, key });
});

// ─── Bulk Restore（首次/全量同步，幂等）────────────────────────
// POST /bulk-restore  body: { materials, assetDetails, processTasks, tasks,
//                             products, flexibleTags, aiRules,
//                             aiRuleSettings, aiConfig, mineruConfig }
app.post('/bulk-restore', (req, res) => {
  const {
    materials, assetDetails, processTasks, tasks,
    products, flexibleTags, aiRules,
    aiRuleSettings, aiConfig, mineruConfig,
  } = req.body;

  const insertMaterial = db.prepare(
    'INSERT OR IGNORE INTO materials (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertDetail = db.prepare(
    'INSERT OR IGNORE INTO asset_details (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertPTask = db.prepare(
    'INSERT OR IGNORE INTO process_tasks (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertTask = db.prepare(
    'INSERT OR IGNORE INTO tasks (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertProduct = db.prepare(
    'INSERT OR IGNORE INTO products (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertTag = db.prepare(
    'INSERT OR IGNORE INTO flexible_tags (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertRule = db.prepare(
    'INSERT OR IGNORE INTO ai_rules (id, data, updated_at) VALUES (?, ?, ?)',
  );
  const insertSetting = db.prepare(
    'INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)',
  );

  const runAll = db.transaction(() => {
    const ts = now();

    for (const m of (materials || [])) insertMaterial.run(m.id, JSON.stringify(m), ts);

    for (const [id, detail] of Object.entries(assetDetails || {})) {
      insertDetail.run(Number(id), JSON.stringify(detail), ts);
    }

    for (const t of (processTasks || [])) insertPTask.run(t.id, JSON.stringify(t), ts);
    for (const t of (tasks || [])) insertTask.run(t.id, JSON.stringify(t), ts);
    for (const p of (products || [])) insertProduct.run(p.id, JSON.stringify(p), ts);
    for (const tag of (flexibleTags || [])) insertTag.run(tag.id, JSON.stringify(tag), ts);
    for (const r of (aiRules || [])) insertRule.run(r.id, JSON.stringify(r), ts);

    if (aiRuleSettings) insertSetting.run('aiRuleSettings', JSON.stringify(aiRuleSettings), ts);
    if (aiConfig) insertSetting.run('aiConfig', JSON.stringify(aiConfig), ts);
    if (mineruConfig) insertSetting.run('mineruConfig', JSON.stringify(mineruConfig), ts);
  });

  runAll();

  res.json({ ok: true, message: 'bulk restore completed (existing rows skipped)' });
});

// ─── 启动 ─────────────────────────────────────────────────────

app.listen(port, () => {
  console.log(`[db-server] listening on http://localhost:${port}`);
  console.log(`[db-server] SQLite database: ${DB_PATH}`);
});
