/**
 * mineru-submit-retryable-smoke.mjs
 * 测试 MinerU 提交阶段不可达进入 submit-failed-retryable
 */

import assert from 'assert';
import { ParseTaskWorker } from '../services/queue/task-worker.mjs';
import { MineruSubmitUnreachableError } from '../services/mineru/local-adapter.mjs';

let failedCount = 0;
let passedCount = 0;

function assertEqual(actual, expected, msg) {
  if (actual === expected) {
    passedCount++;
    console.log(`[PASS] ${msg} (got: ${actual})`);
  } else {
    failedCount++;
    console.error(`[FAIL] ${msg}`);
    console.error(`  Expected: ${expected}`);
    console.error(`  Actual:   ${actual}`);
  }
}

async function runTest() {
  console.log('--- mineru-submit-retryable-smoke ---');
  
  const mockTask = {
    id: 'task-submit-retryable',
    state: 'pending',
    engine: 'local-mineru',
    optionsSnapshot: {
      material: {
        metadata: { objectName: 'originals/test.pdf' }
      }
    },
    metadata: { submitRetries: 0 }
  };

  let updatedState = null;
  let updatedStage = null;
  let updatedMessage = null;
  
  const worker = new ParseTaskWorker({
    minioContext: { getFileStream: async () => ({}) },
    taskClient: {
      getAllTasks: async () => [mockTask],
      updateTask: async (id, patch) => { 
        updatedState = patch.state; 
        updatedStage = patch.stage;
        updatedMessage = patch.message;
      }
    },
    mineruProcessor: async () => { throw new MineruSubmitUnreachableError('timeout'); }
  });

  // Since we can't easily mock processTask internally to use our task object directly
  // we'll just mock the transition function directly or call the exact error handling part.
  
  // Call processTask but intercept updateTask
  await worker.processTask(mockTask);
  
  assertEqual(updatedState, 'pending', 'State remains pending for retry');
  assertEqual(updatedStage, 'upload', 'Stage reverts to upload for retry');
  assertEqual(updatedMessage.includes('可重试'), true, 'Message includes retry hint');
  
  // Now simulate retry limit exceeded
  mockTask.metadata.submitRetries = 5;
  await worker.processTask(mockTask);
  
  assertEqual(updatedState, 'failed', 'State becomes failed after limit');
  assertEqual(updatedStage, 'submit-failed-retryable', 'Stage indicates submit failure');
  assertEqual(updatedMessage.includes('可重试'), true, 'Message still includes retry hint');

  console.log(`\nResults: ${passedCount} passed, ${failedCount} failed`);
  if (failedCount > 0) process.exit(1);
}

runTest().catch(console.error);
