/**
 * mineru-completed-takeover-smoke.mjs
 * 
 * 测试 MinerU completed 结果的接管与错误 failed 纠偏闭环。
 * 确保在各种恢复路径中：
 * 1. 成功纠偏并拉取结果
 * 2. 绝对不重新触发 POST /tasks
 */

import { ParseTaskWorker } from '../services/queue/task-worker.mjs';

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`❌ FAILED: ${message}`);
    testsFailed++;
  } else {
    testsPassed++;
  }
}

async function runTest() {
  console.log('=== P0 MinerU Completed Takeover & Misjudged Failed Correction Smoke Test ===\n');

  // ─── Test 1: failed + mineruTaskId + MinerU completed ───
  console.log('Test 1: failed + mineruTaskId + MinerU completed');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;
    let postTasksCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      const urlStr = url.toString();
      if (options?.method === 'POST' && urlStr.includes('/tasks')) {
        postTasksCalled = true;
      }
      if (urlStr.includes('/tasks/mineru-completed-1')) {
        return { ok: true, status: 200, json: async () => ({ status: 'completed', started_at: '2026-04-25T01:00:00Z' }) };
      }
      return originalFetch(url, options);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
      mineruResumer: async () => {
        resumeCalled = true;
        return { objectName: 'parsed/mat-1/full.md', mineruTaskId: 'mineru-completed-1', parsedPrefix: 'parsed/mat-1/', parsedFilesCount: 5, parsedArtifacts: [], zipObjectName: null, artifactIncomplete: false, markdown: '# Test 1' };
      }
    });

    const failedTasks = [{
      id: 'test-failed-completed-1',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-1',
      message: '超时未完成',
      errorMessage: '超时未完成 (等待超过 3600s)',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'big.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-1/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-completed-1' },
    }];

    await worker.recoverMisjudgedFailedTasks(failedTasks);

    // 断言
    assert(!postTasksCalled, 'Should NOT call POST /tasks');
    assert(resumeCalled, 'Should call resume/result-fetching');
    
    const runningUpdate = taskUpdates.find(u => u.state === 'running');
    assert(runningUpdate !== undefined, 'Task should be corrected to running');
    assert(runningUpdate?.stage === 'result-fetching', 'Task stage should be result-fetching');
    assert(runningUpdate?.metadata?.recoveredFromMisjudgedFailed === true, 'Should mark recoveredFromMisjudgedFailed');
    assert(runningUpdate?.metadata?.previousErrorMessage?.includes('超时'), 'Should preserve previous error message');
    
    const matUpdate = materialUpdates.find(u => u.status === 'processing');
    assert(matUpdate !== undefined, 'Material status should be updated');
    assert(matUpdate?.mineruStatus === 'completed', 'Material mineruStatus should be completed');

    globalThis.fetch = originalFetch;
    console.log('Test 1 Pass ✅\n');
  }

  // ─── Test 2: running/mineru-processing + mineruTaskId + MinerU completed ───
  console.log('Test 2: running/mineru-processing + mineruTaskId + MinerU completed');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;
    let postTasksCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      const urlStr = url.toString();
      if (options?.method === 'POST' && urlStr.includes('/tasks')) {
        postTasksCalled = true;
      }
      if (urlStr.includes('/tasks/mineru-completed-2')) {
        return { ok: true, status: 200, json: async () => ({ status: 'completed', started_at: '2026-04-25T01:00:00Z' }) };
      }
      return originalFetch(url, options);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
      mineruResumer: async () => {
        resumeCalled = true;
        return { objectName: 'parsed/mat-2/full.md', mineruTaskId: 'mineru-completed-2', parsedPrefix: 'parsed/mat-2/', parsedFilesCount: 10, parsedArtifacts: [], zipObjectName: null, artifactIncomplete: false, markdown: '# Test 2' };
      }
    });

    const staleTask = {
      id: 'test-stale-running-2',
      engine: 'local-mineru',
      state: 'running',
      stage: 'mineru-processing',
      materialId: 'mat-2',
      message: 'MinerU 正在解析',
      updatedAt: new Date(Date.now() - 3600000).toISOString(), // 1 hr ago
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'big.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-2/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-completed-2' },
    };

    // Use _adjudicateStaleWithMineruApi which is called by recoverStaleRunningTasks
    await worker._adjudicateStaleWithMineruApi(staleTask, 'mineru-completed-2', 'stale-running-adjudication');

    assert(!postTasksCalled, 'Should NOT call POST /tasks');
    assert(resumeCalled, 'Should call resume/result-fetching');

    const fetchUpdate = taskUpdates.find(u => u.stage === 'result-fetching');
    assert(fetchUpdate !== undefined, 'Task should transition to result-fetching');
    assert(!fetchUpdate?.message?.includes('pending'), 'Message should not be pending');
    assert(fetchUpdate?.metadata?.mineruStatus === 'completed', 'Metadata mineruStatus should be completed');

    globalThis.fetch = originalFetch;
    console.log('Test 2 Pass ✅\n');
  }

  // ─── Test 3: failed + mineruTaskId + MinerU failed ───
  console.log('Test 3: failed + mineruTaskId + MinerU failed');
  {
    const taskUpdates = [];
    let postTasksCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      const urlStr = url.toString();
      if (options?.method === 'POST' && urlStr.includes('/tasks')) {
        postTasksCalled = true;
      }
      if (urlStr.includes('/tasks/mineru-failed-3')) {
        return { ok: true, status: 200, json: async () => ({ status: 'failed', error: 'Internal failure' }) };
      }
      return originalFetch(url, options);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async () => true,
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    const failedTasks = [{
      id: 'test-failed-3',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-3',
      message: '超时未完成',
      errorMessage: '超时未完成',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'bad.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-3/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-failed-3' },
    }];

    await worker.recoverMisjudgedFailedTasks(failedTasks);

    assert(!postTasksCalled, 'Should NOT call POST /tasks');
    
    const failUpdate = taskUpdates.find(u => u.state === 'failed');
    assert(failUpdate !== undefined, 'Task should remain failed');
    assert(failUpdate?.errorMessage?.includes('Internal failure'), 'Should preserve MinerU failure evidence');

    globalThis.fetch = originalFetch;
    console.log('Test 3 Pass ✅\n');
  }

  // ─── Test 4: failed + mineruTaskId + MinerU 404 ───
  console.log('Test 4: failed + mineruTaskId + MinerU 404');
  {
    const taskUpdates = [];
    let postTasksCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      const urlStr = url.toString();
      if (options?.method === 'POST' && urlStr.includes('/tasks')) {
        postTasksCalled = true;
      }
      if (urlStr.includes('/tasks/mineru-404')) {
        return { ok: false, status: 404 };
      }
      return originalFetch(url, options);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async () => true,
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
    });

    const failedTasks = [{
      id: 'test-failed-404',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-4',
      message: '超时未完成',
      errorMessage: '超时未完成',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'lost.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-4/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-404' },
    }];

    await worker.recoverMisjudgedFailedTasks(failedTasks);

    assert(!postTasksCalled, 'Should NOT call POST /tasks');
    
    const failUpdate = taskUpdates.find(u => u.state === 'failed');
    assert(failUpdate !== undefined, 'Task should remain failed');
    assert(failUpdate?.message?.includes('404') || failUpdate?.metadata?.failureEvidenceSource?.includes('404'), 'Should reflect 404 manual-audit semantics');

    globalThis.fetch = originalFetch;
    console.log('Test 4 Pass ✅\n');
  }

  // ─── Summary ───
  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ All P0 MinerU Completed Takeover tests passed!');
    process.exit(0);
  }
}

runTest().catch(err => {
  console.error('Test runner error:', err);
  process.exit(1);
});
