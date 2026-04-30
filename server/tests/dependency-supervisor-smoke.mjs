import { spawn } from 'child_process';
import assert from 'assert';

const PORT = 18089;
const BASE_URL = `http://127.0.0.1:${PORT}`;

async function runTest() {
  console.log('--- Dependency Supervisor Smoke Test ---');

  // Start the supervisor with mocked exec and isolated port
  const supervisor = spawn('node', ['ops/luceon-dependency-supervisor.mjs'], {
    env: { ...process.env, MOCK_EXEC: 'true', SUPERVISOR_PORT: PORT.toString() },
    stdio: ['ignore', 'pipe', 'pipe']
  });

  let outputLog = '';
  supervisor.stdout.on('data', d => { outputLog += d.toString(); });
  supervisor.stderr.on('data', d => { outputLog += d.toString(); });

  // Wait for the server to start
  await new Promise(resolve => setTimeout(resolve, 1500));

  try {
    // 1. Test /status
    const statusRes = await fetch(`${BASE_URL}/status`);
    const statusData = await statusRes.json();
    assert.strictEqual(statusData.ok, true, 'Status should be ok');
    assert.ok(statusData.sessions, 'Status should return sessions object');
    assert.strictEqual(statusData.sessions.mineru, false, 'mineru session should be false in mock');
    console.log('✅ /status returns correctly');

    // 2. Test invalid action returns 400
    const invalidRes = await fetch(`${BASE_URL}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'rm -rf /' })
    });
    assert.strictEqual(invalidRes.status, 400, 'Invalid action should return 400');
    console.log('✅ Invalid action returns 400');

    // 3. Test start-mineru uses tmux background command
    const startRes = await fetch(`${BASE_URL}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'start-mineru' })
    });
    const startData = await startRes.json();
    assert.strictEqual(startData.ok, true, 'start-mineru should be ok');
    assert.strictEqual(startData.detached, true, 'start-mineru should return detached: true');
    assert.strictEqual(startData.session, 'luceon-mineru', 'start-mineru should return correct session');
    
    // Verify the mock log contains the expected tmux new-session command
    assert.ok(outputLog.includes('tmux new-session -d -s luceon-mineru'), 'Log should contain tmux new-session');
    console.log('✅ start-mineru uses detached tmux new-session');

    // 4. Test restart-mineru includes kill session + new session
    outputLog = ''; // clear log
    const restartRes = await fetch(`${BASE_URL}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'restart-mineru' })
    });
    const restartData = await restartRes.json();
    assert.strictEqual(restartData.ok, true, 'restart-mineru should be ok');
    assert.strictEqual(restartData.detached, true, 'restart-mineru should return detached: true');
    
    assert.ok(outputLog.includes('tmux kill-session -t luceon-mineru'), 'Log should contain tmux kill-session');
    assert.ok(outputLog.includes('tmux new-session -d -s luceon-mineru'), 'Log should contain tmux new-session after kill');
    console.log('✅ restart-mineru uses kill-session and new-session');

    // 5. Test start-sidecar
    const sidecarRes = await fetch(`${BASE_URL}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'start-sidecar' })
    });
    const sidecarData = await sidecarRes.json();
    assert.strictEqual(sidecarData.ok, true, 'start-sidecar should be ok');
    assert.strictEqual(sidecarData.detached, true, 'start-sidecar should return detached: true');
    console.log('✅ start-sidecar uses detached tmux new-session');

    console.log('--- Dependency Supervisor Smoke Test Passed ---');
  } catch (e) {
    console.error('Test error:', e);
    console.log('Supervisor output:', outputLog);
    throw e;
  } finally {
    supervisor.kill();
  }
}

runTest().catch(err => {
  console.error('Smoke test failed');
  process.exit(1);
});
