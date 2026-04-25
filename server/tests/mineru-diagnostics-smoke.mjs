import { registerMineruDiagnosticsRoutes } from '../lib/ops-mineru-diagnostics.mjs';

// Mock Express app
const routes = {};
const app = {
  get: (path, handler) => { routes[path] = handler; },
  post: (path, handler) => { routes[path] = handler; }
};

let mockMineruHealth = { processing_tasks: 0, queued_tasks: 0, max_concurrent_requests: 1 };
let mockMineruTasks = {}; // mineruTaskId -> status payload
let mockLuceonTasks = [];

const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options) => {
  const urlStr = url.toString();
  if (urlStr.includes('/settings')) {
    return { ok: true, json: async () => ({}) };
  }
  if (urlStr.includes('/health')) {
    return { ok: true, json: async () => mockMineruHealth };
  }
  if (urlStr.includes('/tasks') && !urlStr.includes('db-server')) {
    // MinerU task lookup
    const id = urlStr.split('/').pop();
    if (mockMineruTasks[id]) {
      return { ok: true, json: async () => mockMineruTasks[id] };
    }
    return { ok: false, status: 404 };
  }
  if (urlStr.includes('/tasks')) { // Luceon tasks
    return { ok: true, json: async () => mockLuceonTasks };
  }
  return originalFetch(url, options);
};

registerMineruDiagnosticsRoutes(app, () => 'http://mock-db-server');

async function runTest() {
  console.log('=== MinerU Diagnostics Smoke Test ===');

  const handler = routes['/ops/mineru/diagnostics'];

  const makeRes = (resolve) => ({
    json: (data) => resolve(data)
  });

  // Test 1: Orphan Processing Blocker
  console.log('Test 1: Orphan Processing Blocker');
  mockMineruHealth = { processing_tasks: 1, queued_tasks: 4 };
  mockLuceonTasks = [
    { id: 't1', state: 'running', stage: 'mineru-queued', metadata: { mineruTaskId: 'known-q1' } },
    { id: 't2', state: 'running', stage: 'mineru-queued', metadata: { mineruTaskId: 'known-q2' } }
  ];
  mockMineruTasks = {}; // MinerU API knows nothing (unknown ID blocking)

  let resData = await new Promise(resolve => handler({}, makeRes(resolve)));
  console.assert(resData.diagnosis.kind === 'orphan-processing-blocker', 'Expected orphan-processing-blocker');
  console.assert(resData.diagnosis.status === 'blocked', 'Expected blocked');
  console.log('Test 1 Pass ✅');

  // Test 2: Luceon Processing
  console.log('Test 2: Luceon Processing (known task is processing)');
  mockMineruHealth = { processing_tasks: 1, queued_tasks: 0 };
  mockLuceonTasks = [
    { id: 't1', state: 'running', stage: 'mineru-processing', metadata: { mineruTaskId: 'known-p1' } }
  ];
  mockMineruTasks = {};

  resData = await new Promise(resolve => handler({}, makeRes(resolve)));
  console.assert(resData.diagnosis.kind === 'luceon-processing', 'Expected luceon-processing');
  console.log('Test 2 Pass ✅');

  // Test 3: Luceon drifting state (MinerU processing, but Luceon thinks it's queued)
  console.log('Test 3: Luceon Drift (MinerU processing a known task but Luceon stage is queued)');
  mockMineruHealth = { processing_tasks: 1, queued_tasks: 0 };
  mockLuceonTasks = [
    { id: 't1', state: 'running', stage: 'mineru-queued', metadata: { mineruTaskId: 'known-p1' } }
  ];
  mockMineruTasks = {
    'known-p1': { status: 'processing', started_at: 'yes' }
  };

  resData = await new Promise(resolve => handler({}, makeRes(resolve)));
  console.assert(resData.diagnosis.kind === 'luceon-processing', 'Expected luceon-processing');
  console.assert(resData.diagnosis.blockingMineruTaskId === 'known-p1', 'Expected actual ID');
  console.log('Test 3 Pass ✅');

  // Test 4: Known failed but MinerU processing
  console.log('Test 4: Known failed but MinerU processing');
  mockMineruHealth = { processing_tasks: 1, queued_tasks: 0 };
  mockLuceonTasks = [
    { id: 't1', state: 'failed', stage: 'mineru-failed', metadata: { mineruTaskId: 'failed-p1' } }
  ];
  mockMineruTasks = {
    'failed-p1': { status: 'processing', started_at: 'yes' }
  };

  resData = await new Promise(resolve => handler({}, makeRes(resolve)));
  console.assert(resData.diagnosis.kind === 'known-failed-but-mineru-processing', 'Expected known-failed-but-mineru-processing');
  console.assert(resData.diagnosis.status === 'blocked', 'Expected blocked');
  console.assert(resData.diagnosis.safeToAutoRecover === false, 'Expected safeToAutoRecover false');
  console.log('Test 4 Pass ✅');

  console.log('✅ Diagnostics语义验证通过！');
  process.exit(0);
}

runTest().catch(console.error);
