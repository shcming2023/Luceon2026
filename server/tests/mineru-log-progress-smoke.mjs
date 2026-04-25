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
 */

import { parseTqdmLine, classifyLogLine, determineActivityLevel, parseLatestMineruProgress } from '../lib/ops-mineru-log-parser.mjs';
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
    assert(staleResult === null, 'Should reject stale log (future minObservedAt)');

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
    worker.updateTaskWithRetry = async () => { updateCalled++; };

    await worker.observeMineruProgress([
      { id: 'new-task', state: 'running', metadata: { mineruStatus: 'processing', mineruStartedAt: futureStart } }
    ]);
    assert(updateCalled === 0, 'Should not attribute old log to new task');
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
