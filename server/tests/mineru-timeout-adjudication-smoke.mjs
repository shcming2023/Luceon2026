/**
 * mineru-timeout-adjudication-smoke.mjs
 * 
 * P0 MinerU 长耗时状态裁决与错误 failed 纠偏的冒烟测试。
 * P1 Material 状态同步与旧 errorMessage 清理验证。
 * 
 * 测试场景：
 * 1. MinerU processing 超过本地 timeout → Luceon 不进入 failed
 * 2. failed + mineruTaskId + MinerU completed → 纠偏并入库 + Material 同步
 * 3. failed + mineruTaskId + MinerU failed → 保持 failed 且 message 有明确证据
 * 4. failed + mineruTaskId + MinerU processing → 纠偏回 running + Material 同步
 * 5. failed + MinerU completed + resume 完成 → Material.status/mineruStatus/aiStatus 完全恢复
 * 6. AI Job 创建失败但 parsed 已入库 → Material.mineruStatus 不回滚
 */

import { ParseTaskWorker } from '../services/queue/task-worker.mjs';
import { MineruStillProcessingError } from '../services/mineru/local-adapter.mjs';

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
  console.log('=== P0/P1 MinerU Timeout Adjudication, Material Sync & Error Cleanup Smoke Test ===\n');

  // ─── Test 1: MinerU processing 超过本地 timeout → Luceon 不进入 failed ───
  console.log('Test 1: MinerU processing 超过本地 timeout → Luceon 不进入 failed');
  {
    const updates = [];
    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { updates.push(update); return true; },
      updateMaterial: async (_id, update) => { updates.push({ _material: true, ...update }); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: {
        getFileStream: async () => ({}),
        saveMarkdown: async () => {},
        saveObject: async () => {},
      },
      taskClient: mockTaskClient,
      mineruProcessor: async () => {
        throw new MineruStillProcessingError('mineru-task-abc', 'processing');
      }
    });

    const task = {
      id: 'test-timeout-1',
      engine: 'local-mineru',
      state: 'pending',
      stage: 'upload',
      progress: 0,
      materialId: 'mat-timeout-1',
      optionsSnapshot: {
        localTimeout: 1,
        localEndpoint: 'http://localhost:8083',
        material: {
          fileName: 'big.pdf',
          mimeType: 'application/pdf',
          metadata: { objectName: 'originals/mat-timeout-1/source.pdf' }
        }
      },
      metadata: {},
    };

    await worker.processTask(task);

    const hasFailed = updates.some(u => u.state === 'failed');
    assert(!hasFailed, 'Task should NOT enter failed when MinerU is still processing');

    const hasRunning = updates.some(u => u.state === 'running' && (u.stage === 'mineru-processing' || u.stage === 'mineru-queued'));
    assert(hasRunning, 'Task should remain running with mineru-processing/queued stage');

    const timeoutUpdate = updates.find(u => u.message?.includes('MinerU 仍在'));
    assert(timeoutUpdate !== undefined, 'Task message should mention MinerU still processing');

    const materialFailed = updates.some(u => u._material && u.status === 'failed');
    assert(!materialFailed, 'Material should NOT be marked failed');

    console.log('Test 1 Pass ✅\n');
  }

  // ─── Test 2: failed + mineruTaskId + MinerU completed → 纠偏 + Material 同步 ───
  console.log('Test 2: failed + mineruTaskId + MinerU completed → 纠偏 + Material 同步');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-completed-xyz')) {
        return { ok: true, status: 200, json: async () => ({ status: 'completed', started_at: '2026-04-25T01:00:00Z' }) };
      }
      return originalFetch(url);
    };

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: {
        getFileStream: async () => ({}),
        saveMarkdown: async () => {},
        saveObject: async () => {},
      },
      taskClient: mockTaskClient,
      mineruResumer: async () => {
        resumeCalled = true;
        return { objectName: 'parsed/mat-2/full.md', mineruTaskId: 'mineru-completed-xyz', parsedPrefix: 'parsed/mat-2/', parsedFilesCount: 1, parsedArtifacts: [], zipObjectName: null, artifactIncomplete: false, markdown: '# Test' };
      }
    });

    const failedTasks = [{
      id: 'test-failed-completed-2',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-2',
      message: '超时未完成',
      errorMessage: '超时未完成 (等待超过 3600s)',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'big.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-2/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-completed-xyz' },
    }];

    await worker.recoverMisjudgedFailedTasks(failedTasks);

    // ParseTask 断言
    const hasRunning = taskUpdates.some(u => u.state === 'running');
    assert(hasRunning, 'Failed task should be corrected to running when MinerU completed');

    const hasResultFetching = taskUpdates.some(u => u.stage === 'result-fetching');
    assert(hasResultFetching, 'Task stage should be result-fetching');

    const correctionUpdate = taskUpdates.find(u => u.state === 'running');
    assert(correctionUpdate?.errorMessage === '', 'ParseTask.errorMessage should be cleared');
    assert(correctionUpdate?.metadata?.recoveredFromMisjudgedFailed === true, 'Metadata should have recoveredFromMisjudgedFailed flag');
    assert(correctionUpdate?.metadata?.previousState === 'failed', 'Metadata should preserve previousState');
    assert(correctionUpdate?.metadata?.previousErrorMessage?.includes('超时'), 'Metadata should preserve previousErrorMessage');

    // Material 断言
    const matUpdate = materialUpdates.find(u => u.status === 'processing');
    assert(matUpdate !== undefined, 'Material.status should be updated to processing');
    assert(matUpdate?.mineruStatus === 'completed', 'Material.mineruStatus should be completed');
    assert(matUpdate?.aiStatus === 'pending', 'Material.aiStatus should be pending (not failed)');
    assert(!matUpdate?.metadata?.processingMsg?.includes('timeout'), 'Material processingMsg should not contain old error');

    await new Promise(r => setTimeout(r, 100));
    assert(resumeCalled, 'resumeMineruTask should be called for completed MinerU task');

    globalThis.fetch = originalFetch;
    console.log('Test 2 Pass ✅\n');
  }

  // ─── Test 3: failed + mineruTaskId + MinerU failed → 保持 failed 且 message 有证据 ───
  console.log('Test 3: failed + mineruTaskId + MinerU failed → 保持 failed 且 message 有明确证据');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-failed-xyz')) {
        return { ok: true, status: 200, json: async () => ({ status: 'failed', error: 'PDF parsing internal error: corrupted file' }) };
      }
      return originalFetch(url);
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

    const failedTasks = [{
      id: 'test-failed-confirmed-3',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-3',
      message: '超时未完成',
      errorMessage: '超时未完成',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'corrupted.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-3/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-failed-xyz' },
    }];

    await worker.recoverMisjudgedFailedTasks(failedTasks);

    // ParseTask 断言
    const hasFailed = taskUpdates.some(u => u.state === 'failed');
    assert(hasFailed, 'Task should remain failed when MinerU also confirms failure');

    const hasRunning = taskUpdates.some(u => u.state === 'running');
    assert(!hasRunning, 'Task should NOT be changed to running');

    const evidenceUpdate = taskUpdates.find(u => u.state === 'failed');
    assert(evidenceUpdate?.message?.includes('MinerU API 明确返回'), 'Failed message should contain MinerU API evidence');
    assert(evidenceUpdate?.message?.includes('corrupted file'), 'Failed message should contain MinerU error detail');
    assert(evidenceUpdate?.metadata?.failureEvidenceSource === 'MinerU API', 'Metadata should have failureEvidenceSource');

    // Material 不应被纠偏为 processing
    const matProcessing = materialUpdates.some(u => u.status === 'processing');
    assert(!matProcessing, 'Material should NOT be changed to processing when MinerU confirmed failed');

    globalThis.fetch = originalFetch;
    console.log('Test 3 Pass ✅\n');
  }

  // ─── Test 4: failed + mineruTaskId + MinerU processing → 纠偏回 running + Material 同步 ───
  console.log('Test 4: failed + mineruTaskId + MinerU still processing → 纠偏 + Material 同步');
  {
    const taskUpdates = [];
    const materialUpdates = [];
    let resumeCalled = false;

    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url) => {
      const urlStr = url.toString();
      if (urlStr.includes('/tasks/mineru-processing-xyz')) {
        return { ok: true, status: 200, json: async () => ({ status: 'processing', started_at: '2026-04-25T01:00:00Z' }) };
      }
      return originalFetch(url);
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
        return { objectName: 'parsed/mat-4/full.md', mineruTaskId: 'mineru-processing-xyz', parsedPrefix: 'parsed/mat-4/', parsedFilesCount: 1, parsedArtifacts: [], zipObjectName: null, artifactIncomplete: false, markdown: '# Test' };
      }
    });

    const failedTasks = [{
      id: 'test-failed-processing-4',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-4',
      message: '超时未完成',
      errorMessage: 'timeout error',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'large.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-4/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-processing-xyz' },
    }];

    await worker.recoverMisjudgedFailedTasks(failedTasks);

    // ParseTask 断言
    const hasRunning = taskUpdates.some(u => u.state === 'running' && u.stage === 'mineru-processing');
    assert(hasRunning, 'Failed task should be corrected to running + mineru-processing');

    const correctionUpdate = taskUpdates.find(u => u.state === 'running');
    assert(correctionUpdate?.message?.includes('纠偏恢复'), 'Message should explain the correction');
    assert(correctionUpdate?.errorMessage === '', 'ParseTask.errorMessage should be cleared');
    assert(correctionUpdate?.metadata?.previousState === 'failed', 'Metadata should preserve previousState');
    assert(correctionUpdate?.metadata?.previousErrorMessage === 'timeout error', 'Metadata should preserve previousErrorMessage');

    // Material 断言
    const matUpdate = materialUpdates.find(u => u.status === 'processing');
    assert(matUpdate !== undefined, 'Material.status should be processing');
    assert(matUpdate?.mineruStatus === 'processing', 'Material.mineruStatus should be processing');
    assert(matUpdate?.aiStatus === 'pending', 'Material.aiStatus should be pending (not failed)');

    await new Promise(r => setTimeout(r, 100));
    assert(resumeCalled, 'resumeMineruTask should be called (not re-POST)');

    globalThis.fetch = originalFetch;
    console.log('Test 4 Pass ✅\n');
  }

  // ─── Test 5: resume 完成后 → Material 完整恢复 ───
  console.log('Test 5: resume 完成后 → Material.status/mineruStatus/aiStatus 完整恢复');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: {
        getFileStream: async () => ({}),
        saveMarkdown: async () => {},
        saveObject: async () => {},
      },
      taskClient: mockTaskClient,
      mineruResumer: async ({ updateProgress }) => {
        await updateProgress({ stage: 'store', state: 'result-store', progress: 90, message: '正在保存...' });
        return {
          objectName: 'parsed/mat-5/full.md',
          mineruTaskId: 'mineru-resume-5',
          parsedPrefix: 'parsed/mat-5/',
          parsedFilesCount: 96,
          parsedArtifacts: [{ objectName: 'parsed/mat-5/full.md', relativePath: 'full.md', size: 1024, mimeType: 'text/markdown' }],
          zipObjectName: 'parsed/mat-5/mineru-result.zip',
          artifactIncomplete: false,
          markdown: '# Full Document'
        };
      }
    });

    const task = {
      id: 'test-resume-5',
      engine: 'local-mineru',
      state: 'failed',
      stage: 'mineru-processing',
      materialId: 'mat-5',
      message: '超时未完成',
      errorMessage: '超时未完成 (等待超过 3600s)',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'doc.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-5/source.pdf' } }
      },
      metadata: {
        mineruTaskId: 'mineru-resume-5',
        recoveredFromMisjudgedFailed: true,
        previousState: 'failed',
        previousErrorMessage: '超时未完成 (等待超过 3600s)',
      },
    };

    await worker.resumeMineruTask(task, 'mineru-resume-5');

    // ParseTask 完成态断言
    const completionUpdate = taskUpdates.find(u => u.state === 'ai-pending');
    assert(completionUpdate !== undefined, 'ParseTask should transition to ai-pending');
    assert(completionUpdate?.stage === 'complete', 'ParseTask stage should be complete');
    assert(completionUpdate?.errorMessage === '', 'ParseTask.errorMessage should be cleared on completion');
    assert(completionUpdate?.message === 'MinerU 解析完成，产物已落库，等待 AI 元数据识别', 'ParseTask.message should reflect current fact');

    // Material 完整恢复断言
    const matFinalUpdate = materialUpdates.find(u => u.mineruStatus === 'completed' && u.status === 'processing');
    assert(matFinalUpdate !== undefined, 'Material should have status=processing, mineruStatus=completed');
    assert(matFinalUpdate?.aiStatus === 'pending', 'Material.aiStatus should be pending');
    assert(matFinalUpdate?.metadata?.processingStage === 'ai', 'Material processingStage should be ai');
    assert(matFinalUpdate?.metadata?.processingMsg === '解析完成，等待 AI 元数据识别', 'Material processingMsg should be current fact');

    // 确保没有 Material status=failed 残留
    const matStillFailed = materialUpdates.some(u => u.status === 'failed');
    assert(!matStillFailed, 'Material should never be set to failed during successful resume');

    console.log('Test 5 Pass ✅\n');
  }

  // ─── Test 6: AI Job 创建失败但 parsed 已入库 → Material.mineruStatus 不回滚 ───
  console.log('Test 6: AI Job 创建失败但 parsed 已入库 → Material.mineruStatus 不回滚');
  {
    const taskUpdates = [];
    const materialUpdates = [];

    const mockTaskClient = {
      getAllTasks: async () => [],
      updateTask: async (_id, update) => { taskUpdates.push(update); return true; },
      updateMaterial: async (_id, update) => { materialUpdates.push(update); return true; },
    };

    const worker = new ParseTaskWorker({
      minioContext: {
        getFileStream: async () => ({}),
        saveMarkdown: async () => {},
        saveObject: async () => {},
      },
      taskClient: mockTaskClient,
      mineruResumer: async ({ updateProgress }) => {
        await updateProgress({ stage: 'store', state: 'result-store', progress: 90, message: '正在保存...' });
        return {
          objectName: 'parsed/mat-6/full.md',
          mineruTaskId: 'mineru-resume-6',
          parsedPrefix: 'parsed/mat-6/',
          parsedFilesCount: 10,
          parsedArtifacts: [{ objectName: 'parsed/mat-6/full.md', relativePath: 'full.md', size: 512, mimeType: 'text/markdown' }],
          zipObjectName: null,
          artifactIncomplete: false,
          markdown: '# Test AI Fail'
        };
      }
    });

    const task = {
      id: 'test-ai-fail-6',
      engine: 'local-mineru',
      state: 'running',
      stage: 'result-fetching',
      materialId: 'mat-6',
      message: '纠偏恢复',
      optionsSnapshot: {
        localEndpoint: 'http://localhost:8083',
        localTimeout: 3600,
        material: { fileName: 'doc.pdf', mimeType: 'application/pdf', metadata: { objectName: 'originals/mat-6/source.pdf' } }
      },
      metadata: { mineruTaskId: 'mineru-resume-6' },
    };

    await worker.resumeMineruTask(task, 'mineru-resume-6');

    // ParseTask 应进入 ai-pending（tryCreateAiJob 失败不会改变这一点）
    const completionUpdate = taskUpdates.find(u => u.state === 'ai-pending');
    assert(completionUpdate !== undefined, 'ParseTask should still reach ai-pending even if AI Job creation fails');

    // Material.mineruStatus 应保持 completed
    const matMineruCompleted = materialUpdates.some(u => u.mineruStatus === 'completed');
    assert(matMineruCompleted, 'Material.mineruStatus should remain completed');

    // Material.status 不应被回滚为 failed
    const matStatusFailed = materialUpdates.some(u => u.status === 'failed');
    assert(!matStatusFailed, 'Material.status should NOT be rolled back to failed');

    // AI 阶段问题应单独表达
    const aiCreateFailed = materialUpdates.some(u => u.aiStatus === 'create-failed');
    assert(aiCreateFailed, 'Material.aiStatus should be create-failed (not material status failed)');

    console.log('Test 6 Pass ✅\n');
  }

  // ─── Summary ───
  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ All P0/P1 MinerU Timeout Adjudication & Material Sync tests passed!');
    process.exit(0);
  }
}

runTest().catch(err => {
  console.error('Test runner error:', err);
  process.exit(1);
});
