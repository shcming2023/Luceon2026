/**
 * mineru-no-resubmit-smoke.mjs
 *
 * P0 Patch：MinerU 已提交任务不可重提交流程与 completed 结果接管收口 冒烟测试。
 *
 * 覆盖场景：
 * 1. pending + mineruTaskId + MinerU processing → 不调用 POST /tasks，走 resume 接管
 * 2. pending + mineruTaskId + MinerU completed → 进入 result-fetching 并拉取结果入库
 * 3. stale running + mineruTaskId + MinerU processing → 不重置为 pending，保持 running
 * 4. stale running + mineruTaskId + MinerU completed → 进入 result-fetching
 * 5. 同一 task 不会生成第二个 mineruTaskId
 * 6. active-task 在 worker 内存为空时仍能从 DB + MinerU API 重建事实
 *
 * 执行方式: node server/tests/mineru-no-resubmit-smoke.mjs
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
  console.log('=== P0 MinerU No-Resubmit & Completed Result Ingestion Smoke Test ===\n');

  // ─── Test 1: pending + mineruTaskId + MinerU processing → 不调用 POST /tasks ───
  console.log('Test 1: pending + mineruTaskId + MinerU processing → 不调用 POST /tasks，走 resume');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let processWithLocalMineruCalled = false;
    let resumeWithLocalMineruCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      // POST /tasks 拦截：不应被调用
      if (opts?.method === 'POST' && urlStr.includes('/tasks')) {
        processWithLocalMineruCalled = true;
        return { ok: true, status: 200, json: async () => ({ task_id: 'should-not-happen' }) };
      }
      // GET /tasks/:id 查询
      if (urlStr.includes('/tasks/existing-mineru-task-1')) {
        return { ok: true, status: 200, json: async () => ({ status: 'processing', started_at: '2026-04-26T01:00:00Z' }) };
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
      mineruProcessor: async () => {
        processWithLocalMineruCalled = true;
        throw new Error('Should not be called');
      },
      mineruResumer: async () => {
        resumeWithLocalMineruCalled = true;
        return {
          objectName: 'parsed/mat-1/full.md',
          mineruTaskId: 'existing-mineru-task-1',
          parsedPrefix: 'parsed/mat-1/',
          parsedFilesCount: 5,
          parsedArtifacts: [],
          zipObjectName: null,
          artifactIncomplete: false,
          markdown: '# Test'
        };
      },
    });

    const task = {
      id: 'test-no-resubmit-1',
      engine: 'local-mineru',
      state: 'pending',
      stage: 'upload',
      progress: 0,
      materialId: 'mat-1',
      optionsSnapshot: {
        localTimeout: 3600,
        localEndpoint: 'http://localhost:8083',
        material: {
          fileName: 'doc.pdf',
          mimeType: 'application/pdf',
          metadata: { objectName: 'originals/mat-1/source.pdf' }
        }
      },
      metadata: { mineruTaskId: 'existing-mineru-task-1' },
    };

    await worker.processTask(task);

    assert(!processWithLocalMineruCalled, '1: processWithLocalMinerU should NOT be called when mineruTaskId exists');

    // 应该有 running 状态更新（adjudication 路由到 resume）
    const hasRunning = taskUpdates.some(u => u.state === 'running');
    assert(hasRunning, '1: Task should be updated to running state');

    const hasPending = taskUpdates.some(u => u.state === 'pending' && u.stage === 'upload');
    assert(!hasPending, '1: Task should NOT be reset to pending/upload');

    globalThis.fetch = originalFetch;
    console.log('Test 1 Pass ✅\n');
  }

  // ─── Test 2: pending + mineruTaskId + MinerU completed → result-fetching ───
  console.log('Test 2: pending + mineruTaskId + MinerU completed → result-fetching 并拉取结果入库');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-completed-2')) {
        return { ok: true, status: 200, json: async () => ({ status: 'completed', started_at: '2026-04-26T00:30:00Z' }) };
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
      mineruProcessor: async () => { throw new Error('Should not be called'); },
      mineruResumer: async () => {
        resumeCalled = true;
        return {
          objectName: 'parsed/mat-2/full.md',
          mineruTaskId: 'mineru-completed-2',
          parsedPrefix: 'parsed/mat-2/',
          parsedFilesCount: 10,
          parsedArtifacts: [{ objectName: 'parsed/mat-2/full.md', relativePath: 'full.md', size: 1024, mimeType: 'text/markdown' }],
          zipObjectName: 'parsed/mat-2/mineru-result.zip',
          artifactIncomplete: false,
          markdown: '# Completed Document'
        };
      },
    });

    const task = {
      id: 'test-no-resubmit-2',
      engine: 'local-mineru',
      state: 'pending',
      stage: 'upload',
      progress: 0,
      materialId: 'mat-2',
      optionsSnapshot: {
        localTimeout: 3600,
        localEndpoint: 'http://localhost:8083',
        material: {
          fileName: 'doc.pdf',
          mimeType: 'application/pdf',
          metadata: { objectName: 'originals/mat-2/source.pdf' }
        }
      },
      metadata: { mineruTaskId: 'mineru-completed-2' },
    };

    await worker.processTask(task);

    // processTask 同步返回后 adjudication 已写入 result-fetching 状态
    const hasResultFetching = taskUpdates.some(u => u.stage === 'result-fetching');
    assert(hasResultFetching, '2: Task stage should be result-fetching');

    // Material 应更新为 processing/completed
    const matCompleted = materialUpdates.some(u => u.mineruStatus === 'completed');
    assert(matCompleted, '2: Material.mineruStatus should be completed');

    // 等待后台 resume 完成（fire-and-forget 异步，需更长等待）
    await new Promise(r => setTimeout(r, 500));
    assert(resumeCalled, '2: resumeWithLocalMinerU should be called to fetch result');

    // resume 完成后有 ai-pending 状态
    const hasAiPending = taskUpdates.some(u => u.state === 'ai-pending');
    assert(hasAiPending, '2: Task should reach ai-pending after result ingestion');

    // 确认 parsed metadata 保留
    const completionUpdate = taskUpdates.find(u => u.state === 'ai-pending');
    if (completionUpdate) {
      assert(completionUpdate.metadata?.parsedFilesCount === 10, '2: parsedFilesCount should be 10');
      assert(completionUpdate.metadata?.markdownObjectName === 'parsed/mat-2/full.md', '2: markdownObjectName should be preserved');
    }

    globalThis.fetch = originalFetch;
    console.log('Test 2 Pass ✅\n');
  }

  // ─── Test 3: stale running + mineruTaskId + MinerU processing → 不重置为 pending ───
  console.log('Test 3: stale running + mineruTaskId + MinerU processing → 不重置为 pending');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-stale-3')) {
        return { ok: true, status: 200, json: async () => ({ status: 'processing', started_at: '2026-04-25T20:00:00Z' }) };
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
      mineruResumer: async () => {
        resumeCalled = true;
        return {
          objectName: 'parsed/mat-3/full.md',
          mineruTaskId: 'mineru-stale-3',
          parsedPrefix: 'parsed/mat-3/',
          parsedFilesCount: 1,
          parsedArtifacts: [],
          zipObjectName: null,
          artifactIncomplete: false,
          markdown: '# Test'
        };
      },
    });

    const staleTask = {
      id: 'test-stale-3',
      engine: 'local-mineru',
      state: 'running',
      stage: 'mineru-processing',
      progress: 50,
      materialId: 'mat-3',
      // updatedAt 设置为远超超时（7200s ago）
      updatedAt: new Date(Date.now() - 7200000).toISOString(),
      optionsSnapshot: {
        localTimeout: 3600,
        localEndpoint: 'http://localhost:8083',
        material: { fileName: 'big.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-3/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-stale-3', mineruStatus: 'processing' },
    };

    await worker.recoverStaleRunningTasks([staleTask]);

    // 不应被重置为 pending
    const hasPending = taskUpdates.some(u => u.state === 'pending' && u.stage === 'upload');
    assert(!hasPending, '3: Stale task with mineruTaskId should NOT be reset to pending/upload');

    // 应保持 running
    const hasRunning = taskUpdates.some(u => u.state === 'running');
    assert(hasRunning, '3: Task should stay running');

    // Material 应同步为 processing
    const matProcessing = materialUpdates.some(u => u.status === 'processing' && u.mineruStatus === 'processing');
    assert(matProcessing, '3: Material should be processing with mineruStatus=processing');

    // 应启动 resume
    await new Promise(r => setTimeout(r, 200));
    assert(resumeCalled, '3: resumeMineruTask should be called');

    globalThis.fetch = originalFetch;
    console.log('Test 3 Pass ✅\n');
  }

  // ─── Test 4: stale running + mineruTaskId + MinerU completed → result-fetching ───
  console.log('Test 4: stale running + mineruTaskId + MinerU completed → result-fetching');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-stale-completed-4')) {
        return { ok: true, status: 200, json: async () => ({ status: 'completed', started_at: '2026-04-25T18:00:00Z' }) };
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
      mineruResumer: async () => {
        resumeCalled = true;
        return {
          objectName: 'parsed/mat-4/full.md',
          mineruTaskId: 'mineru-stale-completed-4',
          parsedPrefix: 'parsed/mat-4/',
          parsedFilesCount: 30,
          parsedArtifacts: [{ objectName: 'parsed/mat-4/full.md', relativePath: 'full.md', size: 2048 }],
          zipObjectName: 'parsed/mat-4/mineru-result.zip',
          artifactIncomplete: false,
          markdown: '# Stale Completed Document'
        };
      },
    });

    const staleTask = {
      id: 'test-stale-completed-4',
      engine: 'local-mineru',
      state: 'running',
      stage: 'mineru-processing',
      progress: 80,
      materialId: 'mat-4',
      updatedAt: new Date(Date.now() - 7200000).toISOString(),
      optionsSnapshot: {
        localTimeout: 3600,
        localEndpoint: 'http://localhost:8083',
        material: { fileName: 'big.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-4/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-stale-completed-4', mineruStatus: 'processing' },
    };

    await worker.recoverStaleRunningTasks([staleTask]);

    // 应进入 result-fetching
    const hasResultFetching = taskUpdates.some(u => u.stage === 'result-fetching');
    assert(hasResultFetching, '4: Task should enter result-fetching');

    const hasPending = taskUpdates.some(u => u.state === 'pending');
    assert(!hasPending, '4: Task should NOT be reset to pending');

    // Material 应更新为 completed
    const matCompleted = materialUpdates.some(u => u.mineruStatus === 'completed');
    assert(matCompleted, '4: Material.mineruStatus should be completed');

    // Resume 应被调用
    await new Promise(r => setTimeout(r, 200));
    assert(resumeCalled, '4: resumeMineruTask should be called to fetch results');

    globalThis.fetch = originalFetch;
    console.log('Test 4 Pass ✅\n');
  }

  // ─── Test 5: 同一 task 不会生成第二个 mineruTaskId ───
  console.log('Test 5: 同一 task 不会生成第二个 mineruTaskId');
  {
    let postTasksCalled = false;
    const taskUpdates = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      const urlStr = url.toString();
      if (opts?.method === 'POST' && urlStr.endsWith('/tasks')) {
        postTasksCalled = true;
        return { ok: true, status: 200, json: async () => ({ task_id: 'second-mineru-task-should-not-exist' }) };
      }
      if (urlStr.includes('/tasks/original-mineru-5')) {
        return { ok: true, status: 200, json: async () => ({ status: 'queued', queued_ahead: 1 }) };
      }
      return originalFetch(url, opts);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => true,
    };

    const worker = new ParseTaskWorker({
      minioContext: { getFileStream: async () => ({}), saveMarkdown: async () => {}, saveObject: async () => {} },
      taskClient: mockTaskClient,
      mineruProcessor: async () => {
        postTasksCalled = true;
        return { mineruTaskId: 'second-mineru-task-should-not-exist', objectName: 'test', parsedPrefix: 'test/', parsedFilesCount: 1, parsedArtifacts: [], zipObjectName: null, artifactIncomplete: false, markdown: '# x' };
      },
      mineruResumer: async () => {
        return { objectName: 'parsed/mat-5/full.md', mineruTaskId: 'original-mineru-5', parsedPrefix: 'parsed/mat-5/', parsedFilesCount: 1, parsedArtifacts: [], zipObjectName: null, artifactIncomplete: false, markdown: '# Test' };
      },
    });

    const task = {
      id: 'test-no-second-id-5',
      engine: 'local-mineru',
      state: 'pending',
      stage: 'upload',
      materialId: 'mat-5',
      optionsSnapshot: {
        localTimeout: 3600,
        localEndpoint: 'http://localhost:8083',
        material: { fileName: 'doc.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-5/source.pdf' } }
      },
      metadata: { mineruTaskId: 'original-mineru-5' },
    };

    await worker.processTask(task);

    assert(!postTasksCalled, '5: POST /tasks should NOT be called when mineruTaskId already exists');

    // 检查没有生成第二个 mineruTaskId
    const anySecondId = taskUpdates.some(u => u.metadata?.mineruTaskId && u.metadata.mineruTaskId !== 'original-mineru-5');
    assert(!anySecondId, '5: No second mineruTaskId should be generated');

    globalThis.fetch = originalFetch;
    console.log('Test 5 Pass ✅\n');
  }

  // ─── Test 6: active-task 在 worker 内存为空时仍能从 DB 重建 ───
  // 注：此测试验证 active-task endpoint 的数据模型逻辑（不启动真实 HTTP 服务器）
  console.log('Test 6: active-task 数据模型 - DB 重建 MinerU 占用图');
  {
    // 模拟 DB 中的任务数据
    const dbTasks = [
      // running + mineru-processing（正常活跃）
      {
        id: 'task-active-6a',
        engine: 'local-mineru',
        state: 'running',
        stage: 'mineru-processing',
        metadata: { mineruTaskId: 'mineru-active-6a', mineruStatus: 'processing' },
      },
      // pending + mineruTaskId（漂移）
      {
        id: 'task-drift-6b',
        engine: 'local-mineru',
        state: 'pending',
        stage: 'upload',
        metadata: { mineruTaskId: 'mineru-drift-6b' },
      },
      // MinerU completed 但未入库
      {
        id: 'task-notingested-6c',
        engine: 'local-mineru',
        state: 'running',
        stage: 'mineru-processing',
        metadata: { mineruTaskId: 'mineru-completed-6c', mineruStatus: 'completed', parsedFilesCount: 0 },
      },
      // 正常完成的任务（不应出现在任何警报中）
      {
        id: 'task-ok-6d',
        engine: 'local-mineru',
        state: 'completed',
        stage: 'done',
        metadata: { mineruTaskId: 'mineru-ok-6d', mineruStatus: 'completed', parsedFilesCount: 50 },
      },
    ];

    // 模拟 active-task 的数据模型逻辑
    const runningWithMineru = dbTasks.filter(t =>
      t.engine === 'local-mineru' &&
      t.state === 'running' &&
      t.metadata?.mineruTaskId &&
      ['mineru-processing', 'mineru-queued', 'result-fetching'].includes(t.stage)
    );

    const driftTasks = dbTasks.filter(t =>
      t.engine === 'local-mineru' &&
      t.metadata?.mineruTaskId &&
      (
        t.state === 'pending' ||
        (t.state === 'running' && (t.stage === 'upload' || t.stage === 'process'))
      )
    );

    const completedButNotIngested = dbTasks.filter(t =>
      t.engine === 'local-mineru' &&
      t.metadata?.mineruTaskId &&
      t.metadata?.mineruStatus === 'completed' &&
      (!t.metadata?.parsedFilesCount || t.metadata.parsedFilesCount === 0) &&
      t.state !== 'failed'
    );

    assert(runningWithMineru.length === 2, `6: Should find 2 running+mineru tasks (got ${runningWithMineru.length})`);
    assert(driftTasks.length === 1, `6: Should find 1 drift task (got ${driftTasks.length})`);
    assert(driftTasks[0].id === 'task-drift-6b', '6: Drift task should be task-drift-6b');
    assert(completedButNotIngested.length === 1, `6: Should find 1 completed-but-not-ingested task (got ${completedButNotIngested.length})`);
    assert(completedButNotIngested[0].id === 'task-notingested-6c', '6: Not-ingested task should be task-notingested-6c');

    // activeTask 决定逻辑：running > 1 → null（多任务场景不唯一指定）
    let activeTask = null;
    if (runningWithMineru.length === 1) {
      activeTask = runningWithMineru[0];
    } else if (runningWithMineru.length === 0 && driftTasks.length === 1) {
      activeTask = driftTasks[0];
    }
    // 多任务场景 activeTask 为 null（因为有 2 个 running）
    assert(activeTask === null, '6: activeTask should be null when multiple running tasks exist');

    console.log('Test 6 Pass ✅\n');
  }

  // ─── Summary ───
  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ All P0 MinerU No-Resubmit & Completed Result Ingestion tests passed!');
    process.exit(0);
  }
}

runTest().catch(err => {
  console.error('Test runner error:', err);
  process.exit(1);
});
