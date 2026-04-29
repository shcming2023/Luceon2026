import assert from 'node:assert';
import express from 'express';
import http from 'node:http';
import { registerTaskActionRoutes } from '../lib/task-actions-routes.mjs';
import { registerConsistencyRoutes } from '../lib/consistency-routes.mjs';

const app = express();
app.use(express.json());

const dbStorage = {
  tasks: [],
  materials: [],
  'task-events': [],
  'ai-metadata-jobs': [],
  'asset-details': {},
  minioObjects: [],
  mockFailDeleteMaterials: false,
  mockFailOrphanCleanup: false
};

const deps = {
  getStorageBackend: () => 'minio',
  getMinioBucket: () => 'eduassets',
  getParsedBucket: () => 'eduassets-parsed',
  listAllObjects: async (bucket, prefix) => {
    return dbStorage.minioObjects
      .filter(o => o.bucket === bucket && o.objectName.startsWith(prefix))
      .map(o => ({ name: o.objectName, size: o.size || 1024, lastModified: new Date() }));
  },
  getMinioClient: () => ({
    removeObjects: async (bucket, objects) => {
      objects.forEach(obj => {
        dbStorage.minioObjects = dbStorage.minioObjects.filter(o => o.objectName !== obj || o.bucket !== bucket);
      });
    },
    removeObject: async (bucket, objectName) => {
      if (dbStorage.mockFailOrphanCleanup) {
         throw new Error('Simulated orphan cleanup failure');
      }
      dbStorage.minioObjects = dbStorage.minioObjects.filter(o => o.objectName !== objectName || o.bucket !== bucket);
    }
  }),
  DB_BASE_URL: 'mock-db' // Handled by fetch override
};

registerTaskActionRoutes(app, deps);
registerConsistencyRoutes(app, deps);

const server = http.createServer(app);
await new Promise(r => server.listen(0, r));
const port = server.address().port;
deps.DB_BASE_URL = `http://127.0.0.1:${port}`; // Set for dbGet in scanOrphansInternal

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

const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const urlStr = url.toString();
  
  if (urlStr.startsWith(`http://127.0.0.1:${port}`)) {
    return originalFetch(url, opts);
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
      }
    }
  }

  return { ok: true, json: async () => ({}) };
};

async function runTests() {
  console.log('=== P0 Reset Test Env Smoke Test (With Orphan Cleanup) ===');

  // Test setup
  dbStorage.tasks = [{ id: 't1', state: 'completed', materialId: 'm1', metadata: { markdownObjectName: 'parsed/m1/t1.md' } }];
  dbStorage.materials = [{ id: 'm1', status: 'completed', metadata: { objectName: 'originals/m1/m1.pdf', zipObjectName: 'parsed/m1/m1.zip' } }];
  dbStorage['task-events'] = [{ id: 'e1', taskId: 't1' }];
  dbStorage['ai-metadata-jobs'] = [{ id: 'j1' }, { id: 'j2' }];
  dbStorage['asset-details'] = { 'a1': { id: 'a1' }, 'a2': { id: 'a2' } };
  
  dbStorage.minioObjects = [
    { bucket: 'eduassets', objectName: 'originals/m1/m1.pdf' },
    { bucket: 'eduassets-parsed', objectName: 'parsed/m1/m1.zip' },
    { bucket: 'eduassets-parsed', objectName: 'parsed/m1/t1.md' },
    // 3 Orphan objects (2 in raw, 1 in parsed)
    { bucket: 'eduassets', objectName: 'originals/orphan1/test.pdf', size: 1000 },
    { bucket: 'eduassets', objectName: 'originals/orphan2/test2.pdf', size: 2000 },
    { bucket: 'eduassets-parsed', objectName: 'parsed/orphan3/test3.md', size: 500 }
  ];

  // Test 1: reset dry-run counts ai jobs but does not mutate
  console.log('Test 1: dry-run counts orphan objects and does not mutate');
  let res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.summary.deletedMinioOriginals, 1);
  assert.equal(res.body.summary.deletedMinioParsed, 2);
  assert.equal(res.body.summary.deletedOrphanObjects, 3);
  assert.equal(res.body.summary.deletedOrphanObjectBytes, 3500);
  
  const eduBuckets = res.body.summary.orphanBuckets.find(b => b.bucket === 'eduassets');
  assert.equal(eduBuckets.objects, 2);
  assert.equal(eduBuckets.bytes, 3000);
  
  const parsedBuckets = res.body.summary.orphanBuckets.find(b => b.bucket === 'eduassets-parsed');
  assert.equal(parsedBuckets.objects, 1);
  assert.equal(parsedBuckets.bytes, 500);
  
  assert.equal(dbStorage.minioObjects.length, 6); // untouched
  console.log('Test 1 Pass ✅');

  // Test 1.5: missing listAllObjects dependency makes reset fail
  console.log('Test 1.5: missing listAllObjects makes reset ok false (500)');
  const originalListAllObjects = deps.listAllObjects;
  deps.listAllObjects = null;
  res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 500);
  assert.equal(res.body.ok, false);
  assert.equal(res.body.error, 'orphan scan unavailable: listAllObjects dependency missing');
  assert.equal(res.body.details.orphanObjects.ok, false);
  deps.listAllObjects = originalListAllObjects; // Restore
  console.log('Test 1.5 Pass ✅');

  // Test 2: orphan cleanup failure makes reset ok false
  console.log('Test 2: orphan cleanup failure makes reset ok false');
  dbStorage.mockFailOrphanCleanup = true;
  res = await mockRequest('/ops/reset-test-env', 'POST', {});
  assert.equal(res.status, 500);
  assert.equal(res.body.ok, false);
  assert.equal(res.body.details.orphanObjects.ok, false);
  assert.equal(res.body.details.orphanObjects.error, 'Simulated orphan cleanup failure');
  // At this point DB is cleared, DB minio objects are cleared, but orphans remain
  assert.equal(dbStorage.tasks.length, 0);
  assert.equal(dbStorage.minioObjects.length, 3); // 6 - 3 = 3 orphans left
  console.log('Test 2 Pass ✅');

  // Test 3: execute deletes orphan objects
  console.log('Test 3: execute deletes orphan objects');
  dbStorage.mockFailOrphanCleanup = false;
  res = await mockRequest('/ops/reset-test-env', 'POST', {});
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.details.orphanObjects.ok, true);
  assert.equal(res.body.summary.deletedOrphanObjects, 3); // It deleted the 3 remaining orphans
  assert.equal(res.body.summary.orphanBuckets.length, 2);
  assert.equal(dbStorage.minioObjects.length, 0); // All clean
  console.log('Test 3 Pass ✅');

  // Test 4: execute then dry-run returns zero including assetDetails and orphans
  console.log('Test 4: execute then dry-run returns zero');
  res = await mockRequest('/ops/reset-test-env', 'POST', { dryRun: true });
  assert.equal(res.status, 200);
  assert.equal(res.body.summary.deletedMaterials, 0);
  assert.equal(res.body.summary.deletedAssetDetails, 0);
  assert.equal(res.body.summary.deletedOrphanObjects, 0);
  assert.equal(res.body.summary.deletedOrphanObjectBytes, 0);
  assert.equal(res.body.summary.orphanBuckets.length, 0);
  console.log('Test 4 Pass ✅');

  // Test 5: execute then audit consistency zero
  console.log('Test 5: execute then audit consistency zero');
  res = await mockRequest('/audit/consistency', 'GET');
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(res.body.findings.length, 0);
  console.log('Test 5 Pass ✅');

  console.log('=== All Tests Passed ===');
  globalThis.fetch = originalFetch;
  server.close();
}

runTests().catch(e => {
  console.error(e);
  server.close();
  process.exit(1);
});
