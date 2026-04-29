import assert from 'node:assert';
import express from 'express';
import http from 'node:http';
import { registerTaskActionRoutes } from '../lib/task-actions-routes.mjs';

const app = express();
app.use(express.json());
registerTaskActionRoutes(app);

const server = http.createServer(app);
await new Promise(r => server.listen(0, r));
const port = server.address().port;

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
  materials: []
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
  if (urlStr.includes('/materials/')) {
     const idMatch = urlStr.match(/\/materials\/([^/]+)$/);
     if (idMatch && opts?.method === 'PATCH') {
        const body = JSON.parse(opts.body);
        const mat = dbStorage.materials.find(t => t.id === idMatch[1]);
        if (mat) {
          Object.assign(mat, body);
          return { ok: true, json: async () => mat };
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
  if (urlStr.includes('/materials/')) {
    const idMatch = urlStr.match(/\/materials\/([^/]+)$/);
    if (idMatch && (!opts || opts.method === 'GET')) {
       return { ok: true, json: async () => dbStorage.materials.find(t => t.id === idMatch[1]) };
    }
  }
  return { ok: true, json: async () => ({}) };
};

async function runTests() {
  console.log('=== P0 Cancel Task/Environment Reset Smoke Test ===');

  // Test 1: cancel single dry-run does not mutate
  console.log('Test 1: cancel single dry-run does not mutate');
  dbStorage.tasks = [{ id: 'task-1', state: 'failed', stage: 'submit-failed-retryable' }];
  let res = await mockRequest('/tasks/task-1/cancel', 'POST', { dryRun: true });
  assert.equal(res.body.ok, true);
  assert.equal(res.body.dryRun, true);
  assert.equal(dbStorage.tasks[0].state, 'failed'); // No mutation
  console.log('Test 1 Pass ✅');

  // Test 2: cancel single execute mutates
  console.log('Test 2: cancel single execute mutates');
  dbStorage.tasks = [{ id: 'task-1', state: 'running', stage: 'mineru-processing', materialId: 'mat-1' }];
  dbStorage.materials = [{ id: 'mat-1', status: 'processing', metadata: {} }];
  res = await mockRequest('/tasks/task-1/cancel', 'POST', {});
  assert.equal(res.body.ok, true);
  assert.equal(dbStorage.tasks[0].state, 'canceled');
  assert.equal(dbStorage.materials[0].status, 'canceled'); // Synchronized
  console.log('Test 2 Pass ✅');

  // Test 3: cancel-all-live dry-run excludes review-pending
  console.log('Test 3: cancel-all-live dry-run excludes review-pending');
  dbStorage.tasks = [
    { id: 'task-3', state: 'review-pending', metadata: { parsedFilesCount: 5 } }
  ];
  res = await mockRequest('/tasks/cancel-all-live', 'POST', { dryRun: true });
  assert.equal(res.body.summary.totalToCancel, 0);
  console.log('Test 3 Pass ✅');

  // Test 4: cancel-all-live includes submit-failed-retryable
  console.log('Test 4: cancel-all-live includes submit-failed-retryable');
  dbStorage.tasks = [
    { id: 'task-4', state: 'failed', stage: 'submit-failed-retryable' }
  ];
  res = await mockRequest('/tasks/cancel-all-live', 'POST', { dryRun: true });
  assert.equal(res.body.summary.totalToCancel, 1);
  console.log('Test 4 Pass ✅');

  console.log('=== All Tests Passed ===');
  globalThis.fetch = originalFetch;
  server.close();
}

runTests().catch(e => {
  console.error(e);
  server.close();
  process.exit(1);
});
