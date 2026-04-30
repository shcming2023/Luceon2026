import assert from 'node:assert';
import express from 'express';
import http from 'node:http';
import { registerTaskActionRoutes } from '../lib/task-actions-routes.mjs';
import { AiMetadataWorker } from '../services/ai/metadata-worker.mjs';
import { ParseTaskWorker } from '../services/queue/task-worker.mjs';
import { spawn } from 'child_process';
import util from 'util';

const app = express();
app.use(express.json());

const deps = {
  getStorageBackend: () => 'minio',
  getMinioBucket: () => 'eduassets',
  getParsedBucket: () => 'eduassets-parsed',
  listAllObjects: async () => [],
  getMinioClient: () => ({
    removeObjects: async () => {},
    removeObject: async () => {}
  }),
  DB_BASE_URL: 'mock-db'
};

registerTaskActionRoutes(app, deps);

const server = http.createServer(app);
await new Promise(r => server.listen(0, r));
const port = server.address().port;
deps.DB_BASE_URL = `http://127.0.0.1:${port}`; // Set for dbGet

// Simple fetch wrapper
async function mockRequest(path, method = 'POST', body = {}) {
  const url = `http://127.0.0.1:${port}${path}`;
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: method !== 'GET' ? JSON.stringify(body) : undefined
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, body: data };
}

// Mock fetch for db-server
const dbStorage = {
  tasks: [],
  materials: [],
  aiJobs: [],
  taskEvents: []
};

const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const urlStr = url.toString();
  
  if (urlStr.startsWith(`http://127.0.0.1:${port}`)) {
    return originalFetch(url, opts);
  }

  if (urlStr.includes('/tasks/')) {
     const idMatch = urlStr.match(/\/tasks\/([^/]+)$/);
     if (idMatch && opts?.method === 'PATCH') {
        const body = JSON.parse(opts.body);
        const task = dbStorage.tasks.find(t => t.id === idMatch[1]);
        if (task) {
          Object.assign(task, body);
          return { ok: true, json: async () => task };
        }
     }
  }
  if (urlStr.includes('/ai-metadata-jobs/')) {
    const idMatch = urlStr.match(/\/ai-metadata-jobs\/([^/]+)$/);
    if (idMatch && opts?.method === 'PATCH') {
       const body = JSON.parse(opts.body);
       const job = dbStorage.aiJobs.find(t => t.id === idMatch[1]);
       if (job) {
         Object.assign(job, body);
         return { ok: true, json: async () => job };
       }
    }
 }
  if (urlStr.endsWith('/tasks') && (!opts || opts.method === 'GET')) {
    return { ok: true, json: async () => dbStorage.tasks };
  }
  if (urlStr.includes('/tasks/')) {
    const idMatch = urlStr.match(/\/tasks\/([^/]+)$/);
    if (idMatch && (!opts || opts.method === 'GET')) {
       return { ok: true, json: async () => dbStorage.tasks.find(t => t.id === idMatch[1]) };
    }
  }
  if (urlStr.endsWith('/materials') && (!opts || opts.method === 'GET')) {
    return { ok: true, json: async () => dbStorage.materials };
  }
  if (urlStr.endsWith('/ai-metadata-jobs') && (!opts || opts.method === 'GET')) {
    return { ok: true, json: async () => dbStorage.aiJobs };
  }
  if (urlStr.endsWith('/task-events') && (!opts || opts.method === 'GET')) {
    return { ok: true, json: async () => dbStorage.taskEvents };
  }
  if (urlStr.endsWith('/task-events') && opts?.method === 'POST') {
    dbStorage.taskEvents.push(JSON.parse(opts.body));
    return { ok: true, json: async () => ({}) };
  }
  return { ok: true, json: async () => ({}) };
};

// Also mock internal modules for workers
global.getTaskById = async (id) => dbStorage.tasks.find(t => t.id === id);
global.updateJob = async (id, update) => {
  const job = dbStorage.aiJobs.find(j => j.id === id);
  if (job) Object.assign(job, update);
  return true;
};
global.getAllJobs = async () => dbStorage.aiJobs;
global.getSettings = async () => ({ aiConfig: { aiEnabled: true } });
global.logTaskEvent = async (event) => {
  dbStorage.taskEvents.push(event);
};

async function runTests() {
  console.log('=== P0 Canceled Hard Stop Smoke Test ===');

  // Test 1: AI worker skip canceled
  console.log('Test 1: AI worker skip canceled');
  dbStorage.tasks = [{ id: 'task-1', state: 'canceled', metadata: { canceledAt: new Date().toISOString() } }];
  dbStorage.aiJobs = [{ id: 'job-1', parseTaskId: 'task-1', state: 'pending' }];
  
  const aiWorker = new AiMetadataWorker({
    getFileStream: () => { throw new Error('should not call'); }
  });
  await aiWorker.processJob(dbStorage.aiJobs[0]);
  
  assert.equal(dbStorage.aiJobs[0].state, 'skipped-canceled');
  console.log('Test 1 Pass ✅');

  // Test 2: ParseTask worker skip completed-after-cancel
  console.log('Test 2: ParseTask worker skip completed-after-cancel');
  dbStorage.tasks = [
    { id: 'task-2', state: 'canceled', metadata: { canceledAt: new Date().toISOString(), mineruTaskId: 'external-1' } }
  ];
  const taskWorker = new ParseTaskWorker({
    taskClient: {
      getAllTasks: async () => dbStorage.tasks,
      updateTaskWithRetry: async () => { throw new Error('should not call'); }
    }
  });
  // Running recovery scan shouldn't touch this task
  await taskWorker.runRecoveryScan();
  assert.equal(dbStorage.tasks[0].state, 'canceled');
  console.log('Test 2 Pass ✅');

  // Test 3: reset-test-env drift count
  console.log('Test 3: reset-test-env drift count');
  dbStorage.tasks = [
    { id: 'task-3', state: 'ai-running', metadata: { canceledAt: new Date().toISOString() } }
  ];
  let res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  if (!res.body.summary) {
    console.error('Test 3 dryRun failed:', res);
    process.exit(1);
  }
  assert.equal(res.body.summary.stateDriftCanceledTasks, 1);
  
  res = await mockRequest('/ops/reset-test-env', 'POST', { force: true });
  if (!res.body.summary) {
    console.error('Test 3 execute failed:', res);
    process.exit(1);
  }
  assert.equal(res.body.summary.stateDriftCanceledTasks, 1);
  console.log('Test 3 Pass ✅');

  console.log('=== All Tests Passed ===');
  globalThis.fetch = originalFetch;
  server.close();
}

runTests().catch(e => {
  console.error(e);
  server.close();
  process.exit(1);
});
