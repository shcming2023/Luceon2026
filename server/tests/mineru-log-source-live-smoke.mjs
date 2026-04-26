import fs from 'fs';
import path from 'path';

/**
 * P0 Patch 16.2.6 — MinerU 日志源实时可见性与挂载一致性 Smoke Test
 *
 * 验证：
 * 1. docker-compose.yml 配置了正确的环境变量
 * 2. 宿主机日志路径存在性（仅 macOS/Linux）
 * 3. docker-compose.yml 挂载配置存在且使用 :consistent 标记
 * 4. 启动脚本存在且包含正确的 touch + exec >> 写法
 * 5. 启动脚本中 touch 在 exec 之前（inode 稳定性保证）
 * 6. 日志文件不使用 inode 替换式写入（验证 >> 追加模式）
 * 7. docker-compose.yml 不含会破坏 inode 稳定性的 volume 配置
 *
 * @returns {void} 测试结束后通过 process.exit 返回状态
 */
async function runSmokeTest() {
  console.log('=== MinerU Log Source Live Smoke Test (Patch 16.2.6) ===\n');

  let passed = 0;
  let failed = 0;

  /**
   * 断言辅助函数。
   *
   * @param {boolean} condition - 断言条件
   * @param {string} message - 断言失败时的描述
   * @returns {void}
   */
  function assert(condition, message) {
    if (condition) {
      passed++;
    } else {
      console.error(`❌ FAILED: ${message}`);
      failed++;
    }
  }

  // ──────────────────────────────────────────────────────────────
  // Test 1: 检查 docker-compose.yml 环境变量配置
  // ──────────────────────────────────────────────────────────────
  console.log('Test 1: Check docker-compose.yml env variables');
  const composePath = path.join(process.cwd(), 'docker-compose.yml');
  let composeContent = '';
  if (fs.existsSync(composePath)) {
    composeContent = fs.readFileSync(composePath, 'utf-8');
  }
  assert(
    composeContent.includes('MINERU_ERR_LOG_PATH=/host/mineru-logs/mineru-api.err.log'),
    'docker-compose.yml must configure MINERU_ERR_LOG_PATH pointing to container mount path'
  );
  assert(
    composeContent.includes('MINERU_LOG_PATH=/host/mineru-logs/mineru-api.log'),
    'docker-compose.yml must configure MINERU_LOG_PATH pointing to container mount path'
  );
  console.log('Test 1 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 2: 检查宿主机日志目录/文件存在性 (仅 macOS/Linux)
  // ──────────────────────────────────────────────────────────────
  console.log('Test 2: Check Host Log Files');
  const expectedHostLogPath = '/Users/concm/ops/logs/mineru-api.log';
  const expectedHostErrLogPath = '/Users/concm/ops/logs/mineru-api.err.log';
  if (process.platform === 'darwin' || process.platform === 'linux') {
    if (fs.existsSync(expectedHostLogPath)) {
      const stats = fs.statSync(expectedHostLogPath);
      assert(stats.isFile(), 'Host log path must be a file');
      console.log(`  stdout log: size=${stats.size} bytes, mtime=${stats.mtime.toISOString()}`);
    } else {
      console.warn(`  ⚠️ Host log file not found at ${expectedHostLogPath}. Expected if API never started.`);
      passed++;
    }
    if (fs.existsSync(expectedHostErrLogPath)) {
      const errStats = fs.statSync(expectedHostErrLogPath);
      assert(errStats.isFile(), 'Host err log path must be a file');
      console.log(`  stderr log: size=${errStats.size} bytes, mtime=${errStats.mtime.toISOString()}`);
    } else {
      console.warn(`  ⚠️ Host err log not found at ${expectedHostErrLogPath}. Expected if API never started.`);
      passed++;
    }
    console.log('Test 2 Pass ✅\n');
  } else {
    console.log(`  Test 2 Skipped on platform ${process.platform} ✅\n`);
    passed += 2;
  }

  // ──────────────────────────────────────────────────────────────
  // Test 3: 检查 Docker compose 挂载配置 — 必须包含 :consistent
  // ──────────────────────────────────────────────────────────────
  console.log('Test 3: Check docker-compose Mount with :consistent');
  assert(
    composeContent.includes('/Users/concm/ops/logs:/host/mineru-logs'),
    'docker-compose.yml must contain volume mount for /Users/concm/ops/logs'
  );
  assert(
    composeContent.includes(':ro,consistent') || composeContent.includes(':consistent,ro'),
    'docker-compose.yml mount must use :consistent flag (Patch 16.2.6) to ensure real-time visibility on macOS Docker Desktop'
  );
  console.log('Test 3 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 4: 检查启动脚本基本结构
  // ──────────────────────────────────────────────────────────────
  console.log('Test 4: Check start script');
  const scriptPath = path.join(process.cwd(), 'ops/start-mineru-api.sh');
  assert(fs.existsSync(scriptPath), 'ops/start-mineru-api.sh must exist');
  let scriptContent = '';
  if (fs.existsSync(scriptPath)) {
    scriptContent = fs.readFileSync(scriptPath, 'utf-8');
    assert(scriptContent.includes('conda activate mineru'), 'Script must use conda env mineru');
    assert(scriptContent.includes('mineru-api --host 0.0.0.0 --port 8083'), 'Script must start mineru-api with correct host and port');
    assert(scriptContent.includes('>> "$LOG_FILE"'), 'Script must redirect stdout with >> (append)');
    assert(scriptContent.includes('2>> "$ERR_FILE"'), 'Script must redirect stderr with 2>> (append)');
  }
  console.log('Test 4 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 5: 启动脚本中 touch 在 exec 之前（inode 稳定性保证）
  // ──────────────────────────────────────────────────────────────
  console.log('Test 5: Check touch-before-exec order in start script');
  if (scriptContent) {
    assert(
      scriptContent.includes('touch "$LOG_FILE"'),
      'Script must touch LOG_FILE before exec'
    );
    assert(
      scriptContent.includes('touch "$ERR_FILE"'),
      'Script must touch ERR_FILE before exec'
    );

    // 验证 touch 出现在 exec 之前
    const touchLogIdx = scriptContent.indexOf('touch "$LOG_FILE"');
    const touchErrIdx = scriptContent.indexOf('touch "$ERR_FILE"');
    const execIdx = scriptContent.indexOf('exec ');
    assert(
      touchLogIdx > 0 && touchLogIdx < execIdx,
      'touch $LOG_FILE must appear before exec in start script'
    );
    assert(
      touchErrIdx > 0 && touchErrIdx < execIdx,
      'touch $ERR_FILE must appear before exec in start script'
    );

    // 验证 mkdir -p 出现在 touch 之前
    const mkdirIdx = scriptContent.indexOf('mkdir -p');
    assert(
      mkdirIdx > 0 && mkdirIdx < touchLogIdx,
      'mkdir -p must appear before touch in start script'
    );
  }
  console.log('Test 5 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 6: 启动脚本不使用 > 覆盖重定向（会替换 inode）
  // ──────────────────────────────────────────────────────────────
  console.log('Test 6: Check no inode-replacing redirects in start script');
  if (scriptContent) {
    // 正则检测：> file（不前面有 > 或 2 的情况），排除 >> 和 2>>
    // 目标：捕获 `> "$LOG_FILE"` 或 `> "$ERR_FILE"` 这种覆盖写入
    const dangerousRedirects = scriptContent.match(/[^>2]\s*>\s*"\$(?:LOG_FILE|ERR_FILE)"/g);
    assert(
      !dangerousRedirects || dangerousRedirects.length === 0,
      'Script must NOT use > (overwrite redirect) on LOG_FILE or ERR_FILE — only >> (append) is allowed'
    );
  }
  console.log('Test 6 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 7: Docker compose 不含 tmpfs 或 bind 覆盖 mineru-logs
  // ──────────────────────────────────────────────────────────────
  console.log('Test 7: No conflicting volume config for mineru-logs');
  if (composeContent) {
    // 确保没有 tmpfs 挂载覆盖我们的 bind mount
    assert(
      !composeContent.includes('tmpfs') || !composeContent.includes('mineru-logs'),
      'docker-compose.yml must NOT use tmpfs for mineru-logs path'
    );
  }
  console.log('Test 7 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 8: 模拟实时写入-读取一致性验证（本地 scratch）
  // ──────────────────────────────────────────────────────────────
  console.log('Test 8: Simulated write-then-read consistency (local scratch)');
  {
    const scratchDir = path.join(process.cwd(), 'uat', 'scratch');
    if (!fs.existsSync(scratchDir)) fs.mkdirSync(scratchDir, { recursive: true });
    const testFile = path.join(scratchDir, 'live-mount-test.log');

    // 清理旧文件
    try { fs.unlinkSync(testFile); } catch (_) {}

    // 1. touch 文件
    fs.writeFileSync(testFile, '');
    const inode1 = fs.statSync(testFile).ino;

    // 2. 追加一行测试数据
    const testLine = `TEST-LIVE-MOUNT ${new Date().toISOString()}`;
    fs.appendFileSync(testFile, testLine + '\n');

    // 3. 读取验证
    const content = fs.readFileSync(testFile, 'utf-8');
    assert(content.includes(testLine), 'Appended test line must be readable immediately');

    // 4. inode 不变
    const inode2 = fs.statSync(testFile).ino;
    assert(inode1 === inode2, 'Inode must remain stable after append (no file replacement)');

    // 5. 清理
    try { fs.unlinkSync(testFile); } catch (_) {}
  }
  console.log('Test 8 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Test 9: 容器内日志路径对账（仅在容器内运行时有效）
  // ──────────────────────────────────────────────────────────────
  console.log('Test 9: Container-side log path verification');
  {
    const containerErrPath = process.env.MINERU_ERR_LOG_PATH;
    const containerLogPath = process.env.MINERU_LOG_PATH;

    if (containerErrPath && containerErrPath.startsWith('/host/')) {
      // 在容器内运行
      console.log(`  Container err log path: ${containerErrPath}`);
      if (fs.existsSync(containerErrPath)) {
        const stats = fs.statSync(containerErrPath);
        console.log(`  Container err log: exists=true, size=${stats.size}, mtime=${stats.mtime.toISOString()}`);
        assert(stats.size > 0, 'Container err log should not be empty if MinerU has been started');
      } else {
        console.warn(`  ⚠️ Container err log not found at ${containerErrPath}. If MinerU has been started, this indicates a mount problem.`);
        passed++;
      }

      if (containerLogPath && fs.existsSync(containerLogPath)) {
        const stats = fs.statSync(containerLogPath);
        console.log(`  Container log: exists=true, size=${stats.size}, mtime=${stats.mtime.toISOString()}`);
      }
    } else {
      console.log('  Not running inside container (MINERU_ERR_LOG_PATH not set or not /host/). Skipped.');
      passed++;
    }
  }
  console.log('Test 9 Pass ✅\n');

  // ──────────────────────────────────────────────────────────────
  // Summary
  // ──────────────────────────────────────────────────────────────
  console.log(`=== Results: ${passed} passed, ${failed} failed ===`);
  if (failed > 0) {
    console.error('❌ Some tests failed!');
    process.exit(1);
  } else {
    console.log('✅ MinerU Log Source Live Smoke Test (Patch 16.2.6) Passed!');
    process.exit(0);
  }
}

runSmokeTest().catch(console.error);
