import { ParseTaskWorker } from '../services/queue/task-worker.mjs';

async function runSmokeTest() {
  console.log('=== P1 Patch: Material metadata.mineruStatus 补偿与更新 Smoke Test ===\n');

  let passed = 0;
  let failed = 0;

  function assert(condition, message) {
    if (condition) {
      console.log(`✅ Pass: ${message}`);
      passed++;
    } else {
      console.error(`❌ Fail: ${message}`);
      failed++;
    }
  }

  // --- 模拟 Context 和 Client ---
  const materials = [
    {
      id: 'm-1',
      status: 'processing',
      mineruStatus: 'completed',
      aiStatus: 'pending',
      metadata: { mineruStatus: 'processing', parsedPrefix: 'mock/path', aiData: 'some-ai' }
    },
    {
      id: 'm-2',
      status: 'reviewing',
      mineruStatus: 'completed',
      aiStatus: 'analyzed',
      metadata: { mineruStatus: 'processing' }
    },
    {
      id: 'm-3',
      status: 'completed',
      mineruStatus: 'completed',
      aiStatus: 'analyzed',
      metadata: { mineruStatus: 'processing' }
    },
    {
      id: 'm-4',
      status: 'failed',
      mineruStatus: 'failed',
      metadata: { mineruStatus: 'failed' } // 不符合条件，不处理
    }
  ];

  const dbCalls = {
    updates: []
  };

  // Mock global fetch
  const originalFetch = global.fetch;
  global.fetch = async (url, options) => {
    if (url.includes('/materials') && (!options || options.method === 'GET')) {
      return {
        ok: true,
        json: async () => materials
      };
    }
    return { ok: false, status: 404 };
  };

  const mockTaskClient = {
    getAllTasks: async () => [],
    updateTask: async () => true,
    updateMaterial: async (id, update) => {
      dbCalls.updates.push({ id, update });
      // Apply update
      const target = materials.find(m => m.id === id);
      if (target) {
        Object.assign(target, update);
        if (update.metadata) {
          target.metadata = { ...target.metadata, ...update.metadata };
        }
      }
      return true;
    }
  };

  const worker = new ParseTaskWorker({
    taskClient: mockTaskClient
  });

  // --- Test 1: cleanupStaleMineruStatus () ---
  console.log('Test 1: cleanupStaleMineruStatus() cleans up residual processing statuses correctly');
  await worker.cleanupStaleMineruStatus();

  // Validate M-1
  assert(dbCalls.updates.some(u => u.id === 'm-1' && u.update.metadata.mineruStatus === 'completed'), "M-1: mineruStatus cleared to 'completed'");
  assert(dbCalls.updates.some(u => u.id === 'm-1' && u.update.metadata.processingStage === 'mineru-completed'), "M-1: processingStage is 'mineru-completed'");
  assert(materials[0].metadata.aiData === 'some-ai', "M-1: preexisting metadata (aiData) is preserved");

  // Validate M-2 (review-pending)
  assert(dbCalls.updates.some(u => u.id === 'm-2' && u.update.metadata.mineruStatus === 'completed'), "M-2: mineruStatus cleared to 'completed'");
  assert(dbCalls.updates.some(u => u.id === 'm-2' && u.update.metadata.processingStage === 'review'), "M-2: processingStage is 'review' for AI review-pending");

  // Validate M-3 (completed)
  assert(dbCalls.updates.some(u => u.id === 'm-3' && u.update.metadata.mineruStatus === 'completed'), "M-3: mineruStatus cleared to 'completed'");
  assert(dbCalls.updates.some(u => u.id === 'm-3' && u.update.metadata.processingStage === 'completed'), "M-3: processingStage is 'completed'");

  // Validate M-4 (ignored)
  assert(!dbCalls.updates.some(u => u.id === 'm-4'), "M-4: not updated because it doesn't match conditions");

  // --- Test 2: Idempotency ---
  console.log('\nTest 2: cleanupStaleMineruStatus() is idempotent');
  dbCalls.updates = [];
  await worker.cleanupStaleMineruStatus();
  assert(dbCalls.updates.length === 0, "No materials updated in the second run (idempotent)");

  global.fetch = originalFetch;

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  if (failed > 0) {
    process.exit(1);
  }
}

runSmokeTest();
