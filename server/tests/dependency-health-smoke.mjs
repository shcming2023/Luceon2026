import assert from 'assert';
import express from 'express';
import multer from 'multer';
import { spawn } from 'child_process';

if (!globalThis.fetch) {
  globalThis.fetch = fetch;
}

let failedCount = 0;
let passedCount = 0;

function assertEqual(actual, expected, msg) {
  try {
    assert.deepStrictEqual(actual, expected);
    passedCount++;
    console.log(`[PASS] ${msg}`);
  } catch (err) {
    failedCount++;
    console.error(`[FAIL] ${msg}`);
    console.error(`  Expected:`, expected);
    console.error(`  Actual:  `, actual);
  }
}

async function runTest() {
  console.log('--- dependency-health-smoke ---');

  // We will run the real upload-server as a child process
  // But first, we create dummy db-server, mineru-server, and ollama-server
  
  const dummyApp = express();
  let mineruOk = false;
  let ollamaOk = false;

  // DB Server settings mock
  dummyApp.get('/settings', (req, res) => {
    res.json({
      mineruConfig: { localEndpoint: 'http://127.0.0.1:18083' },
      aiConfig: { enabled: true, providers: [{ enabled: true, priority: 1, apiEndpoint: 'http://127.0.0.1:11434' }] }
    });
  });

  // DB Server other mocks needed by POST /tasks
  dummyApp.get('/tasks', (req, res) => res.json([]));
  dummyApp.post('/materials', (req, res) => res.json({ id: 'test-mat' }));
  dummyApp.get('/materials/:id', (req, res) => res.status(404).json({}));
  dummyApp.post('/tasks', (req, res) => res.json({ id: 'test-task' }));

  // MinerU Mock
  dummyApp.get('/mineru/health', (req, res) => {
    if (mineruOk) res.sendStatus(200);
    else res.sendStatus(503);
  });

  // Ollama Mock
  dummyApp.get('/ollama/api/tags', (req, res) => {
    if (ollamaOk) res.json({ models: [] });
    else res.sendStatus(503);
  });

  // MinIO Mock
  dummyApp.use((req, res, next) => {
    console.log(`[dummyApp] ${req.method} ${req.url}`);
    if (req.method === 'PUT' || req.method === 'HEAD' || req.method === 'GET') {
      res.set('ETag', '"dummy-etag"');
      res.sendStatus(200);
    } else {
      next();
    }
  });

  const dummyServer = await new Promise((resolve) => {
    const s = dummyApp.listen(0, '127.0.0.1', () => resolve(s));
  });
  const dummyPort = dummyServer.address().port;

  // Rewrite endpoints to hit the dummy server
  // mineru -> http://127.0.0.1:dummyPort/mineru
  // ollama -> http://127.0.0.1:dummyPort/ollama
  const dbApp = express();
  dbApp.get('/settings', (req, res) => {
    res.json({
      mineruConfig: { localEndpoint: `http://127.0.0.1:${dummyPort}/mineru` },
      aiConfig: { enabled: true, providers: [{ enabled: true, priority: 1, apiEndpoint: `http://127.0.0.1:${dummyPort}/ollama` }] }
    });
  });
  dbApp.get('/tasks', (req, res) => res.json([]));
  dbApp.post('/materials', (req, res) => res.json({ id: 'test-mat' }));
  dbApp.get('/materials/:id', (req, res) => res.status(404).json({}));
  dbApp.post('/tasks', (req, res) => res.json({ id: 'test-task' }));
  dbApp.post('/task-events', (req, res) => res.json({}));

  const dbServer = await new Promise((resolve) => {
    const s = dbApp.listen(0, '127.0.0.1', () => resolve(s));
  });
  const dbPort = dbServer.address().port;

  const uploadServerBind = await new Promise((resolve) => {
    const s = express().listen(0, '127.0.0.1', () => resolve(s));
  });
  const uploadPort = uploadServerBind.address().port;
  uploadServerBind.close();

  const child = spawn('node', ['server/upload-server.mjs'], {
    env: {
      ...process.env,
      UPLOAD_PORT: String(uploadPort),
      DB_BASE_URL: `http://127.0.0.1:${dbPort}`,
      STORAGE_BACKEND: 'tmpfiles', // bypass MinIO actual bucket check for this test since we focus on mineru/ollama logic blocking
      ALLOW_LOCAL_AI_ENDPOINT: 'true',
      MINIO_ENDPOINT: '127.0.0.1',
      MINIO_PORT: String(dummyPort),
      DEPENDENCY_HEALTH_REWRITE_LOCAL_ENDPOINTS: 'false'
    }
  });
  
  child.stdout.on('data', d => console.log('[upload-server]', d.toString().trim()));
  child.stderr.on('data', d => console.error('[upload-server err]', d.toString().trim()));
  child.on('exit', code => console.log('[upload-server] EXITED with code', code));

  const uploadBase = `http://127.0.0.1:${uploadPort}`;
  // Wait for upload-server to start by polling /health
  let serverReady = false;
  for (let i = 0; i < 20; i++) {
    try {
      const hRes = await fetch(`${uploadBase}/health`);
      if (hRes.ok) {
        serverReady = true;
        break;
      }
    } catch (e) {
      // ignore
    }
    await new Promise(r => setTimeout(r, 500));
  }
  if (!serverReady) {
    console.error('upload-server failed to start in time');
    process.exit(1);
  }

  try {
    // Test 1: dependency-health reports mineru down
    mineruOk = false;
    ollamaOk = true;
    let res = await fetch(`${uploadBase}/ops/dependency-health`);
    let data = await res.json();
    assertEqual(data.ok, false, 'health.ok should be false when mineru is down');
    assertEqual(data.blocking, true, 'health.blocking should be true');
    assertEqual(data.dependencies.mineru.ok, false, 'mineru.ok should be false');

    // Test 2: POST /tasks blocked when mineru down
    const form1 = new FormData();
    const blob1 = new Blob(['dummy pdf content'], { type: 'application/pdf' });
    form1.append('file', blob1, 'test.pdf');
    
    let taskRes = await fetch(`${uploadBase}/tasks`, { method: 'POST', body: form1 });
    assertEqual(taskRes.status, 503, 'POST /tasks should return 503 when mineru down');
    let taskData = await taskRes.json();
    assertEqual(taskData.code, 'DEPENDENCY_UNHEALTHY', 'Should return DEPENDENCY_UNHEALTHY code');
    assertEqual(taskData.blockingDependency, 'mineru', 'blocking dependency should be mineru');

    // Test 3: POST /tasks allowed when mineru and minio ok
    mineruOk = true;
    // (minio ok is implicit via tmpfiles backend mock)
    const form2 = new FormData();
    form2.append('file', blob1, 'test.pdf');
    taskRes = await fetch(`${uploadBase}/tasks`, { method: 'POST', body: form2 });
    if (taskRes.status !== 200) {
      console.error('Test 3 failed payload:', await taskRes.text());
    }
    assertEqual(taskRes.status, 200, 'POST /tasks should succeed (200) when dependencies are ok');

    // Test 4: ollama down does not block parse but is reported
    mineruOk = true;
    ollamaOk = false;
    let resOllamaDown = await fetch(`${uploadBase}/ops/dependency-health`);
    let dataOllamaDown = await resOllamaDown.json();
    assertEqual(dataOllamaDown.dependencies.mineru.ok, true, 'mineru.ok should be true');
    assertEqual(dataOllamaDown.dependencies.ollama.ok, false, 'ollama.ok should be false');
    assertEqual(dataOllamaDown.dependencies.ollama.blockingParse, false, 'ollama.blockingParse should be false');
    assertEqual(dataOllamaDown.blocking, false, 'blocking should be false when only ollama is down');

    const form3 = new FormData();
    form3.append('file', blob1, 'test.pdf');
    taskRes = await fetch(`${uploadBase}/tasks`, { method: 'POST', body: form3 });
    if (taskRes.status !== 200) {
      console.error('Test 4 failed payload:', await taskRes.text());
    }
    assertEqual(taskRes.status, 200, 'POST /tasks should succeed even if ollama is down');

  } finally {
    child.kill();
    dummyServer.close();
    dbServer.close();
  }

  console.log(`\nResults: ${passedCount} passed, ${failedCount} failed`);
  if (failedCount > 0) process.exit(1);
}

runTest().catch((err) => {
  console.error(err);
  process.exit(1);
});
