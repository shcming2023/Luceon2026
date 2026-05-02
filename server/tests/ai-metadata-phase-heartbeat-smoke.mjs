import assert from 'node:assert';
import { AiMetadataWorker } from '../services/ai/metadata-worker.mjs';

async function runTests() {
  console.log('--- AI Metadata Phase Heartbeat Smoke Test ---');
  
  const originalFetch = globalThis.fetch;
  const requests = [];
  
  globalThis.fetch = async (url, options) => {
    if (typeof url === 'string' && url.includes('localhost:8789')) {
      requests.push({ url, options });
      return { ok: true, json: async () => ({}) };
    }
    return originalFetch(url, options);
  };

  const mockMinio = {
    getFileStream: async () => ({ [Symbol.asyncIterator]: async function* () { yield Buffer.from('mock'); } }),
    saveObject: async () => {}
  };
  
  const worker = new AiMetadataWorker(mockMinio);
  const job = {
    id: 'test-job-heartbeat',
    parseTaskId: 'test-task-heartbeat',
    metadata: { oldField: 'keep' }
  };

  await worker._updatePhase(job, 'repair-pass-running', 40, '正在进行 JSON Repair...');

  const jobReq = requests.find(r => r.url.includes('/ai-metadata-jobs/'));
  assert.ok(jobReq, 'Must patch /ai-metadata-jobs/');
  assert.equal(jobReq.url, 'http://localhost:8789/ai-metadata-jobs/test-job-heartbeat');
  
  const badJobReq = requests.find(r => r.url.match(/\/jobs\/test-job-heartbeat/));
  assert.ok(!badJobReq, 'Must not patch /jobs/');

  const jobPayload = JSON.parse(jobReq.options.body);
  assert.equal(jobPayload.progress, 40);
  assert.equal(jobPayload.message, '正在进行 JSON Repair...');
  assert.equal(jobPayload.metadata.currentPhase, 'repair-pass-running');
  assert.equal(jobPayload.metadata.oldField, 'keep');
  assert.ok(jobPayload.metadata.lastHeartbeatAt);
  assert.ok(jobPayload.metadata.phaseStartedAt);

  const taskReq = requests.find(r => r.url.includes('/tasks/'));
  assert.ok(taskReq, 'Must patch /tasks/');
  assert.equal(taskReq.url, 'http://localhost:8789/tasks/test-task-heartbeat');
  const taskPayload = JSON.parse(taskReq.options.body);
  assert.equal(taskPayload.state, 'ai-running');
  assert.equal(taskPayload.stage, 'ai');
  assert.equal(taskPayload.message, 'AI: 正在进行 JSON Repair...');
  assert.ok(taskPayload.updatedAt);

  console.log('Phase heartbeat correctly targets /ai-metadata-jobs/ ✅');

  globalThis.fetch = originalFetch;
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
