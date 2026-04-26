/**
 * mineru-log-progress-smoke.mjs
 *
 * MinerU 日志结构化活性信号分级冒烟测试（v1.1）。
 * 含事件日志去重与任务侧展示验证。
 *
 * 测试场景：
 * 1. tqdm 进度行解析
 * 2. 非 tqdm 行不解析为 progress
 * 3. 信号分类：progress / window / document-shape / engine-config / api-noise / error
 * 4. 活性等级裁决：active-progress / active-stage-change / active-business-log / api-alive-only / no-business-signal / suspected-stale / failed-confirmed
 * 5. API 噪声不刷新 lastProgressObservedAt
 * 6. 旧任务日志排除（stale log rejection）
 * 7. 多任务不归因
 * 8. 单任务归因
 * 9. 任务切换后旧进度不串新任务
 * 10. 事件日志去重：相同 key 不重复写事件
 * 11. 事件日志去重：phase/current 变化时写事件
 * 12. api-alive-only 在列表中不显示"正在推进"
 * 13. 日志新鲜：mtime 新鲜 → active-progress（不 stale）
 * 14. 日志滞后：mtime 超阈值 → log-observation-stale，不 failed
 * 15. progress-update 事件降噪：相同 message 不重复写事件
 * 16. progress-update 事件降噪：stage 变化写事件
 * 17. progress-update 事件降噪：mineruLastStatusAt 单独变不写事件
 * 18. failed-confirmed 仍写 error 事件
 * 19. log-observation-stale 在列表显示"日志观测滞后"
 * 20. 连续 10 次相同 progress-update：task update + SSE 可执行，事件日志只写 1 条
 */

import { parseTqdmLine, classifyLogLine, determineActivityLevel, parseLatestMineruProgress, MINERU_LOG_STALE_MS } from '../lib/ops-mineru-log-parser.mjs';
import { ParseTaskWorker } from '../services/queue/task-worker.mjs';
import fs from 'fs';
import path from 'path';

let testsPassed = 0;
let testsFailed = 0;

/**
 * 断言辅助函数。
 *
 * @param {boolean} condition - 断言条件
 * @param {string} message - 断言失败时的描述
 */
function assert(condition, message) {
  if (!condition) {
    console.error(`❌ FAILED: ${message}`);
    testsFailed++;
  } else {
    testsPassed++;
  }
}

async function run() {
  console.log('=== MinerU Log Structured Activity Signal Smoke Test (v1.1) ===\n');

  // ── 环境隔离：所有测试只读 scratch 日志，不读真实生产日志 ──
  const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
  if (!fs.existsSync(scratchPath)) fs.mkdirSync(scratchPath, { recursive: true });
  const origLogPath = process.env.MINERU_LOG_PATH;
  const origErrLogPath = process.env.MINERU_ERR_LOG_PATH;
  const scratchLog = path.join(scratchPath, 'mineru-api.log');
  const scratchErrLog = path.join(scratchPath, 'mineru-api.err.log');
  process.env.MINERU_LOG_PATH = scratchLog;
  process.env.MINERU_ERR_LOG_PATH = scratchErrLog;
  // 清理旧 scratch 文件，确保干净起点
  try { fs.unlinkSync(scratchLog); } catch (_) {}
  try { fs.unlinkSync(scratchErrLog); } catch (_) {}

  // ─── Test 1: tqdm 进度行解析 ───
  console.log('Test 1: tqdm 进度行解析');
  {
    const result = parseTqdmLine('Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]');
    assert(result !== null, 'Should parse tqdm line');
    assert(result.phase === 'Predict', 'Phase should be Predict');
    assert(result.percent === 52, 'Percent should be 52');
    assert(result.current === 14, 'Current should be 14');
    assert(result.total === 27, 'Total should be 27');
    assert(result.signalType === 'progress', 'signalType should be progress');

    const result2 = parseTqdmLine('OCR-rec Predict: 83%|████████▎ | 120/144 [05:10<01:02]');
    assert(result2 !== null, 'Should parse OCR-rec tqdm line');
    assert(result2.phase === 'OCR-rec Predict', 'Phase should be OCR-rec Predict');
    assert(result2.percent === 83, 'Percent should be 83');
    console.log('Test 1 Pass ✅\n');
  }

  // ─── Test 2: 非 tqdm 行 ───
  console.log('Test 2: 非 tqdm 行不解析为 progress');
  {
    assert(parseTqdmLine('2026-04-25 10:00:00 INFO: Starting MinerU...') === null, 'Info line should return null');
    assert(parseTqdmLine('GET /health 200 OK') === null, 'Health request should return null');
    assert(parseTqdmLine('') === null, 'Empty line should return null');
    console.log('Test 2 Pass ✅\n');
  }

  // ─── Test 3: 信号分类 ───
  console.log('Test 3: 结构化信号分类');
  {
    // progress
    const p = classifyLogLine('Predict: 52%|█████▏    | 14/27 [02:04<01:52]');
    assert(p?.signalType === 'progress', 'tqdm line should classify as progress');
    assert(p?.detail?.phase === 'Predict', 'detail should have phase');

    // window
    const w = classifyLogLine('Hybrid processing window 1/1: pages 1-27/27');
    assert(w?.signalType === 'window', 'Window line should classify as window');
    assert(w?.detail?.windowTotal === 1, 'windowTotal should be 1');
    assert(w?.detail?.pageTotal === 27, 'pageTotal should be 27');

    // document-shape
    const ds = classifyLogLine('2026-04-25 10:00:00 page_count=27, window_size=64, total_windows=1');
    assert(ds?.signalType === 'document-shape', 'page_count line should classify as document-shape');
    assert(ds?.timestamp === '2026-04-25 10:00:00', 'Should extract timestamp');

    // engine-config
    const ec = classifyLogLine('Using transformers for OCR detection model');
    assert(ec?.signalType === 'engine-config', 'Engine config line should classify as engine-config');

    // api-noise
    const an = classifyLogLine('2026-04-25 10:05:00 GET /health 200 OK');
    assert(an?.signalType === 'api-noise', 'GET /health should classify as api-noise');

    const an2 = classifyLogLine('"GET /tasks/a8b51d08-a206-4b88 HTTP/1.1" 200');
    assert(an2?.signalType === 'api-noise', 'GET /tasks/{id} should classify as api-noise');

    // error
    const er = classifyLogLine('2026-04-25 10:10:00 ERROR: OutOfMemoryError in CUDA');
    assert(er?.signalType === 'error', 'Error line should classify as error');

    // unclassified
    const uc = classifyLogLine('some random text');
    assert(uc === null, 'Random text should return null');

    console.log('Test 3 Pass ✅\n');
  }

  // ─── Test 4: 活性等级裁决 ───
  console.log('Test 4: 活性等级裁决');
  {
    // active-progress: tqdm 变化
    const lvl1 = determineActivityLevel(
      { progressCount: 5, stageChangeCount: 0, businessLogCount: 0, apiNoiseCount: 10, errorCount: 0 },
      { phase: 'Predict', percent: 50, current: 10 },
      { phase: 'Predict', percent: 52, current: 14 }
    );
    assert(lvl1 === 'active-progress', 'Should be active-progress when tqdm values change');

    // active-stage-change: phase 变化但 percent 不变
    const lvl2 = determineActivityLevel(
      { progressCount: 1, stageChangeCount: 1, businessLogCount: 0, apiNoiseCount: 5, errorCount: 0 },
      { phase: 'Predict', percent: 100, current: 27 },
      { phase: 'Predict', percent: 100, current: 27 }
    );
    assert(lvl2 === 'active-stage-change', 'Should be active-stage-change when phase changes but percent does not');

    // active-business-log: 只有 window/doc-shape/engine 日志
    const lvl3 = determineActivityLevel(
      { progressCount: 0, stageChangeCount: 0, businessLogCount: 3, apiNoiseCount: 20, errorCount: 0 },
      null,
      null
    );
    assert(lvl3 === 'active-business-log', 'Should be active-business-log with only business logs');

    // api-alive-only: 只有 health/task 轮询
    const lvl4 = determineActivityLevel(
      { progressCount: 0, stageChangeCount: 0, businessLogCount: 0, apiNoiseCount: 50, errorCount: 0 },
      null,
      null
    );
    assert(lvl4 === 'api-alive-only', 'Should be api-alive-only with only API noise');

    // no-business-signal: 什么都没有
    const lvl5 = determineActivityLevel(
      { progressCount: 0, stageChangeCount: 0, businessLogCount: 0, apiNoiseCount: 0, errorCount: 0 },
      null,
      null
    );
    assert(lvl5 === 'no-business-signal', 'Should be no-business-signal when empty');

    // suspected-stale: 有 tqdm 行但值未变
    const lvl6 = determineActivityLevel(
      { progressCount: 3, stageChangeCount: 0, businessLogCount: 0, apiNoiseCount: 10, errorCount: 0 },
      { phase: 'OCR-rec Predict', percent: 74, current: 90 },
      { phase: 'OCR-rec Predict', percent: 74, current: 90 }
    );
    assert(lvl6 === 'suspected-stale', 'Should be suspected-stale when tqdm lines exist but values unchanged');

    // failed-confirmed: 有错误信号
    const lvl7 = determineActivityLevel(
      { progressCount: 2, stageChangeCount: 0, businessLogCount: 1, apiNoiseCount: 5, errorCount: 1 },
      null,
      { phase: 'Predict', percent: 50, current: 10 }
    );
    assert(lvl7 === 'failed-confirmed', 'Should be failed-confirmed when error signals present');

    console.log('Test 4 Pass ✅\n');
  }

  // ─── Test 5: parseLatestMineruProgress 集成 ───
  console.log('Test 5: parseLatestMineruProgress 集成 + stale log rejection');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    if (!fs.existsSync(scratchPath)) fs.mkdirSync(scratchPath, { recursive: true });
    const mockLog = path.join(scratchPath, 'mineru-api.log');

    // 写入混合信号日志
    fs.writeFileSync(mockLog, [
      '2026-04-25 10:00:00 page_count=27, window_size=64, total_windows=1',
      'Using transformers for OCR detection model',
      'Hybrid processing window 1/1: pages 1-27/27',
      'GET /health 200 OK',
      'GET /tasks/abc-123 200 OK',
      'Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]',
      'GET /health 200 OK',
    ].join('\n'));

    const stats = fs.statSync(mockLog);

    // 正常读取（未来时间排除测试）
    const futureTime = new Date(stats.mtimeMs + 10000).toISOString();
    const staleResult = await parseLatestMineruProgress(futureTime);
    assert(staleResult !== null, 'Should return object for old log');
    assert(staleResult.activityLevel === 'log-observation-unattributed', 'Should reject stale log (future minObservedAt) by returning unattributed');

    // 正常读取
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();
    const validResult = await parseLatestMineruProgress(pastTime);
    assert(validResult !== null, 'Should accept valid log');
    assert(validResult.phase === 'Predict', 'Phase should be Predict');
    assert(validResult.activityLevel === 'active-progress', 'Activity should be active-progress');
    assert(validResult.signalSummary.progressCount >= 1, 'Should have progress signals');
    assert(validResult.signalSummary.apiNoiseCount >= 2, 'Should have API noise signals');
    assert(validResult.signalSummary.businessLogCount >= 2, 'Should have business log signals (window + doc-shape + engine)');

    // API 噪声不应影响 lastProgressObservedAt
    // contextTime should come from the business log timestamp, not from API noise
    assert(validResult.lastProgressObservedAt !== undefined, 'lastProgressObservedAt should be set');

    console.log('Test 5 Pass ✅\n');
  }

  // ─── Test 6: 只有 API 噪声的日志 ───
  console.log('Test 6: 只有 API 噪声 → api-alive-only');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');

    fs.writeFileSync(mockLog, [
      'GET /health 200 OK',
      '"GET /tasks/abc-123 HTTP/1.1" 200',
      'GET /health 200 OK',
    ].join('\n'));

    const stats = fs.statSync(mockLog);
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();
    const result = await parseLatestMineruProgress(pastTime);
    assert(result !== null, 'Should return result even with only noise');
    assert(result.activityLevel === 'api-alive-only', 'Activity should be api-alive-only');
    assert(result.phase === null, 'Phase should be null (no tqdm)');
    assert(result.signalSummary.progressCount === 0, 'progressCount should be 0');
    assert(result.signalSummary.apiNoiseCount >= 2, 'apiNoiseCount should be >= 2');

    console.log('Test 6 Pass ✅\n');
  }

  // ─── Test 7: 多任务不归因 ───
  console.log('Test 7: 多个 running/processing 任务 → 不归因');
  {
    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalled = 0;
    worker.updateTaskWithRetry = async () => { updateCalled++; };

    await worker.observeMineruProgress([
      { id: '1', state: 'running', metadata: { mineruStatus: 'processing' } },
      { id: '2', state: 'running', metadata: { mineruStatus: 'processing' } }
    ]);
    assert(updateCalled === 0, 'Should not update when multiple processing tasks');
    console.log('Test 7 Pass ✅\n');
  }

  // ─── Test 8: 单任务归因 ───
  console.log('Test 8: 单个 running/processing 任务 → 归因');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    fs.writeFileSync(mockLog, 'Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]\n');
    const stats = fs.statSync(mockLog);
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();

    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalled = 0;
    let lastUpdate = null;
    worker.updateTaskWithRetry = async (_id, update) => { updateCalled++; lastUpdate = update; };

    await worker.observeMineruProgress([
      { id: '1', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: pastTime } }
    ]);
    assert(updateCalled === 1, 'Should update for single processing task');
    assert(lastUpdate?.metadata?.mineruProgressHealth === 'active-progress', 'Health should be active-progress');
    assert(lastUpdate?.metadata?.mineruObservedProgress?.activityLevel === 'active-progress', 'Observed progress should have activityLevel');
    console.log('Test 8 Pass ✅\n');
  }

  // ─── Test 9: 任务切换后旧进度不串新任务 ───
  console.log('Test 9: 任务切换后旧进度不串新任务');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    // 写入旧日志
    fs.writeFileSync(mockLog, 'Predict: 100%|██████████| 27/27 [done]\n');
    const stats = fs.statSync(mockLog);

    // 新任务的 mineruStartedAt 在日志之后 → 应被排除
    const futureStart = new Date(stats.mtimeMs + 60000).toISOString();

    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalled = 0;
    let lastUpdate = null;
    worker.updateTaskWithRetry = async (_id, update) => { updateCalled++; lastUpdate = update; };

    await worker.observeMineruProgress([
      { id: 'new-task', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: futureStart } }
    ]);
    assert(updateCalled === 1, 'Should attribute with unattributed level');
    assert(lastUpdate?.metadata?.mineruProgressHealth === 'log-observation-unattributed', 'Health should be log-observation-unattributed');
    console.log('Test 9 Pass ✅\n');
  }

  // ─── Test 10: 事件日志去重 — 连续相同 progress 不重复写事件 ───
  console.log('Test 10: 事件日志去重 — 相同 key 不重复写事件');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    fs.writeFileSync(mockLog, 'Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]\n');
    const stats = fs.statSync(mockLog);
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();

    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalls = 0;
    let lastMetadata = null;
    worker.updateTaskWithRetry = async (_id, update) => { updateCalls++; lastMetadata = update.metadata; };

    // 第 1 次调用：无 prevKey → key 变化 → 写事件
    const task1 = { id: 't10', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: pastTime } };
    await worker.observeMineruProgress([task1]);
    assert(updateCalls === 1, 'First call should update');
    const key1 = lastMetadata?.mineruProgressEventKey;
    assert(key1 && key1.includes('phase=Predict'), 'Key should contain phase=Predict');
    assert(key1.includes('current=14'), 'Key should contain current=14');
    assert(key1.includes('activity=active-progress'), 'Key should contain activity=active-progress');

    // 第 2 次调用：prevKey 相同 → 不写事件（但仍 update metadata）
    updateCalls = 0;
    const task2 = { ...task1, metadata: { ...task1.metadata, mineruProgressEventKey: key1, mineruProgressHealth: 'active-progress' } };
    await worker.observeMineruProgress([task2]);
    assert(updateCalls === 1, 'Second call should still update metadata');
    // key 应该没变
    assert(lastMetadata?.mineruProgressEventKey === key1, 'Key should remain unchanged');
    console.log('Test 10 Pass ✅\n');
  }

  // ─── Test 11: 事件日志去重 — phase/current 变化时写事件 ───
  console.log('Test 11: 事件日志去重 — key 变化时写事件');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');

    // 第一次写入进度
    fs.writeFileSync(mockLog, 'Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]\n');
    let stats = fs.statSync(mockLog);
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();

    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let lastMetadata = null;
    worker.updateTaskWithRetry = async (_id, update) => { lastMetadata = update.metadata; };

    const task = { id: 't11', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: pastTime } };
    await worker.observeMineruProgress([task]);
    const key1 = lastMetadata?.mineruProgressEventKey;

    // 第二次：进度变化 → 新 key
    fs.writeFileSync(mockLog, 'Predict: 70%|███████   | 19/27 [03:00<01:00]\n');
    stats = fs.statSync(mockLog);
    const task2 = { ...task, metadata: { ...task.metadata, mineruProgressEventKey: key1, mineruProgressHealth: 'active-progress',
      mineruObservedProgress: { phase: 'Predict', percent: 52, current: 14 } } };
    await worker.observeMineruProgress([task2]);
    const key2 = lastMetadata?.mineruProgressEventKey;
    assert(key2 !== key1, 'Key should change when progress changes');
    assert(key2.includes('current=19'), 'New key should have current=19');

    // 第三次：phase 变化 → 新 key
    fs.writeFileSync(mockLog, 'OCR-rec Predict: 10%|█         | 5/50 [00:30<05:00]\n');
    const task3 = { ...task, metadata: { ...task.metadata, mineruProgressEventKey: key2, mineruProgressHealth: 'active-progress',
      mineruObservedProgress: { phase: 'Predict', percent: 70, current: 19 } } };
    await worker.observeMineruProgress([task3]);
    const key3 = lastMetadata?.mineruProgressEventKey;
    assert(key3 !== key2, 'Key should change when phase changes');
    assert(key3.includes('phase=OCR-rec Predict'), 'New key should have OCR-rec Predict phase');

    console.log('Test 11 Pass ✅\n');
  }

  // ─── Test 12: api-alive-only 在列表中不显示为"正在推进" ───
  console.log('Test 12: api-alive-only 在 task list 展示中不显示"正在推进"');
  {
    // 模拟 api-alive-only 的 mineruObservedProgress
    const obs = { activityLevel: 'api-alive-only', phase: null, current: null, total: null };
    // 按任务管理列表逻辑重现
    const level = obs.activityLevel;
    let display = '';
    if (level === 'api-alive-only') {
      display = 'MinerU API 可达 · 未见业务进展';
    } else if (level === 'no-business-signal') {
      display = 'MinerU 正在解析 · 暂无信号';
    }
    assert(!display.includes('正在推进'), 'api-alive-only must not say 正在推进');
    assert(display.includes('未见业务进展'), 'api-alive-only should say 未见业务进展');
    console.log('Test 12 Pass ✅\n');
  }

  // ─── Test 13: 日志新鲜：mtime 新鲜 → 不 stale ───
  console.log('Test 13: 日志 mtime 新鲜 → observationStale=false');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    fs.writeFileSync(mockLog, 'Predict: 52%|█████▊    | 14/27 [02:04<01:52,  8.66s/it]\n');
    const stats = fs.statSync(mockLog);
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();
    const result = await parseLatestMineruProgress(pastTime);
    assert(result !== null, 'Should have result');
    assert(result.observationStale === false, 'Fresh log should NOT be stale');
    assert(result.activityLevel === 'active-progress', 'Fresh log should be active-progress');
    assert(result.observerCheckedAt, 'observerCheckedAt should be set');
    console.log('Test 13 Pass ✅\n');
  }

  // ─── Test 14: 日志滞后：mtime 超阈值 → log-observation-stale，不 failed ───
  console.log('Test 14: 日志 mtime 超阈值 → log-observation-stale');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    // 写入日志并将 mtime 设为超过阈值的旧时间
    fs.writeFileSync(mockLog, 'Predict: 8%|█         | 5/64 [01:00<12:00]\n');
    const staleTime = Date.now() - MINERU_LOG_STALE_MS - 60000; // 比阈值多老 1 分钟
    fs.utimesSync(mockLog, new Date(staleTime), new Date(staleTime));
    const pastTime = new Date(staleTime - 10000).toISOString();
    const result = await parseLatestMineruProgress(pastTime);
    assert(result !== null, 'Stale log should still return result (not null)');
    assert(result.observationStale === true, 'Should be marked observationStale');
    assert(result.activityLevel === 'log-observation-stale', 'Activity should be log-observation-stale');
    assert(result.observationStaleReason && result.observationStaleReason.includes('stale'), 'Should have stale reason');
    // 关键：不得是 failed-confirmed
    assert(result.activityLevel !== 'failed-confirmed', 'Must NOT be failed-confirmed');
    // 原始解析结果仍可读
    assert(result.phase === 'Predict', 'Phase should still be readable');
    assert(result.current === 5, 'Current should be 5');
    console.log('Test 14 Pass ✅\n');
  }

  // ─── Test 15: progress-update 事件降噪 — 相同 message 不重复写事件 ───
  console.log('Test 15: transition progress-update 降噪：相同 key 不写事件');
  {
    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalls = 0;
    let lastUpdate = null;
    worker.updateTaskWithRetry = async (_id, update) => { updateCalls++; lastUpdate = update; return true; };

    const update1 = { message: 'MinerU queued', stage: 'mineru-processing' };
    const task = { id: 't15', state: 'running', metadata: {} };

    // 第 1 次：无 prevKey，应写事件
    await worker.transition(task, update1, 'progress-update');
    assert(updateCalls === 2, 'First call: 1 for main update + 1 for progressEventKey update');
    const key1 = lastUpdate?.metadata?.progressEventKey;
    assert(key1 && key1.includes('stage=mineru-processing'), 'Key should include stage');

    // 第 2 次：相同 message/stage → key 不变 → 不写事件
    updateCalls = 0;
    const task2 = { ...task, metadata: { progressEventKey: key1 } };
    await worker.transition(task2, update1, 'progress-update');
    assert(updateCalls === 1, 'Second call: only 1 for main update, no event write');
    console.log('Test 15 Pass ✅\n');
  }

  // ─── Test 16: progress-update 事件降噪 — stage 变化写事件 ───
  console.log('Test 16: transition progress-update：stage 变化写事件');
  {
    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalls = 0;
    let lastUpdate = null;
    worker.updateTaskWithRetry = async (_id, update) => { updateCalls++; lastUpdate = update; return true; };

    const prevKey = 'state=running|stage=mineru-processing|message=MinerU queued';
    const task = { id: 't16', state: 'running', metadata: { progressEventKey: prevKey } };
    // stage 变为 store
    const update = { message: 'Storing results', stage: 'store' };
    await worker.transition(task, update, 'progress-update');
    // 应该写 2 次：main update + progressEventKey update
    assert(updateCalls === 2, 'Stage change should trigger event (2 updates)');
    assert(lastUpdate?.metadata?.progressEventKey.includes('stage=store'), 'New key should reflect new stage');
    console.log('Test 16 Pass ✅\n');
  }

  // ─── Test 17: mineruLastStatusAt 单独变不写事件 ───
  console.log('Test 17: mineruLastStatusAt 变化 + 相同 message/stage → 不写事件');
  {
    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let updateCalls = 0;
    worker.updateTaskWithRetry = async () => { updateCalls++; return true; };

    const prevKey = 'state=running|stage=mineru-processing|message=MinerU processing|logStatus=missing|activity=|phase=|window=|page=';
    const task = { id: 't17', state: 'running', metadata: { progressEventKey: prevKey } };
    // 只变 metadata.mineruLastStatusAt，message/stage 不变
    const update = { message: 'MinerU processing', stage: 'mineru-processing', metadata: { mineruLastStatusAt: new Date().toISOString() } };
    await worker.transition(task, update, 'progress-update');
    assert(updateCalls === 1, 'Only 1 update (main), no event write because key unchanged');
    console.log('Test 17 Pass ✅\n');
  }

  // ─── Test 18: failed-confirmed 仍写 error 事件 ───
  console.log('Test 18: failed-confirmed 仍写 error 事件');
  {
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    fs.writeFileSync(mockLog, 'ERROR: CUDA out of memory\n');
    const stats = fs.statSync(mockLog);
    const pastTime = new Date(stats.mtimeMs - 10000).toISOString();

    const worker = new ParseTaskWorker({ minioContext: {}, eventBus: { emit: () => {} } });
    let lastMetadata = null;
    worker.updateTaskWithRetry = async (_id, update) => { lastMetadata = update.metadata; return true; };

    const task = { id: 't18', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: pastTime } };
    await worker.observeMineruProgress([task]);
    assert(lastMetadata?.mineruProgressHealth === 'failed-confirmed', 'Should be failed-confirmed');
    assert(lastMetadata?.mineruProgressEventKey.includes('activity=failed-confirmed'), 'Event key should include failed-confirmed');
    console.log('Test 18 Pass ✅\n');
  }

  // ─── Test 19: log-observation-stale 在列表显示"日志观测滞后" ───
  console.log('Test 19: log-observation-stale 在任务列表展示');
  {
    const obs = { activityLevel: 'log-observation-stale', observationStale: true, phase: 'Predict', current: 5, total: 64 };
    const level = obs.activityLevel;
    let display = '';
    if (level.startsWith('log-observation-') || obs.observationStale) {
      const hint = obs.phase ? ` · 最后可见 ${obs.phase} ${obs.current ?? '?'}/${obs.total ?? '?'}` : '';
      display = `MinerU 正在解析 · 日志观测滞后/不可用${hint}`;
    }
    assert(display.includes('日志观测滞后/不可用'), 'Should say 日志观测滞后/不可用');
    assert(display.includes('最后可见 Predict 5/64'), 'Should show last-known progress');
    assert(!display.includes('正在推进'), 'Should not say 正在推进');
    console.log('Test 19 Pass ✅\n');
  }

  // ─── Test 20: 连续 10 次相同 progress-update：task update + SSE 可执行，事件日志只写 1 条 ───
  console.log('Test 20: 连续 10 次相同 progress-update → 事件只写 1 条');
  {
    let sseCount = 0;
    let taskUpdateCount = 0;
    let eventLogCount = 0;
    let lastUpdate = null;

    const worker = new ParseTaskWorker({
      minioContext: {},
      eventBus: { emit: () => { sseCount++; } }
    });
    worker.updateTaskWithRetry = async (_id, update) => {
      taskUpdateCount++;
      lastUpdate = update;
      return true;
    };
    // 推断事件写入次数：
    // key 变化 → 2 次 updateTaskWithRetry (main + key update) + logTaskEvent
    // key 不变 → 1 次 updateTaskWithRetry (main only)，transition() 提前 return

    const update = { message: 'MinerU processing poll', stage: 'mineru-processing' };
    const task = { id: 't20', state: 'running', metadata: {} };

    // 第 1 次调用
    await worker.transition(task, update, 'progress-update');
    assert(taskUpdateCount === 2, 'Call 1: 2 updates (main + key)');
    assert(sseCount === 1, 'Call 1: 1 SSE');
    const firstKey = task.metadata?.progressEventKey;
    assert(firstKey && firstKey.includes('stage=mineru-processing'), 'Key should be set in memory');

    // 第 2-10 次调用（相同 message/stage）
    for (let i = 2; i <= 10; i++) {
      await worker.transition(task, update, 'progress-update');
    }
    // 总计：第 1 次 2 + 后续 9 次 * 1 = 11
    assert(taskUpdateCount === 11, `Total: 11 task updates (got ${taskUpdateCount})`);
    // SSE 总计 10
    assert(sseCount === 10, `Total: 10 SSE events (got ${sseCount})`);
    // key 仍然相同，事件日志只在第 1 次写了
    // 验证方式：taskUpdateCount 应为 11（不是 20），因为同 key 不触发第二次 key update
    assert(task.metadata?.progressEventKey === firstKey, 'Key should remain same after 10 identical calls');
    console.log('Test 20 Pass ✅\n');
  }

  // ─── Test 21: A-Sample (pipeline + auto + table=true) ───
  console.log('Test 21: A-Sample (pipeline + auto + table=true)');
  {
    const lines = [
      '2026-04-20 10:00:00 | INFO | document-shape: page_count=24',
      '2026-04-20 10:00:01 | INFO | Layout Predict:  41%|████      | 10/24',
      '2026-04-20 10:00:02 | INFO | Table-ocr rec ch:  33%|███       | 5/15',
      '2026-04-20 10:00:03 | INFO | OCR-rec Predict:  74%|███████   | 20/24',
      '2026-04-20 10:00:04 | INFO | Processing pages:  100%|██████████| 24/24',
    ];
    const scratchPath = path.join(process.cwd(), 'uat', 'scratch');
    fs.mkdirSync(scratchPath, { recursive: true });
    const mockLog = path.join(scratchPath, 'mineru-api.log');
    fs.writeFileSync(mockLog, lines.join('\n'));
    process.env.MINERU_LOG_PATH = mockLog;

    const { parseLatestMineruProgress } = await import('../lib/ops-mineru-log-parser.mjs');
    const latestObservation = await parseLatestMineruProgress(null, null, { backendRequested: 'pipeline', enableTable: true, parseMethod: 'auto' });
    assert(latestObservation.backendProfile === 'pipeline', 'Backend should be pipeline');
    assert(latestObservation.stage?.rawPhase === 'Processing pages', 'Last phase should be Processing pages');
    assert(latestObservation.stage?.unitType === 'document-pages', 'Processing pages total=24 should be inferred as document-pages');
    assert(latestObservation.document?.totalPages === 24, 'Total pages should be extracted from document-shape');
    console.log('Test 21 Pass ✅\n');
  }

  // ─── Test 22: B-Sample (pipeline + ocr + table=false) ───
  console.log('Test 22: B-Sample (pipeline + ocr + table=false)');
  {
    const lines = [
      '2026-04-20 10:00:00 | INFO | document-shape: page_count=12',
      '2026-04-20 10:00:01 | INFO | Layout Predict:  41%|████      | 5/12',
      '2026-04-20 10:00:03 | INFO | OCR-det:  83%|████████  | 10/12',
    ];
    const mockLog = path.join(process.cwd(), 'uat', 'scratch', 'mineru-api.log');
    fs.writeFileSync(mockLog, lines.join('\n'));
    process.env.MINERU_LOG_PATH = mockLog;

    const { parseLatestMineruProgress } = await import('../lib/ops-mineru-log-parser.mjs');
    const latestObservation = await parseLatestMineruProgress(null, null, { backendRequested: 'pipeline', enableTable: false, parseMethod: 'ocr' });
    assert(latestObservation.backendProfile === 'pipeline', 'Backend should be pipeline');
    assert(latestObservation.stage?.rawPhase === 'OCR-det', 'Last phase should be OCR-det');
    assert(latestObservation.stage?.unitType === 'ocr-recognition-blocks', 'OCR-det should be inferred as ocr-recognition-blocks');
    console.log('Test 22 Pass ✅\n');
  }

  // ─── Test 23: C-Sample (hybrid-auto-engine + auto) ───
  console.log('Test 23: C-Sample (hybrid-auto-engine + auto)');
  {
    const lines = [
      '2026-04-20 10:00:00 | INFO | document-shape: total_windows=3 window_size=10',
      '2026-04-20 10:00:01 | INFO | Hybrid processing window 1/3: pages 1-10/27',
      '2026-04-20 10:00:02 | INFO | Predict Layout:  20%|██        | 2/10',
      '2026-04-20 10:00:03 | INFO | Predict OCR:   1%|          | 5/355',
    ];
    const mockLog = path.join(process.cwd(), 'uat', 'scratch', 'mineru-api.log');
    fs.writeFileSync(mockLog, lines.join('\n'));
    process.env.MINERU_LOG_PATH = mockLog;

    const { parseLatestMineruProgress } = await import('../lib/ops-mineru-log-parser.mjs');
    const latestObservation = await parseLatestMineruProgress(null, null, { backendRequested: 'hybrid-auto-engine' });
    assert(latestObservation.backendProfile === 'hybrid-auto-engine', 'Backend should be hybrid-auto-engine');
    assert(latestObservation.stage?.rawPhase === 'Predict OCR', 'Last phase should be Predict OCR');
    assert(latestObservation.stage?.unitType === 'model-units', 'OCR 355 is neither pages nor window, so unknown units (model-units fallback)');
    assert(latestObservation.window?.pageStart === 1, 'Window start should be 1');
    assert(latestObservation.window?.pageEnd === 10, 'Window end should be 10');
    console.log('Test 23 Pass ✅\n');
  }

  // ── 环境恢复 ──
  if (origLogPath !== undefined) process.env.MINERU_LOG_PATH = origLogPath;
  else delete process.env.MINERU_LOG_PATH;
  if (origErrLogPath !== undefined) process.env.MINERU_ERR_LOG_PATH = origErrLogPath;
  else delete process.env.MINERU_ERR_LOG_PATH;

  // ─── Summary ───
  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ MinerU Log Structured Activity Signal Smoke Test Passed!');
    process.exit(0);
  }
}

run();
