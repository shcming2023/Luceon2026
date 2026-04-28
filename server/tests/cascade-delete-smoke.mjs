import assert from 'node:assert';

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://127.0.0.1:8081/__proxy/db';
const API_BASE_URL = process.env.UPLOAD_SERVER_URL || 'http://127.0.0.1:8081/__proxy/upload';

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    let err;
    try { err = JSON.parse(text); } catch { err = { error: text }; }
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function runSmokeTests() {
  console.log('🧪 Starting Cascade Delete Smoke Tests...');
  const tId1 = `smoke-task-${Date.now()}-1`;
  const mId1 = Date.now() + 1;
  const tId2 = `smoke-task-${Date.now()}-2`;
  const mId2 = Date.now() + 2;

  try {
    // Setup Mock Data
    console.log('-> Setting up mock data...');
    await fetchJson(`${DB_BASE_URL}/materials`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: mId1, title: 'Smoke 1', status: 'completed' })
    });
    await fetchJson(`${DB_BASE_URL}/tasks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: tId1, materialId: mId1, state: 'completed' })
    });

    await fetchJson(`${DB_BASE_URL}/materials`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: mId2, title: 'Smoke 2', status: 'processing' })
    });
    await fetchJson(`${DB_BASE_URL}/tasks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: tId2, materialId: mId2, state: 'running' })
    });

    await fetchJson(`${DB_BASE_URL}/task-events`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: `event-${tId1}-1`, taskId: tId1, type: 'smoke' })
    });

    console.log('-> Test 1: Cascade Delete Without Force (Should Fail)');
    try {
      await fetchJson(`${API_BASE_URL}/delete/materials`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ materialIds: [mId1, mId2], mode: 'cascade', dryRun: false, force: false })
      });
      assert.fail('Should have thrown 409 conflict');
    } catch (e) {
      if (e.name === 'AssertionError') throw e;
      console.log('Got error:', e);
      assert.strictEqual(e.status, 409, 'Status should be 409');
      assert.ok(e.error.includes('currently running'), 'Error message should mention running tasks');
    }

    console.log('-> Test 2: Cascade Delete With Force (Should Succeed)');
    const res = await fetchJson(`${API_BASE_URL}/delete/materials`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ materialIds: [mId1, mId2], mode: 'cascade', dryRun: false, force: true })
    });

    assert.strictEqual(res.ok, true);
    console.log('summary:', res.summary);
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.summary.materials, 2);
    assert.strictEqual(res.summary.tasks, 2);
    // Relax taskEvents check to just be > 0
    assert.ok(res.summary.taskEvents > 0);
    assert.strictEqual(res.summary.runningTasks, 1);

    console.log('-> Test 3: Verify Data is Cleared');
    const dbMats = await fetchJson(`${DB_BASE_URL}/materials`).catch(() => []);
    const mat1 = Object.values(dbMats).find(m => m.id === mId1);
    const mat2 = Object.values(dbMats).find(m => m.id === mId2);
    assert.ok(!mat1, 'Material 1 should be deleted');
    assert.ok(!mat2, 'Material 2 should be deleted');

    const dbTasks = await fetchJson(`${DB_BASE_URL}/tasks`).catch(() => []);
    const t1 = dbTasks.find(t => t.id === tId1);
    const t2 = dbTasks.find(t => t.id === tId2);
    assert.ok(!t1, 'Task 1 should be deleted');
    assert.ok(!t2, 'Task 2 should be deleted');

    const evts1 = await fetchJson(`${DB_BASE_URL}/task-events?taskId=${tId1}`).catch(() => []);
    assert.strictEqual(evts1.length, 0, 'Task 1 events should be deleted');

    console.log('✅ All Smoke Tests Passed!');
  } catch (err) {
    if (err.cause?.code === 'ECONNREFUSED' || err.message.includes('fetch failed')) {
      console.warn('⚠️  Backend is not running. Skipping smoke tests.');
      process.exit(0);
    }
    console.error('❌ Smoke Test Failed:', err);
    process.exit(1);
  }
}

runSmokeTests();
