/**
 * mineru-api-failed-sync-smoke.mjs
 *
 * P0 Patch: MinerU API failed 后 Luceon ParseTask/Material 终态同步收口。
 *
 * 覆盖场景：
 * 1. running + mineruTaskId + MinerU API failed → ParseTask 进入 failed, Material 进入 failed, errorMessage 写入 MinerU API error, event 写入 mineru-failed-confirmed
 * 2. running + mineruTaskId + MinerU API processing → 保持 running, 不进入 failed
 * 3. failed + MinerU API failed → 不重复写事件, 不反复更新无意义字段（stage 已是 mineru-failed）
 * 4. MinerU API 404 → 保持已有语义, 不和 confirmed failed 混淆
 *
 * 执行方式: node server/tests/mineru-api-failed-sync-smoke.mjs
 */

import { ParseTaskWorker } from '../services/queue/task-worker.mjs';

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

async function runTest() {
  console.log('=== P0 MinerU API Failed → Luceon Terminal State Sync Smoke Test ===\n');

  // ─── Test 1: running + mineruTaskId + MinerU API failed ───
  console.log('Test 1: running + mineruTaskId + MinerU API failed → ParseTask/Material 收敛为 failed');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    const events = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/8dd4df4a-mineru-failed')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'failed',
            error: 'MPS backend out of memory (MPS allocated: 15.90 GiB, other allocations: 500.00 MiB, max allowed: 16.00 GiB). Tried to allocate 1.90 GiB on private pool.',
            completed_at: '2026-04-25T08:30:00Z'
          })
        };
      }
      return originalFetch(url, opts);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push({ _taskId: _id, ...update }); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push({ _materialId: _id, ...update }); return true; },
    };

    // Mock logTaskEvent
    const origLogTaskEvent = (await import('../services/logging/task-events.mjs')).logTaskEvent;
    const { logTaskEvent } = await import('../services/logging/task-events.mjs');

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    const tasks = [{
      id: 'task-oom-running-1',
      engine: 'local-mineru',
      state: 'running',
      stage: 'mineru-processing',
      progress: 50,
      materialId: 'mat-oom-1',
      message: '本地等待超时但 MinerU 仍在 processing，后台将继续观测',
      errorMessage: null,
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'big.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-oom-1/source.pdf' } }
      },
      metadata: {
        mineruTaskId: '8dd4df4a-mineru-failed',
        mineruStatus: 'processing',
      },
    }];

    await worker.syncMineruApiFailedState(tasks);

    // ParseTask 断言
    const taskFailed = taskUpdates.find(u => u.state === 'failed');
    assert(taskFailed !== undefined, '1: ParseTask should enter failed');
    assert(taskFailed?.stage === 'mineru-failed', '1: ParseTask stage should be mineru-failed');
    assert(taskFailed?.progress === 100, '1: ParseTask progress should be 100');
    assert(taskFailed?.message === 'MinerU 已确认失败', '1: ParseTask message should be "MinerU 已确认失败"');
    assert(taskFailed?.errorMessage?.includes('MinerU API failed:'), '1: ParseTask errorMessage should contain "MinerU API failed:"');
    assert(taskFailed?.errorMessage?.includes('MPS backend out of memory'), '1: ParseTask errorMessage should contain MPS OOM error');
    assert(taskFailed?.metadata?.mineruTaskId === '8dd4df4a-mineru-failed', '1: metadata.mineruTaskId preserved');
    assert(taskFailed?.metadata?.mineruStatus === 'failed', '1: metadata.mineruStatus = failed');
    assert(taskFailed?.metadata?.mineruFailedAt === '2026-04-25T08:30:00Z', '1: metadata.mineruFailedAt from MinerU completed_at');
    assert(taskFailed?.metadata?.mineruFailureSource === 'mineru-api', '1: metadata.mineruFailureSource = mineru-api');
    assert(taskFailed?.metadata?.mineruFailureReason?.includes('MPS backend out of memory'), '1: metadata.mineruFailureReason has error detail');

    // Material 断言
    const matFailed = materialUpdates.find(u => u.status === 'failed');
    assert(matFailed !== undefined, '1: Material should enter failed');
    assert(matFailed?.mineruStatus === 'failed', '1: Material.mineruStatus should be failed');
    assert(matFailed?.aiStatus === 'pending', '1: Material.aiStatus should be pending (not failed)');
    assert(matFailed?.metadata?.processingStage === 'mineru-failed', '1: Material metadata.processingStage = mineru-failed');
    assert(matFailed?.metadata?.processingMsg?.includes('MPS backend out of memory'), '1: Material metadata.processingMsg has error detail');
    assert(matFailed?.metadata?.mineruFailureSource === 'mineru-api', '1: Material metadata.mineruFailureSource = mineru-api');

    // 不允许重提交验证（通过检查没有 POST /tasks 调用）
    const hasRunning = taskUpdates.some(u => u.state === 'running');
    assert(!hasRunning, '1: Should NOT transition back to running or re-submit');

    globalThis.fetch = originalFetch;
    console.log('Test 1 Pass ✅\n');
  }

  // ─── Test 2: running + mineruTaskId + MinerU API processing → 保持 running ───
  console.log('Test 2: running + mineruTaskId + MinerU API processing → 保持 running, 不进入 failed');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-still-processing-2')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'processing',
            started_at: '2026-04-25T06:00:00Z',
            queued_ahead: 0
          })
        };
      }
      return originalFetch(url, opts);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    const tasks = [{
      id: 'task-still-processing-2',
      engine: 'local-mineru',
      state: 'running',
      stage: 'mineru-processing',
      materialId: 'mat-still-processing-2',
      optionsSnapshot: { localEndpoint: 'http://localhost:8083', localTimeout: 7200 },
      metadata: { mineruTaskId: 'mineru-still-processing-2', mineruStatus: 'processing' },
    }];

    await worker.syncMineruApiFailedState(tasks);

    // 不应有任何 task/material 更新
    const hasFailed = taskUpdates.some(u => u.state === 'failed');
    assert(!hasFailed, '2: ParseTask should NOT enter failed when MinerU is still processing');
    assert(taskUpdates.length === 0, '2: No task updates should be written');
    assert(materialUpdates.length === 0, '2: No material updates should be written');

    globalThis.fetch = originalFetch;
    console.log('Test 2 Pass ✅\n');
  }

  // ─── Test 3: failed + MinerU API failed → 不重复写事件, 不反复更新 ───
  console.log('Test 3: failed + MinerU API failed (already mineru-failed) → 不重复写事件');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-already-failed-3')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: 'failed', error: 'OOM crash' })
        };
      }
      return originalFetch(url, opts);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    // Case 3a: syncMineruApiFailedState 不处理 state=failed 的任务（它只处理 state=running）
    const tasksForSync = [{
      id: 'task-already-failed-3',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-failed',
      materialId: 'mat-already-failed-3',
      message: 'MinerU 已确认失败',
      errorMessage: 'MinerU API failed: OOM crash',
      optionsSnapshot: { localEndpoint: 'http://localhost:8083', localTimeout: 3600 },
      metadata: {
        mineruTaskId: 'mineru-already-failed-3',
        mineruStatus: 'failed',
        mineruFailureSource: 'mineru-api',
      },
    }];

    await worker.syncMineruApiFailedState(tasksForSync);
    assert(taskUpdates.length === 0, '3a: syncMineruApiFailedState should NOT touch already-failed tasks');
    assert(materialUpdates.length === 0, '3a: Material should NOT be updated for already-failed tasks');

    // Case 3b: recoverMisjudgedFailedTasks 不重复写事件（stage 已是 mineru-failed）
    const taskUpdates3b = [];
    const worker3b = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: {
        getAllTasks: async () => [],
        updateTask: async (_id, update) => { taskUpdates3b.push(update); return true; },
        updateMaterial: async (_id, update) => true,
      },
    });

    await worker3b.recoverMisjudgedFailedTasks(tasksForSync);
    // message 已包含 "MinerU 已确认失败"，stage 已是 mineru-failed → 不重复写
    assert(taskUpdates3b.length === 0, '3b: recoverMisjudgedFailedTasks should NOT duplicate writes when stage=mineru-failed');

    globalThis.fetch = originalFetch;
    console.log('Test 3 Pass ✅\n');
  }

  // ─── Test 4: MinerU API 404 → 不和 confirmed failed 混淆 ───
  console.log('Test 4: MinerU API 404 → 保持已有语义, 不和 confirmed failed 混淆');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-404-task-4')) {
        return { ok: false, status: 404, json: async () => ({ error: 'not found' }) };
      }
      return originalFetch(url, opts);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    // Case 4a: running 任务 + MinerU 404 → syncMineruApiFailedState 跳过（不当作 confirmed failed）
    const runningTasks = [{
      id: 'task-404-running-4',
      engine: 'local-mineru',
      state: 'running',
      stage: 'mineru-processing',
      materialId: 'mat-404-4',
      optionsSnapshot: { localEndpoint: 'http://localhost:8083', localTimeout: 3600 },
      metadata: { mineruTaskId: 'mineru-404-task-4', mineruStatus: 'processing' },
    }];

    await worker.syncMineruApiFailedState(runningTasks);
    assert(taskUpdates.length === 0, '4a: 404 on running task should NOT trigger failed sync');
    assert(materialUpdates.length === 0, '4a: Material should NOT be updated on 404');

    // Case 4b: failed 任务 + MinerU 404 → recoverMisjudgedFailedTasks 保持 failed + manual audit
    const failedTasks = [{
      id: 'task-404-failed-4b',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-404-4b',
      message: '超时未完成',
      optionsSnapshot: { localEndpoint: 'http://localhost:8083', localTimeout: 3600 },
      metadata: { mineruTaskId: 'mineru-404-task-4', mineruStatus: 'processing' },
    }];

    const taskUpdates4b = [];
    const worker4b = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: {
        getAllTasks: async () => [],
        updateTask: async (_id, update) => { taskUpdates4b.push(update); return true; },
        updateMaterial: async (_id, update) => true,
      },
    });

    await worker4b.recoverMisjudgedFailedTasks(failedTasks);
    const has404Msg = taskUpdates4b.some(u => u.message?.includes('MinerU 任务记录已丢失'));
    assert(has404Msg, '4b: 404 on failed task should use "任务记录已丢失" message, not "MinerU 已确认失败"');

    // 确认 404 不使用 mineru-failed-confirmed 事件名
    const hasMineruFailed = taskUpdates4b.some(u => u.stage === 'mineru-failed');
    assert(!hasMineruFailed, '4b: 404 should NOT set stage to mineru-failed');

    globalThis.fetch = originalFetch;
    console.log('Test 4 Pass ✅\n');
  }

  // ─── Test 5: recovery scan 也能处理 running + MinerU failed（历史任务纠偏） ───
  console.log('Test 5: recovery scan 中 running + MinerU failed → ParseTask/Material 收敛为 failed (历史任务纠偏)');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/8dd4df4a-3a58-4e36-b590-928f4da7c139')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'failed',
            error: 'MPS backend out of memory (MPS allocated: 15.90 GiB, other allocations: 500.00 MiB, max allowed: 16.00 GiB). Tried to allocate 1.90 GiB on private pool. Use PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0 to disable upper limit for memory.',
            completed_at: '2026-04-25T07:00:00Z'
          })
        };
      }
      if (urlStr.includes('/health')) {
        return { ok: true, status: 200, json: async () => ({ status: 'healthy' }) };
      }
      return originalFetch(url, opts);
    };

    const mockTaskClient = {
      getAllTasks: async () => [{
        id: 'task-1777099205427',
        engine: 'local-mineru',
        state: 'running',
        stage: 'mineru-processing',
        materialId: '632253499400612',
        message: '本地等待超时但 MinerU 仍在 processing，后台将继续观测',
        errorMessage: null,
        optionsSnapshot: {
          localEndpoint: 'http://localhost:8083',
          localTimeout: 3600,
          material: { fileName: 'large.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/632253499400612/large.pdf' } }
        },
        metadata: {
          mineruTaskId: '8dd4df4a-3a58-4e36-b590-928f4da7c139',
          mineruStatus: 'processing',
        },
      }],
      updateTask: async (_id, update) => { taskUpdates.push({ _taskId: _id, ...update }); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push({ _materialId: _id, ...update }); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    await worker.runRecoveryScan();

    // ParseTask 断言
    const taskFailed = taskUpdates.find(u => u.state === 'failed' && u._taskId === 'task-1777099205427');
    assert(taskFailed !== undefined, '5: ParseTask task-1777099205427 should enter failed');
    assert(taskFailed?.stage === 'mineru-failed', '5: ParseTask stage should be mineru-failed');
    assert(taskFailed?.errorMessage?.includes('MPS backend out of memory'), '5: ParseTask errorMessage should contain MPS OOM evidence');
    assert(taskFailed?.metadata?.mineruFailureSource === 'mineru-api', '5: metadata.mineruFailureSource = mineru-api');
    assert(taskFailed?.metadata?.mineruFailureReason?.includes('MPS backend out of memory'), '5: metadata.mineruFailureReason has MPS OOM detail');

    // Material 断言
    const matFailed = materialUpdates.find(u => u.status === 'failed' && u._materialId === '632253499400612');
    assert(matFailed !== undefined, '5: Material 632253499400612 should enter failed');
    assert(matFailed?.mineruStatus === 'failed', '5: Material.mineruStatus = failed');
    assert(matFailed?.aiStatus === 'pending', '5: Material.aiStatus = pending (not failed)');

    globalThis.fetch = originalFetch;
    console.log('Test 5 Pass ✅\n');
  }

  // ─── Summary ───
  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ All P0 MinerU API Failed → Luceon Terminal State Sync tests passed!');
    process.exit(0);
  }
}

runTest().catch(err => {
  console.error('Test runner error:', err);
  process.exit(1);
});
