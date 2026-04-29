import assert from 'node:assert';
import express from 'express';
import http from 'node:http';
import { registerTaskActionRoutes } from '../lib/task-actions-routes.mjs';

const app = express();
app.use(express.json());
registerTaskActionRoutes(app, {
  getMinioClient: () => ({
    removeObjects: async (bucket, objects) => {
      objects.forEach(obj => {
        dbStorage.minioObjects = dbStorage.minioObjects.filter(o => o.objectName !== obj || o.bucket !== bucket);
      });
    }
  }),
  getMinioBucket: () => 'eduassets',
  getParsedBucket: () => 'eduassets-parsed'
});

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
  materials: [],
  taskEvents: [],
  'ai-metadata-jobs': [],
  'asset-details': [],
  minioObjects: []
};

const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const urlStr = url.toString();
  
  if (urlStr.startsWith(`http://127.0.0.1:${port}`)) {
    return originalFetch(url, opts);
  }

  for (const coll of ['tasks', 'materials', 'taskEvents', 'ai-metadata-jobs', 'asset-details']) {
    if (urlStr.endsWith(`/${coll}`)) {
      if (!opts || opts.method === 'GET') {
        return { ok: true, json: async () => dbStorage[coll] };
      }
      if (opts.method === 'DELETE') {
        const body = JSON.parse(opts.body);
        dbStorage[coll] = dbStorage[coll].filter(item => !body.ids.includes(item.id));
        return { ok: true, json: async () => ({}) };
      }
    }
    if (urlStr.includes(`/${coll}/`)) {
      const idMatch = urlStr.match(new RegExp(`/${coll}/([^/]+)$`));
      if (idMatch) {
        if (!opts || opts.method === 'GET') {
          return { ok: true, json: async () => dbStorage[coll].find(t => t.id === idMatch[1]) };
        }
        if (opts.method === 'PATCH') {
          const body = JSON.parse(opts.body);
          const item = dbStorage[coll].find(t => t.id === idMatch[1]);
          if (item) {
            Object.assign(item, body);
            return { ok: true, json: async () => item };
          }
        }
      }
    }
  }

  return { ok: true, json: async () => ({}) };
};

async function runTests() {
  console.log('=== P0 Reset Test Env Smoke Test ===');

  // Test setup
  dbStorage.tasks = [{ id: 't1', state: 'completed', metadata: { markdownObjectName: 't1.md' } }];
  dbStorage.materials = [{ id: 'm1', status: 'completed', metadata: { objectName: 'm1.pdf', zipObjectName: 'm1.zip' } }];
  dbStorage.taskEvents = [{ id: 'e1', taskId: 't1' }];
  dbStorage['ai-metadata-jobs'] = [{ id: 'j1' }, { id: 'j2' }];
  dbStorage['asset-details'] = [{ id: 'a1' }];
  dbStorage.minioObjects = [
    { bucket: 'eduassets', objectName: 'm1.pdf' },
    { bucket: 'eduassets-parsed', objectName: 'm1.zip' },
    { bucket: 'eduassets-parsed', objectName: 't1.md' },
    { bucket: 'eduassets', objectName: 'untouched.pdf' }
  ];

  // Test 1: reset dry-run counts ai jobs but does not mutate
  console.log('Test 1: reset dry-run counts ai jobs but does not mutate');
  let res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.dryRun, true);
  assert.equal(res.body.summary.deletedAiJobs, 2);
  assert.equal(res.body.summary.deletedTasks, 1);
  assert.equal(res.body.summary.deletedMaterials, 1);
  assert.equal(res.body.summary.deletedTaskEvents, 1);
  assert.equal(res.body.summary.deletedAssetDetails, 1);
  assert.equal(res.body.summary.deletedMinioOriginals, 1);
  assert.equal(res.body.summary.deletedMinioParsed, 2);
  
  // Data still exists
  assert.equal(dbStorage.tasks.length, 1);
  assert.equal(dbStorage['ai-metadata-jobs'].length, 2);
  assert.equal(dbStorage.minioObjects.length, 4);
  console.log('Test 1 Pass ✅');

  // Test 2: reset execute deletes every dry-run counted collection
  console.log('Test 2: reset execute deletes every dry-run counted collection');
  res = await mockRequest('/ops/reset-test-env', 'POST', {});
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.dryRun, false);
  
  assert.equal(dbStorage.tasks.length, 0);
  assert.equal(dbStorage.materials.length, 0);
  assert.equal(dbStorage.taskEvents.length, 0);
  assert.equal(dbStorage['ai-metadata-jobs'].length, 0);
  assert.equal(dbStorage['asset-details'].length, 0);
  
  // MinIO cleanup
  assert.equal(dbStorage.minioObjects.length, 1);
  assert.equal(dbStorage.minioObjects[0].objectName, 'untouched.pdf');
  console.log('Test 2 Pass ✅');

  // Test 3: reset execute then dry-run returns zero
  console.log('Test 3: reset execute then dry-run returns zero');
  res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 200);
  assert.equal(res.body.summary.deletedAiJobs, 0);
  assert.equal(res.body.summary.deletedTasks, 0);
  assert.equal(res.body.summary.deletedMaterials, 0);
  console.log('Test 3 Pass ✅');

  // Test 4: reset refuses live tasks unless force
  console.log('Test 4: reset refuses live tasks unless force');
  dbStorage.tasks = [{ id: 't2', state: 'running' }];
  
  // without force
  res = await mockRequest('/ops/reset-test-env', 'POST', {});
  assert.equal(res.status, 400);
  assert.equal(dbStorage.tasks.length, 1);
  
  // with force
  res = await mockRequest('/ops/reset-test-env', 'POST', { force: true });
  assert.equal(res.status, 200);
  assert.equal(dbStorage.tasks.length, 0);
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
