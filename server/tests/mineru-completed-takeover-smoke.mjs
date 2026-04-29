/**
 * mineru-completed-takeover-smoke.mjs
 * 测试 failed 且有 mineruTaskId 但 parsedFilesCount=0 时，会被 _adjudicateStaleWithMineruApi 成功纠正。
 */

import assert from 'assert';
import { ParseTaskWorker } from '../services/queue/task-worker.mjs';

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
  console.log('--- mineru-completed-takeover-smoke ---');
  
  let fetchCallCount = 0;
  let resumerCallCount = 0;
  const mockTasks = [
    {
      id: 'task-failed-but-completed',
      state: 'failed',
      engine: 'local-mineru',
      errorMessage: 'timeout',
      metadata: {
        mineruTaskId: 'mineru-123',
        parsedFilesCount: 0
      },
      optionsSnapshot: { localEndpoint: 'http://localhost:8083' }
    }
  ];

  let updatedState = null;
  
  const worker = new ParseTaskWorker({
    taskClient: {
      getAllTasks: async () => mockTasks,
      updateTask: async (id, patch) => { updatedState = patch.state; }
    },
    mineruResumer: async () => { resumerCallCount++; return { objectName: 'test.md' }; }
  });

  // Mock global fetch
  const originalFetch = global.fetch;
  global.fetch = async (url, options) => {
    const urlStr = url ? url.toString() : '';
    if (urlStr.includes('mineru-123')) {
      fetchCallCount++;
      return { ok: true, json: async () => ({ status: 'completed' }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };

  try {
    // Manually trigger the logic
    await worker.recoverMisjudgedFailedTasks(mockTasks);
    
    assertEqual(fetchCallCount, 1, 'fetch called to check mineru status');
    assertEqual(updatedState, 'running', 'task state transitioned back to running to fetch results');
    
    // Check if resume was scheduled (might be async, give it a tiny delay)
    await new Promise(r => setTimeout(r, 100));
    assertEqual(resumerCallCount, 1, 'mineruResumer called to fetch results');
    
  } finally {
    global.fetch = originalFetch;
  }

  console.log(`\nResults: ${passedCount} passed, ${failedCount} failed`);
  if (failedCount > 0) process.exit(1);
}

runTest().catch(console.error);
