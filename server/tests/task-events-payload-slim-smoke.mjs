/**
 * task-events-payload-slim-smoke.mjs
 * 验证 P0 Patch 2 任务事件 payload 瘦身防御：
 * 1. 禁止完整 metadata / parsedArtifacts 进入 payload
 * 2. 大小保护机制（截断 > 2048 字符）
 */
import { logTaskEvent } from '../services/logging/task-events.mjs';

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
  console.log('\n═══ P0 Patch 2: Task Events Payload Slim Smoke Test ═══\n');
  const taskId = `test-events-slim-${Date.now()}`;

  // 1. 尝试传入带大数组的 payload
  console.log('Test 1: 过滤 metadata 与 parsedArtifacts');
  await logTaskEvent({
    taskId,
    event: 'worker-completed',
    payload: {
      metadata: {
        parsedFilesCount: 50,
        parsedArtifacts: Array(1000).fill({ name: 'fake', size: 100 }), // 巨大数组
        artifactManifestObjectName: 'fake.json'
      },
      state: 'completed',
      stage: 'complete'
    }
  });

  const evts = await (await fetch(`${DB_BASE_URL}/task-events?taskId=${taskId}`)).json();
  const evt1 = evts.find(e => e.event === 'worker-completed');
  assert(evt1 !== undefined, '事件已写入');
  assert(evt1.payload.metadata === undefined, '完整 metadata 已被清理');
  assert(evt1.payload.parsedArtifacts === undefined, '完整 parsedArtifacts 已被清理');
  assert(evt1.payload.parsedFilesCount === 50, '摘要字段 parsedFilesCount 已提取');
  assert(evt1.payload.artifactManifestObjectName === 'fake.json', '摘要字段 artifactManifestObjectName 已提取');

  // 2. 尝试传入长达 5000 字符的 error message
  console.log('\nTest 2: Payload > 2KB 大小截断保护');
  const hugeError = 'A'.repeat(5000);
  await logTaskEvent({
    taskId,
    event: 'error-occurred',
    payload: {
      error: hugeError,
      state: 'failed'
    }
  });

  const evts2 = await (await fetch(`${DB_BASE_URL}/task-events?taskId=${taskId}`)).json();
  const evt2 = evts2.find(e => e.event === 'error-occurred');
  assert(evt2 !== undefined, '事件已写入');
  assert(evt2.payloadTruncated === true, '触发 payloadTruncated 标记');
  assert(evt2.payload.error.length <= 500, `长报错被截断 (当前长度: ${evt2.payload.error.length})`);
  assert(JSON.stringify(evt2.payload).length < 2048, '最终 payload JSON 大小受控');

  // 清理
  for (const e of evts2) {
    await fetch(`${DB_BASE_URL}/task-events/${e.id}`, { method: 'DELETE' });
  }

  console.log(`\n═══ Results: ${passed} passed, ${failed} failed ═══`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => {
  console.error('Smoke test crashed:', e);
  process.exit(1);
});
