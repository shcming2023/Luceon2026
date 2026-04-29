/**
 * db-runtime-payload-sanitize-smoke.mjs
 * 验证 P0 Patch 2 DB 运行期防御：
 * 1. POST/PATCH /tasks 不写入 parsedArtifacts
 * 2. PATCH metadata 浅合并时正确处理显式删除
 */


const DB_BASE_URL = process.env.DB_BASE_URL || 'http://localhost:8789';
let passed = 0;
let failed = 0;

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
  console.log('\n═══ P0 Patch 2: DB Runtime Payload Sanitize Smoke Test ═══\n');
  const taskId = `test-sanitize-${Date.now()}`;
  const materialId = `mat-sanitize-${Date.now()}`;

  // 1. POST /tasks 带有大 parsedArtifacts
  console.log('Test 1: POST /tasks 应该过滤 parsedArtifacts');
  let createResp;
  try {
    createResp = await fetch(`${DB_BASE_URL}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: taskId,
        materialId,
        metadata: {
          parsedFilesCount: 50,
          parsedArtifacts: [{ name: 'fake', size: 100 }],
          artifactManifestObjectName: 'fake.json'
        }
      })
    });
  } catch (e) {
    if (e.code === 'ECONNREFUSED' || e.cause?.code === 'ECONNREFUSED') {
      console.error('\n❌ 错误: 真实服务不可达！请在 Lucia 部署环境执行 docker compose up -d --build 后运行。');
      process.exit(1);
    }
    throw e;
  }
  assert(createResp.ok, '创建任务成功');

  const t1 = await (await fetch(`${DB_BASE_URL}/tasks/${taskId}`)).json();
  assert(t1.metadata.parsedArtifacts === undefined, 'POST 创建后无 parsedArtifacts');
  assert(t1.metadata.parsedFilesCount === 50, '保留了 parsedFilesCount');

  // 2. PATCH /tasks 带有大 parsedArtifacts
  console.log('\nTest 2: PATCH /tasks 应该过滤 parsedArtifacts');
  await fetch(`${DB_BASE_URL}/tasks/${taskId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      metadata: {
        parsedArtifacts: [{ name: 'fake2', size: 200 }],
        parsedFilesCount: 100
      }
    })
  });
  const t2 = await (await fetch(`${DB_BASE_URL}/tasks/${taskId}`)).json();
  assert(t2.metadata.parsedArtifacts === undefined, 'PATCH 更新后无 parsedArtifacts');
  assert(t2.metadata.parsedFilesCount === 100, '更新了 parsedFilesCount');

  // 3. 模拟旧数据中有 parsedArtifacts，然后 PATCH
  console.log('\nTest 3: PATCH 处理浅合并和显式 null 删除');
  // 绕过 API，直接修改 DB 以模拟旧数据
  // 由于我们无法直接访问 DB 文件，我们可以测试 PATCH 传 null 能否清掉任何残留
  await fetch(`${DB_BASE_URL}/tasks/${taskId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      metadata: {
        parsedArtifacts: null
      }
    })
  });
  const t3 = await (await fetch(`${DB_BASE_URL}/tasks/${taskId}`)).json();
  // 不仅应该没有数组，而且键应该被完全 delete
  assert(!('parsedArtifacts' in (t3.metadata || {})), 'PATCH 显式 null 后键被完全删除');

  // 清理
  await fetch(`${DB_BASE_URL}/tasks`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [taskId] })
  });

  console.log(`\n═══ Results: ${passed} passed, ${failed} failed ═══`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => {
  console.error('Smoke test crashed:', e);
  process.exit(1);
});
