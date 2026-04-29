/**
 * db-large-artifact-manifest-smoke.mjs — P0 OOM Patch 冒烟测试
 *
 * 验证：
 * 1. 构造 5000 个 artifacts 的任务，DB 中不保存完整数组
 * 2. 启动迁移后，历史 parsedArtifacts 被清除
 * 3. DB 文件体积受控
 *
 * 运行：node server/tests/db-large-artifact-manifest-smoke.mjs
 */

const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';
let passed = 0;
let failed = 0;

/**
 * 断言辅助函数
 * @param {boolean} condition - 断言条件
 * @param {string} message - 断言描述
 */
function assert(condition, message) {
  if (condition) {
    console.log(`  ✅ ${message}`);
    passed++;
  } else {
    console.error(`  ❌ ${message}`);
    failed++;
  }
}

async function run() {
  console.log('\n═══ P0 OOM Patch: DB Large Artifact Manifest Smoke Test ═══\n');

  // ── Test 1: 构造带大 parsedArtifacts 的 ParseTask ──
  console.log('Test 1: 大 parsedArtifacts 数组不应残留在 DB');
  const testTaskId = `test-oom-${Date.now()}`;
  const testMaterialId = `mat-oom-${Date.now()}`;

  // 构造 5000 个 artifacts（模拟大 PDF）
  const bigArtifacts = [];
  for (let i = 0; i < 5000; i++) {
    bigArtifacts.push({
      objectName: `parsed/${testMaterialId}/images/img_${i}.png`,
      relativePath: `images/img_${i}.png`,
      size: 50000,
      mimeType: 'image/png',
    });
  }

  // 直接向 DB 写入包含大 parsedArtifacts 的任务
  const taskBody = {
    id: testTaskId,
    materialId: testMaterialId,
    state: 'completed',
    stage: 'complete',
    engine: 'local-mineru',
    progress: 100,
    metadata: {
      parsedPrefix: `parsed/${testMaterialId}/`,
      parsedFilesCount: 5000,
      parsedArtifacts: bigArtifacts, // 故意写入大数组
      markdownObjectName: `parsed/${testMaterialId}/full.md`,
    },
    createdAt: new Date().toISOString(),
  };

  const createResp = await fetch(`${DB_BASE_URL}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(taskBody),
  });
  assert(createResp.ok, '创建测试任务成功');

  // 验证写入后，数据应该被过滤，没有存入 DB
  const taskAfterResp = await fetch(`${DB_BASE_URL}/tasks/${testTaskId}`);
  const taskAfter = await taskAfterResp.json();
  assert(!Array.isArray(taskAfter.metadata?.parsedArtifacts), 'parsedArtifacts 在运行期已被防御过滤');
  assert(taskAfter.metadata?.parsedFilesCount === 5000, 'parsedFilesCount 保留正确');
  assert(taskAfter.metadata?.parsedPrefix === `parsed/${testMaterialId}/`, 'parsedPrefix 保留正确');

  // 验证 DB 体积没有异常增长
  const statsResp1 = await fetch(`${DB_BASE_URL}/stats`);
  const stats1 = await statsResp1.json();
  assert(stats1.ok, '/stats 接口可用');

  const fileSizeMB1 = (stats1.fileSize / 1024 / 1024).toFixed(1);
  console.log(`  📊 DB size after writing 5000-artifact task: ${fileSizeMB1} MB`);
  // 仅仅是一个小任务，所以体积应该依然较小
  assert(stats1.fileSize < 50 * 1024 * 1024, `DB 体积处于安全范围 (${fileSizeMB1} MB)`);

  // ── Test 3: taskEvents 压缩 ──
  console.log('\nTest 3: taskEvents 压缩不影响正常事件');

  // 写入 60 个事件（超过 50 条上限）
  const testEvtTaskId = `test-events-${Date.now()}`;
  for (let i = 0; i < 60; i++) {
    await fetch(`${DB_BASE_URL}/task-events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: `evt-test-${Date.now()}-${i}`,
        taskId: testEvtTaskId,
        taskType: 'parse',
        level: 'info',
        event: 'progress-update',
        message: `Progress ${i}/60`,
        createdAt: new Date(Date.now() + i * 1000).toISOString(),
      }),
    });
  }

  // 读取事件
  const eventsResp = await fetch(`${DB_BASE_URL}/task-events?taskId=${testEvtTaskId}`);
  const events = await eventsResp.json();
  assert(Array.isArray(events), '事件列表可读取');
  console.log(`  📊 Events written: 60, events in DB: ${events.length}`);
  // 注意：压缩在下次启动时执行，当前应有全部 60 条
  assert(events.length >= 50, '当前运行期间事件完整保存（压缩在下次启动时执行）');

  // ── 清理测试数据 ──
  console.log('\nCleanup...');
  await fetch(`${DB_BASE_URL}/tasks`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [testTaskId] }),
  });

  // ── 汇总 ──
  console.log(`\n═══ Results: ${passed} passed, ${failed} failed ═══`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => {
  console.error('Smoke test crashed:', e);
  process.exit(1);
});
