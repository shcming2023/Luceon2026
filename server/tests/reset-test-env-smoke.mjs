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
  'task-events': [],
  'ai-metadata-jobs': [],
  'asset-details': {}, // Mocking object map as reported in issue
  minioObjects: [],
  mockFailDeleteMaterials: false
};

const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const urlStr = url.toString();
  
  if (urlStr.startsWith(`http://127.0.0.1:${port}`)) {
    return originalFetch(url, opts);
  }

  // Ensure no old /taskEvents routes are hit
  if (urlStr.includes('/taskEvents')) {
    throw new Error('Forbidden route /taskEvents was hit');
  }

  for (const coll of ['tasks', 'materials', 'task-events', 'ai-metadata-jobs', 'asset-details']) {
    if (urlStr.endsWith(`/${coll}`)) {
      if (!opts || opts.method === 'GET') {
        return { ok: true, json: async () => dbStorage[coll] };
      }
      if (opts.method === 'DELETE') {
        if (coll === 'materials' && dbStorage.mockFailDeleteMaterials) {
           return { ok: false, status: 500, json: async () => ({ error: 'Simulated failure' }) };
        }
        const body = JSON.parse(opts.body);
        if (Array.isArray(dbStorage[coll])) {
          dbStorage[coll] = dbStorage[coll].filter(item => !body.ids.includes(item.id));
        } else {
          for (const id of body.ids) {
            delete dbStorage[coll][id];
          }
        }
        return { ok: true, json: async () => ({}) };
      }
    }
    if (urlStr.includes(`/${coll}/`)) {
      const idMatch = urlStr.match(new RegExp(`/${coll}/([^/]+)$`));
      if (idMatch) {
        if (!opts || opts.method === 'GET') {
          const item = Array.isArray(dbStorage[coll]) 
            ? dbStorage[coll].find(t => t.id === idMatch[1])
            : dbStorage[coll][idMatch[1]];
          return { ok: true, json: async () => item };
        }
        if (opts.method === 'PATCH') {
          const body = JSON.parse(opts.body);
          if (Array.isArray(dbStorage[coll])) {
            const item = dbStorage[coll].find(t => t.id === idMatch[1]);
            if (item) Object.assign(item, body);
            return { ok: true, json: async () => item };
          } else {
             if (dbStorage[coll][idMatch[1]]) {
               Object.assign(dbStorage[coll][idMatch[1]], body);
               return { ok: true, json: async () => dbStorage[coll][idMatch[1]] };
             }
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
  dbStorage['task-events'] = [{ id: 'e1', taskId: 't1' }];
  dbStorage['ai-metadata-jobs'] = [{ id: 'j1' }, { id: 'j2' }];
  dbStorage['asset-details'] = { 'a1': { id: 'a1' }, 'a2': { id: 'a2' } };
  dbStorage.minioObjects = [
    { bucket: 'eduassets', objectName: 'm1.pdf' },
    { bucket: 'eduassets-parsed', objectName: 'm1.zip' },
    { bucket: 'eduassets-parsed', objectName: 't1.md' },
    { bucket: 'eduassets', objectName: 'untouched.pdf' }
  ];

  // Test 1: reset dry-run counts ai jobs but does not mutate (also checks asset-details object map)
  console.log('Test 1: reset dry-run counts ai jobs but does not mutate');
  let res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.dryRun, true);
  assert.equal(res.body.summary.deletedAiJobs, 2);
  assert.equal(res.body.summary.deletedTasks, 1);
  assert.equal(res.body.summary.deletedMaterials, 1);
  assert.equal(res.body.summary.deletedTaskEvents, 1);
  assert.equal(res.body.summary.deletedAssetDetails, 2);
  assert.equal(res.body.summary.deletedMinioOriginals, 1);
  assert.equal(res.body.summary.deletedMinioParsed, 2);
  
  // Data still exists
  assert.equal(dbStorage.tasks.length, 1);
  assert.equal(dbStorage['ai-metadata-jobs'].length, 2);
  assert.equal(Object.keys(dbStorage['asset-details']).length, 2);
  assert.equal(dbStorage.minioObjects.length, 4);
  console.log('Test 1 Pass ✅');

  // Test 2: execute returns structured partial failure
  console.log('Test 2: execute returns structured partial failure');
  dbStorage.mockFailDeleteMaterials = true;
  res = await mockRequest('/ops/reset-test-env', 'POST', {});
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, false); // overall false because materials failed
  assert.equal(res.body.dryRun, false);
  assert.equal(res.body.details.materials.ok, false);
  assert.equal(res.body.details.tasks.ok, true);
  assert.equal(res.body.details.assetDetails.ok, true);
  assert.equal(res.body.details.materials.error, 'HTTP 500');
  
  // Data state check
  assert.equal(dbStorage.materials.length, 1); // Not deleted
  assert.equal(dbStorage.tasks.length, 0); // Deleted
  assert.equal(Object.keys(dbStorage['asset-details']).length, 0); // Deleted
  console.log('Test 2 Pass ✅');

  // Test 3: reset execute then dry-run returns zero including assetDetails
  console.log('Test 3: reset execute then dry-run returns zero including assetDetails');
  dbStorage.mockFailDeleteMaterials = false;
  // first delete the remaining materials
  await mockRequest('/ops/reset-test-env', 'POST', {});
  // then do dry-run
  res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 200);
  assert.equal(res.body.summary.deletedAiJobs, 0);
  assert.equal(res.body.summary.deletedTasks, 0);
  assert.equal(res.body.summary.deletedMaterials, 0);
  assert.equal(res.body.summary.deletedAssetDetails, 0);
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
