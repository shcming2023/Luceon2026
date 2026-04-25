import { processWithLocalMinerU } from '../services/mineru/local-adapter.mjs';

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
  console.log('=== P0 MinerU Status Query Abort & BackendEffective Protection Smoke Test ===\n');

  // Test 1: waitMinerUTask() single status fetch AbortError
  console.log('Test 1: waitMinerUTask() single status fetch AbortError does not fail task');
  {
    let fetchCount = 0;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      const urlStr = url.toString();
      if (urlStr.includes('/result')) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({ data: { md_content: '# Success' } }),
          arrayBuffer: async () => new Uint8Array([123, 125]).buffer
        };
      }
      if (urlStr.includes('/tasks/task-abort-test')) {
        fetchCount++;
        if (fetchCount === 1) {
          const err = new Error('The operation was aborted');
          err.name = 'AbortError';
          throw err;
        }
        if (fetchCount === 2) {
          const err = new Error('fetch failed');
          err.name = 'TypeError';
          throw err;
        }
        if (fetchCount === 3) {
          return { ok: true, status: 200, json: async () => ({ status: 'processing', started_at: '2026-04-25T01:00:00Z' }) };
        }
        return { ok: true, status: 200, json: async () => ({ status: 'done', started_at: '2026-04-25T01:00:00Z' }) };
      }if (urlStr.includes('/tasks') && !urlStr.includes('/task-abort-test')) {
        // Create task mock
        return { ok: true, status: 200, headers: { get: () => 'application/json' }, text: async () => JSON.stringify({ task_id: 'task-abort-test' }), json: async () => ({ task_id: 'task-abort-test' }) };
      }
      if (urlStr.includes('/health')) {
        return { ok: true, status: 200, headers: { get: () => 'application/json' } };
      }
      if (urlStr.includes('/result')) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({ data: { md_content: '# Success' } }),
          arrayBuffer: async () => new Uint8Array([123, 125]).buffer
        };
      }
      return originalFetch(url, options);
    };

    const updates = [];
    const updateProgress = async (updateInfo) => {
      updates.push(updateInfo);
    };

    const task = {
      id: 'test-task',
      optionsSnapshot: { localEndpoint: 'http://localhost:8083', backend: 'hybrid-auto-engine', maxPages: 10 },
      metadata: { mineruExecutionProfile: { backendRequested: 'hybrid-auto-engine', backendEffective: 'hybrid-auto-engine' } }
    };

    try {
      await processWithLocalMinerU({
        task,
        material: { fileSize: 3 * 1024 * 1024 },
        fileStream: async function*() { yield Buffer.from('test'); }(),
        fileName: 'test.pdf',
        mimeType: 'application/pdf',
        timeoutMs: 15000,
        minioContext: { saveMarkdown: async () => true, saveObject: async () => true },
        updateProgress
      });
    } catch (e) {
      console.error(e);
      assert(false, `Should not throw error: ${e.message}`);
    }

    const abortWarn = updates.find(u => u.metadata?._synthetic_warn === 'mineru-status-query-timeout');
    if (!abortWarn) console.log(JSON.stringify(updates, null, 2));
    assert(abortWarn !== undefined, 'Should emit synthetic warning for abort/timeout without failing');
    
    // Check if backendEffective is propagated in executionProfile
    const profileUpdate = updates.find(u => u.metadata?.mineruExecutionProfile?.backendEffective === 'hybrid-auto-engine');
    if (!profileUpdate) console.log(JSON.stringify(updates.map(u => u.metadata?.mineruExecutionProfile), null, 2));
    assert(profileUpdate !== undefined, 'backendEffective should be written to executionProfile');

    globalThis.fetch = originalFetch;
    console.log('Test 1 Pass ✅\n');
  }

  console.log(`\n=== Results: ${testsPassed} passed, ${testsFailed} failed ===`);
  if (testsFailed > 0) process.exit(1);
  process.exit(0);
}

runTest().catch(err => {
  console.error(err);
  process.exit(1);
});
