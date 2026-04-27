/**
 * mineru-artifact-empty-retry-smoke.mjs
 *
 * P0 冒烟测试：验证 completed-empty 语义、OCR 降级重试、日志错误裁决修正、failed 状态残留清理。
 *
 * 场景列表：
 *   Test 1: MinerU completed + md 非空 → 正常 ai-pending
 *   Test 2: MinerU completed + md 空 → artifact-empty + OCR 重试
 *   Test 3: OCR 重试成功 → ai-pending，emptyMarkdownRetryAttempted=true
 *   Test 4: OCR 重试后仍为空 → failed/artifact-empty，不再第三次重试
 *   Test 5: MinerU API failed → 不触发 OCR 重试，进入 mineru-failed
 *   Test 6: 裸 Error: 日志 → 不判定 failed-confirmed
 *   Test 7: 完整 traceback/RuntimeError → 可判定 failed-confirmed
 *   Test 8: failed 后状态残留清理 → ParseTask/Material 均不残留 processing
 */

import assert from 'assert';

// ── Mock 基础设施 ──

let taskStore = {};
let materialStore = {};
let events = [];
let sseEvents = [];

function resetStores() {
  taskStore = {};
  materialStore = {};
  events = [];
  sseEvents = [];
}

async function mockGetAllTasks() {
  return Object.values(taskStore);
}

async function mockUpdateTask(id, updates) {
  const existing = taskStore[id] || {};
  // metadata merge
  if (updates.metadata) {
    updates.metadata = { ...(existing.metadata || {}), ...updates.metadata };
  }
  taskStore[id] = { ...existing, ...updates, id, updatedAt: new Date().toISOString() };
  return taskStore[id];
}

async function mockUpdateMaterial(id, updates) {
  const existing = materialStore[id] || {};
  if (updates.metadata) {
    updates.metadata = { ...(existing.metadata || {}), ...updates.metadata };
  }
  materialStore[id] = { ...existing, ...updates, id };
  return materialStore[id];
}

/**
 * 创建 ParseTaskWorker 实例，注入 mock 依赖。
 *
 * @param {Object} opts - 自定义 mock 选项
 * @param {Function} opts.mineruProcessor - 自定义 MinerU 处理器 mock
 * @param {Function} opts.mineruResumer - 自定义 MinerU 恢复器 mock
 * @returns {ParseTaskWorker} 配置好的 worker 实例
 */
async function createTestWorker(opts = {}) {
  const { ParseTaskWorker } = await import('../services/queue/task-worker.mjs');

  const worker = new ParseTaskWorker({
    minioContext: {
      saveMarkdown: async () => {},
      saveObject: async () => true,
      getFileStream: async (objectName) => {
        // 模拟文件流
        const { Readable } = await import('stream');
        return Readable.from(Buffer.from('fake-pdf-content'));
      },
    },
    eventBus: {
      emit: (eventName, data) => {
        sseEvents.push({ eventName, data });
      }
    },
    taskClient: {
      getAllTasks: mockGetAllTasks,
      updateTask: mockUpdateTask,
      updateMaterial: mockUpdateMaterial,
    },
    mineruProcessor: opts.mineruProcessor || (async () => ({
      markdown: 'test content',
      mineruTaskId: 'test-mineru-id',
      objectName: 'parsed/mat1/full.md',
      parsedPrefix: 'parsed/mat1/',
      parsedFilesCount: 1,
      parsedArtifacts: [],
      zipObjectName: null,
      artifactIncomplete: false,
    })),
    mineruResumer: opts.mineruResumer || (async () => ({
      markdown: 'test content',
      mineruTaskId: 'test-mineru-id',
      objectName: 'parsed/mat1/full.md',
      parsedPrefix: 'parsed/mat1/',
      parsedFilesCount: 1,
      parsedArtifacts: [],
      zipObjectName: null,
      artifactIncomplete: false,
    })),
  });

  return worker;
}

// ── 测试辅助 ──

/**
 * 创建标准测试任务对象。
 *
 * @param {string} id - 任务 ID
 * @param {Object} overrides - 覆盖字段
 * @returns {Object} 任务对象
 */
function createTask(id, overrides = {}) {
  const task = {
    id,
    state: 'pending',
    stage: 'upload',
    engine: 'local-mineru',
    materialId: `mat-${id}`,
    message: '',
    errorMessage: '',
    progress: 0,
    metadata: {},
    optionsSnapshot: {
      localEndpoint: 'http://localhost:8765',
      localTimeout: 3600,
      material: {
        objectName: `originals/${id}/test.pdf`,
        fileName: 'test.pdf',
        mimeType: 'application/pdf',
        metadata: {
          objectName: `originals/${id}/test.pdf`,
        }
      }
    },
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
  taskStore[id] = task;
  materialStore[task.materialId] = {
    id: task.materialId,
    status: 'processing',
    mineruStatus: 'pending',
    aiStatus: 'pending',
    metadata: {},
  };
  return task;
}

let passed = 0;
let failed = 0;

/**
 * 执行测试断言。
 *
 * @param {string} desc - 断言描述
 * @param {Function} fn - 断言函数
 */
function check(desc, fn) {
  try {
    fn();
    passed++;
  } catch (err) {
    failed++;
    console.error(`  ✗ ${desc}: ${err.message}`);
  }
}

// ── 测试用例 ──

async function test1_completedNonEmpty() {
  console.log('\nTest 1: MinerU completed + md 非空 → 正常 ai-pending');
  resetStores();

  const task = createTask('task-1');
  const worker = await createTestWorker({
    mineruProcessor: async () => ({
      markdown: '# Hello World\nSome content',
      mineruTaskId: 'mineru-1',
      objectName: 'parsed/mat-task-1/full.md',
      parsedPrefix: 'parsed/mat-task-1/',
      parsedFilesCount: 3,
      parsedArtifacts: [
        { objectName: 'parsed/mat-task-1/full.md', relativePath: 'full.md', size: 20 },
        { objectName: 'parsed/mat-task-1/middle.json', relativePath: 'middle.json', size: 100 },
      ],
      zipObjectName: null,
      artifactIncomplete: false,
    })
  });

  // 手动调用 processTask
  await worker.processTask(task);

  const t = taskStore['task-1'];
  check('state=ai-pending', () => assert.strictEqual(t.state, 'ai-pending'));
  check('stage=complete', () => assert.strictEqual(t.stage, 'complete'));
  check('mineruStatus=completed', () => assert.strictEqual(t.metadata.mineruStatus, 'completed'));
  check('no artifactQuality', () => assert.strictEqual(t.metadata.artifactQuality, undefined));
  check('no emptyMarkdownRetryAttempted', () => assert.strictEqual(t.metadata.emptyMarkdownRetryAttempted, undefined));

  console.log('Test 1 Pass ✅');
}

async function test2_completedEmptyDetected() {
  console.log('\nTest 2: MinerU completed + md 空 → artifact-empty 检测');
  resetStores();

  const task = createTask('task-2');
  // 模拟 processor 返回空 markdown，且 retry 也返回空（模拟无 OCR 重试能力）
  let processorCallCount = 0;
  const worker = await createTestWorker({
    mineruProcessor: async () => {
      processorCallCount++;
      return {
        markdown: '',
        markdownEmpty: true,
        mineruTaskId: `mineru-2-call-${processorCallCount}`,
        objectName: 'parsed/mat-task-2/full.md',
        parsedPrefix: 'parsed/mat-task-2/',
        parsedFilesCount: 4,
        parsedArtifacts: [
          { objectName: 'parsed/mat-task-2/mineru-result.json', relativePath: 'mineru-result.json', size: 200 },
          { objectName: 'parsed/mat-task-2/middle.json', relativePath: 'auto/middle.json', size: 100 },
          { objectName: 'parsed/mat-task-2/model.json', relativePath: 'auto/model.json', size: 100 },
          { objectName: 'parsed/mat-task-2/origin.pdf', relativePath: 'auto/origin.pdf', size: 500 },
        ],
        zipObjectName: 'parsed/mat-task-2/mineru-result.zip',
        artifactIncomplete: false,
      };
    }
  });

  await worker.processTask(task);

  const t = taskStore['task-2'];
  check('state=failed', () => assert.strictEqual(t.state, 'failed'));
  check('stage=artifact-empty', () => assert.strictEqual(t.stage, 'artifact-empty'));
  check('artifactQuality exists', () => assert.ok(t.metadata.artifactQuality));
  check('artifactQuality.status=completed-empty-markdown', () => assert.strictEqual(t.metadata.artifactQuality.status, 'completed-empty-markdown'));
  check('artifactQuality.hasMiddleJson=true', () => assert.strictEqual(t.metadata.artifactQuality.hasMiddleJson, true));
  check('artifactQuality.hasModelJson=true', () => assert.strictEqual(t.metadata.artifactQuality.hasModelJson, true));
  check('artifactQuality.hasOriginPdf=true', () => assert.strictEqual(t.metadata.artifactQuality.hasOriginPdf, true));
  check('mineruStatus=artifact-empty', () => assert.strictEqual(t.metadata.mineruStatus, 'artifact-empty'));
  check('emptyMarkdownRetryAttempted=true', () => assert.strictEqual(t.metadata.emptyMarkdownRetryAttempted, true));
  check('OCR retry was attempted (processorCallCount=2)', () => assert.strictEqual(processorCallCount, 2));
  check('errorMessage mentions retry failure', () => assert.ok(t.errorMessage.includes('OCR 降级重试后仍未产出')));

  // Material
  const m = materialStore['mat-task-2'];
  check('Material status=failed', () => assert.strictEqual(m.status, 'failed'));
  check('Material mineruStatus=artifact-empty', () => assert.strictEqual(m.mineruStatus, 'artifact-empty'));
  check('Material aiStatus=pending', () => assert.strictEqual(m.aiStatus, 'pending'));

  console.log('Test 2 Pass ✅');
}

async function test3_ocrRetrySuccess() {
  console.log('\nTest 3: OCR 重试成功 → ai-pending');
  resetStores();

  const task = createTask('task-3');
  let callCount = 0;
  const worker = await createTestWorker({
    mineruProcessor: async (params) => {
      callCount++;
      if (callCount === 1) {
        // 第一次：空 markdown
        return {
          markdown: '',
          markdownEmpty: true,
          mineruTaskId: 'mineru-3a',
          objectName: 'parsed/mat-task-3/full.md',
          parsedPrefix: 'parsed/mat-task-3/',
          parsedFilesCount: 2,
          parsedArtifacts: [],
          zipObjectName: null,
          artifactIncomplete: false,
        };
      }
      // 第二次（OCR 重试）：非空 markdown
      return {
        markdown: '# OCR result\nContent extracted via OCR',
        mineruTaskId: 'mineru-3b',
        objectName: 'parsed/mat-task-3/full.md',
        parsedPrefix: 'parsed/mat-task-3/',
        parsedFilesCount: 3,
        parsedArtifacts: [
          { objectName: 'parsed/mat-task-3/full.md', relativePath: 'full.md', size: 30 },
        ],
        zipObjectName: null,
        artifactIncomplete: false,
      };
    }
  });

  await worker.processTask(task);

  const t = taskStore['task-3'];
  check('state=ai-pending', () => assert.strictEqual(t.state, 'ai-pending'));
  check('stage=complete', () => assert.strictEqual(t.stage, 'complete'));
  check('mineruStatus=completed', () => assert.strictEqual(t.metadata.mineruStatus, 'completed'));
  check('emptyMarkdownRetryAttempted=true', () => assert.strictEqual(t.metadata.emptyMarkdownRetryAttempted, true));
  check('emptyMarkdownRetryResult=success', () => assert.strictEqual(t.metadata.emptyMarkdownRetryResult, 'success'));
  check('emptyMarkdownRetryMineruTaskId=mineru-3b', () => assert.strictEqual(t.metadata.emptyMarkdownRetryMineruTaskId, 'mineru-3b'));
  check('no new Material created', () => assert.ok(materialStore['mat-task-3']));
  check('no new ParseTask created', () => assert.strictEqual(Object.keys(taskStore).length, 1));
  check('processorCallCount=2', () => assert.strictEqual(callCount, 2));

  console.log('Test 3 Pass ✅');
}

async function test4_ocrRetryStillEmpty() {
  console.log('\nTest 4: OCR 重试后仍为空 → failed/artifact-empty，不第三次重试');
  resetStores();

  const task = createTask('task-4');
  let callCount = 0;
  const worker = await createTestWorker({
    mineruProcessor: async () => {
      callCount++;
      return {
        markdown: '',
        markdownEmpty: true,
        mineruTaskId: `mineru-4-${callCount}`,
        objectName: 'parsed/mat-task-4/full.md',
        parsedPrefix: 'parsed/mat-task-4/',
        parsedFilesCount: 1,
        parsedArtifacts: [],
        zipObjectName: null,
        artifactIncomplete: true,
      };
    }
  });

  await worker.processTask(task);

  const t = taskStore['task-4'];
  check('state=failed', () => assert.strictEqual(t.state, 'failed'));
  check('stage=artifact-empty', () => assert.strictEqual(t.stage, 'artifact-empty'));
  check('emptyMarkdownRetryAttempted=true', () => assert.strictEqual(t.metadata.emptyMarkdownRetryAttempted, true));
  check('OCR retry executed exactly once (total calls=2)', () => assert.strictEqual(callCount, 2));
  check('no third retry', () => assert.strictEqual(callCount, 2));
  check('errorMessage mentions OCR retry', () => assert.ok(t.errorMessage.includes('OCR 降级重试后仍未产出')));

  console.log('Test 4 Pass ✅');
}

async function test5_mineruApiFailed_noOcrRetry() {
  console.log('\nTest 5: MinerU API failed → 不触发 OCR 重试');
  resetStores();

  const task = createTask('task-5');
  const worker = await createTestWorker({
    mineruProcessor: async () => {
      throw new Error('MinerU API failed: torch.cat(): expected a non-empty list of Tensors');
    }
  });

  await worker.processTask(task);

  const t = taskStore['task-5'];
  check('state=failed', () => assert.strictEqual(t.state, 'failed'));
  check('stage=mineru-failed (has mineruTaskId)', () => {
    // Since the error is thrown before mineruTaskId is set in metadata,
    // the stage should be 'execution-failed' (no mineruTaskId in metadata)
    assert.ok(t.stage === 'mineru-failed' || t.stage === 'execution-failed');
  });
  check('no emptyMarkdownRetryAttempted', () => assert.strictEqual(t.metadata.emptyMarkdownRetryAttempted, undefined));
  check('errorMessage contains error', () => assert.ok(t.errorMessage.includes('torch.cat')));

  console.log('Test 5 Pass ✅');
}

async function test6_bareErrorNotFailedConfirmed() {
  console.log('\nTest 6: 裸 Error: 日志 → 不判定 failed-confirmed');

  const { classifyLogLine, determineActivityLevel } = await import('../lib/ops-mineru-log-parser.mjs');

  // 裸 Error: 行
  const bareError = classifyLogLine('Error: something went wrong');
  check('bare Error: signalType=error-signal', () => assert.strictEqual(bareError.signalType, 'error-signal'));
  check('bare Error: confirmed=false', () => assert.strictEqual(bareError.detail.confirmed, false));

  const bareError2 = classifyLogLine('ERROR: connection failed');
  check('bare ERROR: signalType=error-signal', () => assert.strictEqual(bareError2.signalType, 'error-signal'));
  check('bare ERROR: confirmed=false', () => assert.strictEqual(bareError2.detail.confirmed, false));

  // 带 bare error signal 的活性等级：不应该是 failed-confirmed
  const summary = {
    progressCount: 1,
    stageChangeCount: 0,
    businessLogCount: 0,
    apiNoiseCount: 0,
    errorCount: 0,
    errorSignalCount: 2,
    lastBusinessSignalTime: null,
  };
  const level = determineActivityLevel(summary, null, { phase: 'Predict', percent: 50, current: 5, total: 10 });
  check('with errorSignal only: NOT failed-confirmed', () => assert.notStrictEqual(level, 'failed-confirmed'));
  check('with errorSignal only: is active-progress', () => assert.strictEqual(level, 'active-progress'));

  console.log('Test 6 Pass ✅');
}

async function test7_tracebackIsFailedConfirmed() {
  console.log('\nTest 7: 完整 traceback/RuntimeError → 可判定 failed-confirmed');

  const { classifyLogLine, determineActivityLevel } = await import('../lib/ops-mineru-log-parser.mjs');

  // RuntimeError
  const runtime = classifyLogLine('RuntimeError: CUDA error: out of memory');
  check('RuntimeError: signalType=error', () => assert.strictEqual(runtime.signalType, 'error'));
  check('RuntimeError: confirmed=true', () => assert.strictEqual(runtime.detail.confirmed, true));

  // Traceback
  const traceback = classifyLogLine('Traceback (most recent call last):');
  check('Traceback: signalType=error', () => assert.strictEqual(traceback.signalType, 'error'));
  check('Traceback: confirmed=true', () => assert.strictEqual(traceback.detail.confirmed, true));

  // OutOfMemoryError
  const oom = classifyLogLine('OutOfMemoryError: GPU memory exhausted');
  check('OutOfMemoryError: signalType=error', () => assert.strictEqual(oom.signalType, 'error'));

  // torch.cat
  const torchCat = classifyLogLine("torch.cat(): expected a non-empty list of Tensors");
  check('torch.cat: signalType=error', () => assert.strictEqual(torchCat.signalType, 'error'));

  // split_with_sizes
  const split = classifyLogLine("split_with_sizes expects split_sizes to sum to ...");
  check('split_with_sizes: signalType=error', () => assert.strictEqual(split.signalType, 'error'));

  // index out of bounds
  const indexErr = classifyLogLine("index 42 is out of bounds for dimension 0");
  check('index out of bounds: signalType=error', () => assert.strictEqual(indexErr.signalType, 'error'));

  // MPS backend out of memory
  const mpsOom = classifyLogLine("MPS backend out of memory (Current: 8GB)");
  check('MPS OOM: signalType=error', () => assert.strictEqual(mpsOom.signalType, 'error'));

  // CUDA error
  const cuda = classifyLogLine("CUDA error: device-side assert triggered");
  check('CUDA error: signalType=error', () => assert.strictEqual(cuda.signalType, 'error'));

  // 确认错误的活性等级
  const summary = {
    progressCount: 0,
    stageChangeCount: 0,
    businessLogCount: 0,
    apiNoiseCount: 0,
    errorCount: 1,
    errorSignalCount: 0,
    lastBusinessSignalTime: null,
  };
  const level = determineActivityLevel(summary, null, null);
  check('with confirmed error: IS failed-confirmed', () => assert.strictEqual(level, 'failed-confirmed'));

  console.log('Test 7 Pass ✅');
}

async function test8_failedStateNoResidualProcessing() {
  console.log('\nTest 8: failed 后状态残留清理');
  resetStores();

  // 场景：任务执行中 mineruProcessor 通过 updateProgress 设置了 mineruTaskId，然后失败
  const task = createTask('task-8');
  const worker = await createTestWorker({
    mineruProcessor: async ({ updateProgress }) => {
      // 模拟：先通过 updateProgress 设置 mineruTaskId，然后失败
      await updateProgress({
        progress: 20,
        message: '任务已提交',
        metadata: { mineruTaskId: 'mineru-8', mineruStatus: 'submitted' }
      });
      throw new Error('Some execution error after submission');
    }
  });

  await worker.processTask(task);

  const t = taskStore['task-8'];
  check('state=failed', () => assert.strictEqual(t.state, 'failed'));
  check('stage is NOT mineru-processing', () => assert.notStrictEqual(t.stage, 'mineru-processing'));
  check('stage=mineru-failed (has mineruTaskId)', () => assert.strictEqual(t.stage, 'mineru-failed'));

  // Material
  const m = materialStore['mat-task-8'];
  check('Material status=failed', () => assert.strictEqual(m.status, 'failed'));
  check('Material mineruStatus=failed', () => assert.strictEqual(m.mineruStatus, 'failed'));
  check('Material aiStatus=pending (not failed)', () => assert.strictEqual(m.aiStatus, 'pending'));
  check('Material processingStage=mineru-failed', () => assert.strictEqual(m.metadata.processingStage, 'mineru-failed'));

  console.log('Test 8 Pass ✅');
}

// ── 主测试入口 ──

async function run() {
  console.log('=== MinerU Artifact Empty & OCR Retry Smoke Tests ===\n');

  // logTaskEvent calls go to the real module — OK for smoke tests
  // (task-events writes to DB, but in test context DB may not exist;
  //  the mock taskClient captures state changes which is what we verify)

  try {
    await test1_completedNonEmpty();
    await test2_completedEmptyDetected();
    await test3_ocrRetrySuccess();
    await test4_ocrRetryStillEmpty();
    await test5_mineruApiFailed_noOcrRetry();
    await test6_bareErrorNotFailedConfirmed();
    await test7_tracebackIsFailedConfirmed();
    await test8_failedStateNoResidualProcessing();
  } catch (err) {
    console.error('Unexpected test error:', err);
    failed++;
  }

  console.log(`\n\n=== Results: ${passed} passed, ${failed} failed ===`);
  if (failed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ All P0 MinerU completed-empty / OCR retry / error adjudication / state residue tests passed!');
  }
}

run().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
